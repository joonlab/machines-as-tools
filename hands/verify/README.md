# verify — 판정 장부

산출물은 **그 산출물을 만들지 않은 작업자**가 판정한다. 판정 기기에 새로 띄운 `claude -p` 작업자다. 판정에 쓴 증거(파일 sha256)와 기준(criteria sha256)은 해시 체인 장부에 고정한다.
OpenRig 의 증거 동결 패턴을 빌렸다. 증거나 기준이 바뀌면 이전 판정은 더 이상 유효하지 않고 **unknown** 이 된다.

- 본체: `verify.py` (CLI)
- 장부: 기본 `~/.local/share/machines-as-tools/verify/LEDGER.jsonl`. 저장소 밖에 둔다(`.gitignore` 에도 `LEDGER*.jsonl` 이 있다).
- MCP 플러그인: `../../mcp/plugins/verify.py` (도구 `verify_judge`·`verify_check`·`verify_status`)

## 설정 (`config.json` 의 `hands.verify`, 전부 선택)

| 키 | 기본값 | 뜻 |
|---|---|---|
| `host` | `can_ask` 이면서 `judge` 역할인 첫 호스트 | 판정 작업자를 띄울 기기 |
| `ledger` | `~/.local/share/machines-as-tools/verify/LEDGER.jsonl` | 장부 파일 |

## 언제 쓰나

- 공개나 납품 직전에 다른 눈으로 판정하는 게이트가 필요할 때 쓴다. 예: 수치가 원자료와 맞는가, 한계를 정직하게 적었는가.
- 나중에 «이 pass 가 아직 유효한가»를 LLM 없이 기계로 확인해야 할 때 쓴다(`check`·`status`).
- 생성자와 검증자를 분리해야 할 때 쓴다. 판정자 신원은 자기 신고가 아니라 `mm ask` 출력에 찍힌 worker 세션 ID 로 기록한다.

## 언제 쓰지 마나

- grep·테스트·건수 대조처럼 결정론으로 확인할 수 있는 것에는 쓰지 않는다. `mm <host> run` 으로 확인하는 편이 비용도 0 이고 더 정확하다.
- 요약이나 교정 같은 생성 작업에는 쓰지 않는다. 이 도구는 판정만 한다.
- 총 2MB 를 넘는 묶음은 받지 않는다. 요약본이나 핵심 파일만 넘긴다.
- 판정 하나로 결론을 내야 하는 고위험 결정에는 그대로 쓰지 않는다. 아래 «한계»를 본다.

## 명령

```bash
V=hands/verify/verify.py
$V judge <파일…> --criteria "<기준>" [--model haiku|sonnet] [--generator <생성자 sid>] [--host H] [--ledger L]
$V check  [--ledger L] [--strict]     # unknown·broken 목록. broken 이면 exit 1, --strict 면 unknown 일 때 exit 3
$V status <파일…> --criteria "<기준>" # 현재 해시와 기준에 맞는 최신 판정. JSON 한 줄
```

`judge` 는 이렇게 흐른다.

1. 로컬에서 sha256 을 계산한다.
2. `mm <host> run` 으로 base64 조각을 보내 `<workdir>/verify/<id>/` 에 복사한다.
3. 원격에서 `shasum` 으로 해시를 다시 계산해 로컬 값과 대조한다. 다르면 중단한다.
4. `mm <host> ask -m <model>` 로 새 작업자에게 판정을 맡긴다(`-r` 재사용 없음). 작업자는 `verdict`·`reasons`·`evidence[file,line,quote]` 를 JSON 으로 내고 `verdict.json` 도 쓴다.
5. flock 잠금 안에서 장부에 행을 추가한다.

장부 행의 `hash` 는 `hash` 필드만 뺀 행 전체를 정렬 JSON 으로 만든 뒤 그 sha256 이다. 행에 `prev_hash` 가 들어 있으므로 앞 행과 체인으로 이어진다. 첫 행의 `prev_hash` 는 0 이 64개다.

## 실측 (2026-10-09, laptop → homemac)

- 판정 한 번에 20~33초, 0.05~0.12달러가 들었다.
- `check` 는 0.03초에 끝났다. LLM 비용은 0 이다.
- 판정한 파일을 고친 뒤 `check` 를 돌리면 그 행이 unknown 으로 바뀌었다.
- 장부 사본의 verdict 를 도구로 바꾸자 `broken — 행 내용이 hash 와 다름` 이 나오고 exit 1 로 끝났다.
- **같은 입력에 haiku 판정이 fail 과 pass 로 갈렸다.** 그래서 중요한 판정은 sonnet 으로 하거나 두 번 판정한다.
- 첫 실행에서 판정자가 프로젝트 문서의 모호한 표현을 실제로 잡아냈다.
- 비용 표기 주의: homemac 의 claude 가 구버전이라 Haiku 모델을 인식하지 못하고 Sonnet 단가로 계산했을 가능성이 있다.

## 한계

- **판정이 흔들린다.** 장부가 동결하는 것은 «그 판정자가 그때 그렇게 말했다»는 사실이지, 판정이 맞다는 보증이 아니다. 두 번 판정해 결과가 다르면 unsure 로 취급한다. 기준을 «X 가 있으면 fail» 처럼 둘로 가를 수 있게 쓰면 흔들림이 줄어든다.
- **해시 체인은 사후 수정만 잡는다.** 장부를 처음부터 통째로 다시 써서 체인을 재계산하면 탐지하지 못한다. 서명이나 외부 앵커가 없기 때문이다. 필요하면 마지막 hash 를 git 커밋 같은 다른 곳에 남겨 둔다.
- 기준 문구가 다르면 `status` 가 매칭하지 않아 unknown 을 낸다. 이미 있는 행의 기준 본문을 고치면 `check` 가 broken 을 낸다. 의미가 같아도 문구가 다르면 다른 기준으로 본다.
- 판정자 신원은 `[mm] worker=` 줄의 세션 ID 다. 이 값을 원격 세션 파일과 대조하지는 않는다. `generator` 는 호출자가 알려 줄 때만 기록한다.
- 파일 경로는 절대경로로 기록한다. 파일을 옮기면 «사라짐»으로 보고 unknown 이 된다.
- 원격 사본(`<workdir>/verify/<id>/`)은 지우지 않는다. 기본 workdir 이 `/tmp` 아래라 재부팅하면 비워진다.
