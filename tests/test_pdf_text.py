import pytest

from sh_notice_mcp.pdf_text import PdfError, extract_pages, find_section, split_sections

from .conftest import FIX


def sections():
    return split_sections(extract_pages((FIX / "notice_jeonse.pdf").read_bytes()))


def test_korean_text_and_sections():
    secs = sections()
    titles = [s.title for s in secs]
    assert titles == ["머리말", "공급개요", "신청자격", "소득 및 자산 보유 기준", "입주자 선정 순위", "공급일정"]
    by = {s.title: s for s in secs}
    assert by["공급일정"].tags == ["공급일정"] and by["공급일정"].page_from == 3
    assert "무주택세대구성원" in by["신청자격"].text
    assert "1,381세대" in by["공급개요"].text  # '■ 공급호수: ...' 줄은 제목이 아니라 본문


def test_find_section_by_title_or_tag():
    secs = sections()
    assert [s.title for s in find_section(secs, "소득·자산")] == ["소득 및 자산 보유 기준"]
    assert [s.title for s in find_section(secs, "선정 순위")] == ["입주자 선정 순위"]


def test_non_pdf_is_rejected():
    with pytest.raises(PdfError, match="PDF 파일이 아닙니다"):
        extract_pages(b"\xd0\xcf\x11\xe0 hwp file")


# 실제 '제51차 장기전세주택 입주자 모집공고'(64쪽) 구조를 줄인 페이지들:
# 표지(일정 요약) / CONTENTS / 입주자격 기준 요약 / "01 | 공급 일정" ... / <별표>
REAL_LIKE = [
    "장기전세주택\n입주자 모집공고문\n입주자 모집 절차 및 일정\n1순위 접수기간 '26.09.14.(월)~09.15.(화)",
    "CONTENTS\n"
    "1. 공급일정 .......................................... 4\n"
    "2. 신청자격 .......................................... 5\n"
    "3. 입주자 선정 ...................................... 6\n"
    "4. 동일순위 경쟁시 입주자 선정기준 .............. 6\n"
    "<별표2> 소득 및 자산 항목 설명 ...................... 7",
    "< 입주자격 기준 요약 >\n총자산 66,200만원 이하 자동차 4,542만원 이하\n1순위 약정납입회차 24회 이상",
    "01 | 공급 일정\n□ 일반공급 1순위\n2026. 9. 14.(월) 10:00 ~ 2026. 9. 15.(화) 17:00",
    "02 | 신청자격\n가 신청자격\n서울특별시에 거주하는 성년자인 무주택세대구성원",
    "03 | 입주자 선정\n① 주택청약종합저축 24회 이상\n04 | 동일순위 경쟁 시 입주자 선정 기준\n가점 항목",
    "<별표2> 소득 및 자산 항목 설명\n근로소득, 사업소득 ...",
    "(별표 계속) 금융자산 평가 기준",
]


def test_sections_follow_table_of_contents():
    from sh_notice_mcp.pdf_text import parse_toc

    toc = parse_toc(REAL_LIKE)
    assert [t[0] for t in toc] == ["공급일정", "신청자격", "입주자 선정", "동일순위 경쟁시 입주자 선정기준",
                                   "<별표2> 소득 및 자산 항목 설명"]
    secs = split_sections(REAL_LIKE)
    got = [(s.title, s.page_from, s.page_to) for s in secs]
    assert got == [
        ("표지·공고 요약", 1, 3),          # 표지 + 목차 + 입주자격 기준 요약
        ("1. 공급일정", 4, 4),
        ("2. 신청자격", 5, 5),
        ("3. 입주자 선정", 6, 6),           # 4장과 같은 쪽에서 시작 → 같은 쪽 공유
        ("4. 동일순위 경쟁시 입주자 선정기준", 6, 6),
        ("<별표2> 소득 및 자산 항목 설명", 7, 8),  # 마지막 섹션은 끝까지
    ]
    tags = {s.title: s.tags for s in secs}
    assert tags["표지·공고 요약"][0] == "요약"
    assert "공급일정" in tags["1. 공급일정"]
    assert "소득·자산" in tags["<별표2> 소득 및 자산 항목 설명"]
    assert "총자산 66,200만원" in secs[0].text


