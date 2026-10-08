# render — headless 렌더를 다른 기기로 보내기

headless 렌더 파이프라인을 다른 기기로 보내는 예시다. **내 경우 렌더 킷은 motion-reel(비공개 개인 영상 킷)이다.** 그래서 이 도구의 기본 명령과 산출물 경로는 그 킷의 규약을 따른다. 킷 자체는 이 저장소에 없다. 다른 킷을 쓰려면 아래 설정의 명령 템플릿을 바꾼다.

렌더할 프로젝트 폴더를 heavy 역할 원격 기기(내 경우 homemac, 64GB)의 `<workdir>/render/<이름>-<해시6>/` 로 rsync 하고, 거기서 렌더한 뒤 산출물만 지휘 맥으로 가져온다. 렌더하는 동안 지휘 맥 메모리(Chromium 워커 약 1.1GB × 8)를 쓰지 않는다. 나는 한 기기에서 렌더와 병렬 `claude -p` 를 겹쳤다가 커널 패닉을 겪었고, 이 도구는 그 일을 막으려고 만들었다. LLM 비용은 0 이다(T0).

`bin/mm` 은 고치지 않았다. 원격 명령은 전부 `mm <host> run` 으로 보내고, 파일 전송만 rsync 가 맡는다. rsync 도 mm 과 같은 ssh ControlMaster 소켓을 다시 쓴다.

## 설정 (`config.json` 의 `hands.render`)

| 키 | 기본값 | 뜻 |
|---|---|---|
| `kit_dir` | **필수** | 렌더 기기에 있는 킷 경로. 예: `~/kits/motion-reel`. 원격에서 절대경로로 풀어 쓴다 |
| `kit_ref_regex` | 없음 | 프로젝트가 킷을 상대경로로 가리키는 정규식. 원격 복사본에서만 이 부분을 킷 절대경로로 바꾼다 |
| `python` | 없음 | 렌더 기기의 파이썬(numpy 등). 그 폴더를 PATH 맨 앞에 둔다 |
| `python_modules` | `["numpy"]` | `--check` 가 import 해 볼 모듈 |
| `path_extra` | `["/opt/homebrew/bin", "/usr/local/bin"]` | PATH 에 더할 폴더 |
| `guard_cmd` | 없음 | 보내기 전 메모리 판정 명령(`{per_worker}`·`{want}` 치환). exit 2 면 STOP. 비우면 내장 게이트(`memory_pressure` free% < `guard_free_min`(20) 이면 STOP)를 쓴다 |
| `stills_cmd` · `span_cmd` · `full_cmd` | motion-reel 규약 | 명령 템플릿. `{kit} {ver} {stills} {from} {to} {workers} {mb}` 를 치환한다 |
| `host` | heavy 역할 첫 원격 호스트 | 렌더 기기. heavy 역할이 아닌 기기는 거부한다 |

기본 명령 템플릿은 다음과 같다. 산출물은 `build/stills/<ver>_<초>.png`, `build/video_<ver>_<from>-<to>.mp4`, `out/` 에 생긴다고 가정한다.

```
stills_cmd = node {kit}/tools/render.js index.html --ver {ver} --build build --stills {stills}
span_cmd   = node {kit}/tools/render.js index.html --ver {ver} --build build --from {from} --to {to} --workers {workers} --mb {mb} --jpeg
full_cmd   = bash build.sh {ver}
```

## 언제 쓰나

- 지휘 맥에서 무거운 작업이 돌고 있거나 claude 세션이 많아서 렌더를 밖으로 빼고 싶을 때 쓴다.
- 구간 영상이나 전체 빌드를 반복해서 돌리며 지휘 맥은 계속 쓰고 싶을 때 쓴다.

## 언제 쓰지 마나

