import pytest

from sh_notice_mcp.config import BOARDS
from sh_notice_mcp.scraper import ScrapeError, parse_detail, parse_list

from .conftest import fixture_text

RENT = BOARDS["rent_jeonse"]


def test_parse_list_extracts_seq_title_date():
    items = parse_list(fixture_text("list_rent.html"), RENT)
    assert [i.seq for i in items] == ["309467", "308944"]
    first = items[0]
    assert first.title == "제51차 장기전세주택 입주자 모집공고(2026.08.31.공고)"
    assert first.department == "미리내집공급부"
    assert first.posted_at == "2026-08-31"
    assert first.url.endswith("/view.do?seq=309467")


def test_parse_list_raises_when_structure_changes():
    with pytest.raises(ScrapeError):
        parse_list("<html><table><caption>다른 표</caption></table></html>", RENT)


def test_parse_detail_body_and_attachments():
    d = parse_detail(fixture_text("detail_jeonse.html"), RENT, "309467")
    assert d.title.startswith("제51차 장기전세주택")
    assert d.posted_at == "2026-08-31"
    [att] = d.attachments
    assert att.name == "제51차 장기전세 입주자 모집공고.pdf"
    assert att.size == 766468
    assert att.url == ("https://www.i-sh.co.kr/main/com/file/innoFD.do"
                       "?brdId=GS0401&seq=309467&fileTp=A&fileSeq=1")
    assert "[1순위] 2026. 9. 14." in d.body
    assert "청약홈 접속" not in d.body  # 본문 속 안내 표는 제거


def test_merge_notices_dedups_and_sorts_newest_first():
    from sh_notice_mcp.scraper import NoticeSummary, merge_notices

    def n(seq, title):
        return NoticeSummary("sale", seq, title, "분양부", "2026-01-01", "")
    a = [n("311656", "고덕강일 입주자모집공고"), n("269355", "마곡16 사전예약 입주자 모집공고")]
    b = [n("269355", "마곡16 사전예약 입주자 모집공고"), n("301076", "마곡17 입주자모집공고")]
    assert [x.seq for x in merge_notices([a, b])] == ["311656", "301076", "269355"]


def test_sale_board_search_words_and_land_exclusion():
    """실제 제목으로 확인 (2026-10 기준 주택분양 게시판)."""
    sale = BOARDS["sale"]
    assert sale.search_words == ("입주자모집공고", "입주자 모집공고", "입주자 모집 공고")
    keep = [
        ("고덕강일지구 3단지 토지임대부 분양주택 입주자모집공고", "분양부"),
        ("마곡지구 16단지 토지임대부(나눔형) 분양주택 사전예약 입주자 모집공고", "분양부"),
        ("마곡지구 10-2단지 토지임대부 분양주택 사전예약 입주자 모집 공고", "분양부"),
        # '공공주택지구'에는 '택지'라는 글자가 들어 있다 (주택+지구). 실제로 걸러질 뻔한 버그.
        ("고덕강일 공공주택지구 8, 14단지 입주자모집공고", "분양수납부"),
        ("항동 공공주택지구 2,4단지 분양주택 입주자 모집공고", "분양수납부"),
    ]
    drop = [
        ("강서구 외발산동 산27-2번지 토지 분양 공고", "택지판매부"),
        ("은평지구 편익시설용지 분양 재공고", "택지판매부"),
        ("○○지구 토지 분양 입주자모집공고", "분양부"),  # 제목만으로도 걸러짐
    ]
    assert not any(sale.is_excluded(t, d) for t, d in keep)
    assert all(sale.is_excluded(t, d) for t, d in drop)
    # 장기전세는 검색어가 아니라 공급유형 코드(splyTy=03)로 좁힌다
    assert BOARDS["rent_jeonse"].form["splyTy"] == "03"


def test_real_sh_body_with_span_fragments():
    """실제 SH 본문은 한 문단이 글자 단위 <span>으로 쪼개져 있다 (2026-10-21 실제 51차 공고에서 발견).
    태그마다 줄을 바꾸면 '[1순위] 2026. 9. 14.'가 흩어져 1순위 접수일을 놓치고, 서류제출 기간이 하루로 줄었다."""
    from sh_notice_mcp.schedule_parser import extract_schedules

    d = parse_detail(fixture_text("detail_jeonse_real.html"), RENT, "309467")
    assert "○ [1순위] 2026. 9. 14.(월) 10:00 ~ 2026. 9. 15.(화) 17:00" in d.body
    got = {s.label.split(" · ")[-1]: (s.start.isoformat(), s.end.isoformat()) for s in extract_schedules(d.body)}
    assert got["[1순위]"] == ("2026-09-14T10:00:00", "2026-09-15T17:00:00")
    assert got["서류심사대상자 서류제출기간"] == ("2026-10-19T00:00:00", "2026-10-21T00:00:00")
    assert got["당첨자 발표"][0] == "2027-03-05T16:00:00"
