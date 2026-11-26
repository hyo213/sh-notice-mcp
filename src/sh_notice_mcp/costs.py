"""공고문 가격표 → 타입별 비용(대출·필요 현금·취득세·월 부담·보증금 전환).

입력은 PDF 쪽 텍스트와 내 조건(프로필)뿐인 순수 계산이다. PDF를 고르고 내려받는 일은 service가 한다.
모든 금액은 만원(월 금액은 원)이고, 숫자마다 근거(공고문 쪽 또는 일반 기준)를 notes에 남긴다.
"""

from __future__ import annotations

from datetime import date

from . import loans, pricing, taxes


def housing_costs(pages: list[str], title: str, prof: dict, today: date) -> dict:
    out: dict = {"status": "ok", "notice": title, "unit": "만원 (월 금액은 원)"}
    available = prof.get("profile", {}).get("income_assets", {}).get("available_cash")
    first_home = bool(prof.get("profile", {}).get("basic", {}).get("first_home_buyer"))

    sale_rows = pricing.parse_sale_prices(pages)
    if sale_rows:
        return sale_costs(pages, sale_rows, prof, available, first_home, today, out)
    jeonse = pricing.parse_jeonse_prices(pages)
    if jeonse:
        return jeonse_costs(pages, jeonse, prof, available, first_home, today, out)
    out["kind"] = "알 수 없음"
    out["notes"] = ["공고문에서 형식이 확인된 가격표를 찾지 못했습니다. read_notice_pdf로 공급 표를 읽어 정리하세요."]
    return out


