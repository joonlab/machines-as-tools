#!/usr/bin/env python3
"""render 도구 — headless 렌더 파이프라인을 다른 기기로 보내고 산출물만 회수한다(지휘 맥 메모리 보호).

내 경우 렌더 킷은 motion-reel(비공개 개인 영상 킷)이다. 이 스크립트는 그 킷의 규약
(프로젝트 폴더에 index.html, 킷의 tools/render.js, 산출물 build/stills/·build/video_*.mp4·out/)을 기본값으로 쓰고,
명령 템플릿을 설정으로 바꿀 수 있게 했다.

사용법
  render.py <프로젝트폴더> --stills 1,2,3.5      스틸 PNG (초 단위, 쉼표)        → <프로젝트>/build_remote/stills/
  render.py <프로젝트폴더> --span 0 2            구간 영상                        → <프로젝트>/build_remote/
  render.py <프로젝트폴더> --full [ver]          전체 파이프라인(full_cmd)         → <프로젝트>/out_remote/
  render.py --check [<프로젝트폴더>]             원격 전제(node·ffmpeg·python 모듈·킷) 점검 + dry-run
  공통 옵션
    --ver N          타임라인 버전(기본: index.html 의 첫 timeline(N …))
    --mb N           --span 모션블러 샘플(기본 16). 1 = 끔(빠름)
    --workers N|auto --span 워커 수(기본 auto)
    --dest DIR       회수 위치 직접 지정(기본은 위 표). 원본 프로젝트에 쓰기 싫을 때
    --host H         렌더 기기(기본: 설정 hands.render.host → heavy 역할 첫 원격 호스트). heavy 역할이 아니면 거부
    --dry-run        보낼 파일·바꿀 경로·실행할 명령만 출력

설정(config.json 의 hands.render)
  kit_dir        필수. 렌더 기기에 있는 킷 경로(예: "~/kits/motion-reel"). 원격에서 절대경로로 풀어 쓴다
  kit_ref_regex  프로젝트가 킷을 상대경로로 가리키는 정규식. 원격 복사본에서 이 부분을 kit_dir 로 바꾼다. 비우면 안 바꾼다
  python         렌더 기기의 파이썬(numpy 등 필요한 것). 그 폴더를 PATH 맨 앞에 둔다
  path_extra     PATH 에 더할 폴더 목록(기본 ["/opt/homebrew/bin", "/usr/local/bin"])
  python_modules --check 가 import 해 볼 모듈 목록(기본 ["numpy"])
  guard_cmd      보내기 전 메모리 판정 명령({per_worker}·{want} 치환). exit 2 = STOP, 0/1 = 진행.
                 비우면 내장 게이트: memory_pressure free% < guard_free_min(기본 20) 이면 STOP
  stills_cmd / span_cmd / full_cmd   명령 템플릿({kit} {ver} {stills} {from} {to} {workers} {mb})
  host           기본 렌더 기기

동작 (원격 명령은 전부 `mm <host> run`, 전송만 rsync — mm 과 같은 ssh ControlMaster 재사용)
  1. 메모리 게이트 — STOP 이면 중단(아무것도 안 보냄)
  2. 프로젝트를 <workdir>/render/<이름>/ 로 rsync (build/ out/ out_remote/ build_remote/ node_modules/ 제외, 숨김 포함)
  3. 복사본에서 kit_ref_regex → 원격 킷 절대경로로 치환. 원본은 건드리지 않는다
  4. 원격에서 명령 실행(PATH = python 폴더 + path_extra + 원래 PATH)
  5. 산출물만 rsync 회수
원격에서 지우는 동작은 없다(rsync --delete 안 씀). 같은 이름 재실행 시 덮어쓴다.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import config  # noqa: E402
from mmlib import ANSI, mm_raw, workdir  # noqa: E402

EXCLUDES = ["/build/", "/out/", "/out_remote/", "/build_remote/", "node_modules/", ".DS_Store"]
DEFAULTS = {
    "kit_ref_regex": "",
    "python": "",
    "path_extra": ["/opt/homebrew/bin", "/usr/local/bin"],
    "python_modules": ["numpy"],
    "guard_cmd": "",
    "guard_free_min": 20,
    "stills_cmd": "node {kit}/tools/render.js index.html --ver {ver} --build build --stills {stills}",
    "span_cmd": ("node {kit}/tools/render.js index.html --ver {ver} --build build "
                 "--from {from} --to {to} --workers {workers} --mb {mb} --jpeg"),
    "full_cmd": "bash build.sh {ver}",
}


def log(msg: str) -> None:
    print(f"[render] {msg}", file=sys.stderr, flush=True)


def hc() -> dict:
    return dict(DEFAULTS, **config.hand("render"))


def mm_run(host: str, cmd: str, timeout: int) -> Tuple[int, str]:
    rc, out, err = mm_raw([host, "run", cmd], timeout)
    return rc, ANSI.sub("", out + err)


def ssh_e() -> str:
    s = config.get("ssh") or {}
    cm = Path(os.path.expanduser(s.get("control_dir", "~/.ssh/cm")))
    cm.mkdir(parents=True, exist_ok=True)
    opts = ["-o", f"ConnectTimeout={s.get('connect_timeout', 6)}", "-o", "BatchMode=yes",
            "-o", "ControlMaster=auto", "-o", f"ControlPath={cm}/%r@%h:%p",
            "-o", f"ControlPersist={s.get('control_persist', '15m')}"]
    return "ssh " + " ".join(shlex.quote(o) for o in opts)


def remote_spec(host: str, path: str) -> str:
    """rsync 의 원격 경로 표기. ssh=local 이면 그냥 경로."""
    return path if config.is_local(host) else f"{config.host(host)['ssh']}:{path}"


def rsync(host: str, src: str, dst: str, extra: Optional[List[str]] = None) -> None:
    cmd = ["rsync", "-a"] + ([] if config.is_local(host) else ["-e", ssh_e()]) + [*(extra or []), src, dst]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"[render] rsync 실패({p.returncode}): {p.stderr.strip()[:500]}")


def slug(project: Path) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "_", project.name).strip("_") or "proj"
    return f"{base}-{hashlib.md5(str(project).encode()).hexdigest()[:6]}"


def default_ver(project: Path) -> str:
    m = re.search(r"timeline\(\s*([0-9.]+)", (project / "index.html").read_text(encoding="utf-8"))
    return m.group(1) if m else "1"


def size(p: Path) -> str:
    n = p.stat().st_size
    return f"{n / 2**20:.1f}MB" if n > 2**20 else f"{n / 1024:.0f}KB"


def home_path(p: str) -> str:
    """원격 셸에서 펼쳐지도록 맨 앞 ~ 를 $HOME 으로 바꾼다(zsh 의 PATH= 대입은 ~ 를 안 펼칠 수 있다)."""
    return "$HOME" + p[1:] if p.startswith("~") else p


def path_export(c: dict) -> str:
    dirs = []
    if c.get("python"):
        dirs.append(home_path(os.path.dirname(c["python"])))
    dirs += [home_path(d) for d in c.get("path_extra") or []]
    return "export PATH=" + ":".join(dirs + ["$PATH"]) if dirs else "true"


def pick_host(override: str, c: dict) -> str:
    h = override or c.get("host")
    heavy = config.hosts_with_role("heavy")
    if not h:
        remote = [x for x in heavy if not config.is_local(x)]
        h = (remote or heavy or [None])[0]
        if not h:
            raise SystemExit("[render] heavy 역할 기기가 설정에 없다")
    config.host(h)
    if h not in heavy:
        raise SystemExit(f"[render] '{h}' 는 heavy 역할이 아니다 — 렌더를 보내지 않는다")
    return h


def kit_abs(host: str, c: dict) -> str:
    kit = c.get("kit_dir")
    if not kit:
        raise SystemExit("[render] 설정 hands.render.kit_dir 가 비어 있다(렌더 기기에 있는 킷 경로)")
    rc, out = mm_run(host, f"cd {home_path(kit)} && pwd", 30)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("/")]
    if rc or not lines:
        raise SystemExit(f"[render] {host} 에 킷이 없다: {kit} ({out.strip()[:200]})")
    return lines[-1]


def guard(host: str, c: dict, per_worker: float, want: int) -> Tuple[int, str]:
    """(rc, 한 줄). rc 2 = STOP."""
    if c.get("guard_cmd"):
        rc, out = mm_run(host, c["guard_cmd"].format(per_worker=per_worker, want=want), 60)
        line = " ".join(ln for ln in out.splitlines() if ln.strip())[:200]
        return rc, line
    rc, out = mm_run(host, "memory_pressure 2>/dev/null | tail -1 | grep -oE '[0-9]+' | tail -1", 30)
    m = re.search(r"(\d+)\s*$", out.strip())
    if rc or not m:
        return 3, f"free% 조회 실패: {out.strip()[:120]}"
    free, lim = int(m.group(1)), float(c.get("guard_free_min", 20))
    return (2 if free < lim else 0), f"free={free}% (기준 {lim:g}%)"


def check(host: str, c: dict, project: Optional[Path]) -> int:
    mods = ",".join(c.get("python_modules") or [])
    py = home_path(c["python"]) if c.get("python") else "python3"
    kit = home_path(c.get("kit_dir") or "")
    kit_test = (f"test -d {kit} && echo kit ok: $(cd {kit} && pwd) || echo kit 없음: {kit}" if kit
                else "echo 'kit_dir 미설정: config.json 의 hands.render.kit_dir 를 채워라'")
    cmd = (f"{path_export(c)}; echo node $(node -v 2>&1); ffmpeg -version 2>&1 | head -1 | cut -c1-40; "
           f"ls ~/Library/Caches/ms-playwright 2>/dev/null | grep -c chromium | sed 's/^/playwright chromium dirs: /'; "
           + (f"{py} -c 'import {mods};print(\"python modules ok: {mods}\")' 2>&1 | tail -1; " if mods else "")
           + kit_test)
    rc, out = mm_run(host, cmd, 60)
    print(f"[render] host={host}\n" + out.strip())
    grc, gline = guard(host, c, 1.1, 8)
    print(f"guard rc={grc} {gline}")
    return 0 if rc == 0 else rc


def main() -> int:
    ap = argparse.ArgumentParser(description="headless 렌더를 다른 기기로 보내기")
    ap.add_argument("project", nargs="?")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--stills")
    g.add_argument("--span", nargs=2, type=float, metavar=("FROM", "TO"))
    g.add_argument("--full", nargs="?", const="", metavar="VER")
    g.add_argument("--check", action="store_true")
    ap.add_argument("--ver")
    ap.add_argument("--mb", default="16")
    ap.add_argument("--workers", default="auto")
    ap.add_argument("--dest")
    ap.add_argument("--host", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    try:
        c = hc()
        host = pick_host(a.host, c)
    except config.ConfigError as e:
        log(f"설정 오류: {e}")
        return 4
    if a.check:
        rc = check(host, c, None)
        if a.project:
            print("--- dry-run")
            sys.argv = [sys.argv[0], a.project, "--stills", "1", "--dry-run", "--host", host]
            return main()
        return rc
    if not a.project or not (a.stills or a.span or a.full is not None):
        ap.error("프로젝트와 --stills/--span/--full 중 하나가 필요하다(또는 --check)")

    project = Path(a.project).expanduser().resolve()
    if not (project / "index.html").is_file():
        raise SystemExit(f"[render] index.html 없음: {project}")
    mode = "stills" if a.stills else "span" if a.span else "full"
    if a.stills and not re.fullmatch(r"[0-9.]+(,[0-9.]+)*", a.stills):
        raise SystemExit("[render] --stills 는 1,2.5,3 형식")
    if mode == "full" and not (project / "build.sh").is_file() and "build.sh" in c["full_cmd"]:
        raise SystemExit("[render] --full 은 프로젝트에 build.sh 가 있어야 한다")
    ver = a.ver or (a.full if mode == "full" and a.full else None) or default_ver(project)
    if not re.fullmatch(r"[0-9.]+", ver) or not re.fullmatch(r"\d+|auto", a.workers) or not re.fullmatch(r"\d+", a.mb):
        raise SystemExit("[render] --ver/--workers/--mb 값이 이상하다")
    dest = Path(a.dest).expanduser().resolve() if a.dest else project / ("out_remote" if mode == "full" else "build_remote")
    rdir = f"{workdir()}/render/{slug(project)}"
    kit_re = c.get("kit_ref_regex") or ""

    # 원본에 킷 외의 ../ 참조가 남아 있으면 원격에서 깨진다 → 미리 알린다
    stray = []
    for f in project.rglob("*"):
        if f.suffix in (".html", ".py", ".sh", ".js", ".css") and f.is_file() and not any(
                part in ("build", "out", "out_remote", "build_remote", "node_modules") for part in f.relative_to(project).parts):
            txt = f.read_text(encoding="utf-8", errors="replace")
            if kit_re:
                txt = re.sub(kit_re, "", txt)
            if re.search(r"['\"(]\.\./", txt):
                stray.append(str(f.relative_to(project)))
    if stray:
        log(f"경고: 킷 외 상위경로(../) 참조가 있다 — 원격에서 깨질 수 있음: {', '.join(stray[:5])}")

    f0 = f1 = ""
    if a.span:
        f0, f1 = (f"{x:g}" for x in a.span)
    fmt = {"kit": "{kit}", "ver": ver, "stills": a.stills or "", "from": f0, "to": f1, "workers": a.workers, "mb": a.mb}
    if mode == "stills":
        job_t, mem_per, want = c["stills_cmd"], 0.9, 1
    elif mode == "span":
        job_t, mem_per, want = c["span_cmd"], 1.1, (8 if a.workers == "auto" else int(a.workers))
    else:
        job_t, mem_per, want = c["full_cmd"], 1.1, 8
    job_preview = job_t.format(**fmt)

    if a.dry_run:
        print(json.dumps({"mode": mode, "ver": ver, "host": host, "remote_dir": rdir, "dest": str(dest),
                          "job": job_preview, "kit_dir": c.get("kit_dir"), "kit_ref_regex": kit_re,
                          "excludes": EXCLUDES, "stray_relative_refs": stray}, ensure_ascii=False, indent=1))
        return 0

    T = {}
    t_all = time.time()
    # 1. 메모리 게이트
    t = time.time()
    grc, gline = guard(host, c, mem_per, want)
    T["guard"] = round(time.time() - t, 1)
    if grc == 2:
        log(f"{host} 메모리 게이트 STOP — 중단. {gline}")
        return 2
    if grc not in (0, 1):
        log(f"게이트 확인 실패(rc={grc}): {gline}")
        return 3
    log(f"guard: {gline}")
    kit = kit_abs(host, c)
    job = job_t.format(**dict(fmt, kit=kit))

    # 2. 보내기
    t = time.time()
    rc, out = mm_run(host, f"mkdir -p {rdir}", 30)
    if rc:
        raise SystemExit(f"[render] 원격 폴더 생성 실패: {out[:300]}")
    rsync(host, f"{project}/", remote_spec(host, f"{rdir}/"), [f"--exclude={e}" for e in EXCLUDES])
    T["push"] = round(time.time() - t, 1)

    # 3. 킷 경로 치환 (원격 복사본만)
    kitfix = "0"
    if kit_re:
        fix = f"""
