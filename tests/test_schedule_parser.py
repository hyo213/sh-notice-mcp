from datetime import datetime

from sh_notice_mcp.config import BOARDS
from sh_notice_mcp.schedule_parser import extract_schedules
from sh_notice_mcp.scraper import parse_detail

from .conftest import fixture_text


def _as_tuples(scheds):
    return [(s.label, s.start, s.end, s.all_day) for s in scheds]


def test_jeonse_notice_schedules():
    body = parse_detail(fixture_text("detail_jeonse.html"), BOARDS["rent_jeonse"], "309467").body
    got = _as_tuples(extract_schedules(body))
    sec = "청약신청 일정(인터넷 및 방문)"
    assert got == [
        ("모집공고일", datetime(2026, 8, 31), datetime(2026, 8, 31), True),
        (f"{sec} · [1순위]", datetime(2026, 9, 14, 10), datetime(2026, 9, 15, 17), False),
        (f"{sec} · [2순위]", datetime(2026, 9, 16, 10), datetime(2026, 9, 16, 17), False),
        (f"{sec} · [3·4순위]", datetime(2026, 9, 17, 10), datetime(2026, 9, 17, 17), False),
        (f"{sec} · 1순위 청약접수 결과 및 2순위 신청가능여부", datetime(2026, 9, 16), datetime(2026, 9, 16), True),
        ("서류심사대상자 발표", datetime(2026, 10, 14, 16), datetime(2026, 10, 14, 17), False),
        ("서류심사대상자 서류제출기간", datetime(2026, 10, 19), datetime(2026, 10, 21), True),
        ("당첨자 발표", datetime(2027, 3, 5, 16), datetime(2027, 3, 5, 17), False),
    ]


def test_sentence_dates_are_ignored():
    body = parse_detail(fixture_text("detail_jeonse.html"), BOARDS["rent_jeonse"], "309467").body
    starts = {s.start.date().isoformat() for s in extract_schedules(body)}
    assert "2027-02-19" not in starts  # '(당첨자 발표 14일 전인 2027.02.19. …)' 문장 속 날짜


def test_sale_notice_with_nested_groups_and_short_end_dates():
    got = extract_schedules(fixture_text("body_sale.txt"))
    by_label = {s.label: s for s in got}
    assert len(got) == 10

    pre = by_label["접수일 · 인터넷접수 · 사전청약 당첨자"]
    assert (pre.start, pre.end) == (datetime(2026, 10, 26, 10), datetime(2026, 10, 27, 17))

    g1 = by_label["접수일 · 일반공급 · 1순위자(서울 2년 이상 계속 거주자)"]
    assert (g1.start, g1.end) == (datetime(2026, 11, 4, 10), datetime(2026, 11, 4, 17))

    docs = by_label["심사대상자 서류제출기간"]
    assert docs.all_day and (docs.start.date().isoformat(), docs.end.date().isoformat()) == (
        "2026-11-30", "2026-12-09")


def test_contract_notice_with_square_markers():
    """실제 '당첨자 및 예비입주자 발표' 공고 형식 (□ 소제목, 공백 있는 짧은 종료일)."""
    body = (
        "□ 계약체결기간\n"
        "○ 온라인전자계약: 2026. 3. 11.(수) 10:00 ~ 3. 13.(금) 17:00\n"
        "○ 방문계약: 2026. 3. 12.(목), 3. 13.(금) 각일 10시 ~ 17시(점심시간: 12시~13시 제외)\n"
        "□ 유의사항\n"
        "○ 예비자의 공급 유효기간은 2027. 2. 5.(당첨일로부터 12개월)까지입니다.\n"
    )
    got = _as_tuples(extract_schedules(body))
    assert got == [
        ("계약체결기간 · 온라인전자계약", datetime(2026, 3, 11, 10), datetime(2026, 3, 13, 17), False),
        ("계약체결기간 · 방문계약", datetime(2026, 3, 12), datetime(2026, 3, 12), True),
    ]


def test_group_heading_with_colon_and_date_only_ranges():
    body = (
        "■ 접수일\n○ 인터넷접수:\n"
        "- 특별공급: 2024.01.15.(월) ~ 2024.01.16.(화)\n"
        "■ 당첨자발표: 2024.01.31.(수)\n"
    )
    got = _as_tuples(extract_schedules(body))
    assert got == [
        ("접수일 · 인터넷접수 · 특별공급", datetime(2024, 1, 15), datetime(2024, 1, 16), True),
        ("당첨자발표", datetime(2024, 1, 31), datetime(2024, 1, 31), True),
    ]


def test_correction_notice_has_no_schedule():
    """정정 공고처럼 일정이 없는 글에서 아무것도 만들지 않는다."""
    body = (
        "■ 입주자모집공고일(2026.02.27.) 현재 대출 상품을 협의 중입니다.\n"
        "2026.03.10.\n서울주택도시개발공사 사장"
    )
    assert extract_schedules(body) == []


def test_year_rollover_in_short_end_date():
    [s] = extract_schedules("■ 서류제출기간: 2026.12.28.(월) ~ 01.05.(화)")
    assert s.end == datetime(2027, 1, 5)


def test_lines_without_schedule_keywords_are_skipped():
    assert extract_schedules("■ 작성일: 2026.10.01.") == []
