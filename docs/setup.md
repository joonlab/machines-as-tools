# 설치와 설정

이 문서는 machines-as-tools 를 내 맥이 아닌 다른 사람의 맥 구성에서 돌리는 방법을 적는다.
예시의 호스트 이름 `laptop`(지휘하는 맥) · `homemac`(상시 켜진 큰 맥) · `air`(메모리가 작은 맥)는 설명용이다. 이름은 설정 파일에서 마음대로 정한다.

## 1. 요구사항

| 항목 | 필요 여부 | 메모 |
|---|---|---|
| macOS | 필수 | `status` 가 `memory_pressure`·`sysctl` 을 쓴다. 나는 Apple Silicon 맥 세 대에서만 돌려 봤다. |
| ssh 키 인증 | 필수 | 지휘 맥에서 다른 맥으로 비밀번호 없이 `ssh <별칭>` 이 되어야 한다. `mm` 은 `BatchMode=yes` 로 접속하므로 비밀번호를 묻는 순간 실패한다. |
| Tailscale | 권장 | 집 밖에서도 같은 별칭으로 붙기 위해 쓴다. 같은 LAN 에서만 쓴다면 없어도 된다. |
| Claude Code | 필수(지휘 맥), 선택(작업자 맥) | `ask`·`fork` 를 맡길 맥에만 설치돼 있으면 된다. |
| Python 3.10+ 와 `mcp` 패키지 | MCP 서버에 필수 | `pip install -r requirements.txt`. `bin/mm` 과 `lib/config.py` 는 macOS 기본 python3(3.9)에서도 돈다. |
| cmux | 선택 | 원격 브라우저(`read-url`·`browser`·`close-tab`)를 쓸 때만. 그 맥에서 cmux 앱이 떠 있어야 한다. |

ssh 는 ControlMaster 로 연결을 재사용한다(`~/.ssh/cm/` 에 소켓이 생긴다). 내 환경에서 매번 새로 접속하면 왕복 0.2~0.3초, 재사용하면 0.04초였다.

## 2. 설정 파일 작성

설정 파일 기본 위치는 `~/.config/machines-as-tools/config.json` 이다. 환경변수 `MAT_CONFIG` 로 다른 경로를 줄 수 있다. 저장소에는 예시(`config.example.json`)만 있고, 실제 설정은 저장소 밖에 둔다.

```bash
mkdir -p ~/.config/machines-as-tools
cp config.example.json ~/.config/machines-as-tools/config.json
chmod 600 ~/.config/machines-as-tools/config.json
```

`_` 로 시작하는 키(`_comment` 등)는 설명용이라 읽을 때 버린다. 비워 둔 항목은 기본값으로 채운다.

### hosts.<이름>

| 키 | 기본값 | 뜻 |
|---|---|---|
| `ssh` | (필수) | `~/.ssh/config` 의 별칭. `"local"` 이면 ssh 없이 지휘 맥에서 직접 실행한다. |
| `description` | `""` | 한 줄 설명. MCP 서버가 모델에게 보여 주는 안내문에 들어간다(예: `"64GB, 상시 켜짐"`). |
| `claude_bin` | `"claude"` | 그 맥의 claude 실행 파일. ssh 비대화 셸은 PATH 가 짧으므로 절대경로를 권한다. 원격 셸에 그대로 들어가므로 `$HOME`·`~` 는 원격에서 펼쳐진다. |
| `token_env` | `""` | ssh 로 띄운 claude 가 로그인할 때 읽을 env 파일(원격 경로). 아래 3절 참고. |
| `cmux_bin` | `cmux.bin` | 그 맥의 cmux CLI 경로. |
| `cmux_password_file` | `cmux.password_file` | 그 맥의 cmux 소켓 비밀번호가 담긴 **지휘 맥 로컬** 파일. 맥마다 비밀번호가 다를 때만 쓴다. |
| `can_ask` | `false` | `true` 인 맥에만 `ask`·`fork`(claude 작업자)를 보낸다. 메모리가 작거나 로그인 토큰이 없는 맥은 `false` 로 둔다. |
| `roles` | `[]` | `heavy`(빌드·렌더) · `judge`(판단 작업자) · `browser`(원격 브라우저) · `light`(가벼운 셸) 중 고른다. 도구(플러그인)은 호스트 이름 대신 역할로 기기를 고른다. |

