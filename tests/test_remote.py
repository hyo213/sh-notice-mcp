"""원격 서버: 개인정보 도구가 없고, 토큰 없이는 막히고, 토큰이 있으면 도구가 돈다."""

import asyncio

import httpx
import pytest

from sh_notice_mcp import remote

TOKEN = "t" * 32


def test_only_read_tools_no_personal_data():
    names = {t.name for t in asyncio.run(remote.remote.list_tools())}
    assert names == {"find_notices", "recent_notices", "get_notice", "read_notice_pdf", "extract_schedule",
                     "list_types", "get_housing_costs", "check_results"}
    # 프로필·캘린더·접수번호 저장 도구는 원격에 없다
    assert not names & {"get_my_profile", "update_my_profile", "confirm_registration", "save_receipt_numbers",
                        "my_receipts", "record_application"}


def test_short_token_refused():
    with pytest.raises(SystemExit):
        remote.build_app("short")


async def _run(app):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_types", "arguments": {}}}
    init = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    base = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    out = []
    inner = app.app
    async with inner.router.lifespan_context(inner):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://x") as c:
            for extra in ({}, {"authorization": "Bearer wrong"}, {"authorization": f"Bearer {TOKEN}"}):
                h = {**base, **extra}
                r = await c.post("/mcp", json=init, headers=h)
                if r.status_code == 200:
                    r = await c.post("/mcp", json=body, headers=h)
                out.append(r)
    return out


def test_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("SH_NOTICE_DATA_DIR", str(tmp_path))
    no, wrong, ok = asyncio.run(_run(remote.build_app(TOKEN)))
    assert no.status_code == 401 and wrong.status_code == 401
    assert ok.status_code == 200, ok.text
    assert "rent_happy" in ok.text
