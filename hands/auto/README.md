# auto — 능력으로 부르면 기기를 고른다

원칙 «기기 이름이 아니라 능력으로 부른다»를 구현한 도구다. 지휘 세션은 `heavy`·`judge`·`browser`·`light` 중 하나만 말한다.
auto 는 후보 기기의 상태를 한 번에 재고, 게이트를 통과한 첫 기기에서 `mm <host> …` 를 실행한다. 판정에는 LLM 을 쓰지 않는다(T0).

| 파일 | 역할 |
|---|---|
| `auto.py` | 실행기. 상태 조회 → 게이트 → 선택 이유 한 줄 → `bin/mm` exec |
| `capabilities.example.json` | 레지스트리를 직접 정하고 싶을 때 쓰는 예시(보통은 필요 없다) |
| `../../mcp/plugins/auto.py` | MCP 도구 `auto_dispatch`, `auto_capabilities` |

## 능력 → 기기 순서는 어디서 오나

1. 환경변수 `AUTO_REGISTRY=<json>` 이 있으면 그 파일을 쓴다.
2. `config.json` 의 `hands.auto.registry` 에 경로가 있으면 그 파일을 쓴다.
3. 둘 다 없으면 `hosts.*.roles` 에서 자동으로 만든다. 그 역할을 가진 호스트를 설정 순서대로 늘어놓되, `ssh: "local"` 인 지휘 맥은 맨 뒤 예비로 둔다. `ask`·`fork` 는 `can_ask: true` 인 호스트로만 간다.

예시 설정(laptop=지휘, homemac=상시 켜진 큰 맥, air=8GB)이라면 이렇게 된다.

| 능력 | 순서 | 용도 |
|---|---|---|
| heavy | homemac → laptop | 렌더·빌드·대량 처리 |
| judge | homemac | LLM 작업자(`mm ask`) |
| browser | air → laptop | GUI cmux 브라우저 읽기 |
| light | homemac → air → laptop | 가벼운 셸 |

게이트는 `free < 20%` 또는 `load1 > 코어 × 2` 이면 STOP 이고, ssh 연결이 실패하면 DOWN 이다. 둘 다 다음 후보로 넘어간다. 후보가 모두 STOP·DOWN 이면 아무것도 실행하지 않고 exit 3 으로 끝낸다. 게이트 값은 `hands.auto.gate` 로 바꾼다. claude 프로세스 수 게이트(`max_claude`)는 기본으로 꺼 두었다.

## 언제 쓰나

- 어느 기기가 한가한지 모르거나, 배정을 신경 쓰고 싶지 않을 때 쓴다.
- 같은 종류의 일을 반복해서 보낼 때 쓴다. 큰 맥이 바빠지면 자동으로 지휘 맥으로 떨어진다.
- 배정 결과만 미리 보고 싶을 때는 `--dry-run` 이나 `auto_capabilities` 를 쓴다.

## 언제 쓰지 마나

- 특정 기기에만 있는 파일이나 로그인 세션이 필요한 일에는 쓰지 않는다. `mm <host> …` 나 MCP `run`·`read_url` 에 host 를 직접 적는다.
- 여러 작업을 동시에 돌릴 때는 `mm batch` 를 쓴다. auto 는 한 번에 하나만 보낸다.
- 무거운 렌더 직전의 메모리 판정을 대신하지 않는다. auto 의 게이트는 거친 1차 필터다. render 도구는 따로 게이트를 본다.

## 명령

```bash
A=hands/auto/auto.py
$A --probe                                  # 설정된 모든 기기 상태와 게이트
$A --list                                   # 능력별로 '지금 어디로 가나'
$A heavy -- run "hostname"                  # host 는 쓰지 않는다
$A browser -- read-url https://example.com 2000
$A judge -- ask -m haiku "요약해라: …"
$A heavy --dry-run -- run "make"            # 고르기만 한다
$A light --dry-run --json -- run uptime     # 선택 결과를 JSON 으로(stdout)
AUTO_FORCE_STOP=homemac $A heavy -- run hostname   # homemac 을 STOP 으로 가정
```

선택 이유는 stderr 에 한 줄로 나온다. 예: `[auto] heavy → laptop (free=86% …) | 제외: homemac:STOP(AUTO_FORCE_STOP)  [probe 0.10s]`.
테스트와 수동 개입용 환경변수는 `AUTO_FORCE_STOP`, `AUTO_FORCE_DOWN`(쉼표로 구분한 호스트), `AUTO_FREE_MIN`, `AUTO_LOAD_PER_CORE`, `AUTO_MAX_CLAUDE`, `AUTO_REGISTRY`, `AUTO_MM` 이다.

MCP 에서는 `auto_dispatch(capability, args, dry_run=False)` 를 쓴다. `args` 는 host 뒤에 오는 mm 인자 목록이다. `auto_capabilities()` 는 레지스트리와 지금의 배정을 보여 준다.

## 실측 (2026-10-09, 맥 세 대)

- `heavy -- run hostname` 은 homemac 을 골랐고 0.44초에 끝났다.
- `browser` 는 air 를 골랐다.
- homemac 을 STOP 으로 두면 heavy 는 laptop 으로 떨어졌다.
- 후보가 모두 STOP 이면 실행을 거부하고 exit 3 으로 끝났다.

LLM 비용은 0 이다.

## 한계

- 게이트는 조회한 순간의 스냅샷이다. 조회 직후 다른 세션이 같은 기기로 몰려도 막지 못한다. 잠금이나 예약은 없다.
- 지휘 맥에 claude 프로세스가 100개 넘게 떠 있어도 free% 가 높으면 통과한다. 필요하면 `max_claude` 를 켠다.
- free% 는 `memory_pressure` 값이다. 압축기와 스왑은 반영하지 않는다.
- ask 가 되는 기기가 하나뿐이면 judge 의 후보도 하나다. 그 기기가 STOP 이면 대체 없이 거부한다.
- `batch`·`status` 는 배정하지 않는다. 실행 내용의 책임은 넘긴 mm 인자에 있다.
