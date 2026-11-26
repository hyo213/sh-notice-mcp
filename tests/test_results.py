"""당첨자 발표: 명단 파싱, 발표 글 판별, 접수번호 대조, 서비스 흐름 (네트워크 없음)."""

import json

from sh_notice_mcp import results as res
from sh_notice_mcp.service import NoticeService

from .conftest import downlist, wrap_body

# 실제 SH 명단 PDF(2026-04 마곡 17단지 본청약)의 텍스트 모양을 본뜬 가짜 데이터. 이름·번호는 지어낸 것.
WIN_PAGES = [
    "연번 주택형 접수번호 신청구분 당첨자\n1 059.0000H 500142 사전청약-청년 가*나\n"
    "2 049.0000H 511111 생애최초 다*라\n○○지구 본청약 당첨자 명단\n1",
    "3 059.0000H 522222 일반 마*바\n4 084.0000H 533333 신생아 사*\n5 059.0000H 5131",  # 마지막 줄은 쪽 경계에서 잘림
]
WAIT_PAGES = [
    "구분 주택형 쪽 번호\n59㎡ 1 ~ 21\n순위 주택형 접수번호 신청구분 예비입주자\n"
    "1 049.0000H 544444 생애최초 아*자\n2 049.0000H 512345 신혼부부 차*카\n특별공급 주택형 49㎡",
]

NOTICE = "[정정] 고덕강일지구 3단지 토지임대부 분양주택 입주자모집공고"


def test_parse_result_list():
    rows = res.parse_result_list(WIN_PAGES)
    assert [r["receipt_no"] for r in rows] == ["500142", "511111", "522222", "533333"]  # 잘린 줄은 버림
    assert rows[1] == {"order": 2, "house_type": "49㎡", "house_code": "049.0000H", "receipt_no": "511111",
                       "category": "생애최초", "page": 1}
    assert rows[0]["category"] == "사전청약-청년"


def test_list_kind():
    assert res.list_kind("1_ 마곡지구 17단지 토지임대부 분양주택 본청약 당첨자 명단.pdf") == "당첨"
    assert res.list_kind("2_ ○○ 본청약 예비입주자 명단.pdf") == "예비"
    assert res.list_kind("1_ ○○ 당첨자 확정 명단.pdf") == "당첨"
    assert res.list_kind("3_ ○○ 당첨하한선.pdf") is None
    assert res.list_kind("11_ 위임장.pdf") is None


def test_notice_key_and_post_matching():
    assert res.notice_key(NOTICE) == "고덕강일지구 3단지 토지임대부 분양주택"
    assert res.is_result_post_for("고덕강일지구 3단지 토지임대부 분양주택 당첨자 발표 및 서류제출 안내", NOTICE)
    assert res.is_result_post_for("고덕강일3단지 토지임대부 분양주택 당첨자 확정 명단 게시", NOTICE)
    # 사전청약 발표, 다른 단지, 접수 결과 글은 아니다
    assert not res.is_result_post_for("고덕강일3단지 토지임대부 분양주택 2차 사전예약 당첨자 확정 명단 게시", NOTICE)
    assert not res.is_result_post_for("마곡지구 17단지 토지임대부 분양주택 본청약 당첨자 발표 및 서류제출 안내", NOTICE)
    assert not res.is_result_post_for("고덕강일지구 3단지 토지임대부 분양주택 일반공급 3일차(최종) 접수결과 게시", NOTICE)


def test_judge():
    found = {"당첨": res.parse_result_list(WIN_PAGES), "예비": res.parse_result_list(WAIT_PAGES)}
    assert res.judge("511111", found)["result"] == "당첨"
    w = res.judge("512-345", found)  # 하이픈 등은 무시
    assert (w["result"], w["rank"], w["of"], w["group"]) == ("예비", 2, 2, "49㎡ 특별공급")
    assert res.supply_group("사전청약-일반") == "일반공급" and res.supply_group("생애최초") == "특별공급"
    assert res.judge("599999", found)["result"] == "명단에 없음"
    assert res.judge("599999", {"당첨": [], "예비": []})["result"] == "확인 불가"


