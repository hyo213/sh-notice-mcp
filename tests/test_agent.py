"""에이전트 루프: 가짜 Claude가 도구를 고르면, 실제 MCP 서버(하위 프로세스)에서 실행되는지."""
import asyncio
from datetime import date
import sys
from types import SimpleNamespace as NS

from mcp import StdioServerParameters

from sh_notice_mcp import agent


class FakeClaude:
    """정해진 순서대로 tool_use를 내놓고, 마지막에 글로 답하는 대역."""

    def __init__(self, script):
        self.script = list(script)
        self.seen_tools = None
        self.seen_results = []
        self.messages = self

    async def create(self, model, max_tokens, system, tools, messages):
        self.seen_tools = [t["name"] for t in tools]
        last = messages[-1]["content"]
        if isinstance(last, list):
            self.seen_results += [r for r in last if r.get("type") == "tool_result"]
        content = self.script.pop(0)
        return NS(content=content, usage=NS(input_tokens=10, output_tokens=5))


def tool_use(id_, name, args):
    return NS(type="tool_use", id=id_, name=name, input=args)


def text(t):
    return NS(type="text", text=t)


def params(tmp_path):
    return StdioServerParameters(command=sys.executable, args=["-m", "sh_notice_mcp.server"],
                                 env={"SH_NOTICE_DATA_DIR": str(tmp_path)})


def test_loop_calls_real_mcp_server_and_blocks_disallowed(tmp_path):
    born = f"{date.today().year - 34}-01-01"  # 언제 돌려도 34세
    (tmp_path / "profile.toml").write_text(f'[basic]\nbirth_date = "{born}"\n', encoding="utf-8")
    fake = FakeClaude([
        [tool_use("t1", "get_my_profile", {}), tool_use("t2", "update_my_profile", {"changes": {}, "reason": "x"})],
        [text("나이는 34세입니다.")],
    ])
    logs = []
    out = asyncio.run(agent.run_agent("내 나이?", agent.DAILY_TOOLS, log=logs.append, client=fake,
                                      params=params(tmp_path)))
    assert out == "나이는 34세입니다."
    assert "update_my_profile" not in fake.seen_tools  # 혼자 도는 모드에서는 내 조건 수정 도구를 보여주지 않는다
    ok, blocked = fake.seen_results
    assert '"age": 34' in ok["content"] and not ok["is_error"]
    assert blocked["is_error"] and "허용되지 않은 도구" in blocked["content"]
    assert any("Claude → get_my_profile" in m for m in logs) and any("[완료] 2단계" in m for m in logs)


def test_loop_stops_at_max_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "MAX_STEPS", 2)
    fake = FakeClaude([[tool_use(f"t{i}", "get_my_profile", {})] for i in range(5)])
    out = asyncio.run(agent.run_agent("무한 반복", agent.DAILY_TOOLS, log=lambda m: None, client=fake,
                                      params=params(tmp_path)))
    assert "최대 반복" in out


def test_daily_does_nothing_without_new_notices(tmp_path, fake_client):
    from .test_service import make_service

    s = make_service(tmp_path, fake_client)
    s.check_new()  # 기준선

    def must_not_run(*a, **k):
        raise AssertionError("새 공고가 없으면 AI를 부르면 안 된다")

    assert agent.run_daily(service=s, agent=must_not_run) == 0


def test_daily_runs_agent_only_for_new_notices(tmp_path, fake_client, monkeypatch):
    from .test_service import make_service

    monkeypatch.setenv("SH_NOTICE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent, "notify", lambda *a: None)
    monkeypatch.setattr(agent.platform, "system", lambda: "Linux")
    s = make_service(tmp_path, fake_client)
    s.check_new()
    fake_client.add("sale", "999", "새 지구 입주자모집공고", "")
    seen = {}

    async def fake_agent(task, allowed, log):
        seen["task"], seen["allowed"] = task, allowed
        return "# 요약"

    assert agent.run_daily(service=s, agent=fake_agent) == 0
    assert "seq=999" in seen["task"] and "confirm_registration" in seen["allowed"]
    assert "update_my_profile" not in seen["allowed"]
    assert list((tmp_path / "reports").glob("*.md"))[0].read_text(encoding="utf-8") == "# 요약"
