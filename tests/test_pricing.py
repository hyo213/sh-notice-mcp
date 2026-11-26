"""가격표 파싱·계산. 입력은 실제 SH 공고문 PDF에서 뽑은 쪽 텍스트(tests/fixtures/real_pages.json)."""
import json

from sh_notice_mcp import pricing as pr

from .conftest import FIX

REAL = json.loads((FIX / "real_pages.json").read_text(encoding="utf-8"))


def sale_pages():
    pages = [""] * 82
    pages[43], pages[51], pages[80], pages[81] = REAL["sale_p44"], REAL["sale_p52"], REAL["sale_p81"], REAL["sale_p82"]
    return pages


def test_sale_rows_parsed_from_real_page():
    rows = pr.parse_sale_prices(sale_pages())
    assert len(rows) == 19  # p.52의 세대 행 전부
    r = rows[0]
    assert (r.dong, r.ho, r.house_type, r.plan_type, r.price_k, r.balcony_won, r.page) == (
        "301", "201", "59B", "59B", 371544, 5825600, 52)
    # 발코니 확장비 표(p.81)의 '049.0000A 49A 987,800 98,780 889,020'은 세대 행이 아니므로 잡히면 안 된다
    assert all(r.page == 52 for r in rows)


def test_rows_with_broken_numbers_are_rejected():
    page = "301 201 059.0000B 59B 371,544 99,999 334,390 5,825,600"  # 계 ≠ 계약금 + 잔금
    assert pr.parse_sale_prices([page]) == []


def test_summary_by_representative_type_in_man_won():
    s = {x["type"]: x for x in pr.summarize_sale(pr.parse_sale_prices(sale_pages()))}
    assert list(s) == ["49F", "59B"]  # 면적 → 타입 순
    f = s["49F"]
    assert f["units"] == 9
    # 301,842천원×3 + 308,002천원×6 → 평균 305,948.67천원 → 30,595만원
    assert f["avg_price_man"] == 30595 and f["min_price_man"] == 30184 and f["max_price_man"] == 30800
    assert f["avg_balcony_man"] == 250.7
    assert f["avg_total_with_balcony_man"] == 30846  # 308,455,567원 → 30,845.6만 → 반올림
    assert s["59B"]["units"] == 10


def test_sub_plan_types_roll_up_to_representative():
    rows = pr.parse_sale_prices([
        "305 101 049.0000C 49C1 300,000 30,000 270,000 1,465,200 "
        "305 102 049.0000C 49C2 310,000 31,000 279,000 1,402,500"])
    [c] = pr.summarize_sale(rows)
    assert c["type"] == "49C" and c["plans"] == ["49C1", "49C2"] and c["units"] == 2


def test_land_rent_table():
    rent, page = pr.parse_land_rent(sale_pages())
    assert rent == {49: 426000, 59: 511500} and page == 81


def test_conversion_rule_and_formula_match_notice_example():
    rule = pr.parse_conversion(sale_pages())
    assert (rule.max_ratio, rule.unit_won, rule.rate, rule.page) == (0.6, 1_000_000, 0.025, 82)
    assert rule.rate_is_example  # '확정될 예정' → 이율은 예시값
    # 공고문 p.82 예시표와 원 단위까지 같아야 한다
    assert pr.convert_rent(426000, 0.6, 0.025, 1_000_000) == {
        "deposit_won": 122_000_000, "monthly_after_won": 171_830, "monthly_saved_won": 254_170}
    assert pr.convert_rent(511500, 0.6, 0.025, 1_000_000)["deposit_won"] == 147_000_000
    assert pr.convert_rent(511500, 0.6, 0.025, 1_000_000)["monthly_after_won"] == 205_250


def test_loan_terms():
    [loan] = pr.parse_loan_terms(sale_pages())
    assert loan["page"] == 44
    assert (loan["ltv_max_pct"], loan["limit_eok"], loan["max_years"]) == ("80", "5", "40")
    assert loan["rate"].replace(" ", "") == "1.9~3.0%"


def test_jeonse_rows_from_real_page():
    rows = pr.parse_jeonse_prices(["", "", "", "", "", "", "", "", "", "", "", "", REAL["jeonse_p13"]])
    assert len(rows) == 12
    first = rows[0]
    assert (first.deposit_k, first.exclusive_m2, first.page) == (717600, 45.98, 13)
    assert "래미안레벤투스" in first.context
    assert any(r.deposit_k == 1170000 and "청담르엘" in r.context for r in rows)


