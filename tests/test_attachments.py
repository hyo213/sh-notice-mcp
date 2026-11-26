"""모집공고문 PDF 고르기. 파일명은 실제 SH 공고(2026-10 기준)의 첨부 목록 그대로다."""
from sh_notice_mcp.attachments import Attachment, parse_attachments, select_notice_pdf

from .conftest import downlist, wrap_body


def atts(names, sizes=None):
    return parse_attachments(wrap_body("t", "", downlist("1", names, sizes)))


def test_sale_notice_picks_main_pdf_among_forms():
    a = atts([
        "고덕강일지구 3단지 토지임대부 분양주택 입주자모집공고문.pdf",
        "1_ 위임장.pdf",
        "2-1_ 개인정보 수집·이용 및 제3자 제공 동의서(사전청약 당첨자용).pdf",
        "2-2_ 개인정보 수집·이용 및 제3자 제공 동의서(본청약자용).pdf",
    ], [7540395, 38718, 55282, 58413])
    sel = select_notice_pdf(a)
    assert sel.chosen.name.endswith("입주자모집공고문.pdf")
    assert sel.chosen.file_seq == "1"


def test_pamphlet_is_skipped_even_if_larger():
    a = atts([
        "마곡지구 17단지 토지임대부 분양주택 팸플릿.pdf",
        "마곡지구 17단지 토지임대부 분양주택 입주자모집공고문.pdf",
        "1. 위임장.pdf",
        "3. 주택취득자금 조달 및 입주계획서.pdf",
    ], [9_000_000, 2_785_280, 3_604_480, 3_604_480])
    assert select_notice_pdf(a).chosen.name.endswith("입주자모집공고문.pdf")


def test_single_attachment_jeonse():
    sel = select_notice_pdf(atts(["제51차 장기전세 입주자 모집공고.pdf"]))
    assert sel.chosen.name == "제51차 장기전세 입주자 모집공고.pdf"


def test_correction_notice_is_not_excluded():
    sel = select_notice_pdf(atts(["[정정] 마곡지구 17단지 입주자모집공고문.pdf", "위임장.pdf"]))
    assert sel.chosen.name.startswith("[정정]")


def test_result_list_only_is_not_chosen():
    sel = select_notice_pdf(atts(["제48차 장기전세 당첨자 및 예비입주자 명단.pdf"]))
    assert sel.chosen is None and "서식·결과" in sel.reason


def test_two_notice_pdfs_need_human_choice():
    sel = select_notice_pdf(atts(["A단지 입주자모집공고문.pdf", "B단지 입주자모집공고문.pdf"]))
    assert sel.chosen is None
    assert len(sel.candidates) == 2


def test_no_pdf_hwp_only():
    sel = select_notice_pdf(atts(["입주자모집공고문.hwp"]))
    assert sel.chosen is None and "PDF" in sel.reason


def test_no_downlist_means_no_attachments():
    assert parse_attachments("<html>no script</html>") == []
    assert isinstance(Attachment("1", "a.pdf", 1, "GS0401", "1", "A").url, str)
