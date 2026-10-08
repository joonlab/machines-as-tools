"""플러그인 예시(밑줄로 시작해 로드되지 않음). 새 도구는 이 형태로 만든다.

규칙
- 파일 하나 = 도구 하나. register(mcp, mm) 만 정의한다.
- mm(args, timeout, max_chars) 는 bin/mm 을 부르고 정리된 문자열을 돌려준다. 원격 일은 전부 이걸로 한다.
- 호스트 이름을 코드에 박지 않는다. 역할(heavy·judge·browser·light)로 설정에서 고른다.
- 도구별 설정은 config.json 의 hands.<이름> 에 두고 config.hand("<이름>") 로 읽는다.
- 도구 설명(docstring)은 곧 라우팅 규칙이다. 언제 쓰는지 · 언제 쓰지 않는지 · 비용을 적는다.

예시
----
import config
from mmlib import TAG

def register(mcp, mm):
    @mcp.tool()
    def hello(role: str = "light") -> str:
        \"\"\"<role> 역할을 가진 첫 기기에서 hostname 을 찍는다. LLM 비용 0. 연결 확인용.\"\"\"
        hosts = config.hosts_with_role(role)
        if not hosts:
            return f"{TAG} '{role}' 역할 기기가 설정에 없다"
        return mm([hosts[0], "run", "hostname -s"], 30, 500)
"""
