#!/usr/bin/env python3
"""machines-as-tools MCP 서버 — bin/mm 을 Claude Code 도구로 노출한다.

지휘 세션 하나가 다른 맥을 자기 도구처럼 부른다. 실제 동작은 전부 bin/mm 이 하고,
이 서버는 인자 검증 · 출력 정리(제어문자 제거·길이 제한) · 동시 실행 묶음만 맡는다.
호스트 목록·역할·ask 가능 여부는 설정 파일에서 읽어 도구 스키마(enum)와 설명에 넣는다.

플러그인: mcp/plugins/*.py (밑줄로 시작하는 파일은 건너뜀). 각 파일은 register(mcp, mm) 하나를 정의한다.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from mcp.server.fastmcp import FastMCP  # noqa: E402

import config  # noqa: E402
from mmlib import TAG, check_host, mm  # noqa: E402

# ── 설정에서 호스트 enum 만들기 ──────────────────────────────────────────────
try:
    _CFG = config.load()
    _CFG_ERR = ""
except config.ConfigError as e:
    _CFG, _CFG_ERR = {"hosts": {}}, str(e)

HOSTS = list(_CFG["hosts"].keys())
ASK_HOSTS = [n for n, h in _CFG["hosts"].items() if h.get("can_ask")]
BROWSER_HOSTS = [n for n, h in _CFG["hosts"].items() if "browser" in (h.get("roles") or [])]
MODELS = list((_CFG.get("models") or {"haiku": "", "sonnet": "", "opus": ""}).keys())

Host = Literal[tuple(HOSTS)] if HOSTS else str  # type: ignore[valid-type]
AskHost = Literal[tuple(ASK_HOSTS)] if ASK_HOSTS else str  # type: ignore[valid-type]
Model = Literal[tuple(MODELS)] if MODELS else str  # type: ignore[valid-type]

DEFAULT_BROWSER = BROWSER_HOSTS[0] if BROWSER_HOSTS else (HOSTS[0] if HOSTS else "")
DEFAULT_ASK = ASK_HOSTS[0] if ASK_HOSTS else ""
DEFAULT_MODEL = str((_CFG.get("worker") or {}).get("default_model") or "haiku")


def _host_lines() -> str:
    if _CFG_ERR:
        return f"설정 오류: {_CFG_ERR}"
    out = []
    for n, h in _CFG["hosts"].items():
        bits = [f"역할={','.join(h.get('roles') or []) or '-'}", f"ask={'가능' if h.get('can_ask') else '불가'}"]
        if h.get("description"):
            bits.insert(0, h["description"])
        out.append(f"{n}({', '.join(bits)})")
    return " · ".join(out)


mcp = FastMCP(
    "multimac",
    instructions=(
        f"여러 맥에 일을 나눠 맡기는 도구. 기기: {_host_lines()}. "
        "싼 것부터 쓴다: run·read_url(LLM 0) → ask(가벼운 작업자) → fork(대화 전체 복제, 비쌈·최후 수단). "
        "여러 작업을 동시에 하려면 batch 하나로 묶는다. 메모리가 작은 기기에는 무거운 일을 보내지 않는다. "
        "보내기 전에 status 로 메모리를 확인한다."
    ),
)


@mcp.tool()
def status() -> str:
    """설정된 모든 맥의 메모리 여유(free%)·claude 프로세스 수·부하·코어 수를 한 화면으로 본다. 무거운 일을 보내기 전에 쓴다."""
    return mm(["status"], 40, 2000)


@mcp.tool()
def run(host: Host, command: str, timeout_s: int = 300, max_chars: int = 4000) -> str:
    """원격(또는 이 맥) 셸 명령을 실행한다. LLM 비용 0. 빌드·렌더·집계·파일 처리처럼 할 일이 정해진 작업용.
    그 기기의 CPU·RAM 을 쓴다. 출력은 max_chars 로 잘라 돌려준다(긴 결과는 파일로 쓰게 하라)."""
    return check_host(host) or mm([host, "run", command], timeout_s, max_chars)


@mcp.tool()
def read_url(url: str, host: Host = DEFAULT_BROWSER, max_chars: int = 4000) -> str:  # type: ignore[assignment]
    """원격 맥의 GUI cmux 브라우저로 페이지를 열어 본문 텍스트를 읽고 탭을 닫는다. LLM 비용 0.
    기본은 browser 역할 기기. 로그인된 사이트도 그 기기 cmux 브라우저 세션 기준으로 읽힌다."""
    return check_host(host) or mm([host, "read-url", url, str(max_chars)], 90, max_chars + 500)


@mcp.tool()
def browser(host: Host, args: list[str], max_chars: int = 4000) -> str:
    """원격 맥의 cmux browser 를 세부 조작한다(클릭·입력·스크린샷 등). args 는 `cmux browser` 뒤에 올 인자 목록.
    예: ["open","https://…"] → 출력의 surface:N 을 받아 ["surface:N","click","button.x"].
    연 탭은 끝나면 반드시 close_tab 으로 닫는다. 단순히 읽기만 하면 read_url 이 낫다."""
    return check_host(host) or mm([host, "browser", *args], 90, max_chars)


@mcp.tool()
def close_tab(host: Host, surface: str) -> str:
    """원격 맥의 브라우저 탭(surface:N)을 닫는다. surface:N 형식만 받는다."""
    if not re.fullmatch(r"surface:\d+", surface):
        return f"{TAG} surface:N 형식이 필요하다"
    return check_host(host) or mm([host, "close-tab", surface], 30, 1000)


@mcp.tool()
def ask(prompt: str, model: Model = DEFAULT_MODEL, brief: str = "",  # type: ignore[assignment]
        resume_worker: str = "", host: AskHost = DEFAULT_ASK, timeout_s: int = 600) -> str:  # type: ignore[assignment]
    """ask 가능 기기에 가벼운 claude 작업자를 띄워 판단이 필요한 일을 맡긴다
    (빈 설정 디렉터리: 전역 CLAUDE.md·훅·MCP·스킬 없이 시작. 실측 맥락 약 2.7만 토큰).
    작업자는 이 대화를 모르므로 필요한 배경은 brief 에 요약해 넣는다(대화 전체 복제보다 수십~백 배 싸다).
    같은 작업자에게 이어서 시키려면 이전 응답 끝 [mm] 줄의 worker 값을 resume_worker 로 넘긴다.
    삭제처럼 되돌리기 어려운 작업은 맡기지 않는다(작업자에게는 안전 훅이 없다)."""
    if not host:
        return f"{TAG} can_ask=true 인 호스트가 설정에 없다"
    err = check_host(host)
    if err:
        return err
    args = [host, "ask", "-m", model]
    tmp = None
    if brief:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        tmp.write(brief)
        tmp.close()
        args += ["-b", tmp.name]
    if resume_worker:
        args += ["-r", resume_worker]
    try:
        return mm([*args, prompt], timeout_s, 6000)
    finally:
        if tmp:
            os.unlink(tmp.name)


@mcp.tool()
def fork(session_id: str, prompt: str, host: AskHost = DEFAULT_ASK, timeout_s: int = 900) -> str:  # type: ignore[assignment]
    """지휘 세션의 대화 전체를 다른 맥에 복제한 분신에게 일을 시킨다. 비싸다(맥락 26만 토큰 세션에서 약 2달러).
    그 맥에 세션 기록(.jsonl)이 동기화돼 있어야 한다. brief 로 옮길 수 없는 미묘한 대화 맥락이 꼭 필요할 때만 쓴다.
    현재 세션 ID 는 Bash 의 $CLAUDE_CODE_SESSION_ID."""
    if not host:
        return f"{TAG} can_ask=true 인 호스트가 설정에 없다"
    return check_host(host) or mm([host, "fork", session_id, prompt], timeout_s, 8000)


@mcp.tool()
def batch(jobs: list[dict], timeout_s: int = 900) -> str:
    """여러 작업을 여러 맥에서 동시에 실행하고 요약표만 돌려준다(전체 결과는 표에 적힌 results 폴더의 <id>.out).
    jobs 원소 = {"id": "짧은이름", "host": "<설정의 호스트>", "action": "run|read-url|ask|browser", "args": [문자열,…]}
    예: [{"id":"page","host":"air","action":"read-url","args":["https://…","3000"]},
         {"id":"build","host":"homemac","action":"run","args":["make -C /tmp/mmwork/x"]},
         {"id":"sum","host":"homemac","action":"ask","args":["-m","haiku","요약해라: …"]}]"""
    lines = []
    for j in jobs:
        jid, host, action = str(j.get("id", "")), str(j.get("host", "")), str(j.get("action", ""))
        if not re.fullmatch(r"[\w.-]{1,40}", jid) or host not in HOSTS \
                or action not in ("run", "read-url", "ask", "browser"):
            return f"{TAG} 잘못된 job: {j} (host 는 {', '.join(HOSTS)} 중 하나)"
        args = [str(a).replace("\t", " ").replace("\n", " ") for a in j.get("args", [])]
        lines.append("\t".join([jid, host, action, *args]))
    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        path = f.name
    try:
        return mm(["batch", path], timeout_s, 6000)
    finally:
        os.unlink(path)


def _load_plugins() -> list[str]:
    """mcp/plugins/*.py 를 불러온다. 각 플러그인은 register(mcp, mm) 하나를 정의한다.
    mm(args, timeout, max_chars) 는 bin/mm 을 부르는 같은 헬퍼(lib/mmlib.py) — 새 도구는 기본 동작을 조합해 만든다.
    플러그인은 `import config`, `from mmlib import …` 로 설정과 헬퍼를 직접 쓸 수 있다(lib 가 sys.path 에 있다)."""
    import importlib.util
    loaded = []
    for f in sorted((Path(__file__).resolve().parent / "plugins").glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"multimac_plugin_{f.stem}", f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.register(mcp, mm)
            loaded.append(f.stem)
        except Exception as e:  # 플러그인 하나가 깨져도 서버는 뜬다
            print(f"{TAG} plugin {f.name} 실패: {e!r}", file=sys.stderr)
    return loaded


PLUGINS = _load_plugins()

if __name__ == "__main__":
    if _CFG_ERR:
        print(f"{TAG} {_CFG_ERR}", file=sys.stderr)
    mcp.run()