def _service_with_cached_pages(tmp_path, fake_client, pages, board="sale", seq="311656"):
    from .test_flow import make

    s = make(tmp_path, fake_client)
    cache = tmp_path / "pdf" / f"{board}_{seq}_1.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    return s


def test_housing_costs_sale_end_to_end(tmp_path, fake_client):
    s = _service_with_cached_pages(tmp_path, fake_client, sale_pages())
    c = s.housing_costs("sale", "311656")
    assert c["kind"] == "분양" and c["total_units"] == 19
    t = {x["type"]: x for x in c["types"]}
    f = t["49F"]
    assert f["land_rent_monthly_won"] == 426000
    conv = f["max_conversion"]
    assert (conv["deposit_man"], conv["land_rent_monthly_after_won"]) == (12200, 171830)
    # 공고문 p.44의 LTV 80%·40년·금리 상단 3.0%로 계산
    assert c["loan_basis"]["from_notice"] is True and c["loan_basis"]["ltv_pct"] == 80
    assert c["loan_basis"]["years"] == 40 and c["loan_basis"]["rate_pct"] == 3.0
    assert f["loan_man"] == 30595 * 80 // 100  # 5억 한도 안
    assert f["min_cash_man"] == f["avg_total_with_balcony_man"] - f["loan_man"]
    assert f["monthly_total_won"] == f["monthly_loan_won"] + 426000
    assert f["monthly_interest_won"] == round(f["loan_man"] * 10_000 * 0.03 / 12)  # 첫 달 이자
    assert f["monthly_interest_won"] + f["monthly_principal_won"] == f["monthly_loan_won"]
    # 보증금을 최대로 넣으면 필요한 현금은 늘고 월 부담은 준다
    assert conv["min_cash_man"] == f["min_cash_man"] + 12200
    assert conv["monthly_total_won"] == f["monthly_loan_won"] + 171830
    assert t["59B"]["max_conversion"]["deposit_man"] == 14700
    assert c["conversion"]["rate_is_example"] is True
    assert any("예시값" in n for n in c["notes"])
    assert c["land_rent"]["page"] == 81


def test_housing_costs_jeonse_end_to_end(tmp_path, fake_client):
    from .conftest import downlist, wrap_body

    fake_client.add("rent_jeonse", "309467", "제51차 장기전세주택",
                    wrap_body("제51차 장기전세주택", "본문", downlist("309467", ["제51차 장기전세 입주자 모집공고.pdf"])))
    pages = [""] * 12 + [REAL["jeonse_p13"]]
    s = _service_with_cached_pages(tmp_path, fake_client, pages, "rent_jeonse", "309467")
    c = s.housing_costs("rent_jeonse", "309467")
    assert c["kind"] == "장기전세" and len(c["rows"]) == 12
    first = c["rows"][0]
    assert first["deposit_man"] == 71760 and first["exclusive_m2"] == 45.98 and first["page"] == 13
    assert any("월 임대료가 없습니다" in n for n in c["notes"])


def test_convert_by_deposit_matches_notice_example():
    from sh_notice_mcp.pricing import convert_by_deposit
    assert convert_by_deposit(426_000, 122_000_000, 0.025) == 171_830  # 공고문 p.82 예시
    assert convert_by_deposit(511_500, 147_000_000, 0.025) == 205_250


