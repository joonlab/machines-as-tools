#!/usr/bin/env python3
"""mine 도구 — 세션 기록 마이닝. 원격 기기에 동기화된 Claude Code 세션 기록에서 키워드로 후보를 고르고(T0),
가벼운 작업자 여럿이 동시에 카드를 써서(T1) 하나의 cards.md 로 모은다.

사용법:
  mine.py "<키워드 정규식>" --since YYYY-MM-DD --question "<무엇을 뽑을지>" [--top 15] [--batches 3]
          [--min-hits 3] [--model haiku] [--host H] [--out-root <폴더>] [--scan-only]

  예) mine.py "render|렌더|영상" --since 2026-10-01 --question "영상 제작 작업들이 어떤 종류였나"
      mine.py "deploy|배포" --since 2026-09-25 --scan-only        # LLM 0, 후보 표만

흐름:
  1) T0  mm <host> run  : <sessions_dir>/*/*.jsonl 중 --since 이후 수정분의 «사용자 발화»만 읽어
                          정규식 적중 수로 순위 → 상위 --top 개를 --batches 묶음 JSON 으로 <workdir>/mine/<run>/ 에 쓴다.
                          작업자에게 넘기는 것은 sid 앞 8자·세션 이름·첫 지시(400자)·적중 수·적중어 빈도뿐(본문 없음).
  2) T1  mm batch       : 묶음마다 작업자 하나가 동시에 카드를 써서 <workdir>/mine/<run>/cards_K.md 에 저장.
  3) T0  mm <host> run  : 카드 회수 → <out-root>/<시각>/cards.md · trace.json · candidates.tsv
  지휘 세션에는 요약 10줄 이내와 경로만 출력한다.

설정(config.json 의 hands.mine, 전부 선택):
  host          세션 기록이 있고 ask 가 되는 기기. 기본: 첫 can_ask 호스트
  sessions_dir  원격의 세션 기록 폴더. 기본 ~/.claude/projects
  names_file    원격의 세션 이름 JSON({sid: 이름} 또는 {sid: {"name": 이름}}). 기본 없음
  python        원격 파이썬(표준 라이브러리만 씀). 기본 python3
  out_root      결과 폴더. 기본 ~/.local/share/machines-as-tools/mine (저장소 밖)

⚠ 출력(candidates.tsv·cards.md·trace.json)에는 세션 이름과 첫 지시 등 개인 작업 기록이 담긴다. 공개 저장소에 커밋하지 말 것.
규칙: mm 은 고치지 않고 run·ask·batch 만 조합한다. 원격 쓰기는 <workdir>/mine/ 아래만.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import config  # noqa: E402
from mmlib import ANSI, mm_raw, workdir  # noqa: E402

DEFAULT_OUT = "~/.local/share/machines-as-tools/mine"

# 원격에서 도는 T0 스캐너(표준 라이브러리만, Python 3.8+). 인자는 base64 JSON 하나.
SCANNER = r'''
import base64, collections, glob, json, os, re, sys, time
a = json.loads(base64.b64decode(sys.argv[1]).decode())
root = os.path.expanduser(a["sessions_dir"])
since = time.mktime(time.strptime(a["since"], "%Y-%m-%d"))
kw = re.compile(a["pattern"], re.I)
names = {}
if a.get("names_file"):
    try:
        names = json.load(open(os.path.expanduser(a["names_file"])))
    except Exception:
        names = {}
rows, scanned, mb = [], 0, 0.0
for f in glob.glob(os.path.join(root, "*", "*.jsonl")):
    try:
        if os.path.getmtime(f) < since: continue
    except OSError: continue
    scanned += 1; mb += os.path.getsize(f) / 1e6
    sid = os.path.basename(f)[:-6]; texts = []
    for line in open(f, errors="ignore"):
        if '"type":"user"' not in line: continue
        try: m = json.loads(line).get("message", {})
        except Exception: continue
        c = m.get("content")
        if isinstance(c, str):
            if not c.startswith("<"): texts.append(c)
        elif isinstance(c, list):
            t = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            if t and not t.startswith("<"): texts.append(t)
    if not texts: continue
    found = kw.findall("\n".join(texts))
    if len(found) < a["min_hits"]: continue
    nm = names.get(sid); nm = nm.get("name") if isinstance(nm, dict) else nm
    first = re.sub(r"<pasted_content[^>]*>", "", texts[0])
    first = re.sub(r"\s+", " ", first).strip()[:400]
    freq = collections.Counter((x if isinstance(x, str) else x[0]).lower() for x in found).most_common(6)
    rows.append({"sid": sid[:8], "name": nm or "", "hits": len(found), "turns": len(texts),
                 "date": time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(f))),
                 "terms": ", ".join("%s×%d" % (w, n) for w, n in freq), "first": first})
rows.sort(key=lambda r: -r["hits"]); top = rows[:a["top"]]
d = a["dir"]; os.makedirs(d, exist_ok=True)
nb = max(1, min(a["batches"], len(top)))
for k in range(nb):
    json.dump(top[k::nb], open("%s/batch%d.json" % (d, k + 1), "w"), ensure_ascii=False, indent=1)
json.dump({"scanned": scanned, "mb": round(mb), "matched": len(rows), "top": top, "batches": nb if top else 0},
          sys.stdout, ensure_ascii=False)
'''


def mm(args: List[str], timeout: int = 900) -> Tuple[int, str]:
    rc, out, err = mm_raw(args, timeout)
    return rc, ANSI.sub("", out + err)


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def pick_host(override: str = "") -> str:
    h = override or config.hand("mine").get("host")
    if h:
        config.host(h)
        return h
    ask = config.ask_hosts()
    if not ask:
        raise config.ConfigError("can_ask=true 인 호스트가 없다(hands.mine.host 로 지정)")
    return ask[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="세션 기록 마이닝 (원격 T0 스캔 → 작업자 동시 카드)")
    ap.add_argument("pattern", help="키워드 정규식(대소문자 무시). 사용자 발화에서만 센다")
    ap.add_argument("--since", required=True, help="YYYY-MM-DD 이후 수정된 세션만")
    ap.add_argument("--question", default="", help="무엇을 뽑을지 (카드 지시·브리프의 원천)")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--batches", type=int, default=3)
    ap.add_argument("--min-hits", type=int, default=3)
    ap.add_argument("--model", default="haiku", choices=["haiku", "sonnet"])
    ap.add_argument("--host", default="")
    ap.add_argument("--out-root", default="")
    ap.add_argument("--scan-only", action="store_true", help="LLM 0: 후보 표만 만들고 끝낸다")
    a = ap.parse_args()
    re.compile(a.pattern)
    time.strptime(a.since, "%Y-%m-%d")
    if not a.scan_only and not a.question:
        ap.error("--question 이 필요하다 (--scan-only 가 아니면)")
    a.batches = max(1, min(a.batches, 6))   # 동시 작업자 상한 6 (메모리 안전)
    try:
        hc = config.hand("mine")
        host = pick_host(a.host)
        if not a.scan_only and host not in config.ask_hosts():
            raise config.ConfigError(f"'{host}' 는 can_ask=false — 카드 단계를 못 한다(--scan-only 는 가능)")
    except config.ConfigError as e:
        print(f"mine: 설정 오류: {e}", file=sys.stderr)
        return 4
    sessions_dir = hc.get("sessions_dir") or "~/.claude/projects"
    names_file = hc.get("names_file") or ""
    rpy = hc.get("python") or "python3"
    out_root = Path(os.path.expanduser(a.out_root or hc.get("out_root") or DEFAULT_OUT))

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    rdir = f"{workdir()}/mine/{run_id}"
    t0 = time.time()
    trace = {"run_id": run_id, "pattern": a.pattern, "since": a.since, "question": a.question,
             "top": a.top, "batches": a.batches, "model": a.model, "host": host, "remote_dir": rdir,
             "started": datetime.now().isoformat(timespec="seconds"), "phases": []}

    # 1) T0 스캔
    args_b64 = b64(json.dumps({"pattern": a.pattern, "since": a.since, "top": a.top, "batches": a.batches,
                               "min_hits": a.min_hits, "dir": rdir, "sessions_dir": sessions_dir,
                               "names_file": names_file}, ensure_ascii=False))
    s = time.time()
    rc, txt = mm([host, "run", f"mkdir -p {rdir} && echo {b64(SCANNER)} | base64 -d > {rdir}/scan.py"
                  f" && {shlex.quote(rpy)} {rdir}/scan.py {args_b64}"], 300)
    e = time.time()
    try:
        scan = json.loads(txt[txt.find("{"):])
    except Exception:
        print(f"mine: 스캔 실패 rc={rc}\n{txt[:800]}")
        return 1
    trace["phases"].append({"id": "scan", "host": host, "tier": "T0", "t_start": round(s - t0, 2),
                            "t_end": round(e - t0, 2), "secs": round(e - s, 1), "rc": rc, "cost_usd": 0.0,
                            "summary": f"scanned={scan['scanned']} ({scan['mb']}MB) matched={scan['matched']} top={len(scan['top'])}"})
    top = scan["top"]
    with open(out / "candidates.tsv", "w", encoding="utf-8") as f:
        f.write("sid\tdate\thits\tturns\tname\tterms\tfirst\n")
        for r in top:
            f.write("\t".join(str(r[k]).replace("\t", " ").replace("\n", " ")
                              for k in ("sid", "date", "hits", "turns", "name", "terms", "first")) + "\n")

    cards_text, cost = "", 0.0
    if top and not a.scan_only:
        nb = scan["batches"]
        brief = (
            "사용자의 Claude Code 세션 기록을 마이닝한다.\n"
            f"질문: {a.question}\n"
            f"후보 선정: 사용자 발화에서 정규식 /{a.pattern}/ 적중이 많은 세션 ({a.since} 이후 수정분) 상위 {len(top)}개를 {nb}묶음으로 나눴다.\n"
            "각 후보의 필드: sid(세션 앞 8자) · name(세션 이름, 비어 있을 수 있음) · first(첫 지시 400자) · "
            "hits(적중 수) · terms(적중어 빈도) · turns(사용자 발화 수) · date(마지막 수정일).\n"
            "근거는 이 필드뿐이다. 세션 본문은 열지 않는다 — 비용 규칙.\n"
            "같은 작업으로 보이는 여러 세션은 카드 하나로 묶는다. 키워드가 우연히 걸린(질문과 무관한) 후보는 제외하고 사유 한 줄."
        )
        (out / "brief.md").write_text(brief, encoding="utf-8")
        instr = ("{rdir}/batch{k}.json 을 Read 로 읽어라(후보 세션 목록). 질문 「{q}」에 답하는 데 쓸 카드를 써서 "
                 "{rdir}/cards_{k}.md 에 Write 로 저장하라. 카드 형식: ## 작업 이름 / - sid: … / - 종류: 짧은 분류 꼬리표 / "
                 "- 무엇을 했나: 1문장 / - 질문과의 관계: 1문장 / - 근거: name·first 중 무엇에서 봤는지. "
                 "카드 끝에 「## 제외」 로 무관 후보와 사유 한 줄씩. 추정은 (추정) 표시. 다른 파일은 열지 마라. "
                 "답은 카드 제목만 6줄 이내.")
        jobs = out / "jobs.tsv"
        with open(jobs, "w", encoding="utf-8") as f:
            for k in range(1, nb + 1):
                f.write("\t".join([f"mine{k}", host, "ask", "-m", a.model, "-b", str(out / "brief.md"),
                                   instr.format(rdir=rdir, k=k, q=a.question.replace("\t", " "))]) + "\n")
        s = time.time()
        rc, btxt = mm(["batch", str(jobs)], 900)
        e = time.time()
        (out / "batch_stdout.txt").write_text(btxt, encoding="utf-8")
        m = re.search(r"results=(\S+)", btxt)
        rd = Path(m.group(1)) if m else None
        for k in range(1, nb + 1):
            ph = {"id": f"mine{k}", "host": host, "tier": "T1", "model": a.model, "cost_usd": 0.0}
            if rd and (rd / f"mine{k}.meta").exists():
                _id, _h, prc, secs, ps, pe, *_ = (rd / f"mine{k}.meta").read_text().strip().split("\t")
                ph.update(rc=int(prc), secs=float(secs), t_start=round(float(ps) - t0, 2), t_end=round(float(pe) - t0, 2))
                o = (rd / f"mine{k}.out").read_text(errors="replace")
                mc = re.search(r"\[mm\] worker=(\S+) model=(\S*) .*?\$([0-9.]+)", o)
                if mc:
                    ph.update(worker=mc.group(1), model_id=mc.group(2), cost_usd=float(mc.group(3)))
                ph["summary"] = " ".join(ln for ln in o.splitlines() if ln.strip() and not ln.startswith("[mm]"))[:200]
            cost += ph["cost_usd"]
            trace["phases"].append(ph)
        trace["batch_results_dir"] = str(rd) if rd else None

        # 3) 카드 회수 (T0)
        s = time.time()
        rc, ctxt = mm([host, "run",
                       f"for k in $(seq 1 {nb}); do echo; echo '<!-- batch '$k' -->'; "
                       f"cat {rdir}/cards_$k.md 2>/dev/null || echo '(카드 파일 없음: cards_'$k'.md)'; done"], 120)
        e = time.time()
        trace["phases"].append({"id": "fetch", "host": host, "tier": "T0", "t_start": round(s - t0, 2),
                                "t_end": round(e - t0, 2), "secs": round(e - s, 1), "rc": rc, "cost_usd": 0.0})
        cards_text = ctxt.strip()
        header = (f"# mine — {a.question}\n\n- 정규식: `{a.pattern}` · 기간: {a.since}~ · 스캔 {scan['scanned']}세션"
                  f"({scan['mb']}MB) · 적중 {scan['matched']} · 상위 {len(top)} → {nb}묶음 {a.model}\n"
                  f"- 근거: 세션 이름·첫 지시·적중어 빈도만(본문 미열람). 후보 표: candidates.tsv\n"
                  f"- ⚠ 개인 작업 기록이 담긴 파일이다. 공개 저장소에 커밋하지 말 것.\n\n")
        (out / "cards.md").write_text(header + cards_text + "\n", encoding="utf-8")

    trace["ended"] = datetime.now().isoformat(timespec="seconds")
    trace["wall_secs"] = round(time.time() - t0, 1)
    trace["cost_usd_total"] = round(cost, 4)
    (out / "trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=1), encoding="utf-8")

    # 지휘 세션용 요약 (10줄 이내)
    lines = [f"mine: {host} 스캔 {scan['scanned']}세션 {scan['mb']}MB → 적중 {scan['matched']} · 상위 {len(top)}"
             f" | wall {trace['wall_secs']}s · ${trace['cost_usd_total']}"]
    if a.scan_only or not top:
        for r in top[:6]:
            lines.append(f"  {r['sid']} {r['date']} hits={r['hits']} {(r['name'] or r['first'])[:50]}")
    else:
        titles = [ln[3:].strip() for ln in cards_text.splitlines()
                  if ln.startswith("## ") and not ln.startswith("## 제외")]
        lines.append(f"  카드 {len(titles)}장: " + " · ".join(t[:28] for t in titles[:8])[:300])
        bad = [p["id"] for p in trace["phases"] if p.get("rc") not in (0, None)]
        if bad:
            lines.append(f"  실패 단계: {', '.join(bad)}")
    lines.append(f"  결과: {out / ('cards.md' if (out / 'cards.md').exists() else 'candidates.tsv')}")
    lines.append(f"  trace: {out / 'trace.json'}")
    print("\n".join(lines[:10]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
