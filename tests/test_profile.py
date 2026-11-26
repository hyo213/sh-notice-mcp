"""내 조건 파일: 모름/값 구분, 계산값, 검증, 주석 보존 수정, 공고별 빠진 항목."""
import asyncio
from datetime import date, datetime

import pytest

from sh_notice_mcp import profile as pf
from sh_notice_mcp.confirm import ConfirmResult

from .test_flow import make


def test_unknown_is_not_zero(tmp_path):
    p = pf.load_profile(tmp_path, date(2026, 10, 21))
    assert p["profile"] == {"basic": {}, "income_assets": {}, "savings_account": {}, "wishes": {}, "watch": {}, "loan": {}, "memo": {}}
    assert "basic.children" in p["missing"]  # 0명이 아니라 '모름'


def test_derived_values(tmp_path):
    pf.update_profile(tmp_path, {"basic.birth_date": "1992-05-15", "basic.marriage_date": "2021-06-12",
                                 "basic.seoul_since": 2005})
    d = pf.load_profile(tmp_path, date(2026, 10, 21))["derived"]
    assert d["age"] == 34
    assert d["marriage_months"] == 64 and d["newlywed_within_7y"] is True
    assert d["newlywed_7y_until"] == "2028-06-12"
    assert d["seoul_years_if_continuous"] == 21
    d = pf.load_profile(tmp_path, date(2028, 6, 12))["derived"]
    assert d["newlywed_within_7y"] is False  # 7년이 되는 날부터는 아님


@pytest.mark.parametrize("key,value", [
    ("basic.unknown", 1), ("nodot", 1), ("basic.birth_date", "1992/05/15"),
    ("basic.children", -1), ("basic.children", True), ("basic.household_head", "yes"),
    ("basic.marital_status", "신혼"), ("memo.note", "a # b"), ("basic.seoul_since", 1800),
])
def test_invalid_changes_rejected(key, value):
    with pytest.raises(ValueError):
        pf.validate_change(key, value)


def test_set_values_keeps_comments_and_other_lines(tmp_path):
    pf.update_profile(tmp_path, {"basic.marital_status": "기혼", "wishes.regions": ["강동구"]})
    text = (tmp_path / "profile.toml").read_text(encoding="utf-8")
    assert 'marital_status = "기혼"   # 미혼 / 예비신혼' in text
    assert 'regions = ["강동구"]' in text
    assert "# children = 0" in text  # 모르는 항목은 그대로
    assert text.startswith("# SH 공고 자격 판단용")


class FakeChooser:
    def __init__(self, pick):
        self.pick, self.items = pick, None

    async def __call__(self, ctx, window, prompt, ok, items):
        self.items = items
        return ConfirmResult(list(self.pick), "fake")


def test_update_profile_saves_only_what_human_checked(tmp_path, fake_client):
    s = make(tmp_path, fake_client)
    chooser = FakeChooser(pick=[1])
    s._chooser = chooser
    r = asyncio.run(s.update_profile({"basic.marital_status": "미혼", "basic.children": 1}, "대화에서 언급"))
    assert chooser.items == ["basic.marital_status: (모름) → 미혼", "basic.children: (모름) → 1"]
    assert r["saved"] == {"basic.children": 1} and r["not_saved"] == ["basic.marital_status"]
    assert s.profile()["profile"]["basic"] == {"children": 1}


def test_gaps_follow_what_the_notice_asks(tmp_path, fake_client):
    s = make(tmp_path, fake_client)
    pf.update_profile(tmp_path, {"basic.homeless_household": True, "income_assets.total_assets": 15000})
    g = s.profile_gaps("sale", "311656")  # 테스트 공고문: 무주택·총자산·자동차·청약통장 24회 언급
    fields = {q["field"] for q in g["questions"]}
    assert "income_assets.car_value" in fields
    assert "savings_account.payments" in fields
    assert "basic.homeless_household" not in fields and "income_assets.total_assets" not in fields
    assert "basic.disability" not in fields  # 이 공고엔 장애인 조건이 없다
    car = next(q for q in g["questions"] if q["field"] == "income_assets.car_value")
    assert car["why"]["source"].startswith("공고문 p.") and "자동차" in car["why"]["quote"]
    assert g["ready"] is False