def _setup(tmp_path, fake_client, with_post=True):
    fake_client.add("sale", "312005", NOTICE, wrap_body(NOTICE, "■ 당첨자 발표: 2026.11.26.(목) 16:00 이후"))
    fake_client.lists.setdefault("results", [])
    if with_post:
        title = "고덕강일지구 3단지 토지임대부 분양주택 당첨자 발표 및 서류제출 안내"
        files = downlist("313900", ["1_ 고덕강일 당첨자 명단.pdf", "2_ 고덕강일 예비입주자 명단.pdf",
                                    "3_ 고덕강일 당첨하한선.pdf"])
        fake_client.add("sale", "313900", title, wrap_body(title, "당첨자를 발표합니다.", files))
        fake_client.lists["results"] = [fake_client.lists["sale"][0]]
        pdf = tmp_path / "pdf"
        pdf.mkdir(exist_ok=True)
        (pdf / "results_313900_1.json").write_text(json.dumps(WIN_PAGES, ensure_ascii=False), encoding="utf-8")
        (pdf / "results_313900_2.json").write_text(json.dumps(WAIT_PAGES, ensure_ascii=False), encoding="utf-8")
    return NoticeService(client=fake_client, base_dir=tmp_path)


def test_save_and_check_results(tmp_path, fake_client):
    s = _setup(tmp_path, fake_client)
    out = s.save_receipts("sale", "312005", [
        {"receipt_no": "511111", "applicant": "본인", "supply_type": "생애최초 특별공급", "house_type": "49D"},
        {"receipt_no": "512345", "applicant": "배우자", "house_type": "49D"},
        {"receipt_no": "599999", "applicant": "배우자", "house_type": "59E"},
    ])
    assert [r["receipt_no"] for r in out["saved"]] == ["511111", "512345", "599999"]
    r = s.check_results()
    entry = r["results"][0]
    assert entry["status"] == "checked"
    assert entry["list_sizes"] == {"당첨": 4, "예비": 2}
    assert entry["other_attachments"] == ["3_ 고덕강일 당첨하한선.pdf"]
    got = {x["receipt_no"]: x["result"] for x in entry["receipts"]}
    assert got == {"511111": "당첨", "512345": "예비", "599999": "명단에 없음"}
    assert "ARS" in r["official_check"]
    assert fake_client.downloads == 0  # 캐시를 썼다
    mine = {x["receipt_no"]: x for x in s.my_receipts()}
    assert mine["511111"]["result"] == "당첨" and mine["512345"]["result_detail"]["rank"] == 2


def test_check_before_announcement(tmp_path, fake_client):
    s = _setup(tmp_path, fake_client, with_post=False)
    assert s.check_results()["status"] == "no_receipts"
    s.save_receipts("sale", "312005", [{"receipt_no": "511111"}])
    entry = s.check_results()["results"][0]
    assert entry["status"] == "not_yet" and entry["receipts"][0]["applicant"] == "본인"


def test_bad_receipt_number(tmp_path, fake_client):
    import pytest
    s = _setup(tmp_path, fake_client, with_post=False)
    with pytest.raises(ValueError):
        s.save_receipts("sale", "312005", [{"receipt_no": "12"}])


def test_check_results_for_without_saving(tmp_path, fake_client):
    s = _setup(tmp_path, fake_client)
    out = s.check_results_for("sale", "312005", ["511111", "599999"])
    assert {r["receipt_no"]: r["result"] for r in out["receipts"]} == {"511111": "당첨", "599999": "명단에 없음"}
    assert "ARS" in out["official_check"]
    assert s.my_receipts() == []  # 원격용: 아무것도 저장하지 않는다