def test_toc_page_offset_is_detected():
    """목차의 쪽 번호가 PDF 쪽 번호와 다를 때(앞에 표지가 한 장 더 있는 경우) 'NN |' 제목으로 보정."""
    shifted = ["추가 표지"] + REAL_LIKE
    secs = split_sections(shifted)
    by = {s.title: s for s in secs}
    assert by["1. 공급일정"].page_from == 5
    assert "01 | 공급 일정" in by["1. 공급일정"].text


def test_without_toc_falls_back_to_headings():
    pages = ["01 | 공급 일정\n접수 2026. 9. 14.\n02 | 신청자격\n무주택세대구성원"]
    assert [s.title for s in split_sections(pages)] == ["1. 공급 일정", "2. 신청자격"]


def test_repeated_subheadings_get_parent_title():
    """실제 분양 공고문(목차 없음): 특별공급 유형마다 '■ 신청자격'이 반복된다."""
    pages = [
        "1. 청년 특별공급\n■ 신청자격\n만 19세 이상 39세 이하\n■ 당첨자 선정방법\n추첨",
        "2. 신혼부부 특별공급\n■ 신청자격\n혼인기간 7년 이내\n3. 생애최초 특별공급\n■ 신청자격\n2) 세대주 일 것",
    ]
    titles = [s.title for s in split_sections(pages)]
    assert titles == [
        "1. 청년 특별공급", "1. 청년 특별공급 › 신청자격", "1. 청년 특별공급 › 당첨자 선정방법",
        "2. 신혼부부 특별공급", "2. 신혼부부 특별공급 › 신청자격",
        "3. 생애최초 특별공급", "3. 생애최초 특별공급 › 신청자격",
    ]
    by = {s.title: s for s in split_sections(pages)}
    assert "혼인기간 7년" in by["2. 신혼부부 특별공급 › 신청자격"].text
    assert "신청자격" in by["2. 신혼부부 특별공급 › 신청자격"].tags and "특별공급" in by["2. 신혼부부 특별공급 › 신청자격"].tags


def test_running_footer_is_not_a_heading_and_price_rows_survive():
    """실제 분양 공고문 pypdf 출력: 짝수 쪽 아래 '44 | 공고명' 꼬리글이 장 제목처럼 보였다."""
    footer = "고덕강일지구 3단지 토지임대부 분양주택 입주자모집공고"
    rows = "301 201 059.0000B 59B 371,544 37,154 334,390 5,825,600\n301 202 059.0000B 59B 363,961 36,396 327,565 5,825,600"
    pages = ["팸플릿 표지"] + [
        (f"3. 공급가격\n■ 건물분양가\n{rows}" if i == 0 else rows) + (f"\n{50 + i} | {footer}" if i % 2 == 0 else f"\n{footer} | {50 + i}")
        for i in range(8)
    ]
    secs = split_sections(pages)
    titles = [s.title for s in secs]
    assert not any(footer in t for t in titles)
    price = next(s for s in secs if s.title == "3. 공급가격 › 건물분양가")
    assert price.page_from == 2 and price.page_to == 9  # 쪼개지지 않고 끝까지
    assert price.text.count("059.0000B") == 16  # 숫자 행은 지워지지 않음
    assert footer not in price.text


def test_selection_method_is_not_a_schedule():
    from sh_notice_mcp.pdf_text import _tags_for

    assert "공급일정" not in _tags_for("1. 청년 특별공급 › 당첨자 선정방법", "")
    assert "공급일정" in _tags_for("1. 신청일정", "")
    assert "공급일정" in _tags_for("10. 계약일정 · 장소 및 구비서류", "")


def test_list_items_ending_with_geot_are_not_headings():
    pages = ["5. 일반공급\n1. 기존 소유 주택의 처분 조건을 승낙할 것\n내용"]
    assert [s.title for s in split_sections(pages)] == ["5. 일반공급"]
