"""auto 도구 — 능력 이름(heavy·judge·browser·light)으로 부르면 기기 상태를 재서 mm 실행 기기를 고른다.

본체: hands/auto/auto.py (이 맥에서 실행하고, 그 안에서 고른 기기로 bin/mm 을 exec 한다).
"""
from __future__ import annotations

import subprocess
import sys
from typing import List, Literal

from mmlib import clean, hand_dir

AUTO = str(hand_dir("auto") / "auto.py")
Cap = Literal["heavy", "judge", "browser", "light"]


def _auto(argv: List[str], timeout: int, max_chars: int) -> str:
    """선택 이유(stderr 한 줄)가 결과 맨 앞에 오도록 stderr + stdout 순서로 붙인다."""
    try:
        p = subprocess.run([sys.executable, AUTO, *argv], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"[auto] {timeout}초 시간 초과"
    body = (p.stderr or "") + (p.stdout or "")
    return (clean(body, max_chars) or "(출력 없음)") + ("" if p.returncode == 0 else f"\n[auto] exit={p.returncode}")


def register(mcp, mm):
    @mcp.tool()
    def auto_dispatch(capability: Cap, args: List[str], dry_run: bool = False,
                      timeout_s: int = 600, max_chars: int = 4000) -> str:
        """능력 이름으로 작업을 보내면 후보 기기 상태(free%·load1·claude 수)를 1초 안에 재고 첫 통과 기기에서 mm 을 실행한다. 판정 LLM 0.
        args = mm 의 host 뒤 인자 목록(host 는 넣지 않는다). 예:
          heavy   ["run","make -C /tmp/mmwork/x"]      큰 원격 기기 → (STOP 이면) 지휘 맥
          browser ["read-url","https://…","3000"]      browser 역할 기기
          judge   ["ask","-m","haiku","요약해라: …"]    can_ask 기기만(모두 STOP 이면 실행 거부)
          light   ["run","uptime"]                     남는 기기부터
        후보 순서는 설정의 roles(지휘 맥은 맨 뒤 예비) 또는 hands.auto.registry 파일. auto_capabilities 로 확인.
        게이트: free<20% 또는 load1>코어×2 → STOP, 연결 실패 → DOWN → 다음 후보.
        출력 첫 줄 "[auto] <능력> → <기기> (상태)" 뒤에 mm 출력. "실행 거부" + exit=3 이면 아무것도 실행되지 않았다.
        쓸 때: 어느 기기가 한가한지 모르거나 신경 쓰고 싶지 않을 때, 같은 일을 반복 배정할 때.
        쓰지 말 때: 특정 기기의 파일·로그인 세션이 필요한 일(→ run/read_url 에 host 명시), 여러 작업 동시 실행(→ batch).
        dry_run=True 면 고르기만 하고 실행하지 않는다."""
        argv = [capability] + (["--dry-run"] if dry_run else []) + ["--", *[str(a) for a in args]]
        return _auto(argv, timeout_s, max_chars)

    @mcp.tool()
    def auto_capabilities() -> str:
        """능력 레지스트리(능력 → 기기 우선순위)와 '지금 부르면 각 능력이 어느 기기로 가는지', 기기별 게이트 판정을 본다.
        상태 조회 1회(1초 미만), LLM 0. auto_dispatch 전에 배정 결과를 미리 보고 싶을 때 쓴다."""
        return _auto(["--list"], 30, 2500)