import re,pathlib
R=pathlib.Path({rdir!r}); n=0
for f in R.rglob('*'):
    if f.is_file() and f.suffix in ('.html','.py','.sh','.js','.css') and not any(p in ('build','out','node_modules') for p in f.relative_to(R).parts):
        s=f.read_text(encoding='utf-8',errors='replace'); s2,k=re.subn({kit_re!r},{kit!r},s)
        if k: f.write_text(s2,encoding='utf-8'); n+=k
print('kitfix',n)
"""
        rc, out = mm_run(host, f"echo {base64.b64encode(fix.encode()).decode()} | base64 -d | python3", 60)
        if rc or "kitfix" not in out:
            raise SystemExit(f"[render] 킷 경로 치환 실패: {out[:300]}")
        kitfix = out.strip().split()[-1]

    # 4. 렌더 (원격 로그는 rdir/render_remote.log)
    t = time.time()
    rlog = f"{rdir}/render_remote.log"
    cmd = (f"{path_export(c)}; cd {rdir} && mkdir -p build && ( {job} ) > {rlog} 2>&1; "
           f"rc=$?; echo RC=$rc >> {rlog}; tail -5 {rlog}; exit $rc")
    rc_job, out = mm_run(host, cmd, 7200)
    T["render"] = round(time.time() - t, 1)

    # 5. 회수
    t = time.time()
    dest.mkdir(parents=True, exist_ok=True)
    rsync(host, remote_spec(host, rlog), str(dest) + "/")
    got: List[Path] = []
    if rc_job == 0 or mode == "full":
        if mode == "stills":
            (dest / "stills").mkdir(exist_ok=True)
            for n in [f"{ver}_{float(x):.2f}.png" for x in a.stills.split(",")]:
                rsync(host, remote_spec(host, f"{rdir}/build/stills/{n}"), str(dest / "stills") + "/")
                got.append(dest / "stills" / n)
        elif mode == "span":
            tag = f"{ver}_{f0}-{f1}"
            rsync(host, remote_spec(host, f"{rdir}/build/video_{tag}.mp4"), str(dest) + "/")
            got.append(dest / f"video_{tag}.mp4")
        else:
            rc2, _ = mm_run(host, f"test -d {rdir}/out", 30)
            if rc2 == 0 and rc_job == 0:   # 실패 시 out/ 은 이전 실행분일 수 있다 → 회수 안 함(qa 만)
                rsync(host, remote_spec(host, f"{rdir}/out/"), str(dest) + "/")
                got += sorted(dest.glob("*.mp4"))
            rc3, _ = mm_run(host, f"test -d {rdir}/build/qa", 30)
            if rc3 == 0:
                rsync(host, remote_spec(host, f"{rdir}/build/qa/"), str(dest / "qa") + "/")
    T["pull"] = round(time.time() - t, 1)
    T["total"] = round(time.time() - t_all, 1)

    manifest = {"mode": mode, "ver": ver, "host": host, "remote_dir": rdir, "job": job, "rc": rc_job,
                "kit_refs_rewritten": kitfix, "guard": gline, "seconds": T,
                "files": [str(p) for p in got if p.exists()], "ts": time.strftime("%F %T")}
    (dest / "render_remote.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"render {mode} ver={ver} rc={rc_job} host={host}  총 {T['total']}s "
          f"(guard {T['guard']} · push {T['push']} · render {T['render']} · pull {T['pull']})")
    for p in got:
        print(f"  {p}  {size(p) if p.exists() else '(없음)'}")
    if rc_job:
        print("  원격 로그 끝:\n    " + "\n    ".join(out.strip().splitlines()[-5:]))
    print(f"  상세: {dest}/render_remote.json · render_remote.log")
    return rc_job


if __name__ == "__main__":
    sys.exit(main())
