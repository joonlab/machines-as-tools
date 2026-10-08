# research — 레포 URL 하나로 조사 팬아웃

GitHub 레포 URL 하나를 받아 여러 맥에 일을 나눈다. 클론 기기(내 경우 homemac)는 클론·집계·요약을, 브라우저 기기(내 경우 air)는 화면 읽기를 맡는다. 끝나면 수치 표, 요약, 출처를 담은 보고서 한 장을 만든다. OpenRig 를 도구로 분석하던 일을 자동화하려고 만들었다.

- 본체: `research.py` (CLI). 원격 집계기는 `agg.py` 이고, 실행할 때마다 base64 로 실어 보낸다.
- MCP 플러그인: `../../mcp/plugins/research.py` (도구 `research_repo`·`research_reports`)
- 결과: `<out_dir>/<repo>_<YYYYmmdd_HHMMSS>.md` + `.trace.json`. 기본 `out_dir` 은 저장소 밖(`~/.local/share/machines-as-tools/research`)이다.
- 원문 보관: `<out_dir>/runs/<repo>_<시각>/` (front.txt·releases.txt·brief.md·jobs.tsv)
- 원격 작업 폴더: `<workdir>/research/<repo>` (클론. 다시 돌리면 fetch 후 reset 으로 갱신한다)

## 설정 (`config.json` 의 `hands.research`, 전부 선택)

| 키 | 기본값 | 뜻 |
|---|---|---|
| `clone_host` | `can_ask`·`heavy` 첫 호스트 → 첫 `can_ask` → 첫 `heavy` | 클론·집계·요약 기기. 요약 작업자가 클론을 직접 읽으므로 ask 를 쓰려면 `can_ask` 여야 한다 |
| `browser_host` | `browser` 역할 첫 호스트 | 화면 읽기 기기. 없으면 (b)(c) 단계를 건너뛴다 |
| `python` | `python3` | 원격 파이썬. 집계기는 표준 라이브러리만 쓴다 |
| `out_dir` | `~/.local/share/machines-as-tools/research` | 보고서 폴더 |

## 흐름

1. **1단, `mm batch` 로 동시에 실행 (LLM 0)**
   - (a) `<clone_host> run`: `git clone --depth 50` 뒤 `agg.py` 가 집계한다. 파일 수, 언어별 줄 수, 테스트 파일 수, 최근 커밋 20개의 기간과 작성자 분포, 라이선스, package.json·pyproject·Cargo 버전, 최근 태그와 날짜, 문서 후보 목록을 낸다.
   - (b) `<browser_host> read-url`: 레포 첫 화면에서 스타, 포크, 워처, 이슈, PR, 소개, 토픽을 정규식으로 뽑는다.
   - (c) `<browser_host> read-url`: `/releases` 페이지에서 릴리스 태그를 뽑는다. 화면에 날짜가 없으면 클론의 태그 날짜로 채우고 `(tag)` 를 붙인다.
2. **2단, `<clone_host> ask -m haiku` (T1)**: 브리프에 focus, T0 수치, 문서 후보를 넣는다. 작업자는 README·AGENTS·CONTRIBUTING·docs 를 읽고 필요한 소스 2~4개를 더 본 뒤, «무엇인가 / 핵심 개념(focus) / 설치 영향 / 성숙도 신호»를 20줄 이내로 쓴다. 주장마다 근거 파일 경로를 붙인다.
3. 보고서와 trace 를 쓰고, 지휘 세션에는 10줄만 돌려준다.

## 언제 쓰나

- 처음 보는 오픈소스를 도입하거나 벤치마킹할지 판단할 때 쓴다. 숫자와 근거 경로가 붙은 1차 개요가 필요한 경우다.
- 여러 후보 레포를 같은 형식으로 나란히 비교할 때 쓴다.
- 수치만 필요하면 `--no-ask` 를 쓴다. LLM 비용이 0 이다.

## 언제 쓰지 마나

- 이미 아는 레포에 대한 질문 하나에는 쓰지 않는다. `mm <host> run` 으로 grep 하거나 `ask` 를 직접 쓰는 편이 싸다.
- 클론이 수 GB 인 거대 모노레포나 LFS 레포에는 쓰지 않는다. 원격 `/tmp` 를 채운다.
- github.com 이 아닌 호스트나 비공개 레포에는 쓰지 않는다. URL 검사에서 거부되거나 클론이 실패한다.
- 보안 감사나 코드 품질 판정처럼 깊은 판단에는 쓰지 않는다. 이 도구는 1차 개요까지만 한다.

## 명령

```bash
R=hands/research/research.py
$R https://github.com/<owner>/<repo> --focus "<관심사>"        # 기본: haiku 요약 포함
$R https://github.com/<owner>/<repo> --no-ask                  # T0 만 (LLM 0)
$R https://github.com/<owner>/<repo> --model sonnet --out DIR  # 더 깊은 요약 / 출력 폴더 지정
```

MCP 에서는 `research_repo(url, focus="", model="haiku", ask=True)` 와 `research_reports(repo="", limit=5)` 를 쓴다. `research_reports` 는 LLM 0 이고, 같은 레포를 다시 돌리기 전에 최근 보고서가 있는지 확인하는 데 쓴다.

## 실측 (2026-10-09)

- 공개 레포 `modelcontextprotocol/python-sdk` 의 1차 개요를 약 40초, 0.23~0.27달러에 만들었다.
- 비용 표기 주의: homemac 의 claude 가 구버전이라 Haiku 모델을 인식하지 못하고 Sonnet 단가로 계산했을 가능성이 있다.

## 한계

- 스타, 이슈 수, 소개는 로그아웃 상태의 GitHub 화면 텍스트를 정규식으로 뽑은 값이다. GitHub 가 화면 문구를 바꾸면 `None` 이 나온다. 원문은 `runs/.../front.txt` 에 남는다.
- releases 페이지에는 상대 시각 요소 때문에 날짜 텍스트가 없다. 그래서 날짜를 클론 태그 날짜로 채운다. 클론이 depth 50 이라 오래된 태그는 빠질 수 있다.
- 커밋 수, 기간, 작성자는 얕은 클론(50개) 범위 안의 값이다. 프로젝트 전체 이력이 아니다.
- 언어별 줄 수는 확장자 기준이고 빈 줄과 주석을 포함한다. 2MB 를 넘는 파일, node_modules, .venv, dist, build 는 뺀다.
- 테스트 파일 수는 경로 패턴(`tests/`, `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`)으로 센다. fixtures 도 들어간다.
- haiku 요약은 같은 입력에도 회차마다 표현과 강조가 달라진다. 수치는 표(T0)를 정본으로 본다.
- 원격 클론은 `<workdir>/research/<repo>` 에 남는다. 이름이 같은 레포가 소유자만 다르면 같은 폴더를 덮어쓴다.