def sale_costs(pages: list[str], sale_rows, prof: dict, available, first_home: bool, today: date, out: dict) -> dict:
    """분양: 대표 타입별 분양가·대출·취득세·필요 현금·월 부담·토지임대료 전환."""
    out["kind"] = "분양"
    rent, rent_page = pricing.parse_land_rent(pages)
    rule = pricing.parse_conversion(pages)
    notice_loans = pricing.parse_loan_terms(pages)
    basis = loans.sale_basis(notice_loans, prof)
    types = []
    for t in pricing.summarize_sale(sale_rows):
        row = dict(t)
        loan = loans.max_loan_man(t["avg_price_man"], basis)
        pay = loans.monthly_payment_won(loan, basis)
        row["loan_man"] = loan
        row["min_cash_man"] = t["avg_total_with_balcony_man"] - loan
        tax = taxes.estimate(t["avg_total_with_balcony_man"], t["area_m2"], first_home, today)
        row["acquisition_tax"] = tax
        row["min_cash_with_tax_man"] = row["min_cash_man"] + tax["total_man"]
        interest = loans.first_month_interest_won(loan, basis)
        row["monthly_loan_won"] = pay
        row["monthly_interest_won"] = interest
        row["monthly_principal_won"] = pay - interest
        row["monthly_total_won"] = pay
        monthly = rent.get(t["area_m2"])
        if monthly:
            row["land_rent_monthly_won"] = monthly
            row["monthly_total_won"] = pay + monthly
            if rule and rule.rate:
                c = pricing.convert_rent(monthly, rule.max_ratio, rule.rate, rule.unit_won)
                dep = c["deposit_won"] // 10_000
                row["max_conversion"] = {
                    "deposit_man": dep,
                    "land_rent_monthly_after_won": c["monthly_after_won"],
                    "min_cash_man": row["min_cash_man"] + dep,
                    "monthly_total_won": pay + c["monthly_after_won"],
                }
                if isinstance(available, int):
                    row["my_conversion"] = my_conversion(
                        available, row["min_cash_with_tax_man"], monthly, c["deposit_won"], rule, pay)
        row["by_term"] = by_term(loan, basis, row)
        types.append(row)
    out["types"] = types
    out["total_units"] = len(sale_rows)
    out["land_rent"] = {"by_area_won": rent, "page": rent_page} if rent else None
    out["conversion"] = (
        {"max_ratio": rule.max_ratio, "unit_won": rule.unit_won, "rate": rule.rate,
         "rate_is_example": rule.rate_is_example, "page": rule.page} if rule else None
    )
    out["loan_basis"] = basis.to_dict()
    out["available_cash_man"] = available
    out["loan"] = notice_loans
    out["notes"] = [
        "금액은 평면유형·층·향에 따라 다르며 표는 대표 타입(청약 신청 단위)별 평균·최저·최고입니다.",
        "발코니 확장비는 분양가와 별도로 냅니다 (avg_total_with_balcony_man = 평균 분양가 + 평균 확장비).",
        f"loan_man = 평균 분양가 × LTV {basis.ltv_pct}%"
        + (f" (한도 {basis.cap_man}만원)" if basis.cap_man else "") + f" — 근거: {basis.source}. 실제 대출액은 심사에 따라 다릅니다.",
        "min_cash_man = 분양가+발코니 − 대출 (계약금·잔금 시점 합계). min_cash_with_tax_man = 여기에 취득세 합계를 더한 값(이사비 제외).",
        "acquisition_tax: 과세표준 = 분양가+발코니"
        + (" (토지임대부라 토지는 사지 않으므로 건물분만)" if rent else "")
        + ", 1주택 세율(6억 이하 1%), 지방교육세(세율의 1/10), 농특세(85㎡ 초과만 0.2%), "
        + ("생애최초 감면 최대 200만원(12억 이하, 2028-12-31까지, 3개월 내 전입·3년 실거주 안 하면 추징)"
           if first_home else "생애최초 아님(감면 없음)")
        + f". {taxes.AS_OF} 기준 추정이며 실제 세액은 취득 시 위택스·구청에서 확정.",
        f"by_term[만기].by_rate[금리]: 만기 {'·'.join(map(str, loans.terms(basis)))}년 × 금리 "
        f"{basis.rate_pct:g}%({basis.rate_source})"
        + (f"·{basis.rate_high:g}%(공고 상단)" if basis.rate_high not in (None, basis.rate_pct) else "")
        + "별 월 이자·원금·총부담. "
        + (f"best_case는 공고 하단 {basis.rate_low:g}%일 때 월 총부담. " if basis.rate_low is not None else "")
        + "금리를 정하는 우대 조건(소득·청약통장·생애최초 등)은 공고문에 없으므로 대출기관에서 확인해야 합니다.",
        f"monthly_loan_won = 원리금균등 {basis.years}년, 금리 {basis.rate_pct}% 가정 월 상환액 "
        "(= monthly_interest_won 첫 달 이자 + monthly_principal_won 원금). "
        "monthly_total_won = monthly_loan_won + land_rent_monthly_won. 표에서는 이자·원금·토지임대료·총부담을 각각 다른 열로.",
        "max_conversion: 토지임대료 보증금을 최대로 넣으면 필요한 현금은 그만큼 늘고 월 부담은 줄어듭니다.",
    ]
    if rent and rule and rule.rate:
        out["notes"].append(
            "my_conversion: 내 가용 현금(available_cash) − 최소 필요 현금(취득세 포함)을 토지임대료 보증금으로 넣은 경우. "
            f"{rule.unit_won // 10_000}만원 단위로 내림, 최대 전환 한도 안에서, 전환이율 {rule.rate:.1%}(p.{rule.page}). "
            "shortfall_man이 있으면 가용 현금이 최소 필요 현금보다 모자란다는 뜻."
            if isinstance(available, int) else
            "내 가용 현금(income_assets.available_cash)을 모릅니다. 알려주면 '내 현금으로 전환했을 때' 월 토지임대료를 계산합니다.")
    if rule and rule.rate_is_example:
        out["notes"].append(f"보증금 전환이율 {rule.rate:.1%}은 공고문 예시값이며 계약 전 확정됩니다 (p.{rule.page}).")
    return out


