import asyncio
import json
from datetime import datetime

from sh_notice_mcp import server
from sh_notice_mcp.calendar_backends import ICSBackend
from sh_notice_mcp.service import NoticeService
from sh_notice_mcp.store import Store

from .conftest import fixture_text, wrap_body


def _call(name, args):
    """도구를 호출하고 (에러 여부, 결과 dict 또는 에러 메시지)를 돌려준다."""
    try:
        r = asyncio.run(server.mcp.call_tool(name, args))
    except Exception as e:  # SDK가 예외를 그대로 올리는 경우
        return True, str(e)
    if r.is_error:
        return True, r.content[0].text
    if r.structured_content is not None:
        return False, r.structured_content.get("result", r.structured_content)
    return False, json.loads(r.content[0].text)


def test_tools_are_registered_with_safety_hints():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    assert set(tools) == {
        "list_boards", "check_new_notices", "find_notices", "recent_notices", "record_application", "get_notice", "read_notice_pdf", "extract_schedule",
        "get_my_profile", "check_profile_gaps", "update_my_profile", "get_housing_costs",
        "propose_registration", "confirm_registration",
        "mark_notice_processed", "recent_calendar_events",
        "save_receipt_numbers", "check_my_results", "my_receipts",
    }
    # 확인 없이 바로 쓰는 도구나 삭제 도구는 없다
    assert "add_calendar_event" not in tools
    assert not any("delete" in n for n in tools)
    assert tools["read_notice_pdf"].annotations.read_only_hint is True
    assert tools["propose_registration"].annotations.read_only_hint is True
    w = tools["confirm_registration"].annotations
    assert w.read_only_hint is False and w.destructive_hint is False
    # ctx는 AI가 채우는 인자가 아니어야 한다
    assert set(tools["confirm_registration"].input_schema["properties"]) == {"plan_id"}
    assert set(tools["update_my_profile"].input_schema["properties"]) == {"changes", "reason"}


def test_mcp_flow_read_propose_confirm(tmp_path, fake_client):
    from .test_flow import FakeHuman, make

    human = FakeHuman(pick=[0])
    server._service = make(tmp_path, fake_client, human)
    try:
        err, doc = _call("read_notice_pdf", {"board_id": "sale", "seq": "311656"})
        assert not err and doc["status"] == "ok"
        assert isinstance(doc["notable_rules"], list)  # 특이 규칙은 목차와 함께 전달
        assert doc["key_sections"][0]["tags"] == ["공급일정"]  # 일정·자격 섹션이 먼저
        err, sec = _call("read_notice_pdf", {"board_id": "sale", "seq": "311656", "section": "소득·자산"})
        assert "총자산" in sec["section_text"]

        err, plan = _call("propose_registration", {
            "board_id": "sale", "seq": "311656", "summary": "요약",
            "items": [{"label": "당첨자 발표", "start": "2026-11-26T16:00"},
                      {"label": "지난 일정", "start": "2026-10-01"}]})
        assert not err and len(plan["to_register"]) == 1 and len(plan["rejected"]) == 1

        err, r = _call("confirm_registration", {"plan_id": plan["plan_id"]})
        assert not err and r["results"][0]["status"] == "added"

        err, msg = _call("read_notice_pdf", {"board_id": "sale", "seq": "311656", "section": "없는섹션"})
        assert err and "찾지 못했습니다" in msg
    finally:
        server._service = None


def test_key_sections_are_never_cut_in_the_middle(tmp_path, fake_client, monkeypatch):
    """예산을 넘는 섹션은 반쯤 잘라 주지 않고 read_next로 미룬다."""
    from sh_notice_mcp import pdf_text
    from sh_notice_mcp.pdf_text import Section

    from .test_flow import make

    long_text = "신청자격 " * 3000  # 약 15,000자
    secs = [Section(0, "표지·공고 요약", 1, 3, "요약 " * 100, ["요약"]),
            Section(1, "1. 공급일정", 4, 4, "일정 " * 100, ["공급일정"]),
            Section(2, "2. 신청자격", 5, 9, long_text, ["신청자격"])]
    monkeypatch.setattr("sh_notice_mcp.service.split_sections", lambda pages: secs)
    server._service = make(tmp_path, fake_client)
    try:
        err, doc = _call("read_notice_pdf", {"board_id": "sale", "seq": "311656"})
        assert not err
        assert [k["title"] for k in doc["key_sections"]] == ["표지·공고 요약", "1. 공급일정"]
        assert [r["title"] for r in doc["read_next"]] == ["2. 신청자격"]
        assert all("생략" not in k["text"] for k in doc["key_sections"])
    finally:
        server._service = None
