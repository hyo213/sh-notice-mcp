"""날짜 근거 확인. 날짜 표기는 실제 SH 게시글·공고문 PDF에서 확인한 형식이다."""
from datetime import date, datetime

from sh_notice_mcp.grounding import SourceIndex, dates_in_line

from .conftest import fixture_text


def test_date_formats_seen_in_real_notices():
    assert dates_in_line("○ [1순위] 2026. 9. 14.(월) 10:00 ~ 2026. 9. 15.(화) 17:00") == {
        date(2026, 9, 14), date(2026, 9, 15)}
    assert dates_in_line("- 특별공급: 2026.11.02.(월) 10:00 ~ 11.03.(화) 17:00") == {
        date(2026, 11, 2), date(2026, 11, 3)}
    assert dates_in_line("’26.10.14.(수) ~ ’27.03.05.(금)") == {date(2026, 10, 14), date(2027, 3, 5)}
    assert dates_in_line("서류제출: 2026.12.28.(월) ~ 01.05.(화)") == {date(2026, 12, 28), date(2027, 1, 5)}
    assert dates_in_line("2026년 9월 14일, 2026-09-15") == {date(2026, 9, 14), date(2026, 9, 15)}
    assert dates_in_line("공급호수 1,381세대, 60㎡ 이하 1.5배") == set()
    assert dates_in_line("2026. 2. 30.") == set()  # 없는 날짜


def index():
    return SourceIndex.build([
        ("게시글 본문", fixture_text("body_sale.txt")),
        ("공고문 p.3", "Ⅴ. 공급일정\n계약체결: 2027. 1. 12.(화) ~ 1. 14.(목)"),
    ])


def test_grounded_event_gets_quote_and_source():
    ev, warn = index().ground("일반공급 1순위 (서울 2년 이상)", datetime(2026, 11, 4, 10),
                              datetime(2026, 11, 4, 17), False)
    assert ev[0].source == "게시글 본문"
    assert "1순위자(서울 2년 이상 계속 거주자)" in ev[0].quote  # 라벨과 가장 잘 맞는 줄
    assert warn == []


def test_range_prefers_line_with_both_ends():
    ev, _ = index().ground("특별공급 접수", datetime(2026, 11, 2, 10), datetime(2026, 11, 3, 17), False)
    assert "11.03." in ev[0].quote


def test_pdf_only_date_is_grounded_with_page():
    ev, _ = index().ground("계약체결", datetime(2027, 1, 12), datetime(2027, 1, 14), True)
    assert ev[0].source == "공고문 p.3"


def test_off_by_one_or_invented_date_is_not_grounded():
    assert index().ground("당첨자 발표", datetime(2026, 11, 27, 16), datetime(2026, 11, 27, 17), False)[0] == []
    ev, warn = index().ground("서류제출", datetime(2026, 11, 30), datetime(2026, 12, 10), True)
    assert ev == [] and "종료일 2026-12-10" in warn[0]  # 실제 종료일은 12.09.


def test_wrong_time_is_warned():
    ev, warn = index().ground("당첨자 발표", datetime(2026, 11, 26, 10), datetime(2026, 11, 26, 11), False)
    assert ev and "10:00" in warn[0] and "16:00" in warn[0]


def test_date_from_a_different_schedule_is_warned():
    """날짜는 원문에 있지만(모집공고일 10/16) 라벨('당첨자 발표')과 관계없는 줄이면 경고."""
    ev, warn = index().ground("당첨자 발표", datetime(2026, 10, 16), datetime(2026, 10, 16), True)
    assert ev and "다른 일정의 날짜" in warn[0]
