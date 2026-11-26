from datetime import datetime

import pytest
from pydantic import ValidationError

from sh_notice_mcp.calendar_backends import ICSBackend, to_ics
from sh_notice_mcp.models import CalendarEvent


def ev(**kw):
    base = dict(title="[SH] 테스트", start=datetime(2026, 11, 4, 10), end=datetime(2026, 11, 4, 17),
                board_id="sale", seq="1", label="1순위")
    base.update(kw)
    return CalendarEvent(**base)


def test_end_before_start_rejected():
    with pytest.raises(ValidationError):
        ev(end=datetime(2026, 11, 3))


def test_absurd_span_rejected():
    with pytest.raises(ValidationError):
        ev(end=datetime(2027, 6, 1))


def test_foreign_url_rejected():
    with pytest.raises(ValidationError):
        ev(url="https://evil.example.com/phish")


def test_dedup_key_is_stable_and_specific():
    assert ev().dedup_key == ev().dedup_key
    assert ev().dedup_key != ev(label="2순위").dedup_key


def test_ics_all_day_end_is_exclusive(tmp_path):
    e = CalendarEvent.all_day_event(datetime(2026, 11, 30).date(), datetime(2026, 12, 9).date(),
                                    title="서류제출, 방문; 접수")
    text = to_ics(e)
    assert "DTSTART;VALUE=DATE:20261130" in text
    assert "DTEND;VALUE=DATE:20261210" in text
    assert r"서류제출\, 방문\; 접수" in text
    path = ICSBackend(tmp_path).add_event(e)
    assert path.endswith(".ics")
