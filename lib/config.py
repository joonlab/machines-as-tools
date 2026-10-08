#!/usr/bin/env python3
"""machines-as-tools 설정 읽기.

설정 파일: $MAT_CONFIG, 없으면 ~/.config/machines-as-tools/config.json
값이 비어 있는 항목은 DEFAULTS 로 채운다. 호스트는 설정에 적힌 이름만 허용한다.

Python 3.9 에서도 돈다(macOS 기본 /usr/bin/python3). 표준 라이브러리만 쓴다.

CLI (bin/mm 이 쓴다):
  config.py path                     설정 파일 경로
  config.py hosts                    호스트 이름 목록(설정 순서)
  config.py role <role>              그 역할을 가진 호스트 목록(설정 순서)
  config.py get <a.b.c>              값 하나(JSON 이면 JSON 으로)
  config.py shell [<host>]           bash 에서 eval 할 변수 대입문
  config.py check                    설정 검증 결과
"""
from __future__ import annotations

import copy
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ENV_VAR = "MAT_CONFIG"
DEFAULT_PATH = "~/.config/machines-as-tools/config.json"
ROLES = ("heavy", "judge", "browser", "light")

DEFAULT_RULES = (
    "너는 원격 작업자다. 지휘 세션이 맡긴 일 하나만 한다. "
    "rm 으로 삭제하지 않는다(필요하면 mv 로 ~/.Trash). 메일·메시지 발송과 외부 게시는 하지 않는다. "
    "작업 폴더는 {workdir} 이고 산출물은 거기에 쓴다. "
    "지휘자에게 돌려줄 답은 20줄 이내로 쓰고, 긴 결과는 파일로 쓴 뒤 경로만 알려라. "
    "확인한 사실과 추측을 구분하라."
)

DEFAULTS: Dict[str, Any] = {
    "hosts": {},
    "cmux": {
        "password_file": "~/.config/cmux/cmux.json",
        "bin": "/Applications/cmux.app/Contents/Resources/bin/cmux",
    },
    "worker": {
        "lean_config_dir": "~/.claude-lean",
        "workdir": "/tmp/mmwork",
        "tools": "Bash,Read,Write,Edit,Glob,Grep",
        "default_model": "haiku",
        "rules": DEFAULT_RULES,
    },
    "models": {
        "haiku": "claude-haiku-5-5",
        "sonnet": "claude-sonnet-5-5",
        "opus": "claude-opus-5-5",
    },
    "ssh": {
        "connect_timeout": 6,
        "control_persist": "15m",
        "control_dir": "~/.ssh/cm",
    },
    "state_dir": "~/.local/state/machines-as-tools",
    "hands": {},
}

HOST_DEFAULTS: Dict[str, Any] = {
    "ssh": None,              # ssh 별칭, 또는 "local"(이 맥에서 직접 실행)
    "description": "",        # 사람과 모델이 읽는 한 줄 설명(예: "M3 Max 64GB, 상시 켜짐")
    "claude_bin": "claude",   # 원격 셸에 그대로 들어간다($HOME·~ 는 원격에서 펼쳐진다)
    "token_env": "",          # ssh 비대화 로그인용 env 파일(원격 경로). 비우면 안 읽는다
    "cmux_bin": None,         # 비우면 cmux.bin
    "cmux_password_file": None,  # 비우면 cmux.password_file(지휘 맥 로컬 파일)
    "can_ask": False,         # ask·fork(claude 작업자) 허용 여부
    "roles": [],              # heavy · judge · browser · light
}


class ConfigError(Exception):
    pass


def config_path() -> Path:
    return Path(os.path.expanduser(os.environ.get(ENV_VAR) or DEFAULT_PATH))


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _strip_comments(obj: Any) -> Any:
    """"_comment" 같은 밑줄 키는 설명용이므로 버린다."""
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj


_CACHE: Optional[Dict[str, Any]] = None


def load(reload: bool = False) -> Dict[str, Any]:
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE
    p = config_path()
    if not p.exists():
        raise ConfigError(
            f"설정 파일이 없다: {p}\n"
            f"  config.example.json 을 복사해 고쳐라(또는 {ENV_VAR} 로 경로 지정)."
        )
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"설정 JSON 오류 ({p}): {e}") from e
    cfg = _merge(DEFAULTS, _strip_comments(raw))
    hosts = {}
    for name, h in (cfg.get("hosts") or {}).items():
        hosts[name] = _merge(HOST_DEFAULTS, h or {})
    cfg["hosts"] = hosts
    _CACHE = cfg
    return cfg


