"""research 도구 — GitHub 레포 URL 하나로 조사를 클론 기기(클론·집계·요약)와 브라우저 기기(화면 읽기)에 팬아웃한다.

본체: hands/research/research.py (CLI 와 같은 코드를 import 해 쓰고, 원격 호출은 서버의 mm 헬퍼로 한다).
"""
from __future__ import annotations

import importlib.util
import json

import config
from mmlib import hand_dir

_SRC = hand_dir("research") / "research.py"


def _load():
    spec = importlib.util.spec_from_file_location("mat_hand_research", _SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def register(mcp, mm):
    core = _load()

    @mcp.tool()
    def research_repo(url: str, focus: str = "", model: str = "haiku", ask: bool = True) -> str:
        """GitHub 레포 하나를 조사해 보고서(md)+trace.json 을 출력 폴더(설정 hands.research.out_dir, 기본 저장소 밖)에 쓰고 10줄만 돌려준다.
        동시에: 클론 기기가 depth 50 클론 후 T0 집계(파일·언어별 줄·테스트 파일·최근 커밋 20개·라이선스·버전·태그),
        browser 역할 기기가 레포 첫 화면(스타·포크·이슈·PR·소개)과 releases 페이지를 읽는다. 그다음 클론 기기의 haiku 작업자가
        README·ARCHITECTURE·AGENTS 류 문서를 읽고 «무엇인가·핵심 개념(focus)·설치 영향·성숙도 신호» 20줄 요약.
        쓸 때: 처음 보는 오픈소스를 도입·벤치마킹할지 판단할 때, 숫자와 근거 경로가 붙은 1차 개요가 필요할 때.
        쓰지 말 때: 이미 아는 레포의 특정 질문 하나(→ run 으로 grep, 또는 ask 직접) · 비공개/거대 모노레포 ·
        github.com 이 아닌 호스트 · 코드 품질 감사처럼 깊은 판단(이 도구는 1차 개요까지).
        비용 실측(공개 레포 하나): 약 40초 · 0.23~0.27달러. ask=False 면 LLM 0."""
        try:
            t = core.run(url, focus, model, ask, mm)
        except (SystemExit, config.ConfigError) as e:
            return f"[research] {e}"
        return core.fmt(t)

    @mcp.tool()
    def research_reports(repo: str = "", limit: int = 5) -> str:
        """지금까지 만든 research 보고서 목록(최신순)을 돌려준다. LLM 0, 비용 0.
        같은 레포를 다시 research_repo 로 돌리기 전에 최근 보고서가 있는지 확인하는 데 쓴다. repo 로 이름 필터."""
        out = core.default_out()
        rows = []
        for tr in sorted(out.glob("*.trace.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                t = json.loads(tr.read_text())
            except Exception:  # noqa: BLE001
                continue
            if repo and repo.lower() not in f"{t.get('owner')}/{t.get('repo')}".lower():
                continue
            rows.append(f"{t.get('started')}  {t.get('owner')}/{t.get('repo')}  focus={t.get('focus') or '-'}  "
                        f"{t.get('wall_s')}s ${t.get('cost_usd')}  {tr.with_name(tr.name.replace('.trace.json', '.md'))}")
            if len(rows) >= limit:
                break
        return "\n".join(rows) or "(보고서 없음)"
