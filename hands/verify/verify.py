#!/usr/bin/env python3
"""verify 도구 — 산출물을 «다른 작업자»가 판정하고, 증거를 해시로 동결하는 장부.

사용법:
  verify.py judge <파일…> --criteria "<기준>" [--model haiku|sonnet] [--generator <생성자 sid>] [--host H] [--ledger L]
      파일 sha256 → 판정 기기 <workdir>/verify/<id>/ 로 복사(원격 sha256 대조) → mm <host> ask 로 판정
      → 장부(JSONL)에 해시 체인 행 추가. 판정자 신원 = ask 출력의 worker 세션 ID(자기 신고 아님).
  verify.py check [--ledger L] [--strict]
      모든 행의 파일 해시를 다시 계산 → 바뀐/사라진 파일이 있는 행은 unknown.
      행 해시·prev_hash 체인·criteria_sha256 이 안 맞으면 broken → exit 1. (--strict: unknown 도 exit 3)
  verify.py status <파일…> --criteria "<기준>" [--ledger L]
      «지금 이 파일들 + 이 기준»에 유효한 최신 판정. 해시가 하나라도 다르면 unknown. (LLM 0, 게이트용)

설정(config.json 의 hands.verify, 전부 선택):
  host     판정 기기. 기본: can_ask 이면서 judge 역할인 첫 호스트(없으면 첫 can_ask 호스트)
  ledger   장부 파일. 기본 ~/.local/share/machines-as-tools/verify/LEDGER.jsonl (저장소 밖)

비용: judge = 회당 수 센트(README 실측), check·status = 0.
원칙: mm 을 고치지 않고 mm <host> run / ask 만 조합한다. 원격 쓰기는 <workdir>/verify/ 아래만.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import config  # noqa: E402
from mmlib import mm as cli_mm, workdir  # noqa: E402

GENESIS = "0" * 64
MAX_BYTES = 2_000_000          # 판정자에게 넘길 총량 상한(작업자 맥락 보호)
CHUNK = 60_000                 # base64 한 번에 보내는 양(ssh 명령줄 길이 안전권)
WORKER_RE = re.compile(r"\[mm\] worker=(\S+) model=(\S*) (\d+)ms \$([0-9.]+)")
DEFAULT_LEDGER = "~/.local/share/machines-as-tools/verify/LEDGER.jsonl"


def default_ledger() -> Path:
    try:
        p = config.hand("verify").get("ledger") or DEFAULT_LEDGER
    except config.ConfigError:
        p = DEFAULT_LEDGER
    return Path(os.path.expanduser(p)).resolve()


def judge_host(override: Optional[str] = None) -> str:
    h = override or config.hand("verify").get("host")
    if h:
        config.host(h)  # 없으면 ConfigError
        if h not in config.ask_hosts():
            raise SystemExit(f"verify: '{h}' 는 can_ask=false 라 판정자를 띄울 수 없다")
        return h
    ask = config.ask_hosts()
    if not ask:
        raise SystemExit("verify: can_ask=true 인 호스트가 설정에 없다")
    judges = [x for x in ask if x in config.hosts_with_role("judge")]
    return (judges or ask)[0]


# ---------- 해시 ----------
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def row_hash(row: dict) -> str:
    body = {k: v for k, v in row.items() if k != "hash"}
    return sha256_text(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def read_rows(ledger: Path) -> List[dict]:
    if not ledger.exists():
        return []
    rows = []
    for i, line in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"_bad_line": i})
    return rows


def append_row(ledger: Path, row: dict) -> dict:
    """잠금 안에서 마지막 hash 를 읽어 체인을 잇는다(동시 judge 대비)."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with open(str(ledger) + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        rows = read_rows(ledger)
        row["prev_hash"] = rows[-1].get("hash", "?") if rows else GENESIS
        row["hash"] = row_hash(row)
        with open(ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


# ---------- judge ----------
def remote_names(paths: List[Path]) -> List[str]:
    seen, out = set(), []
    for i, p in enumerate(paths):
        n = re.sub(r"[^\w.\-가-힣]", "_", p.name) or f"file{i}"
        if n in seen:
            n = f"{i}_{n}"
        seen.add(n)
        out.append(n)
    return out


def copy_to_remote(mm, host: str, rdir: str, path: Path, name: str) -> Optional[str]:
    """base64 조각을 mm run 으로 이어 붙이고, 원격 shasum 을 돌려준다(비교는 호출자)."""
    data = base64.b64encode(path.read_bytes()).decode()
    tgt = shlex.quote(f"{rdir}/{name}")
    tmp = shlex.quote(f"{rdir}/.{name}.b64")
    mm([host, "run", f"mkdir -p {shlex.quote(rdir)} && : > {tmp}"], 30, 500)
    for i in range(0, len(data), CHUNK):
        mm([host, "run", f"printf %s {data[i:i+CHUNK]} >> {tmp}"], 60, 500)
    out = mm([host, "run", f"base64 -d -i {tmp} -o {tgt} 2>/dev/null || base64 -d < {tmp} > {tgt}; "
              f"mv {tmp} {shlex.quote(rdir)}/.trash_{name}.b64; shasum -a 256 {tgt}"], 60, 1000)
    m = re.search(r"\b([0-9a-f]{64})\b", out)
    return m.group(1) if m else None


def build_prompt(rdir: str, names: List[str], criteria: str) -> str:
    flist = "\n".join(f"- {rdir}/{n}" for n in names)
    return f"""너는 독립 검증자다. 이 파일들을 만든 사람이 아니며, 생성자의 주장을 믿지 말고 파일 내용만으로 판정한다.

[판정 대상 파일] (Read 도구로 전부 읽어라. 수정 금지)
{flist}

[판정 기준]
{criteria}

[규칙]
- 기준을 확인할 수 없으면 추측하지 말고 verdict 를 "unsure" 로 한다.
- 수치 대조가 기준에 있으면 실제로 계산해 본다(필요하면 python3 사용).
- reasons 는 3~6개, 각 1문장. evidence 는 판정의 근거가 된 줄(파일명, 줄 번호, 원문 일부 80자 이내).
- 결과 JSON 하나를 {rdir}/verdict.json 에 Write 하고, 답으로도 그 JSON 만 출력한다(코드블록·설명 없이).

[출력 형식]
{{"verdict":"pass|fail|unsure","reasons":["..."],"evidence":[{{"file":"이름","line":12,"quote":"..."}}]}}"""


def parse_verdict(text: str) -> Optional[dict]:
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        d = json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None
    if d.get("verdict") not in ("pass", "fail", "unsure"):
        return None
    return d


def judge(files: List[str], criteria: str, model: str = "haiku", generator: Optional[str] = None,
          ledger: Optional[Path] = None, mm=cli_mm, host: Optional[str] = None) -> dict:
    ledger = ledger or default_ledger()
    paths = [Path(f).expanduser().resolve() for f in files]
    for p in paths:
        if not p.is_file():
            raise SystemExit(f"verify: 파일 없음 {p}")
    if sum(p.stat().st_size for p in paths) > MAX_BYTES:
        raise SystemExit(f"verify: 총 {MAX_BYTES}바이트 초과 — 판정 대상을 줄이거나 요약본을 넘겨라")
    if model not in ("haiku", "sonnet"):
        raise SystemExit("verify: model 은 haiku|sonnet")
    host = judge_host(host)
    t0 = dt.datetime.now()
    hashes = [sha256_file(p) for p in paths]
    crit_sha = sha256_text(criteria)
    vid = t0.strftime("%Y%m%d-%H%M%S-") + sha256_text("".join(hashes) + crit_sha)[:6]
    rdir = f"{workdir()}/verify/{vid}"
    names = remote_names(paths)

    # 1) 복사 + 원격 해시 대조 (판정자가 보는 바이트 = 장부에 적는 바이트)
    for p, n, h in zip(paths, names, hashes):
        rh = copy_to_remote(mm, host, rdir, p, n)
        if rh != h:
            raise SystemExit(f"verify: 원격 복사 해시 불일치 {p.name} local={h[:12]} remote={str(rh)[:12]}")

    # 2) 판정 (새 작업자 — -r 재사용 안 함: 생성자·검증자 분리)
    out = mm([host, "ask", "-m", model, build_prompt(rdir, names, criteria)], 600, 20000)
    m = WORKER_RE.search(out)
    worker, mdl, ms, cost = (m.group(1), m.group(2), int(m.group(3)), float(m.group(4))) if m else (None, "", None, None)
    body = out[:m.start()] if m else out
    v = parse_verdict(body)
    if v is None:  # 답이 흐트러졌으면 작업자가 쓴 파일을 본다
        v = parse_verdict(mm([host, "run", f"cat {shlex.quote(rdir)}/verdict.json"], 30, 20000))
    if v is None:
        v = {"verdict": "unsure", "reasons": ["판정자 출력 해석 실패: " + body[:200].replace("\n", " ")], "evidence": []}

    row = {
        "id": vid,
        "files": [{"path": str(p), "sha256": h, "remote": f"{rdir}/{n}"} for p, n, h in zip(paths, names, hashes)],
        "criteria": criteria, "criteria_sha256": crit_sha,
        "judge_worker": worker, "judge_host": host, "model": mdl or model, "model_tier": model,
        "generator": generator,
        "independent": None if not generator else (generator != worker),
        "verdict": v["verdict"], "reasons": v.get("reasons", [])[:8], "evidence": v.get("evidence", [])[:12],
        "cost_usd": cost, "judge_ms": ms, "wall_s": round((dt.datetime.now() - t0).total_seconds(), 1),
        "ts": t0.isoformat(timespec="seconds"),
    }
    if worker is None:
        row["verdict"] = "unsure"
        row["reasons"] = ["ask 출력에 worker 세션 ID 가 없어 판정자 신원을 고정할 수 없음"] + row["reasons"]
    return append_row(ledger, row)


# ---------- check / status ----------
def check(ledger: Optional[Path] = None) -> Tuple[List[dict], bool]:
    ledger = ledger or default_ledger()
    rows, prev, broken_any, report = read_rows(ledger), GENESIS, False, []
    for i, r in enumerate(rows, 1):
        probs = []
        if "_bad_line" in r:
            report.append({"n": i, "id": "?", "state": "broken", "why": ["JSON 해석 불가"]})
            broken_any, prev = True, "?"
            continue
        if r.get("prev_hash") != prev:
            probs.append("prev_hash 가 앞 행 hash 와 다름(행 삭제·삽입·순서 변경)")
        if row_hash(r) != r.get("hash"):
            probs.append("행 내용이 hash 와 다름(사후 수정)")
        if sha256_text(r.get("criteria", "")) != r.get("criteria_sha256"):
            probs.append("criteria 본문이 criteria_sha256 과 다름")
        prev = r.get("hash")
        if probs:
            broken_any = True
            report.append({"n": i, "id": r.get("id"), "state": "broken", "why": probs})
            continue
        changed = []
        for f in r.get("files", []):
            p = Path(f["path"])
            if not p.is_file():
                changed.append(f"사라짐 {p.name}")
            elif sha256_file(p) != f["sha256"]:
                changed.append(f"바뀜 {p.name}")
        report.append({"n": i, "id": r.get("id"), "state": "unknown" if changed else r.get("verdict"),
                       "recorded": r.get("verdict"), "why": changed, "worker": r.get("judge_worker")})
    return report, broken_any


def status(files: List[str], criteria: str, ledger: Optional[Path] = None) -> dict:
    ledger = ledger or default_ledger()
    want = sorted((str(Path(f).expanduser().resolve()), sha256_file(Path(f).expanduser().resolve()))
                  for f in files)
    crit = sha256_text(criteria)
    report, broken = check(ledger)
    states = {x["id"]: x["state"] for x in report}
    for r in reversed(read_rows(ledger)):
        if r.get("criteria_sha256") == crit and sorted((f["path"], f["sha256"]) for f in r.get("files", [])) == want:
            st = "broken-ledger" if broken else states.get(r["id"], "unknown")
            return {"state": st, "id": r["id"], "worker": r.get("judge_worker"), "ts": r.get("ts")}
    return {"state": "unknown", "id": None, "why": "현재 해시·기준과 맞는 판정 없음"}


def fmt_judge(row: dict) -> str:
    lines = [f"verdict={row['verdict']}  id={row['id']}  host={row['judge_host']}  worker={row['judge_worker']}  "
             f"model={row['model']}  ${row['cost_usd']}  {row['wall_s']}s"]
    lines += [f"  - {r}" for r in row["reasons"][:6]]
    for e in row["evidence"][:4]:
        if isinstance(e, dict):
            lines.append(f"  · {e.get('file')}:{e.get('line')} {str(e.get('quote', ''))[:80]}")
    lines.append(f"  hash={row['hash'][:16]}…  prev={row['prev_hash'][:16]}…")
    return "\n".join(lines[:20])


def fmt_check(report: List[dict], broken: bool, ledger: Path) -> str:
    cnt: Dict[str, int] = {}
    for x in report:
        cnt[x["state"]] = cnt.get(x["state"], 0) + 1
    head = f"ledger={ledger}  rows={len(report)}  " + " ".join(f"{k}={v}" for k, v in sorted(cnt.items()))
    head += "  CHAIN=" + ("BROKEN" if broken else "ok")
    body = [f"  #{x['n']} {x['id']} {x['state']}" + (f" (기록:{x.get('recorded')})" if x["state"] == "unknown" else "")
            + (f" — {'; '.join(x['why'])}" if x["why"] else "") for x in report
            if x["state"] in ("unknown", "broken")]
    return "\n".join([head] + body[:18])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="verify 도구 — 판정 장부")
    sub = ap.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("judge"); j.add_argument("files", nargs="+"); j.add_argument("--criteria", required=True)
    j.add_argument("--model", default="haiku", choices=["haiku", "sonnet"]); j.add_argument("--generator")
    j.add_argument("--host"); j.add_argument("--ledger")
    c = sub.add_parser("check"); c.add_argument("--ledger"); c.add_argument("--strict", action="store_true")
    s = sub.add_parser("status"); s.add_argument("files", nargs="+"); s.add_argument("--criteria", required=True)
    s.add_argument("--ledger")
    a = ap.parse_args(argv)
    led = Path(a.ledger).expanduser().resolve() if a.ledger else default_ledger()
    try:
        if a.cmd == "judge":
            print(fmt_judge(judge(a.files, a.criteria, a.model, a.generator, led, cli_mm, a.host)))
            return 0
        if a.cmd == "check":
            rep, broken = check(led)
            print(fmt_check(rep, broken, led))
            if broken:
                return 1
            return 3 if a.strict and any(x["state"] == "unknown" for x in rep) else 0
        print(json.dumps(status(a.files, a.criteria, led), ensure_ascii=False))
        return 0
    except config.ConfigError as e:
        print(f"verify: 설정 오류: {e}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
