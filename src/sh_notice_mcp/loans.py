"""대출 가정과 월 상환액 계산.

원칙
  1. 공고문에 대출 조건(LTV·한도·만기·금리)이 있으면 그 값을 쓴다 (출처: 공고문 p.N).
  2. 없으면 아래 '일반 기준'을 쓴다. 정책은 자주 바뀌므로 값마다 기준 시점과 출처를 함께 돌려주고,
     요약에도 '일반 기준(추정)'이라고 표시하게 한다. 바뀌면 이 파일의 숫자만 고치면 된다.
  3. 금리가 범위(1.9~3.0%)로 나오면 보수적으로 상단을 쓴다. 월 부담을 낮게 잡는 쪽이 더 위험하다.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

AS_OF = "2026-10"

# 서울은 2025-10-16부터 전역이 규제지역 (10·15 대책). 무주택 LTV 40%, 생애최초 70%, 한도 6억, 만기 30년.
MORTGAGE = {
    "name": "일반 주택담보대출(서울 규제지역)",
    "ltv_homeless": 40,
    "ltv_first_home": 70,
    "cap_man": 60_000,
    "years": 30,
    "rate_pct": 4.0,  # 시중은행 주담대 금리 가정치
    "source": "10·15 부동산대책(2025) 규제지역 LTV, 금리는 가정치",
}

# 전세대출. 신혼부부 전용 버팀목: 부부합산 연소득 7,500만 이하, 수도권 최대 3억, 보증금 80%, 연 1.5~2.7%.
JEONSE = {
    "newlywed": {"name": "신혼부부 전용 버팀목 전세대출", "ratio": 80, "cap_man": 30_000, "rate_pct": 2.7,
                 "income_cap_year_man": 7_500,
                 "source": "주택도시기금 버팀목(신혼부부 전용) 2026 기준, 금리는 상단"},
    "general": {"name": "일반 버팀목 전세대출", "ratio": 80, "cap_man": 12_000, "rate_pct": 2.9,
                "income_cap_year_man": 5_000,
                "source": "주택도시기금 버팀목(일반) 수도권 한도 하단 기준, 금리는 가정치"},
    "bank": {"name": "시중은행 전세대출", "ratio": 80, "cap_man": 20_000, "rate_pct": 4.0,
             "income_cap_year_man": None,
             "source": "시중은행 보증부 전세대출 가정치 (보증기관·은행마다 다름)"},
}


@dataclass
class LoanBasis:
    name: str
    ltv_pct: int  # 분양: LTV, 전세: 보증금 대비 비율
    cap_man: int | None
    years: int | None  # 전세대출은 만기 일시상환(이자만) → None
    rate_pct: float  # 표에 쓰는 기준 금리 (내 예상 금리가 있으면 그것, 없으면 공고 범위 상단)
    from_notice: bool
    source: str
    rate_low: float | None = None  # 공고 금리 범위 하단
    rate_high: float | None = None  # 공고 금리 범위 상단
    rate_source: str = ""  # 기준 금리를 어디서 정했는지

    def to_dict(self) -> dict:
        return asdict(self)


def _rates(text: str | None) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text or "")]


def _upper_rate(text: str | None) -> float | None:
    nums = _rates(text)
    return max(nums) if nums else None


def expected_rate(profile: dict) -> float | None:
    v = profile.get("profile", {}).get("loan", {}).get("expected_rate_pct")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _apply_expected(b: "LoanBasis", profile: dict) -> "LoanBasis":
    exp = expected_rate(profile)
    if exp is None:
        b.rate_source = "공고 금리 범위 상단(보수적)" if b.from_notice else "일반 가정치"
        return b
    b.rate_pct = exp
    out_of_range = b.rate_low is not None and b.rate_high is not None and not (b.rate_low <= exp <= b.rate_high)
    b.rate_source = "내 예상 금리(프로필 loan.expected_rate_pct)" + (" ⚠ 공고 범위 밖" if out_of_range else "")
    return b


def terms(basis: "LoanBasis") -> list[int]:
    """비교할 만기: 30년과 최장 만기(공고가 40년이면 30·40). 최장이 30년 이하면 그것 하나."""
    longest = basis.years or 30
    return sorted({min(30, longest), longest})


def with_terms(basis: "LoanBasis", years: int, rate_pct: float | None = None) -> "LoanBasis":
    from dataclasses import replace
    return replace(basis, years=years, rate_pct=basis.rate_pct if rate_pct is None else rate_pct)


def sale_basis(notice_loans: list[dict], profile: dict) -> LoanBasis:
    """분양: 공고문 대출 조건이 있으면 그것, 없으면 일반 주담대 기준."""
    for x in notice_loans:
        if "ltv_max_pct" in x:
            limit = x.get("limit_eok")
            rs = _rates(x.get("rate"))
            return _apply_expected(LoanBasis(
                name="공고문 안내 대출",
                ltv_pct=int(x["ltv_max_pct"]),
                cap_man=int(float(limit) * 10_000) if limit else None,
                years=int(x["max_years"]) if x.get("max_years") else MORTGAGE["years"],
                rate_pct=_upper_rate(x.get("rate")) or MORTGAGE["rate_pct"],
                from_notice=True,
                source=f"공고문 p.{x['page']}" + ("" if x.get("rate") else " (금리는 일반 가정치)"),
                rate_low=min(rs) if rs else None,
                rate_high=max(rs) if rs else None,
            ), profile)
    first = bool(profile.get("profile", {}).get("basic", {}).get("first_home_buyer"))
    return _apply_expected(LoanBasis(
        name=MORTGAGE["name"] + (" · 생애최초" if first else " · 무주택"),
        ltv_pct=MORTGAGE["ltv_first_home"] if first else MORTGAGE["ltv_homeless"],
        cap_man=MORTGAGE["cap_man"],
        years=MORTGAGE["years"],
        rate_pct=MORTGAGE["rate_pct"],
        from_notice=False,
        source=f"일반 기준({AS_OF}, 추정): {MORTGAGE['source']}",
    ), profile)


def jeonse_basis(profile: dict) -> LoanBasis:
    """장기전세: 소득·신혼 여부로 버팀목(신혼/일반) 또는 시중은행 전세대출."""
    p = profile.get("profile", {})
    monthly = p.get("income_assets", {}).get("monthly_household_income")
    yearly = monthly * 12 if isinstance(monthly, (int, float)) else None
    newlywed = bool(profile.get("derived", {}).get("newlywed_within_7y"))
    if newlywed and yearly is not None and yearly <= JEONSE["newlywed"]["income_cap_year_man"]:
        key = "newlywed"
    elif yearly is not None and yearly <= JEONSE["general"]["income_cap_year_man"]:
        key = "general"
    else:
        key = "bank"
    j = JEONSE[key]
    why = f"연소득 {yearly}만원" if yearly is not None else "소득 모름"
    return LoanBasis(name=j["name"], ltv_pct=j["ratio"], cap_man=j["cap_man"], years=None,
                     rate_pct=j["rate_pct"], from_notice=False,
                     source=f"일반 기준({AS_OF}, 추정, {why}): {j['source']}")


def max_loan_man(price_man: int, basis: LoanBasis) -> int:
    cap = price_man * basis.ltv_pct // 100
    return min(cap, basis.cap_man) if basis.cap_man else cap


def monthly_payment_won(principal_man: int, basis: LoanBasis) -> int:
    """원리금균등 월 상환액(원). 만기 없음(전세대출)은 이자만."""
    p = principal_man * 10_000
    r = basis.rate_pct / 100 / 12
    if p <= 0:
        return 0
    if not basis.years:
        return round(p * r)
    n = basis.years * 12
    if r == 0:
        return round(p / n)
    return round(p * r / (1 - (1 + r) ** -n))


def first_month_interest_won(principal_man: int, basis: LoanBasis) -> int:
    """첫 달 이자(원). 원리금균등에서 이자는 시간이 갈수록 줄어든다."""
    return round(principal_man * 10_000 * basis.rate_pct / 100 / 12)
