"""주택 취득세 추정 (유상취득, 1주택 기준).

과세표준: 분양가 + 발코니 확장비 (확장비도 취득가액에 들어간다).
토지임대부 분양주택은 토지를 사지 않으므로 건물 분양가만 과세표준이다.

세율 (지방세법 제11조, 1주택)
  6억 이하 1% / 6억~9억 (취득가액 × 2/3억 − 3)% / 9억 초과 3%
지방교육세: 취득세율의 1/10.  농어촌특별세: 전용 85㎡ 초과만 0.2%.
생애최초 감면 (지방세특례제한법 제36조의3): 12억 이하, 취득세에서 최대 200만원, 2028-12-31까지.
  (2026 지방세제 개편안의 '40세 미만 300만원'은 아직 확정 전이라 넣지 않는다.)
감면 추징: 3개월 안에 전입하지 않거나 3년 안에 매각·임대하면 돌려내야 한다.
"""

from __future__ import annotations

from datetime import date

AS_OF = "2026-10"
FIRST_HOME_RELIEF_WON = 2_000_000
FIRST_HOME_PRICE_CAP_MAN = 120_000
FIRST_HOME_UNTIL = date(2028, 12, 31)


def acquisition_rate_pct(price_man: float) -> float:
    if price_man <= 60_000:
        return 1.0
    if price_man <= 90_000:
        return round(price_man * 2 / 30_000 - 3, 2)
    return 3.0


def estimate(price_man: float, exclusive_m2: float, first_home: bool, today: date | None = None) -> dict:
    """과세표준(만원) → 취득세·지방교육세·농특세·생애최초 감면·합계(원)."""
    today = today or date.today()
    base_won = round(price_man * 10_000)
    rate = acquisition_rate_pct(price_man)
    acq = round(base_won * rate / 100)
    edu = round(base_won * rate / 10 / 100)
    rural = round(base_won * 0.2 / 100) if exclusive_m2 > 85 else 0
    relief = 0
    if first_home and price_man <= FIRST_HOME_PRICE_CAP_MAN and today <= FIRST_HOME_UNTIL:
        relief = min(acq, FIRST_HOME_RELIEF_WON)
    total = acq - relief + edu + rural
    return {
        "base_man": round(price_man),
        "rate_pct": rate,
        "acquisition_won": acq,
        "education_won": edu,
        "rural_won": rural,
        "first_home_relief_won": relief,
        "total_won": total,
        "total_man": round(total / 10_000),
    }
