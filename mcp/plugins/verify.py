"""verify 도구 — 산출물을 다른 작업자가 판정하고 증거를 sha256 으로 동결하는 장부(해시 체인).

본체: hands/verify/verify.py (CLI 와 같은 코드를 import 해 쓰고, 원격 호출은 서버의 mm 헬퍼로 한다).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import List

import config
from mmlib import hand_dir

_SRC = hand_dir("verify") / "verify.py"


def _load():
    spec = importlib.util.spec_from_file_location("mat_hand_verify", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def register(mcp, mm):
    core = _load()

    @mcp.tool()
    def verify_judge(files: List[str], criteria: str, model: str = "haiku", generator: str = "") -> str:
        """산출물(파일 1~수 개, 총 2MB 이하)을 판정 기기(can_ask·judge 역할)의 «새» 작업자가 기준에 따라 pass|fail|unsure 로
        판정하고, 파일 sha256·기준 해시·판정자 worker 세션 ID 를 장부(JSONL, 해시 체인)에 동결한다.
        쓸 때: 생성자와 다른 눈의 판정이 필요할 때 · 나중에 «이 판정이 아직 유효한가»를 기계로 확인해야 할 때
        (공개·납품 전 게이트, 수치 대조, 한계 서술 점검). 대조 자료(trace·원자료)도 files 에 함께 넣어라.
        쓰지 말 때: 결정론으로 확인 가능한 것(테스트·grep·건수 대조 → run 으로), 단순 요약/교정, 2MB 넘는 묶음.
        model: haiku(기본) | sonnet. 실측 판정 20~33초 · 회당 약 0.05~0.12달러.
        주의: 같은 입력에도 haiku 판정이 fail/pass 로 갈린 실측이 있다 — 중요한 판정은 sonnet 이나 2회 판정으로.
        generator: 생성자 세션 ID(알면). 판정자 ID 와 같으면 independent=false 로 기록된다."""
        try:
            row = core.judge(files, criteria, model, generator or None, None, mm)
        except (SystemExit, config.ConfigError) as e:
            return f"[verify] {e}"
        return core.fmt_judge(row)

    @mcp.tool()
    def verify_check(ledger: str = "") -> str:
        """장부 전체를 재검사한다(LLM 0, 비용 0, 1초 미만). 파일이 바뀌거나 사라진 행은 unknown,
        행 수정·삭제·순서 변경으로 해시 체인이 깨지면 broken. 판정을 근거로 무언가를 내보내기 직전에 쓴다.
        ledger 비우면 기본 장부(설정 hands.verify.ledger, 기본 ~/.local/share/machines-as-tools/verify/LEDGER.jsonl)."""
        p = Path(ledger).expanduser().resolve() if ledger else core.default_ledger()
        rep, broken = core.check(p)
        return core.fmt_check(rep, broken, p)

    @mcp.tool()
    def verify_status(files: List[str], criteria: str) -> str:
        """«지금 이 파일들 + 이 기준»에 유효한 최신 판정을 돌려준다(LLM 0). 해시가 하나라도 다르면 unknown →
        그때 verify_judge 로 다시 판정한다. 같은 판정을 반복 지출하지 않기 위한 게이트."""
        return json.dumps(core.status(files, criteria), ensure_ascii=False)
