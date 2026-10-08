#!/usr/bin/env python3
"""research 도구 — GitHub 레포 URL 하나로 조사를 여러 맥에 팬아웃한다.

사용법:
  research.py <github-url> [--focus "<관심사>"] [--model haiku|sonnet] [--no-ask] [--out DIR]

흐름:
  1단(mm batch 동시, LLM 0)
    (a) <clone_host> run    : <workdir>/research/<repo> 에 git clone --depth 50 → agg.py 가 T0 집계 JSON
    (b) <browser_host> read-url : 레포 첫 화면 (스타·포크·이슈·PR·소개·토픽)
    (c) <browser_host> read-url : releases 페이지 (최근 릴리스 이름·날짜)
  2단(T1)
    (d) <clone_host> ask -m haiku : 클론의 README·ARCHITECTURE 류 문서를 Read 로 읽고
        «무엇인가·핵심 개념·설치 영향·성숙도 신호» 20줄 (브리프에 focus 반영)
결과: <out>/<repo>_<YYYYmmdd_HHMMSS>.md (수치 표 + 요약 + 출처) + 같은 이름의 .trace.json
      원문(front.txt·releases.txt·brief.md·jobs.tsv)은 <out>/runs/<repo>_<시각>/
지휘 세션 출력: 10줄 이내.

설정(config.json 의 hands.research, 전부 선택):
  clone_host    클론·집계·요약 기기. 요약 작업자가 클론을 직접 읽으므로 can_ask 여야 한다(--no-ask 면 아무 기기).
                기본: can_ask 이면서 heavy 역할인 첫 호스트 → 첫 can_ask → 첫 heavy
  browser_host  화면 읽기 기기. 기본: browser 역할인 첫 호스트(없으면 (b)(c) 생략)
  python        원격 파이썬(표준 라이브러리만). 기본 python3
  out_dir       보고서 폴더. 기본 ~/.local/share/machines-as-tools/research (저장소 밖)

규칙: mm 은 고치지 않고 run/read-url/ask/batch 만 조합한다. 원격 쓰기는 <workdir>/research/ 아래만.
      브라우저 탭은 read-url 이 스스로 닫는다.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))
import config  # noqa: E402
from mmlib import mm as cli_mm, workdir  # noqa: E402

AGG = HERE / "agg.py"
DEFAULT_OUT = "~/.local/share/machines-as-tools/research"


def default_out() -> Path:
    try:
        p = config.hand("research").get("out_dir") or DEFAULT_OUT
    except config.ConfigError:
        p = DEFAULT_OUT
    return Path(os.path.expanduser(p)).resolve()


def pick_hosts(ask: bool) -> Tuple[str, Optional[str]]:
    hc = config.hand("research")
    ask_hosts = config.ask_hosts()
    heavy = sorted(config.hosts_with_role("heavy"), key=config.is_local)  # 원격 먼저
    clone = hc.get("clone_host")
    if clone:
        config.host(clone)
    else:
        both = [h for h in heavy if h in ask_hosts]
        clone = (both or ask_hosts or heavy or [None])[0]
    if not clone:
        raise SystemExit("[research] 클론할 기기가 없다(heavy 역할 또는 can_ask 호스트 필요)")
    if ask and clone not in ask_hosts:
        raise SystemExit(f"[research] clone_host '{clone}' 는 can_ask=false — 요약 작업자가 클론을 못 읽는다. --no-ask 로 돌려라")
    browser = hc.get("browser_host")
    if browser:
        config.host(browser)
    else:
        bh = config.hosts_with_role("browser")
        browser = bh[0] if bh else None
    return clone, browser


def parse_url(url: str) -> Tuple[str, str]:
    m = re.match(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?(?:[#?].*)?$", url.strip())
    if not m:
        raise SystemExit(f"[research] github.com/<owner>/<repo> 형식이 아님: {url}")
    return m.group(1), m.group(2)


# ---------- 1단 파서 (T0) ----------
def _num(s: Optional[str]) -> Optional[str]:
    return s.strip() if s else None


def _g(pat: str, txt: str) -> Optional[str]:
    m = re.search(pat, txt, re.M)
    return m.group(1) if m else None


def parse_front(txt: str) -> dict:
    about = None
    m = re.search(r"^About\s*\n+(.+?)\n", txt, re.M)
    if m:
        about = m.group(1).strip()
    topics = []
    m = re.search(r"^Topics\n((?:[a-z0-9][a-z0-9-]*\n){1,20})", txt, re.M)
    if m:
        topics = m.group(1).split()
    return {
        "stars": _num(_g(r"^([\d.,]+k?) stars?$", txt)) or _num(_g(r"Star\s*([\d.,]+k?)", txt)),
        "forks": _num(_g(r"^([\d.,]+k?) forks?$", txt)) or _num(_g(r"Fork\s*([\d.,]+k?)", txt)),
        "watchers": _num(_g(r"^([\d.,]+k?) watching$", txt)),
        "issues": _num(_g(r"^Issues\n([\d.,]+k?)$", txt)),
        "pulls": _num(_g(r"^Pull requests\n([\d.,]+k?)$", txt)),
        "license_badge": _num(_g(r"^(.+? license)$", txt)),
        "about": about,
        "topics": topics,
        "chars": len(txt),
    }


REL_DATE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}(?:, \d{4})?\b")


def parse_releases(txt: str) -> dict:
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    rels = []
    for i, ln in enumerate(lines):
        if re.fullmatch(r"v?\d+\.\d+(?:\.\d+)?(?:[-.+][0-9A-Za-z.]+)?", ln) or re.fullmatch(r"[\w.-]*v\d+\.\d+[\w.-]*", ln):
            date = None
            for near in lines[max(0, i - 4): i + 6]:
                d = REL_DATE.search(near)
                if d:
                    date = d.group(0)
                    break
            if ln not in [r["tag"] for r in rels]:
                rels.append({"tag": ln, "date": date})
    n = re.search(r"Releases?\s*\n?\s*([\d,]+)", txt)
    return {"releases": rels[:8], "count_hint": n.group(1) if n else None,
            "latest_badge": "Latest" in txt, "pre_release": "Pre-release" in txt, "chars": len(txt)}


def parse_ask(out: str) -> Tuple[str, dict]:
    lines = out.strip().splitlines()
    meta = {}
    if lines and lines[-1].startswith("[mm] worker="):
        last = lines.pop()
        m = re.search(r"worker=(\S+) model=(\S+) (\d+)ms \$([0-9.]+)", last)
        if m:
            meta = {"worker": m.group(1), "model": m.group(2), "ms": int(m.group(3)), "cost_usd": float(m.group(4))}
    return "\n".join(lines).strip(), meta


# ---------- 본체 ----------
def run(url: str, focus: str, model: str, ask: bool, mm=cli_mm, out_dir: Optional[Path] = None) -> dict:
    owner, repo = parse_url(url)
    url = f"https://github.com/{owner}/{repo}"
    clone_host, browser_host = pick_hosts(ask)
    out_dir = out_dir or default_out()
    rpy = config.hand("research").get("python") or "python3"
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out_dir / "runs" / f"{repo}_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    trace: dict = {"url": url, "owner": owner, "repo": repo, "focus": focus, "started": ts,
                   "clone_host": clone_host, "browser_host": browser_host, "steps": {}}
    root = f"{workdir()}/research"
    wd = f"{root}/{repo}"
    t_all = time.time()

    # 1단: batch 동시
    b64 = base64.b64encode(AGG.read_bytes()).decode()
    remote = (f"mkdir -p {root} && echo {b64} | base64 -d > {root}/agg.py && "
              f"{shlex.quote(rpy)} {root}/agg.py {url}.git {wd} {root}")
    rows = [["a_clone", clone_host, "run", remote]]
    if browser_host:
        rows += [["b_front", browser_host, "read-url", url, "9000"],
                 ["c_releases", browser_host, "read-url", url + "/releases", "7000"]]
    jobs = run_dir / "jobs.tsv"
    jobs.write_text("\n".join("\t".join(r) for r in rows) + "\n")
    t1 = time.time()
    summary = mm(["batch", str(jobs)], 600, 4000)
    trace["steps"]["batch"] = {"wall_s": round(time.time() - t1, 1), "summary": summary[:1500]}
    m = re.search(r"results=(\S+)", summary)
    if not m:
        raise SystemExit(f"[research] batch 결과 경로를 못 찾음:\n{summary[:800]}")
    rd = Path(m.group(1))
    trace["results_dir"] = str(rd)

    def res(i: str) -> Tuple[str, Dict]:
        txt = (rd / f"{i}.out").read_text(errors="replace") if (rd / f"{i}.out").exists() else ""
        meta = {}
        if (rd / f"{i}.meta").exists():
            f = (rd / f"{i}.meta").read_text().split("\t")
            meta = {"rc": int(f[2]), "secs": float(f[3])}
        return txt, meta

    a_txt, a_meta = res("a_clone")
    try:
        agg = json.loads(a_txt[a_txt.find("{"):].splitlines()[0]) if "{" in a_txt else {"error": a_txt[:500]}
    except json.JSONDecodeError:
        agg = {"error": "json parse", "raw": a_txt[:500]}
    b_txt, b_meta = res("b_front")
    c_txt, c_meta = res("c_releases")
    front = parse_front(b_txt)
    rels = parse_releases(c_txt)
    (run_dir / "front.txt").write_text(b_txt)
    (run_dir / "releases.txt").write_text(c_txt)
    for r in rels["releases"]:  # 화면에 날짜가 안 찍히는 경우(상대시각 요소) 클론 태그 날짜로 보충
        if not r["date"] and r["tag"] in (agg.get("tag_dates") or {}):
            r["date"] = agg["tag_dates"][r["tag"]] + " (tag)"
    trace["steps"].update({"a_clone": {**a_meta, "agg": agg}, "b_front": {**b_meta, "parsed": front},
                           "c_releases": {**c_meta, "parsed": rels}})

    # 2단: 작업자 요약
    summ, ask_meta = "", {}
    if ask and "error" not in agg:
        docs = [d["path"] for d in agg.get("docs", []) if d["bytes"] < 200_000][:8]
        brief = (
            f"조사 대상: {url} ({clone_host} 클론 경로 {wd}, 얕은 클론 depth 50).\n"
            f"지휘자의 관심사(focus): {focus or '(없음 — 일반 개요)'}\n"
            f"T0 집계(이미 확인된 수치, 다시 세지 마라): 파일 {agg.get('files')}개, 테스트 파일 {agg.get('test_files')}개, "
            f"언어별 줄 {json.dumps(agg.get('lines_by_lang'), ensure_ascii=False)}, 라이선스 {agg.get('license')}, "
            f"버전 {json.dumps(agg.get('versions'), ensure_ascii=False)}, 최근 태그(날짜) {agg.get('tag_dates')}, "
            f"최근 커밋 20개 {json.dumps(agg.get('recent20'), ensure_ascii=False)}, 클론 HEAD {agg.get('head')}.\n"
            f"읽을 문서 후보(클론 기준 상대경로, 앞쪽이 우선): {docs}\n"
            "규칙: 클론을 수정하지 마라. 파일을 새로 쓰지 마라. Read/Grep/Glob 만 쓴다(Bash 는 ls 정도만). "
            "문서 후보를 먼저 읽고, focus 에 답하려면 필요한 소스 파일 2~4개만 더 본다."
        )
        (run_dir / "brief.md").write_text(brief)
        instr = (
            "브리프의 레포를 조사해 정확히 아래 4개 소제목으로 한국어 20줄 이내로 답하라. "
            "각 줄은 '- ' 로 시작하고, 주장마다 근거 파일 경로를 괄호로 붙여라.\n"
            "## 무엇인가\n## 핵심 개념 (focus 중심)\n## 설치 영향 (의존성·런타임·파이썬/노드 버전·부작용)\n"
            "## 성숙도 신호 (버전·테스트·문서·변경 빈도. 확인한 것과 추측을 구분)"
        )
        t2 = time.time()
        ask_out = mm([clone_host, "ask", "-m", model, "-b", str(run_dir / "brief.md"), instr], 600, 8000)
        summ, ask_meta = parse_ask(ask_out)
        trace["steps"]["d_ask"] = {"wall_s": round(time.time() - t2, 1), **ask_meta, "chars": len(summ)}
    trace["wall_s"] = round(time.time() - t_all, 1)
    trace["cost_usd"] = ask_meta.get("cost_usd", 0.0)

    # 보고서
    md = out_dir / f"{repo}_{ts}.md"
    tr = out_dir / f"{repo}_{ts}.trace.json"

    def L(d):
        return ", ".join(f"{k} {v:,}" for k, v in (d or {}).items())

    rc = agg.get("recent20") or {}
    rows_md = [
        ("소개", front.get("about") or "?", "b 첫 화면"),
        ("스타 / 포크 / 워처", f"{front.get('stars')} / {front.get('forks')} / {front.get('watchers')}", "b 첫 화면"),
        ("열린 이슈 / PR", f"{front.get('issues')} / {front.get('pulls')}", "b 첫 화면"),
        ("토픽", ", ".join(front.get("topics") or []) or "-", "b 첫 화면"),
        ("라이선스", f"{agg.get('license')} ({agg.get('license_file')}) · 배지 {front.get('license_badge')}", "a 클론 · b"),
        ("버전(매니페스트)", json.dumps(agg.get("versions"), ensure_ascii=False), "a 클론"),
        ("최근 태그", ", ".join(agg.get("tags") or []) or "-", "a 클론"),
        ("최근 릴리스", ", ".join(f"{r['tag']}({r['date'] or '?'})" for r in rels["releases"][:5]) or "-", "c releases"),
        ("파일 수 / 테스트 파일", f"{agg.get('files')} / {agg.get('test_files')}", "a 클론(.git·node_modules 제외)"),
        ("언어별 줄 수", L(agg.get("lines_by_lang")), "a 클론(확장자 기준, 2MB 초과 제외)"),
        ("최근 커밋 20개 기간", f"{rc.get('first')} ~ {rc.get('last')}", "a git log"),
        ("최근 20개 작성자", L(rc.get("authors")), "a git log"),
        ("HEAD", f"{agg.get('head')} (클론 내 커밋 {agg.get('commits_in_clone')}개, {agg.get('clone')})", "a 클론"),
    ]
    body = [f"# {owner}/{repo} 조사", "",
            f"- URL: {url}", f"- focus: {focus or '-'}", f"- 시각: {ts} · 총 {trace['wall_s']}초 · LLM 비용 ${trace['cost_usd']}", "",
            "## 수치 (T0, LLM 0)", "", "| 항목 | 값 | 출처 |", "|---|---|---|"]
    body += [f"| {k} | {str(v).replace('|', '/')} | {s} |" for k, v, s in rows_md]
    body += ["", f"## 요약 (T1 {ask_meta.get('model', model)})", "", summ or "(요약 생략 또는 실패 — trace 참조)", "",
             "## 출처", "",
             f"- (a) {clone_host} `{wd}` 클론 + agg.py 집계 — {a_meta.get('secs')}초, rc={a_meta.get('rc')}",
             f"- (b) {browser_host or '-'} read-url {url} — {b_meta.get('secs')}초, {front['chars']}자",
             f"- (c) {browser_host or '-'} read-url {url}/releases — {c_meta.get('secs')}초, {rels['chars']}자",
             f"- (d) {clone_host} ask worker={ask_meta.get('worker')} — {ask_meta.get('ms')}ms, ${ask_meta.get('cost_usd')}",
             f"- 원문: `{run_dir}` (front.txt·releases.txt·brief.md·jobs.tsv), batch 결과 `{rd}`",
             "- 주의: 스타·이슈 수는 GitHub 화면 텍스트를 정규식으로 뽑은 값이다. 클론은 depth 50 이라 전체 이력이 아니다."]
    md.write_text("\n".join(body) + "\n")
    tr.write_text(json.dumps(trace, ensure_ascii=False, indent=1))
    trace["md"], trace["trace"] = str(md), str(tr)
    return trace


def fmt(t: dict) -> str:
    a = t["steps"].get("a_clone", {}).get("agg", {})
    f = t["steps"].get("b_front", {}).get("parsed", {})
    r = t["steps"].get("c_releases", {}).get("parsed", {})
    d = t["steps"].get("d_ask", {})
    top = ", ".join(f"{k} {v:,}" for k, v in list((a.get("lines_by_lang") or {}).items())[:3])
    rel = (r.get("releases") or [{}])[0]
    r20 = a.get("recent20") or {}
    return "\n".join([
        f"[research] {t['owner']}/{t['repo']}  총 {t['wall_s']}s  ${t.get('cost_usd', 0)}  (클론 {t['clone_host']} · 화면 {t['browser_host'] or '-'})",
        f"  소개: {(f.get('about') or '?')[:90]}",
        f"  ★{f.get('stars')} fork {f.get('forks')} 이슈 {f.get('issues')} PR {f.get('pulls')} · 라이선스 {a.get('license')}",
        f"  파일 {a.get('files')} · 테스트 {a.get('test_files')} · {top}",
        f"  버전 {json.dumps(a.get('versions'), ensure_ascii=False)} · 최신 릴리스 {rel.get('tag')} ({rel.get('date')})",
        f"  최근20 커밋 {r20.get('first')}~{r20.get('last')} 작성자 {len(r20.get('authors') or {})}명",
        f"  단계: batch {t['steps']['batch']['wall_s']}s · ask {d.get('wall_s', '-')}s ({d.get('model', '생략')})"
        + (f" · 오류 {a.get('error')}" if a.get("error") else ""),
        f"  보고서 {t.get('md')}",
        f"  trace  {t.get('trace')}",
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--focus", default="")
    ap.add_argument("--model", default="haiku", choices=["haiku", "sonnet"])
    ap.add_argument("--no-ask", action="store_true", help="T0 만 (LLM 0)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    try:
        out = Path(os.path.expanduser(a.out)).resolve() if a.out else default_out()
        print(fmt(run(a.url, a.focus, a.model, not a.no_ask, cli_mm, out)))
    except config.ConfigError as e:
        print(f"[research] 설정 오류: {e}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