def validate(cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """문제 목록을 돌려준다(비어 있으면 정상)."""
    cfg = cfg or load()
    errs = []
    if not cfg["hosts"]:
        errs.append("hosts 가 비어 있다")
    for name, h in cfg["hosts"].items():
        if not name.replace("-", "").replace("_", "").isalnum():
            errs.append(f"호스트 이름 '{name}': 영문·숫자·-·_ 만")
        if name in ("status", "batch", "hosts", "config", "help"):
            errs.append(f"호스트 이름 '{name}' 은 mm 명령어와 겹친다")
        if not h.get("ssh"):
            errs.append(f"hosts.{name}.ssh 가 비어 있다(ssh 별칭 또는 \"local\")")
        bad = [r for r in h.get("roles") or [] if r not in ROLES]
        if bad:
            errs.append(f"hosts.{name}.roles 에 모르는 역할 {bad} (허용: {', '.join(ROLES)})")
    dm = cfg["worker"].get("default_model")
    if dm and dm not in cfg["models"] and not str(dm).startswith("claude-"):
        errs.append(f"worker.default_model '{dm}' 이 models 에 없다")
    return errs


def host_names() -> List[str]:
    return list(load()["hosts"].keys())


def host(name: str) -> Dict[str, Any]:
    hosts = load()["hosts"]
    if name not in hosts:
        raise ConfigError(f"설정에 없는 호스트: '{name}' (있는 것: {', '.join(hosts) or '없음'})")
    return hosts[name]


def hosts_with_role(role: str) -> List[str]:
    return [n for n, h in load()["hosts"].items() if role in (h.get("roles") or [])]


def ask_hosts() -> List[str]:
    return [n for n, h in load()["hosts"].items() if h.get("can_ask")]


def is_local(name: str) -> bool:
    return host(name).get("ssh") == "local"


def model_id(alias: Optional[str]) -> str:
    cfg = load()
    alias = alias or cfg["worker"]["default_model"]
    return cfg["models"].get(alias, alias)


def get(dotted: str, default: Any = None) -> Any:
    cur: Any = load()
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def hand(name: str) -> Dict[str, Any]:
    """도구(플러그인)별 설정: config.json 의 hands.<name>. 없으면 빈 dict."""
    return dict((load().get("hands") or {}).get(name) or {})


def state_dir() -> Path:
    p = Path(os.path.expanduser(load()["state_dir"]))
    p.mkdir(parents=True, exist_ok=True)
    return p


def cmux_password(name: Optional[str] = None) -> str:
    """cmux 소켓 비밀번호를 지휘 맥의 로컬 파일에서 읽는다.
    JSON 이면 automation.socketPassword, 아니면 파일 첫 줄."""
    pf = None
    if name:
        pf = host(name).get("cmux_password_file")
    pf = os.path.expanduser(pf or load()["cmux"]["password_file"])
    try:
        text = Path(pf).read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        return str((json.loads(text).get("automation") or {}).get("socketPassword") or "")
    except (json.JSONDecodeError, AttributeError):
        return text.strip().splitlines()[0] if text.strip() else ""


def shell_vars(name: Optional[str] = None) -> Dict[str, str]:
    cfg = load()
    w = cfg["worker"]
    s = cfg["ssh"]
    v = {
        "MAT_CONFIG_PATH": str(config_path()),
        "MAT_HOSTS": " ".join(cfg["hosts"].keys()),
        "MAT_STATE_DIR": str(state_dir()),
        "MAT_WORKDIR": w["workdir"],
        "MAT_LEAN_DIR": w["lean_config_dir"],
        "MAT_TOOLS": w["tools"],
        "MAT_DEFAULT_MODEL": w["default_model"],
        "MAT_RULES": w["rules"].replace("{workdir}", w["workdir"]),
        "MAT_SSH_TIMEOUT": str(s["connect_timeout"]),
        "MAT_SSH_PERSIST": str(s["control_persist"]),
        "MAT_SSH_CMDIR": os.path.expanduser(s["control_dir"]),
    }
    for alias, mid in cfg["models"].items():
        v[f"MAT_MODEL_{alias.upper().replace('-', '_')}"] = mid
    if name:
        h = host(name)
        v.update({
            "MAT_HOST": name,
            "MAT_SSH": h["ssh"],
            "MAT_CLAUDE": h["claude_bin"],
            "MAT_TOKEN_ENV": h.get("token_env") or "",
            "MAT_CMUX": h.get("cmux_bin") or cfg["cmux"]["bin"],
            "MAT_CAN_ASK": "1" if h.get("can_ask") else "0",
            "MAT_ROLES": " ".join(h.get("roles") or []),
        })
    return v


def _main(argv: List[str]) -> int:
    if not argv:
        print(__doc__.strip())
        return 2
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "path":
            print(config_path())
        elif cmd == "hosts":
            print("\n".join(host_names()))
        elif cmd == "role":
            print("\n".join(hosts_with_role(rest[0])))
        elif cmd == "get":
            val = get(rest[0])
            print(val if isinstance(val, str) else json.dumps(val, ensure_ascii=False))
        elif cmd == "password":
            print(cmux_password(rest[0] if rest else None))
        elif cmd == "model":
            print(model_id(rest[0] if rest else None))
        elif cmd == "shell":
            for k, val in shell_vars(rest[0] if rest else None).items():
                print(f"{k}={shlex.quote(val)}")
        elif cmd == "check":
            errs = validate()
            if errs:
                print("\n".join("- " + e for e in errs))
                return 1
            cfg = load()
            print(f"ok: {config_path()}")
            for n, h in cfg["hosts"].items():
                print(f"  {n:<10} ssh={h['ssh']:<10} ask={'yes' if h['can_ask'] else 'no ':<3} "
                      f"roles={','.join(h['roles']) or '-'}  {h.get('description') or ''}")
        else:
            print(f"config.py: 모르는 명령 {cmd}", file=sys.stderr)
            return 2
    except ConfigError as e:
        print(f"mm: {e}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
