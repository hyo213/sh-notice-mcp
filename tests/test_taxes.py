from datetime import date

from sh_notice_mcp import taxes


def test_rates_by_price():
    assert taxes.acquisition_rate_pct(30_000) == 1.0
    assert taxes.acquisition_rate_pct(60_000) == 1.0
    assert taxes.acquisition_rate_pct(75_000) == 2.0  # 7.5억 × 2/3억 − 3 = 2%
    assert taxes.acquisition_rate_pct(90_001) == 3.0


def test_first_home_relief_and_small_house():
    # 고덕강일 49C 평균 3억 649만 (건물분+발코니), 전용 49㎡, 생애최초
    t = taxes.estimate(30_649, 49, True, date(2026, 11, 1))
    assert t["acquisition_won"] == 3_064_900 and t["education_won"] == 306_490
    assert t["rural_won"] == 0  # 85㎡ 이하
    assert t["first_home_relief_won"] == 2_000_000
    assert t["total_won"] == 3_064_900 - 2_000_000 + 306_490
    no = taxes.estimate(30_649, 49, False, date(2026, 11, 1))
    assert no["first_home_relief_won"] == 0 and no["total_won"] == 3_371_390
    assert taxes.estimate(30_649, 49, True, date(2029, 1, 1))["first_home_relief_won"] == 0  # 기한 지남
    assert taxes.estimate(100_000, 90, False)["rural_won"] == 2_000_000  # 85㎡ 초과 농특세
