from sh_notice_mcp import loans


def prof(first=None, income=None, newlywed=False):
    return {"profile": {"basic": {"first_home_buyer": first} if first is not None else {},
                        "income_assets": {"monthly_household_income": income} if income else {}},
            "derived": {"newlywed_within_7y": newlywed}}


def test_notice_loan_terms_win_and_use_upper_rate():
    b = loans.sale_basis([{"page": 44, "ltv_max_pct": "80", "limit_eok": "5", "max_years": "40",
                           "rate": "1.9~3.0%"}], prof(first=True))
    assert (b.from_notice, b.ltv_pct, b.cap_man, b.years, b.rate_pct) == (True, 80, 50_000, 40, 3.0)
    assert b.source == "공고문 p.44"
    assert loans.max_loan_man(70_000, b) == 50_000  # 한도 5억


def test_general_mortgage_depends_on_first_home():
    assert loans.sale_basis([], prof(first=True)).ltv_pct == 70
    b = loans.sale_basis([], prof(first=False))
    assert b.ltv_pct == 40 and not b.from_notice and "추정" in b.source
    assert loans.max_loan_man(200_000, b) == 60_000  # 한도 6억


def test_jeonse_product_by_income_and_newlywed():
    assert loans.jeonse_basis(prof(income=480, newlywed=True)).name.startswith("신혼부부")  # 연 5,760만
    assert loans.jeonse_basis(prof(income=400)).name == "일반 버팀목 전세대출"  # 연 4,800만
    assert loans.jeonse_basis(prof(income=700, newlywed=True)).name == "시중은행 전세대출"  # 연 8,400만
    assert loans.jeonse_basis(prof()).name == "시중은행 전세대출"  # 소득 모름 → 보수적


def test_monthly_payment():
    b = loans.LoanBasis("x", 80, None, 40, 3.0, True, "")
    assert abs(loans.monthly_payment_won(10_000, b) - 357_990) < 50  # 1억·3%·40년 원리금균등
    interest_only = loans.LoanBasis("x", 80, None, None, 2.7, False, "")
    assert loans.monthly_payment_won(10_000, interest_only) == 225_000  # 1억 × 2.7% ÷ 12
    assert loans.monthly_payment_won(0, b) == 0