- 지휘 맥이 한가한데 스틸 1~2장만 미리 보고 싶을 때는 로컬에서 돌리는 편이 단순하다.
- heavy 역할이 아닌 기기(예: 8GB 맥)로는 보내지 않는다. 코드에서 막아 두었다.
- 프로젝트가 킷 말고도 `../` 상위 경로의 자원(이미지·폰트 등)을 참조하면 원격 복사본에서 깨진다. 이런 참조가 있으면 실행 전에 경고가 나온다.
- 렌더 기기의 메모리 게이트가 STOP 이면 아무것도 보내지 않고 exit 2 로 끝낸다.

## 명령

```bash
R=hands/render/render.py
$R --check [<프로젝트>]                     # 렌더 기기의 node·ffmpeg·chromium·python 모듈·킷, 게이트, (프로젝트를 주면) dry-run
$R <프로젝트> --stills 2.5,11,23            # 스틸 PNG       → <프로젝트>/build_remote/stills/
$R <프로젝트> --span 0 2 [--mb 16]          # 구간 영상      → <프로젝트>/build_remote/
$R <프로젝트> --full [ver]                  # 전체 파이프라인 → <프로젝트>/out_remote/ (+ qa/)
   공통: --ver N · --workers N|auto · --dest DIR(회수 위치) · --host H · --dry-run
```

MCP 도구(`../../mcp/plugins/render.py`)는 `render_remote(project, mode=stills|span|full, …)` 와 `render_check(project)` 다.

처리 순서는 이렇다.

1. 메모리 게이트를 본다. STOP 이면 중단한다.
2. rsync 로 보낸다. `/build/ /out/ /out_remote/ /build_remote/ node_modules/` 는 빼고, 숨김 파일은 포함한다.
3. 원격 복사본에서 `kit_ref_regex` 에 걸리는 경로를 킷 절대경로로 바꾼다. 원본은 건드리지 않는다.
4. 명령을 실행한다. PATH 는 `python` 폴더, `path_extra`, 원래 PATH 순서다.
5. 산출물과 `render_remote.log` 를 가져오고 `render_remote.json`(단계별 초·파일 목록)을 쓴다.

## 실측 (2026-10-09, laptop → homemac)

- 스틸 3장을 원격에서 렌더해 가져오는 왕복이 2.3초였다.
- 2초 구간 영상은 18.6초였다.
- 71초 컨셉 영상(`media/machines-as-tools_71s.mp4`)도 이 도구로 homemac 에서 렌더했다. 도중에 homemac 부하가 급등하자 렌더러가 115초 동안 스스로 멈췄다가 다시 이어 갔다.
- LLM 비용은 0 이다. 렌더 기기의 CPU 와 메모리만 쓴다.

## 함정

- 렌더 기기의 기본 `python3`(macOS 의 `/usr/bin` 3.9)에는 numpy 가 없었다. 그래서 `python` 설정으로 numpy 가 있는 파이썬을 PATH 맨 앞에 둔다. 이걸 빠뜨리면 사운드·QA 단계에서 깨진다.
- 한글이 들어간 프로젝트 경로는 원격 폴더 이름을 ASCII slug 로 바꿔 피한다.

## 한계

- 원격 작업 폴더를 자동으로 지우지 않는다(rsync `--delete` 도 쓰지 않는다). 같은 프로젝트를 다시 렌더하면 덮어쓴다. 프로젝트에서 지운 파일은 원격에 남는다.
- `--full` 이 실패하면 `qa/` 만 가져오고 `out/` 은 가져오지 않는다. 원격 `out/` 에 이전 실행분이 남아 있을 수 있기 때문이다.
- 렌더하는 동안 진행률을 보여 주지 않고 끝난 뒤 한 번에 요약한다. 긴 작업은 `mm <host> run "tail <workdir>/render/<이름>/render_remote.log"` 로 들여다본다.
- 렌더 기기의 Chromium 버전이 지휘 맥과 다르면 글리프가 서브픽셀 단위로 어긋날 수 있다.
- 산출물 경로 규약(`build/stills/…`, `build/video_…`)은 코드에 박혀 있다. 다른 규약의 킷이면 명령 템플릿과 함께 이 부분도 고쳐야 한다.