호스트 이름은 영문·숫자·`-`·`_` 만 쓴다. `status`·`batch`·`hosts` 는 명령어와 겹치므로 쓸 수 없다. `mm` 과 MCP 서버는 설정에 적힌 이름만 받는다.

### 나머지 섹션

| 키 | 기본값 | 뜻 |
|---|---|---|
| `cmux.password_file` | `~/.config/cmux/cmux.json` | JSON 이면 `automation.socketPassword` 를, 아니면 첫 줄을 비밀번호로 읽는다. |
| `cmux.bin` | `/Applications/cmux.app/Contents/Resources/bin/cmux` | |
| `worker.lean_config_dir` | `~/.claude-lean` | 작업자용 빈 Claude 설정 디렉터리(원격 경로). 4절 참고. |
| `worker.workdir` | `/tmp/mmwork` | 작업자가 일하는 폴더(원격 경로). |
| `worker.tools` | `Bash,Read,Write,Edit,Glob,Grep` | 작업자에게 주는 도구. |
| `worker.default_model` | `haiku` | `ask` 에서 `-m` 을 생략했을 때의 모델 별칭. |
| `worker.rules` | (한국어 기본 규칙) | 작업자 시스템 프롬프트에 덧붙는 규칙. `{workdir}` 는 실제 작업 폴더로 바뀐다. |
| `models` | haiku·sonnet·opus → 모델 ID | 별칭 → 모델 ID. 별칭에 없는 값은 모델 ID 로 그대로 넘긴다. |
| `ssh.connect_timeout` · `ssh.control_persist` · `ssh.control_dir` | `6` · `"15m"` · `~/.ssh/cm` | ssh 연결 옵션. |
| `state_dir` | `~/.local/state/machines-as-tools` | 실행 장부(`mm.log`)와 `batch` 결과(`results/`)를 쓰는 곳. |
| `hands.<이름>` | `{}` | 도구별 설정 자리. 플러그인이 `config.hand("<이름>")` 로 읽는다. |

경로 값(`workdir`·`lean_config_dir`·`token_env`)은 원격 셸에 따옴표 없이 들어간다. 공백이 없는 경로를 쓴다.

## 3. 원격 claude 로그인(token_env)

ssh 로 들어간 비대화 셸에서는 macOS 키체인이 잠겨 있어서, 구독 로그인 상태의 claude 가 인증 정보를 못 읽는다. 나는 그 맥에서 `claude setup-token` 으로 장기 토큰을 만들고, 그 맥 안의 파일에 아래 한 줄을 적어 둔다.

```bash
# 원격 맥에서. 파일 이름과 위치는 아무거나 괜찮다.
umask 077; echo 'CLAUDE_CODE_OAUTH_TOKEN=<setup-token 출력>' > ~/.config/<아무 폴더>/claude-token.env
```

그리고 설정의 `hosts.<이름>.token_env` 에 그 경로를 적는다. `mm` 은 작업자를 띄우기 직전에 이 파일을 `set -a; . <파일>; set +a` 로 읽는다. 토큰 파일은 저장소에 넣지 않는다(`.gitignore` 가 `*.env` 를 막는다).

참고: `claude --bare` 같은 최소 모드는 구독 로그인으로는 쓸 수 없었다(API 키 전용). 그래서 아래의 "빈 설정 디렉터리" 방식을 쓴다.

## 4. 가벼운 작업자와 빈 설정 디렉터리

`ask` 는 원격 맥에서 claude 를 이렇게 띄운다(요약).

```bash
CLAUDE_CONFIG_DIR=~/.claude-lean claude -p "<지시>" --output-format json \
  --strict-mcp-config --disable-slash-commands --setting-sources '' \
  --tools Bash,Read,Write,Edit,Glob,Grep --allowedTools Bash,Read,Write,Edit,Glob,Grep \
  --model <모델 ID> --append-system-prompt "<worker.rules>"
```

