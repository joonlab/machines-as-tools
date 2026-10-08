#!/usr/bin/env python3
"""`claude -p --output-format json` 출력에서 핵심 수치만 뽑는다.

출력은 배열(이벤트 목록)이거나 단일 객체다. ssh 너머로 오면 터미널 제어문자가 섞일 수 있어 먼저 걷어낸다.

  cjson.py <file>          → 한 줄 JSON {session_id, result(앞 300자), models, duration_ms, cost_usd, cache_read, cache_create, out}
  cjson.py --human <file>  → 작업자 답 전문 + 마지막 줄 "[mm] worker=<id> model=… <ms>ms $<비용> read=… create=…"

모듈로도 쓴다: parse(text) -> dict, human(text) -> str
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any, Dict

ANSI = re.compile(r"\x1b\][^\x07]*\x07|\x1b\[[0-9;?]*[A-Za-z]")


def _result_event(text: str) -> Dict[str, Any]:
    raw = ANSI.sub("", text)
    starts = [i for i in (raw.find("["), raw.find("{")) if i >= 0]
    if not starts:
        raise ValueError("JSON 이 없다")
    ev, _ = json.JSONDecoder().raw_decode(raw[min(starts):])
    ev = ev if isinstance(ev, list) else [ev]
    res = [e for e in ev if isinstance(e, dict) and (e.get("type") == "result" or "total_cost_usd" in e)]
    if not res:
        raise ValueError("result 이벤트가 없다")
    return res[-1]


def parse(text: str) -> Dict[str, Any]:
    res = _result_event(text)
    u = res.get("usage") or {}
    return {
        "session_id": res.get("session_id"),
        "result": (res.get("result") or "")[:300],
        "full_result": res.get("result") or "",
        "is_error": bool(res.get("is_error")),
        "models": list((res.get("modelUsage") or {}).keys()),
        "duration_ms": res.get("duration_ms"),
        "cost_usd": round(res.get("total_cost_usd") or 0, 4),
        "cache_read": u.get("cache_read_input_tokens"),
        "cache_create": u.get("cache_creation_input_tokens"),
        "out": u.get("output_tokens"),
    }


def human(text: str) -> str:
    d = parse(text)
    return (f"{d['full_result']}\n[mm] worker={d['session_id']} model={','.join(d['models'])} "
            f"{d['duration_ms']}ms ${d['cost_usd']} read={d['cache_read']} create={d['cache_create']}")


def _main(argv) -> int:
    as_human = "--human" in argv
    args = [a for a in argv if a != "--human"]
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    with open(args[0], encoding="utf-8", errors="replace") as f:
        text = f.read()
    try:
        if as_human:
            print(human(text))
        else:
            d = parse(text)
            d.pop("full_result")
            print(json.dumps(d, ensure_ascii=False))
    except (ValueError, json.JSONDecodeError) as e:
        print(f"cjson: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
