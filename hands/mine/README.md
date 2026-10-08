# mine — 세션 기록 마이닝

> ⚠ **출력에 개인 세션 정보가 담긴다.** `candidates.tsv`·`cards.md`·`trace.json` 에는 세션 이름, 첫 지시 400자, 세션 ID 앞 8자가 들어간다. 공개 저장소에 커밋하지 않는다.
> 그래서 기본 출력 위치를 저장소 밖(`~/.local/share/machines-as-tools/mine/`)에 두었다. `--out-root` 로 저장소 안을 가리키더라도 `.gitignore` 가 `candidates.tsv`·`cards.md`·`*.trace.json` 을 막는다. 커밋 전에 `git status` 로 한 번 더 확인한다.

Claude Code 세션 기록에서 키워드로 후보를 고르고, 가벼운 작업자 여럿이 동시에 «무슨 일을 했나» 카드를 쓴다. 내 경우 `~/.claude` 를 Syncthing 으로 맥끼리 동기화하므로 세션 기록이 homemac 에도 있다. 그래서 스캔과 카드 작성을 전부 homemac 에 맡기고, 지휘 맥은 요약 10줄만 받는다.

## 흐름

1. **T0 스캔 (LLM 0)**: 원격의 `<sessions_dir>/*/*.jsonl` 중 `--since` 이후 수정된 것에서 **사용자 발화만** 읽어 정규식 적중 수로 순위를 매긴다. 상위 `--top` 개를 `--batches` 묶음 JSON 으로 `<workdir>/mine/<run>/` 에 쓴다.
2. **T1 카드 (동시)**: `mm batch` 로 묶음마다 haiku 작업자 하나가 `--question` 에 답하는 카드를 쓴다. 작업자가 보는 것은 세션 ID 앞 8자, 세션 이름, 첫 지시 400자, 적중 수, 적중어 빈도뿐이다. 세션 본문은 보지 않는다.
3. **T0 회수**: 카드 파일을 `mm run cat` 으로 가져와 `cards.md` 로 합치고 `trace.json`(단계별 기기·시작·끝·초·비용)을 쓴다.

## 설정 (`config.json` 의 `hands.mine`, 전부 선택)

| 키 | 기본값 | 뜻 |
|---|---|---|
| `host` | 첫 `can_ask` 호스트 | 세션 기록이 있고 ask 가 되는 기기 |
| `sessions_dir` | `~/.claude/projects` | 원격의 세션 기록 폴더 |
| `names_file` | 없음 | 원격의 세션 이름 JSON(`{sid: 이름}` 또는 `{sid: {"name": 이름}}`). 없으면 이름 칸이 빈다 |
| `python` | `python3` | 원격 파이썬. 스캐너는 표준 라이브러리만 쓴다 |
| `out_root` | `~/.local/share/machines-as-tools/mine` | 결과 폴더(저장소 밖) |

## 언제 쓰나

- «최근 N주 동안 X 와 관련해 어떤 일을 했나»를 물을 때 쓴다. 회고, 주간 리뷰, 포트폴리오 고르기에 맞다.
- «그 작업이 어느 세션이었지»를 찾을 때는 `--scan-only` 로 충분하다. 비용이 0 이다.

## 언제 쓰지 마나

- 세션 하나의 상세 경위가 필요할 때는 쓰지 않는다. 본문을 봐야 하므로 그 세션을 직접 읽는다.
- 정확도가 곧 품질인 산출물에는 그대로 쓰지 않는다. 카드는 첫 지시를 근거로 한 초안이라 사람이 확인해야 한다.

## 명령

```bash
M=hands/mine/mine.py
$M "render|렌더|영상" --since 2026-10-01 --question "영상 제작 작업들이 어떤 종류였나"
$M "deploy|배포" --since 2026-09-25 --scan-only            # LLM 0, 후보 표만
# 옵션: --top 15  --batches 3(최대 6)  --min-hits 3  --model haiku|sonnet  --host H  --out-root <폴더>
```

MCP(`../../mcp/plugins/mine.py`):

- `mine_scan(pattern, since, top=15, min_hits=3)`: T0 만 한다.
- `mine_sessions(pattern, since, question, top=15, batches=3, min_hits=3)`: 전체 흐름을 돈다.

## 실측 (2026-10-09, laptop 지휘 → homemac)

- 세션 248개(1.2GB)를 2.2초에 스캔했다. LLM 비용은 0 이다.
- 상위 15개를 haiku 3묶음으로 나눠 카드를 쓰니 전체 40초, 0.30~0.35달러가 들었다.
- 같은 입력으로 다시 돌리면 카드 수가 달라졌다. 그래서 실측을 범위로 적는다.
- 더 큰 실전 재현에서는 세션 기록 535개(2GB)를 3.3초에 스캔했다(LLM 0).
- 비용 표기 주의: homemac 의 claude 가 구버전이라 Haiku 모델을 인식하지 못하고 Sonnet 단가로 계산했을 가능성이 있다.

## 한계

- 근거가 **첫 지시 400자와 세션 이름**뿐이다. 키워드가 첫 지시 밖에서만 나온 세션은 «추정»이나 «판정 보류»로 남는다.
- 적중은 **단어 빈도**다. 폴더명·경로·URL 에 들어간 키워드도 센다. 걸러내는 일은 작업자의 판단에 맡긴다.
- 이 도구를 부른 세션 자신도 후보에 걸릴 수 있다. 제외 옵션은 아직 없다.
- 기간 필터는 파일 **수정 시각** 기준이다. 오래전에 시작해 최근까지 이어진 세션도 들어온다.
- 묶음 사이 중복은 합치지 않는다. 같은 작업이 다른 묶음에 흩어지면 카드가 두 장 나올 수 있다.
- 동기화가 늦으면 지휘 맥의 최신 세션이 빠진다.
- 원격 `<workdir>/mine/<run>/` 의 중간 파일은 지우지 않는다. 기본 workdir 이 `/tmp` 아래라 재부팅하면 비워진다.