**의미.** `~/.claude-lean` 은 `settings.json` 에 `{}` 만 든 빈 설정 디렉터리다(없으면 `mm` 이 만든다). 여기에 `CLAUDE_CONFIG_DIR` 를 돌리고 설정 출처를 끄면, 그 맥의 `~/.claude` 에 있는 전역 CLAUDE.md·메모리·스킬·훅·MCP 서버가 하나도 안 실린다. 내 환경에서 같은 작업의 첫 턴 맥락이 기본 구성 95,209 토큰($0.3119)에서 가벼운 구성 26,934 토큰($0.0283)으로 줄었다. 같은 작업자를 `-r <worker-id>` 로 이어 쓰면 새로 만들어지는 캐시는 225 토큰이었다.

**위험.** 같은 이유로 **전역 훅이 없다.** 내가 `~/.claude` 에 걸어 둔 안전 장치(삭제 명령 차단, 발송 전 확인, 비밀 파일 읽기 금지 같은 훅과 권한 규칙)는 작업자에게 적용되지 않는다. 게다가 작업자는 비대화 모드에서 Bash·Write·Edit 를 허락받은 상태로 돈다. 작업자를 묶는 것은 `worker.rules` 에 적은 문장과 작업 폴더 관례뿐이고, 이것은 강제가 아니라 부탁이다. 그래서 나는 다음을 지킨다.

- 삭제·발송·배포처럼 되돌리기 어려운 일은 `ask` 로 보내지 않는다. 그런 일은 지휘 세션이 직접 한다.
- 작업자가 쓸 파일은 `worker.workdir`(기본 `/tmp/mmwork`) 아래로만 받는다.
- 비밀 파일이 있는 맥에는 `can_ask` 를 켜기 전에 한 번 더 생각한다. 작업자는 그 맥 사용자 권한으로 무엇이든 읽을 수 있다.
- 작업자에게 필요한 맥락은 대화 전체 복제(`fork`) 대신 짧은 브리프(`-b`)로 준다. 내 실측에서 같은 요약 작업이 fork 로는 $2.14, 약 1,000자 브리프 + Haiku 작업자로는 $0.0146 이었다.

`fork` 는 작업자 맥의 전체 설정(`~/.claude`)으로 돈다. 지휘 세션의 기록(.jsonl)이 그 맥에도 있어야 하므로, 나는 `~/.claude` 를 Syncthing 으로 동기화해 둔 맥에서만 쓴다.

## 5. 설치와 첫 확인

`<R>` 은 이 저장소를 내려받은 절대경로다.

```bash
git clone https://github.com/joonlab/machines-as-tools.git
cd machines-as-tools                       # 이후 <R>
python3 -m pip install -r requirements.txt  # MCP 서버를 돌릴 python 에 설치

<R>/bin/mm hosts                      # 설정 검증 + 호스트·역할 목록
<R>/bin/mm status                     # 모든 기기의 메모리 여유·claude 수·부하·코어 수
<R>/bin/mm homemac run 'uptime'       # LLM 0, 원격 셸
<R>/bin/mm air read-url https://example.com 500   # 원격 cmux 브라우저(cmux 를 쓸 때만)
<R>/bin/mm homemac ask -m haiku "1+1 을 숫자 하나로만 답해라."   # 가벼운 작업자 1회
```

`ask` 출력의 마지막 줄은 `[mm] worker=<id> model=<모델> <ms>ms $<비용> read=<캐시 읽기> create=<캐시 생성>` 이다. 같은 작업자에게 이어서 시키려면 `-r <id>` 를 준다.

여러 기기에 동시에 보내려면 탭으로 구분한 작업 파일을 만든다.

```bash
printf 'a\tlaptop\trun\tuname -m\nb\thomemac\trun\tuptime\nc\tair\tread-url\thttps://example.com\t200\n' > jobs.tsv
<R>/bin/mm batch jobs.tsv     # 요약표만 출력, 전체 결과는 state_dir/results/<시각>/<id>.out
```

내 세 대 구성에서 이 세 줄짜리 batch 는 3.7초에 끝났다(브라우저로 페이지를 여는 `read-url` 이 3.4초로 가장 길다).

## 6. MCP 서버 등록