def jeonse_costs(pages: list[str], jeonse, prof: dict, available, first_home: bool, today: date, out: dict) -> dict:
    """장기전세: 공급분별 전세금·전세대출·필요 현금·월 이자, 가용 현금으로 가능한지."""
    out["kind"] = "장기전세"
    basis = loans.jeonse_basis(prof)
    rows = []
    for r in jeonse:
        dep = round(r.deposit_k / 10)
        loan = loans.max_loan_man(dep, basis)
        row = {"page": r.page, "deposit_man": dep, "exclusive_m2": r.exclusive_m2,
               "units": r.units, "supply": r.supply_kind, "context": r.context[-70:],
               "loan_man": loan, "min_cash_man": dep - loan,
               "monthly_interest_won": loans.monthly_payment_won(loan, basis)}
        if isinstance(available, int):
            row["affordable"] = row["min_cash_man"] <= available
        rows.append(row)
    out["rows"] = rows
    out["loan_basis"] = basis.to_dict()
    out["notes"] = [
        "장기전세는 월 임대료가 없습니다 (관리비 별도). 보증금(전세금)만 냅니다.",
        "context는 표에서 그 행 앞에 있던 글자입니다. 단지명·자치구는 context와 해당 쪽을 보고 정리하세요.",
        f"loan_man = 전세금 × {basis.ltv_pct}% (한도 {basis.cap_man}만원) — {basis.name}, 근거: {basis.source}.",
        f"monthly_interest_won = 대출 × 연 {basis.rate_pct}% ÷ 12 (전세대출은 보통 이자만 냄). 공고문에는 대출 조건이 없습니다.",
        (f"affordable = 필요 현금(min_cash_man) ≤ 내 가용 현금 {available}만원. 가능한 공급분은 모두 표에 넣는다."
         if isinstance(available, int) else "내 가용 현금을 모릅니다. 알려주면 가능한 공급분만 골라 보여줍니다."),
    ]
    return out


def by_term(loan_man: int, basis, row: dict) -> dict:
    """만기별(30년·최장) × 비교 금리별 월 부담.

    비교 금리: 내 예상 금리(있으면)와 공고 범위 상단(보수적). 둘이 같으면 하나.
    공고 범위 하단은 최선의 경우로 월 총부담만 함께 준다.
    """
    rent = row.get("land_rent_monthly_won", 0)
    my_rent = row.get("my_conversion", {}).get("land_rent_monthly_after_won")
    rates = []
    for r in (basis.rate_pct, basis.rate_high):
        if r is not None and r not in rates:
            rates.append(r)
    out = {}
    for y in loans.terms(basis):
        per_rate = {}
        for r in rates:
            b = loans.with_terms(basis, y, r)
            pay = loans.monthly_payment_won(loan_man, b)
            interest = loans.first_month_interest_won(loan_man, b)
            t = {"monthly_loan_won": pay, "monthly_interest_won": interest,
                 "monthly_principal_won": pay - interest, "monthly_total_won": pay + rent}
            if my_rent is not None:
                t["monthly_total_my_conversion_won"] = pay + my_rent
            per_rate[f"{r:g}"] = t
        entry = {"by_rate": per_rate}
        if basis.rate_low is not None and basis.rate_low not in rates:
            lo = loans.monthly_payment_won(loan_man, loans.with_terms(basis, y, basis.rate_low))
            entry["best_case"] = {"rate_pct": basis.rate_low, "monthly_total_won": lo + rent}
        out[str(y)] = entry
    return out


def my_conversion(available_man: int, min_cash_man: int, monthly_won: int, max_deposit_won: int,
                   rule, pay_won: int) -> dict:
    """가용 현금 중 최소 필요 현금을 빼고 남는 돈을 토지임대료 보증금으로 전환한다 (전환 단위 내림, 최대 한도까지)."""
    room_man = available_man - min_cash_man
    if room_man < 0:
        return {"shortfall_man": -room_man, "deposit_man": 0,
                "land_rent_monthly_after_won": monthly_won, "monthly_total_won": pay_won + monthly_won}
    unit = rule.unit_won or 1_000_000
    deposit = min(room_man * 10_000 // unit * unit, max_deposit_won)
    after = pricing.convert_by_deposit(monthly_won, deposit, rule.rate)
    return {
        "deposit_man": deposit // 10_000,
        "capped_at_max": deposit == max_deposit_won,
        "land_rent_monthly_after_won": after,
        "monthly_total_won": pay_won + after,
        "cash_used_man": min_cash_man + deposit // 10_000,
        "cash_left_man": available_man - min_cash_man - deposit // 10_000,
    }