def test_my_conversion_uses_available_cash(tmp_path, fake_client):
    """보증금 = 가용 현금 − 최소 필요 현금(취득세 포함) (100만원 단위 내림, 최대 한도까지)."""
    s = _service_with_cached_pages(tmp_path, fake_client, sale_pages())
    (tmp_path / "profile.toml").write_text("[income_assets]\navailable_cash = 15000\n", encoding="utf-8")
    t = {x["type"]: x for x in s.housing_costs("sale", "311656")["types"]}
    f = t["49F"]
    mine = f["my_conversion"]
    assert f["min_cash_with_tax_man"] == f["min_cash_man"] + f["acquisition_tax"]["total_man"]
    expect = (15000 - f["min_cash_with_tax_man"]) // 100 * 100
    assert mine["deposit_man"] == expect and mine["capped_at_max"] is False
    assert mine["land_rent_monthly_after_won"] == int((426_000 - expect * 10_000 * 0.025 / 12) // 10 * 10)
    assert mine["monthly_total_won"] == f["monthly_loan_won"] + mine["land_rent_monthly_after_won"]
    assert mine["cash_left_man"] == 15000 - f["min_cash_with_tax_man"] - expect

    # 현금이 많으면 최대 전환 한도에서 멈춘다
    (tmp_path / "profile.toml").write_text("[income_assets]\navailable_cash = 90000\n", encoding="utf-8")
    big = {x["type"]: x for x in s.housing_costs("sale", "311656")["types"]}["49F"]["my_conversion"]
    assert big["deposit_man"] == 12200 and big["capped_at_max"] is True
    assert big["land_rent_monthly_after_won"] == 171_830

    # 모자라면 부족액만 알려준다
    (tmp_path / "profile.toml").write_text("[income_assets]\navailable_cash = 1000\n", encoding="utf-8")
    low = {x["type"]: x for x in s.housing_costs("sale", "311656")["types"]}["49F"]
    assert low["my_conversion"]["shortfall_man"] == low["min_cash_with_tax_man"] - 1000


def test_no_available_cash_asks(tmp_path, fake_client):
    s = _service_with_cached_pages(tmp_path, fake_client, sale_pages())
    c = s.housing_costs("sale", "311656")
    assert "my_conversion" not in c["types"][0]
    assert any("가용 현금" in n and "모릅니다" in n for n in c["notes"])


def test_terms_and_rate_range(tmp_path, fake_client):
    """만기 30·40년 × (내 예상 금리, 공고 상단 3.0%). 공고 하단 1.9%는 best_case로만."""
    from sh_notice_mcp import loans

    s = _service_with_cached_pages(tmp_path, fake_client, sale_pages())
    c = s.housing_costs("sale", "311656")
    b = c["loan_basis"]
    assert (b["rate_low"], b["rate_high"], b["rate_pct"]) == (1.9, 3.0, 3.0)  # 예상 금리 없으면 상단 하나
    f = {x["type"]: x for x in c["types"]}["49F"]
    assert set(f["by_term"]) == {"30", "40"}
    assert list(f["by_term"]["40"]["by_rate"]) == ["3"]
    t30, t40 = f["by_term"]["30"]["by_rate"]["3"], f["by_term"]["40"]["by_rate"]["3"]
    assert t40["monthly_loan_won"] == f["monthly_loan_won"]
    assert t30["monthly_loan_won"] > t40["monthly_loan_won"]
    assert t30["monthly_interest_won"] == t40["monthly_interest_won"]  # 첫 달 이자는 만기와 무관
    best = f["by_term"]["40"]["best_case"]
    assert best["rate_pct"] == 1.9 and best["monthly_total_won"] < t40["monthly_total_won"]

    (tmp_path / "profile.toml").write_text("[loan]\nexpected_rate_pct = 2.5\n", encoding="utf-8")
    c2 = s.housing_costs("sale", "311656")
    assert c2["loan_basis"]["rate_pct"] == 2.5 and "내 예상 금리" in c2["loan_basis"]["rate_source"]
    f2 = {x["type"]: x for x in c2["types"]}["49F"]
    rates = f2["by_term"]["30"]["by_rate"]
    assert list(rates) == ["2.5", "3"]  # 예상 금리와 공고 상단을 나란히
    expect = loans.monthly_payment_won(f2["loan_man"], loans.LoanBasis("x", 80, None, 30, 2.5, True, ""))
    assert rates["2.5"]["monthly_loan_won"] == expect
    assert rates["3"]["monthly_loan_won"] == t30["monthly_loan_won"]


def test_expected_rate_validation():
    from sh_notice_mcp import profile as pf
    assert pf.validate_change("loan.expected_rate_pct", "2.5%") == 2.5
    assert pf.validate_change("loan.expected_rate_pct", 3) == 3.0
    import pytest
    with pytest.raises(ValueError):
        pf.validate_change("loan.expected_rate_pct", 25)


def test_jeonse_affordable_flag(tmp_path, fake_client):
    from .conftest import downlist, wrap_body

    fake_client.add("rent_jeonse", "309467", "제51차 장기전세주택",
                    wrap_body("제51차 장기전세주택", "본문", downlist("309467", ["제51차 장기전세 입주자 모집공고.pdf"])))
    pages = [""] * 12 + [REAL["jeonse_p13"]]
    s = _service_with_cached_pages(tmp_path, fake_client, pages, "rent_jeonse", "309467")
    (tmp_path / "profile.toml").write_text("[income_assets]\navailable_cash = 10000\n", encoding="utf-8")
    rows = s.housing_costs("rent_jeonse", "309467")["rows"]
    assert all(r["affordable"] == (r["min_cash_man"] <= 10000) for r in rows)
    assert any(r["affordable"] for r in rows) and not all(r["affordable"] for r in rows)
