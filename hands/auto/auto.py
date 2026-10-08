#!/usr/bin/env python3
"""auto 도구 — 능력(heavy·judge·browser·light)으로 부르면 기기 상태를 재서 mm 실행 기기를 고른다. LLM 0.

사용법:
  auto.py <capability> [--dry-run] [--json] -- <mm 인자…>   (host 는 빼고 run/read-url/ask… 부터)
  auto.py --list                       능력 레지스트리와 지금 각 능력이 어디로 갈지(상태 1회 조회)
  auto.py --probe [host…]              기기 상태만 본다(기본: 설정의 모든 호스트)

  예) auto.py heavy -- run "hostname"
      auto.py browser -- read-url https://example.com 2000
      auto.py judge -- ask -m haiku "요약해라: …"
      auto.py heavy --dry-run -- run "make"

레지스트리(능력 → 후보 기기 순서) 출처, 앞의 것이 이긴다:
  1) 환경변수 AUTO_REGISTRY=<json 파일>
  2) config.json 의 hands.auto.registry (json 파일 경로)
  3) config.json 의 hosts.*.roles 에서 자동 생성 — 그 역할을 가진 호스트를 설정 순서대로,
     단 ssh=local(지휘 맥)은 맨 뒤 예비로 둔다. ask/fork 는 can_ask=true 호스트만.
  파일 형식은 capabilities.example.json 참고.

동작:
  1) 후보 전부를 병렬로 1회 조회한다(mm 과 같은 ssh ControlMaster 소켓 재사용).
     조회값: memory_pressure free%, load1, 코어 수, claude 프로세스 수, RAM.
  2) 게이트: free < free_min_pct(20) 또는 load1 > 코어×load_per_core_max(2.0) 또는 (설정 시) claude > max_claude → STOP.
     연결 실패 → DOWN. 둘 다 다음 후보로.
  3) 첫 통과 기기로 `mm <host> <인자…>` 를 exec 한다. 선택 이유 한 줄을 먼저 stderr 에 낸다.
  4) 후보가 모두 STOP/DOWN 이면 실행하지 않고 exit 3.

환경변수(테스트·수동 개입):
  AUTO_FORCE_STOP=homemac,air   해당 기기를 조회 결과와 무관하게 STOP 으로 간주
  AUTO_FORCE_DOWN=air           해당 기기를 DOWN 으로 간주(조회 안 함)
  AUTO_FREE_MIN=30 AUTO_LOAD_PER_CORE=1.5 AUTO_MAX_CLAUDE=40   게이트 값 덮어쓰기
  AUTO_REGISTRY=/path/capabilities.json                      레지스트리 교체
  AUTO_MM=/path/mm                                           실행기 교체(기본 <repo>/bin/mm)

exit: mm 의 exit 그대로 / 2 사용법 오류 / 3 모든 후보 STOP·DOWN(실행 거부) / 4 설정 오류
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import config  # noqa: E402
from mmlib import MM as DEFAULT_MM  # noqa: E402

MM = os.environ.get("AUTO_MM", DEFAULT_MM)
GATE_DEFAULT = {"free_min_pct": 20, "load_per_core_max": 2.0, "max_claude": None}
WHY = {
    "heavy": "렌더·빌드·대량 처리",
    "judge": "판정·요약 같은 LLM 작업자(ask)",
    "browser": "GUI cmux 브라우저 읽기",
    "light": "가벼운 셸",
}
PROBE = ("echo \"$(memory_pressure 2>/dev/null | tail -1 | grep -oE '[0-9]+' | tail -1)"
         " $(sysctl -n vm.loadavg | awk '{print $2}') $(sysctl -n hw.ncpu)"
         " $(pgrep -x claude | wc -l | tr -d ' ') $(( $(sysctl -n hw.memsize) / 1073741824 ))\"")


def ssh_opts() -> List[str]:
    s = config.get("ssh") or {}
    cm = Path(os.path.expanduser(s.get("control_dir", "~/.ssh/cm")))
    cm.mkdir(parents=True, exist_ok=True)
    return ["-o", f"ConnectTimeout={s.get('connect_timeout', 6)}", "-o", "BatchMode=yes",
            "-o", "ControlMaster=auto", "-o", f"ControlPath={cm}/%r@%h:%p",
            "-o", f"ControlPersist={s.get('control_persist', '15m')}"]


def envlist(k: str) -> Set[str]:
    return {x.strip() for x in os.environ.get(k, "").split(",") if x.strip()}


def derived_registry() -> dict:
    """설정의 roles 에서 레지스트리를 만든다. 지휘 맥(ssh=local)은 각 능력의 맨 뒤 예비."""
    caps = {}
    for role in config.ROLES:
        hosts = sorted(config.hosts_with_role(role), key=config.is_local)  # 안정 정렬: 원격 먼저
        if hosts:
            caps[role] = {"hosts": hosts, "why": WHY.get(role, "")}
    ask = config.ask_hosts()
    return {"capabilities": caps, "deny": {}, "action_hosts": {"ask": ask, "fork": ask},
            "gate": dict(GATE_DEFAULT, **(config.hand("auto").get("gate") or {}))}


def load_reg() -> Tuple[dict, str]:
    src = os.environ.get("AUTO_REGISTRY") or config.hand("auto").get("registry")
    if src:
        p = Path(os.path.expanduser(src))
        r = json.loads(p.read_text(encoding="utf-8"))
        r["gate"] = dict(GATE_DEFAULT, **(r.get("gate") or {}))
        origin = str(p)
    else:
        r, origin = derived_registry(), "config.json hosts.*.roles"
    g = r["gate"]
    if "AUTO_FREE_MIN" in os.environ: g["free_min_pct"] = float(os.environ["AUTO_FREE_MIN"])
    if "AUTO_LOAD_PER_CORE" in os.environ: g["load_per_core_max"] = float(os.environ["AUTO_LOAD_PER_CORE"])
    if "AUTO_MAX_CLAUDE" in os.environ: g["max_claude"] = int(os.environ["AUTO_MAX_CLAUDE"])
    known = set(config.host_names())
    for cap, c in r.get("capabilities", {}).items():
        bad = [h for h in c.get("hosts", []) if h not in known]
        if bad:
            raise config.ConfigError(f"레지스트리 {cap} 에 설정에 없는 호스트 {bad}")
    return r, origin


def probe(host: str) -> dict:
    """기기 상태 1회 조회. 실패하면 {'host', 'down': 사유}."""
    t0 = time.time()
    if host in envlist("AUTO_FORCE_DOWN"):
        return {"host": host, "down": "AUTO_FORCE_DOWN", "ms": 0}
    try:
        if config.is_local(host):
            p = subprocess.run(["bash", "-c", PROBE], capture_output=True, text=True, timeout=8)
        else:
            p = subprocess.run(["ssh", *ssh_opts(), config.host(host)["ssh"], PROBE],
                               capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired:
        return {"host": host, "down": "timeout", "ms": int((time.time() - t0) * 1000)}
    ms = int((time.time() - t0) * 1000)
    m = re.search(r"(\d+) ([\d.]+) (\d+) (\d+) (\d+)\s*$", p.stdout or "")
    if p.returncode or not m:
        return {"host": host, "down": f"unreachable rc={p.returncode} {(p.stderr or '').strip()[:80]}", "ms": ms}
    free, load1, ncpu, nclaude, ram = m.groups()
    return {"host": host, "free": int(free), "load1": float(load1), "ncpu": int(ncpu),
            "claude": int(nclaude), "ram_gb": int(ram), "ms": ms}


def judge(st: dict, gate: dict) -> Optional[str]:
    """None=통과, 문자열=STOP/DOWN 사유."""
    if "down" in st:
        return f"DOWN({st['down']})"
    if st["host"] in envlist("AUTO_FORCE_STOP"):
        return "STOP(AUTO_FORCE_STOP)"
    why = []
    if st["free"] < gate.get("free_min_pct", 20):
        why.append(f"free {st['free']}%<{gate.get('free_min_pct', 20):g}%")
    lim = st["ncpu"] * gate.get("load_per_core_max", 2.0)
    if st["load1"] > lim:
        why.append(f"load1 {st['load1']:.1f}>{lim:g}")
    mc = gate.get("max_claude")
    if mc is not None and st["claude"] > mc:
        why.append(f"claude {st['claude']}>{mc}")
    return f"STOP({', '.join(why)})" if why else None


def candidates(reg: dict, cap: str, action: Optional[str]) -> Tuple[List[str], List[str]]:
    """(후보, 제외 사유 목록)."""
    caps = reg["capabilities"]
    if cap not in caps:
        raise KeyError(cap)
    out, skipped = [], []
    allowed = reg.get("action_hosts", {}).get(action) if action else None
    for h in caps[cap]["hosts"]:
        if cap in reg.get("deny", {}).get(h, []):
            skipped.append(f"{h}:deny")
        elif allowed is not None and h not in allowed:
            skipped.append(f"{h}:{action} 불가")
        else:
            out.append(h)
    return out, skipped


def short(st: dict) -> str:
    if "down" in st:
        return "down"
    return f"free={st['free']}% load1={st['load1']:.1f}/{st['ncpu']}c claude={st['claude']}"


def probe_all(hosts: List[str]) -> List[dict]:
    if not hosts:
        return []
    with ThreadPoolExecutor(len(hosts)) as ex:
        return list(ex.map(probe, hosts))


def choose(reg: dict, cap: str, action: Optional[str]) -> Tuple[Optional[str], str, List[dict]]:
    hosts, skipped = candidates(reg, cap, action)
    sts = probe_all(hosts)
    for st in sts:
        r = judge(st, reg["gate"])
        if r is None:
            why = f"{cap} → {st['host']} ({short(st)})"
            if skipped:
                why += " | 제외: " + ", ".join(skipped)
            return st["host"], why, sts
        skipped.append(f"{st['host']}:{r}")
    return None, f"{cap} → 실행 거부: 후보 모두 불가 ({', '.join(skipped) or '후보 없음'})", sts


def main(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.split("레지스트리(")[0].strip())
        return 0 if argv else 2
    try:
        reg, origin = load_reg()
    except (config.ConfigError, OSError, json.JSONDecodeError) as e:
        print(f"[auto] 설정 오류: {e}", file=sys.stderr)
        return 4
    gate = reg["gate"]

    if argv[0] == "--probe":
        hosts = argv[1:] or config.host_names()
        for h in hosts:
            if h not in config.host_names():
                print(f"[auto] 설정에 없는 호스트 {h!r}", file=sys.stderr)
                return 2
        t0 = time.time()
        for st in probe_all(hosts):
            print(f"{st['host']:<10} {judge(st, gate) or 'OK':<28} {short(st)}  ({st['ms']}ms)")
        print(f"[auto] probe wall={time.time() - t0:.2f}s")
        return 0

    if argv[0] == "--list":
        t0 = time.time()
        allh = list(dict.fromkeys(h for c in reg["capabilities"].values() for h in c["hosts"]))
        cache = {s["host"]: s for s in probe_all(allh)}
        print(f"[auto] 레지스트리: {origin}")
        for cap, c in reg["capabilities"].items():
            hosts, _ = candidates(reg, cap, None)
            pick = next((h for h in hosts if judge(cache[h], gate) is None), None)
            print(f"{cap:<8} {'>'.join(c['hosts']):<26} 지금→ {pick or '거부'}   # {c.get('why', '')}")
        print(f"  ask/fork 가능: {', '.join(reg.get('action_hosts', {}).get('ask') or []) or '없음'}")
        for h, st in cache.items():
            print(f"  {h:<10} {judge(st, gate) or 'OK':<26} {short(st)} ({st['ms']}ms)")
        print(f"[auto] list wall={time.time() - t0:.2f}s")
        return 0

    cap, rest = argv[0], argv[1:]
    dry = js = False
    while rest and rest[0] != "--":
        f = rest.pop(0)
        if f == "--dry-run":
            dry = True
        elif f == "--json":
            js = True
        else:
            print(f"[auto] 모르는 옵션 {f} (mm 인자는 -- 뒤에)", file=sys.stderr)
            return 2
    if not rest or rest[0] != "--" or len(rest) < 2:
        print("[auto] 사용법: auto.py <capability> [--dry-run] -- <mm 인자…>", file=sys.stderr)
        return 2
    mmargs = rest[1:]
    if mmargs[0] in (*config.host_names(), "status", "batch", "hosts"):
        print("[auto] mm 인자에 host 를 넣지 마라(auto 가 고른다). batch/status 는 mm 을 직접 쓴다", file=sys.stderr)
        return 2
    t0 = time.time()
    try:
        host, why, sts = choose(reg, cap, mmargs[0])
    except KeyError:
        print(f"[auto] 모르는 능력 {cap!r}. 있는 것: {', '.join(reg['capabilities']) or '없음(roles 확인)'}",
              file=sys.stderr)
        return 2
    probe_s = time.time() - t0
    if js:
        print(json.dumps({"capability": cap, "host": host, "reason": why, "probe_s": round(probe_s, 2),
                          "states": sts, "mm": [host, *mmargs] if host else None}, ensure_ascii=False))
    else:
        print(f"[auto] {why}  [probe {probe_s:.2f}s]", file=sys.stderr, flush=True)
    if host is None:
        return 3
    if dry:
        if not js:
            print(f"[auto] dry-run: mm {host} " + " ".join(mmargs), file=sys.stderr)
        return 0
    sys.stdout.flush()
    os.execv(MM, [MM, host, *mmargs])
    return 0  # 도달하지 않음


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
