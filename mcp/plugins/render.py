"""render 도구 — headless 렌더를 heavy 기기에 맡기고 산출물만 회수한다. 본체: hands/render/render.py.

이 맥에서 render.py 를 실행하고, 그 안에서 원격은 `mm <host> run` + rsync 로 다룬다.
"""
from __future__ import annotations

import sys
from typing import Literal

from mmlib import hand_dir, run_local

RENDER = str(hand_dir("render") / "render.py")


def register(mcp, mm):
    @mcp.tool()
    def render_remote(project: str, mode: Literal["stills", "span", "full"], stills: str = "",
                      span_from: float = 0.0, span_to: float = 2.0, ver: str = "", mb: int = 16,
                      dest: str = "", timeout_s: int = 3600) -> str:
        """렌더 프로젝트(index.html 있는 폴더)를 heavy 역할 원격 기기에서 렌더하고 결과 파일만 이 맥으로 회수한다. LLM 비용 0.
        킷 경로·명령 템플릿은 설정 hands.render(kit_dir 필수). 기본 규약은 내 개인 영상 킷(motion-reel, 비공개)의 것.
        언제: 이 맥 메모리가 빠듯하거나(다른 무거운 작업·claude 세션 다수) 렌더를 밖으로 빼고 싶을 때.
          mode=stills  stills="2.5,11,23" (초) → PNG.      실측 3장 왕복 2.3초.
          mode=span    span_from~span_to 구간 영상(모션블러 mb). 실측 2초 구간 18.6초.
          mode=full    전체 파이프라인(full_cmd) → out_remote/. 수 분.
        회수 위치: 기본 <project>/build_remote/ (full 은 out_remote/), dest 로 바꿀 수 있다. 원격 작업폴더는 <workdir>/render/<이름>/.
        쓰지 말 때: 스틸 1~2장 미리보기만 필요하고 이 맥이 한가할 때(로컬이 더 단순), heavy 가 아닌 기기로 보내기(거부됨),
          킷 외에 ../ 상위경로 자원을 참조하는 프로젝트(경고가 나오며 원격에서 깨질 수 있다), 원격 메모리 게이트가 STOP 일 때(자동 중단).
        출력: 요약 몇 줄(시간·파일 경로). 상세는 회수 폴더의 render_remote.json/log."""
        args = [sys.executable, RENDER, project]
        if mode == "stills":
            if not stills:
                return "[render] mode=stills 는 stills='1,2.5' 가 필요하다"
            args += ["--stills", stills]
        elif mode == "span":
            args += ["--span", f"{span_from:g}", f"{span_to:g}", "--mb", str(mb)]
        else:
            args += ["--full"] + ([ver] if ver else [])
        if ver and mode != "full":
            args += ["--ver", ver]
        if dest:
            args += ["--dest", dest]
        return run_local(args, timeout_s, 3000)

    @mcp.tool()
    def render_check(project: str = "") -> str:
        """렌더 보내기 전 점검(LLM 0, 몇 초): 렌더 기기의 node·ffmpeg·playwright chromium·python 모듈·킷 유무와 메모리 게이트 판정,
        project 를 주면 보낼 내용(dry-run: 원격 폴더·실행 명령·킷 외 상위경로 참조 경고)까지 보여 준다.
        render_remote 가 실패했을 때 원인 좁히기용. 매번 부를 필요는 없다(render_remote 도 게이트는 스스로 본다)."""
        return run_local([sys.executable, RENDER, "--check"] + ([project] if project else []), 90, 3000)
