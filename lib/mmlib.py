"""도구(MCP 플러그인)과 서버가 함께 쓰는 헬퍼.

  from mmlib import mm, clean, config, REPO, MM

  mm(["homemac", "run", "hostname"], timeout=30, max_chars=500)   # bin/mm 호출 → 정리된 문자열
  mm_raw([...], timeout)                                          # (returncode, stdout, stderr) 그대로
  clean(text, max_chars)                                          # 제어문자 제거 + 길이 제한
  config.hosts_with_role("heavy")                                 # 설정 읽기(lib/config.py)
  config.hand("render")                                           # config.json 의 hands.render 섹션
  hand_dir("render")                                              # <repo>/hands/render (도구 본체 스크립트 자리)
  workdir()                                                       # 원격 작업 폴더(기본 /tmp/mmwork)

플러그인 API 는 그대로 register(mcp, mm) 다. 서버가 넘기는 mm 이 바로 이 모듈의 mm 이다.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

LIB = Path(__file__).resolve().parent
REPO = LIB.parent
MM = str(REPO / "bin" / "mm")

if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import config  # noqa: E402  (lib/config.py)

ANSI = re.compile(r"\x1b\][^\x07]*\x07|\x1b\[[0-9;?]*[A-Za-z]")
TAG = "[multimac]"


def clean(text: str, max_chars: int = 4000) -> str:
    """터미널 제어문자를 걷어내고 max_chars 로 자른다. 지휘 세션 맥락을 아끼는 마지막 관문."""
    out = ANSI.sub("", text or "").strip()
    if max_chars and len(out) > max_chars:
        out = out[:max_chars] + f"\n…{TAG} {len(out) - max_chars}자 생략 (max_chars 로 조절)"
    return out


def mm_raw(args: Sequence[str], timeout: int = 300) -> Tuple[int, str, str]:
    """bin/mm 을 그대로 부른다. 시간 초과면 returncode=124."""
    try:
        # read-url 은 바이트 단위로 자르므로 글자 중간이 잘릴 수 있다 → 깨진 바이트는 치환
        p = subprocess.run([MM, *map(str, args)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"{timeout}초 시간 초과"
    return p.returncode, p.stdout or "", p.stderr or ""


def mm(args: Sequence[str], timeout: int = 300, max_chars: int = 4000) -> str:
    """bin/mm 을 부르고 정리된 문자열을 돌려준다. 실패해도 예외 대신 설명 문자열을 준다."""
    rc, out, err = mm_raw(args, timeout)
    if rc == 124 and not out:
        return f"{TAG} {timeout}초 시간 초과: mm {' '.join(map(str, args[:3]))} …"
    body = out + (("\n[stderr]\n" + err) if rc and err else "")
    return (clean(body, max_chars) or "(출력 없음)") + ("" if rc == 0 else f"\n{TAG} exit={rc}")


def run_local(argv: Sequence[str], timeout: int = 300, max_chars: int = 4000) -> str:
    """이 맥(지휘 맥)에서 스크립트를 직접 실행한다. 도구 본체를 부를 때 쓴다(원격은 그 안에서 bin/mm 으로)."""
    try:
        p = subprocess.run(list(map(str, argv)), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"{TAG} {timeout}초 시간 초과: {shlex.join(list(map(str, argv[:2])))} …"
    body = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.returncode and p.stderr else "")
    return (clean(body, max_chars) or "(출력 없음)") + ("" if p.returncode == 0 else f"\n{TAG} exit={p.returncode}")


def hand_dir(name: str) -> Path:
    """도구 본체(CLI) 자리: <repo>/hands/<name>."""
    return REPO / "hands" / name


def workdir() -> str:
    return str(config.get("worker.workdir", "/tmp/mmwork"))


def host_names() -> List[str]:
    try:
        return config.host_names()
    except config.ConfigError:
        return []


def check_host(name: str) -> str:
    """설정에 있는 호스트면 "" , 아니면 오류 문자열."""
    names = host_names()
    return "" if name in names else f"{TAG} 설정에 없는 호스트 '{name}' (있는 것: {', '.join(names) or '없음 — 설정 파일 확인'})"
