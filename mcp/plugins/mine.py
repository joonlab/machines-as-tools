"""mine 도구 — 세션 기록 마이닝. 본체: hands/mine/mine.py (mm run·batch·ask 조합)."""
from __future__ import annotations

import sys

from mmlib import hand_dir, run_local

MINE = str(hand_dir("mine") / "mine.py")


def register(mcp, mm):
    @mcp.tool()
    def mine_scan(pattern: str, since: str, top: int = 15, min_hits: int = 3) -> str:
        """[T0 · LLM 0 · 수 초] 세션 기록이 동기화된 기기(설정 hands.mine.host, 기본 첫 can_ask 기기)에서
        Claude Code 세션 기록의 사용자 발화에 정규식 pattern 이 많이 적중한 세션을 순위로 뽑아 표(sid·날짜·적중·이름)만 돌려준다.
        since=YYYY-MM-DD 이후 수정된 세션만. 쓸 때: «그 작업 어느 세션이었지», mine_sessions 전에 키워드가
        적절한지 미리 볼 때. 쓰지 말 때: 세션 내용 요약·분류가 필요할 때(→ mine_sessions), 한 세션 본문 정독(→ 직접 읽기).
        전체 후보는 응답 끝 경로의 candidates.tsv(저장소 밖, 개인 기록이므로 공개 금지)."""
        return run_local([sys.executable, MINE, pattern, "--since", since, "--top", str(top),
                          "--min-hits", str(min_hits), "--scan-only"], 300, 3000)

    @mcp.tool()
    def mine_sessions(pattern: str, since: str, question: str, top: int = 15, batches: int = 3,
                      min_hits: int = 3) -> str:
        """[T0 스캔 + T1 haiku 동시 작업자 · 실측 약 40초 · 0.30~0.35달러(15후보·3묶음)]
        세션 기록 마이닝: 정규식 pattern 으로 since 이후 세션 후보를 고르고(LLM 0), 후보를 batches 묶음으로
        나눠 작업자들이 동시에 question 에 답하는 카드(작업 이름·종류·무엇을 했나·근거)를 쓴다.
        작업자는 세션 이름·첫 지시 400자·적중어 빈도만 본다(본문 미열람 → 싸지만 첫 지시 밖 내용은 «추정»).
        쓸 때: «최근 N주 동안 X 관련해 어떤 일들을 했나», 회고·주간 리뷰·포트폴리오용 작업 목록.
        쓰지 말 때: 세션 하나의 상세 경위(본문이 필요), 키워드만 찾으면 될 때(→ mine_scan, 무료),
        결과 정확도가 중요한 산출물(카드는 초안 — 사람이 확인. 재실행하면 카드 수가 달라진다).
        반환: 요약 10줄 이내 + cards.md·trace.json 경로(개인 기록이므로 공개 금지)."""
        return run_local([sys.executable, MINE, pattern, "--since", since, "--question", question,
                          "--top", str(top), "--batches", str(batches), "--min-hits", str(min_hits)], 900, 3000)
