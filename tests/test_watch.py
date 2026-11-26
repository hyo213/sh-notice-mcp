"""프로필 [watch] → 조회할 게시판."""

import pytest

from sh_notice_mcp import profile as pf
from sh_notice_mcp.config import ALL_NOTICES_PATH, BOARDS, RENT_TYPES, rent_type_name, watched_boards
from sh_notice_mcp.service import NoticeService

from .conftest import wrap_body


def ids(data):
    return [b.id for b in watched_boards(data)]


def test_defaults_sale_and_all_rent():
    assert ids({}) == ["sale", "rent_all"]


def test_sale_rent_switches_and_types():
    assert ids({"watch": {"sale": False}}) == ["rent_all"]
    assert ids({"watch": {"rent": False}}) == ["sale"]
    assert ids({"watch": {"sale": True, "rent": True, "rent_types": ["장기전세주택"]}}) == ["sale", "rent_jeonse"]
    assert ids({"watch": {"sale": False, "rent_types": ["행복주택", "장기전세주택"]}}) == ["rent_happy", "rent_jeonse"]
    assert ids({"watch": {"rent": False, "rent_types": ["행복주택"]}}) == ["sale"]  # rent=false면 유형 무시
    assert ids({"watch": {"sale": False, "rent": False}}) == []


def test_happy_housing_form():
    (b,) = watched_boards({"watch": {"sale": False, "rent_types": ["행복주택"]}})
    assert b.path == ALL_NOTICES_PATH  # 모두 전체 게시판에서 조회
    assert b.form == {"multi_itm_seq": "2", "itm_seq_1": "1", "srchTp": "0", "splyTy": "07"}


def test_every_dropdown_option_has_unique_board():
    assert len(RENT_TYPES) == 14  # SH '공급유형' 드롭다운의 '전체' 뺀 14개
    assert len(BOARDS) == 16 and all(b.path == ALL_NOTICES_PATH for b in BOARDS.values())


@pytest.mark.parametrize("short,full", [
    ("장기전세", "장기전세주택"), ("국민임대", "국민공공임대주택"), ("영구임대", "영구임대주택"),
    ("재개발임대", "재개발임대주택"), ("매입임대", "매입임대주택"), ("행복 주택", "행복주택"),
])
def test_short_names(short, full):
    assert rent_type_name(short) == full


@pytest.mark.parametrize("bad", ["오피스텔", "임대", "전세"])  # 없는 값, 애매한 값('전세' → 장기전세? 전세임대?)
def test_unknown_or_ambiguous_rejected(bad):
    with pytest.raises(ValueError):
        rent_type_name(bad)


def test_title_filters_and_result_posts():
    (b,) = watched_boards({"watch": {"sale": False, "rent_types": ["행복주택"],
                                     "include_words": ["강동"], "exclude_words": ["잔여세대"]}})
    assert not b.is_excluded("강동구 행복주택 입주자 모집공고")
    assert b.is_excluded("마포구 행복주택 입주자 모집공고")  # include 단어 없음
    assert b.is_excluded("강동구 행복주택 잔여세대 모집공고")  # exclude 단어
    plain = BOARDS["rent_happy"]
    assert plain.is_excluded("2025년 1차 행복주택 입주자 모집(2025.04.25.공고) 최종 청약 경쟁률")
    assert plain.is_excluded("제48차 장기전세주택 입주자 모집공고 당첨자 및 예비입주자 발표")
    assert not plain.is_excluded("[정정]2025년 3차 행복주택 입주자 모집공고(2025.12.31.공고)")


def test_profile_validates_watch(tmp_path):
    assert pf.validate_change("watch.rent_types", ["행복", "행복주택", "장기전세"]) == ["행복주택", "장기전세주택"]
    with pytest.raises(ValueError):
        pf.validate_change("watch.rent_types", ["아파트"])
    with pytest.raises(ValueError):
        pf.validate_change("watch.sale", "yes")
    pf.update_profile(tmp_path, {"watch.sale": False, "watch.rent_types": ["행복주택"]})
    w = pf.load_profile(tmp_path)["profile"]["watch"]
    assert w == {"sale": False, "rent_types": ["행복주택"]}


def test_service_follows_profile(tmp_path, fake_client):
    fake_client.add("rent_happy", "900", "2026년 3차 행복주택 입주자 모집공고",
                    wrap_body("2026년 3차 행복주택 입주자 모집공고", "본문"))
    fake_client.add("rent_jeonse", "901", "제52차 장기전세주택 입주자 모집공고",
                    wrap_body("제52차 장기전세주택 입주자 모집공고", "본문"))
    s = NoticeService(client=fake_client, base_dir=tmp_path)
    assert s.watched_ids() == ["sale", "rent_all"]
    pf.update_profile(tmp_path, {"watch.sale": False, "watch.rent_types": ["행복주택"]})
    assert s.watched_ids() == ["rent_happy"]
    assert [n["seq"] for n in s.find_notices()["notices"]] == ["900"]
    # 감시하지 않는 유형도 board_id로 직접 열 수는 있다
    assert s.board("rent_jeonse").id == "rent_jeonse"