지휘 맥에서 한 번만 등록한다. `<python>` 은 `mcp` 를 설치한 python 의 절대경로다(`python3 -c 'import sys;print(sys.executable)'`).

```bash
claude mcp add -s user multimac -- <python> <R>/mcp/server.py
```

등록 후 새 Claude Code 세션에서 `/mcp` 로 `multimac` 이 연결됐는지 본다. 도구는 19개다. 기본 도구 8개(`status` · `run` · `read_url` · `browser` · `close_tab` · `ask` · `fork` · `batch`)에, `mcp/plugins/` 에서 자동으로 불러오는 조합 도구 11개(render·verify·mine·research·auto)가 더해진다. 호스트 인자는 설정의 호스트 이름 목록이 그대로 선택지(enum)가 되고, `ask`·`fork` 의 호스트는 `can_ask=true` 인 맥만 선택지가 된다. 설정을 바꾸면 세션을 다시 열어야 도구 스키마에 반영된다.

설정을 다른 곳에 두었다면 등록할 때 환경변수를 같이 준다.

```bash
claude mcp add -s user -e MAT_CONFIG=/path/to/config.json multimac -- <python> <R>/mcp/server.py
```

## 7. 도구(플러그인) 추가

`mcp/plugins/*.py` 는 서버가 뜰 때 자동으로 읽힌다(밑줄로 시작하는 파일은 건너뛴다). 파일마다 `register(mcp, mm)` 하나를 정의하면 된다. `mm(args, timeout, max_chars)` 는 `bin/mm` 을 부르고 정리된 문자열을 돌려주는 헬퍼다. 플러그인 안에서는 `import config` 와 `from mmlib import ...` 로 설정과 공용 헬퍼를 바로 쓸 수 있다.

| 헬퍼 | 하는 일 |
|---|---|
| `config.hosts_with_role("heavy")` | 그 역할을 가진 호스트 이름 목록(설정 순서) |
| `config.ask_hosts()` | `can_ask=true` 인 호스트 목록 |
| `config.hand("render")` | `hands.render` 설정 섹션 |
| `config.model_id("haiku")` | 모델 별칭 → ID |
| `mmlib.mm(args, timeout, max_chars)` | `bin/mm` 호출 + 출력 정리 |
| `mmlib.mm_raw(args, timeout)` | `(returncode, stdout, stderr)` 그대로 |
| `mmlib.run_local(argv, timeout, max_chars)` | 지휘 맥에서 도구 본체 스크립트 실행 |
| `mmlib.clean(text, max_chars)` | 제어문자 제거 + 길이 제한 |
| `mmlib.hand_dir("render")` · `mmlib.workdir()` · `mmlib.MM` | 도구 본체 위치 · 원격 작업 폴더 · `bin/mm` 경로 |

형식은 `mcp/plugins/_example.py` 를 본다. 플러그인 하나가 깨져도 서버는 뜨고, 실패 이유는 stderr 에 남는다.

## 8. 자주 막히는 곳

| 증상 | 원인과 해결 |
|---|---|
| `mm: 설정 파일이 없다` | `~/.config/machines-as-tools/config.json` 이 없다. 2절대로 만든다. |
| `mm: 설정에 없는 호스트` | ssh 별칭이 아니라 설정의 호스트 이름을 써야 한다. `mm hosts` 로 확인한다. |
| `status` 에서 `unreachable` | `ssh -o BatchMode=yes <별칭> true` 가 되는지 먼저 본다. 비밀번호나 호스트 키 확인을 묻고 있으면 실패한다. |
| `ask` 가 `mm: ask 실패` 와 로그인 오류 | `token_env` 가 비었거나 원격 경로가 틀렸다. 그 맥에서 `claude setup-token` 을 다시 한다. |
| `ask` 가 `command not found` | `claude_bin` 을 절대경로로 적는다(ssh 비대화 셸은 PATH 가 짧다). |
| `read-url` 이 `탭 열기 실패` | 그 맥에서 cmux 앱이 떠 있는지, 소켓 비밀번호(`cmux.password_file`)가 맞는지 본다. |
| MCP 도구에 호스트 선택지가 안 바뀜 | 설정을 바꾼 뒤 Claude Code 세션을 새로 연다. |
