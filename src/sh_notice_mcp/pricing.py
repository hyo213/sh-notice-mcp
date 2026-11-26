"""공고문 PDF의 가격표 → 평형별 비용 요약 (계산은 모두 여기서, AI는 숫자를 지어내거나 암산하지 않는다).

실제 공고문(2026-10)을 분석해 정한 형식
  [분양] 고덕강일지구 3단지 토지임대부 (136쪽: 앞 43쪽은 팸플릿, p.44부터 공고문)
    p.52~80 건물분양가: "동 호 주택형 평면유형 주택가격계(천원) 계약금 잔금 발코니확장비(원)" 한 줄에 한 세대
        301 201 059.0000B 59B 371,544 37,154 334,390 5,825,600
    p.81 토지임대료(원/월): "49 426,000 전용 49㎡ 전체 주택형에 동일 적용"
    p.82 보증금 전환: "월 토지임대료의 최대 60%를 보증금으로 전환", "100만원 단위", 예시 이율 2.5%
         예시: 49㎡ 426,000 → 전환보증금 122,000,000, 전환 후 171,830 (아래 계산식으로 정확히 재현됨)
    p.44 잔금 대출: "최대 5억원 한도(LTV 최대 80%, 만기 최장 40년, 고정금리 1.9~3.0% 등)"
  [장기전세] 제51차 (64쪽) p.13~19 공급현황: "… 전세금 계 계약금(10%) 잔금(90%) 전용 주거공용 기타공용 합계 …"
        일반 11 616,980 61,698 555,282 84.97 31.75 60.63 177.35 개별난방   (천원)
    단지명이 여러 줄에 걸쳐 있어 행마다 앞뒤 문맥을 함께 돌려주고, 단지 이름 매칭은 Claude가 한다.

숫자는 '계 = 계약금 + 잔금, 계약금 ≈ 계의 10%' 관계가 맞을 때만 받아들인다 (잘못 붙은 숫자 거르기).
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass

_NUM = r"\d{1,3}(?:,\d{3})+|\d+"


def _n(s: str) -> int:
    return int(s.replace(",", ""))


def _flat(page: str) -> str:
    return " ".join(page.split())


def _deposit_ok(total: int, first: int, rest: int) -> bool:
    return total == first + rest and abs(first - total / 10) <= max(2, total * 0.002)


# ------------------------------------------------------------------ 분양가


@dataclass
class SaleRow:
    dong: str
    ho: str
    house_type: str  # 대표 타입 (청약은 이 단위로 신청): 059.0000B → 59B
    plan_type: str  # 세부 평면유형: 59B, 49C1 …
    price_k: int  # 주택가격 계 (천원)
    balcony_won: int  # 발코니 확장비 (원)
    page: int


_SALE_ROW = re.compile(
    rf"(?<!\d)(\d{{3,4}})\s+(\d{{3,4}})\s+(0?\d{{2}}\.\d{{4}}[A-Z])\s+(\d{{2,3}}[A-Z]\d?)\s+"
    rf"({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})(?![\d,])"
)


def _rep_type(code: str) -> str:
    m = re.match(r"0?(\d{2,3})\.\d{4}([A-Z])", code)
    return f"{int(m.group(1))}{m.group(2)}" if m else code


def parse_sale_prices(pages: list[str]) -> list[SaleRow]:
    rows = []
    for pno, page in enumerate(pages, start=1):
        for m in _SALE_ROW.finditer(_flat(page)):
            dong, ho, code, plan, total, first, rest, balcony = m.groups()
            t, f, r = _n(total), _n(first), _n(rest)
            if not _deposit_ok(t, f, r):
                continue
            rows.append(SaleRow(dong, ho, _rep_type(code), plan, t, _n(balcony), pno))
    return rows


def summarize_sale(rows: list[SaleRow]) -> list[dict]:
    """대표 타입별 평균. 금액 단위는 만원. 발코니 확장비를 더한 금액도 함께."""
    groups: dict[str, list[SaleRow]] = defaultdict(list)
    for r in rows:
        groups[r.house_type].append(r)

    def won_to_man(won: float) -> int:
        return round(won / 10_000)

    out = []
    for t in sorted(groups, key=lambda x: (int(re.match(r"\d+", x).group()), x)):
        g = groups[t]
        prices = [r.price_k * 1000 for r in g]
        totals = [r.price_k * 1000 + r.balcony_won for r in g]
        out.append({
            "type": t,
            "area_m2": int(re.match(r"\d+", t).group()),
            "plans": sorted({r.plan_type for r in g}),
            "units": len(g),
            "avg_price_man": won_to_man(sum(prices) / len(g)),
            "min_price_man": won_to_man(min(prices)),
            "max_price_man": won_to_man(max(prices)),
            "avg_balcony_man": round(sum(r.balcony_won for r in g) / len(g) / 10_000, 1),
            "avg_total_with_balcony_man": won_to_man(sum(totals) / len(g)),
            "pages": f"{min(r.page for r in g)}-{max(r.page for r in g)}",
        })
    return out


# ------------------------------------------------------------------ 토지임대료·전환


def parse_land_rent(pages: list[str]) -> tuple[dict[int, int], int | None]:
    """{전용면적(㎡): 월 토지임대료(원)}, 쪽 번호."""
    for pno, page in enumerate(pages, start=1):
        flat = _flat(page)
        i = flat.find("토지임대료(원/월)")
        if i < 0:
            continue
        found = {}
        for m in re.finditer(rf"(?<![\d.,])(\d{{2,3}})\s+({_NUM})\s+전용", flat[i : i + 600]):
            found[int(m.group(1))] = _n(m.group(2))
        if found:
            return found, pno
    return {}, None


@dataclass
class ConversionRule:
    max_ratio: float  # 월 임대료 중 보증금으로 바꿀 수 있는 최대 비율
    unit_won: int  # 전환 단위
    rate: float | None  # 전환이율 (공고 예시값. 확정 전일 수 있음)
    rate_is_example: bool
    page: int


def parse_conversion(pages: list[str]) -> ConversionRule | None:
    for pno, page in enumerate(pages, start=1):
        flat = _flat(page)
        m = re.search(r"최대\s*(\d{1,3})\s*%\s*를?\s*보증금으로\s*전환", flat)
        if not m:
            continue
        unit = re.search(r"(\d+)\s*만\s*원\s*단위", flat)
        ex = re.search(r"전환\s*예시.*?(\d{1,3})\s*%\s+(\d+(?:\.\d+)?)\s*%", flat)
        return ConversionRule(
            max_ratio=int(m.group(1)) / 100,
            unit_won=int(unit.group(1)) * 10_000 if unit else 1,
            rate=float(ex.group(2)) / 100 if ex else None,
            rate_is_example=bool(ex) and ("확정" in flat or "예시" in flat),
            page=pno,
        )
    return None


def convert_rent(monthly_won: int, ratio: float, rate: float, unit_won: int) -> dict:
    """월 임대료 일부를 보증금으로 바꿨을 때. 공고문 예시와 같은 방식:
    보증금 = (월 임대료 × 비율 × 12 ÷ 이율)을 전환 단위로 내림
    전환 후 월 임대료 = 월 임대료 − 보증금 × 이율 ÷ 12 (10원 미만 버림)"""
    deposit = math.floor(monthly_won * ratio * 12 / rate / unit_won) * unit_won
    after = monthly_won - deposit * rate / 12
    after = math.floor(after / 10) * 10
    return {"deposit_won": deposit, "monthly_after_won": int(after), "monthly_saved_won": monthly_won - int(after)}


def convert_by_deposit(monthly_won: int, deposit_won: int, rate: float) -> int:
    """정해진 보증금을 넣었을 때 전환 후 월 임대료 (공고문 예시와 같은 계산, 10원 미만 버림)."""
    return int(math.floor((monthly_won - deposit_won * rate / 12) / 10) * 10)


# ------------------------------------------------------------------ 장기전세 전세금


@dataclass
class JeonseRow:
    deposit_k: int  # 전세금 계 (천원)
    exclusive_m2: float
    contract_m2: float | None
    units: int | None
    supply_kind: str  # 일반 / 주거약자 / 우선 등 (보이면)
    context: str  # 앞쪽 문맥 (단지명 매칭용)
    page: int


_JEONSE_ROW = re.compile(
    rf"(?P<pre>(?:\S+\s+){{0,8}}?)(?P<total>{_NUM})\s+(?P<first>{_NUM})\s+(?P<rest>{_NUM})\s+"
    r"(?P<ex>\d{2,3}\.\d{2})\s+(?P<pub>\d{1,3}\.\d{2})\s+(?P<etc>\d{1,3}\.\d{2})\s+(?P<sum>\d{2,3}\.\d{2})"
)


def parse_jeonse_prices(pages: list[str]) -> list[JeonseRow]:
    rows = []
    for pno, page in enumerate(pages, start=1):
        flat = _flat(page)
        if "전세금액" not in flat:
            continue
        last_end = 0
        for m in _JEONSE_ROW.finditer(flat):
            t, f, r = _n(m["total"]), _n(m["first"]), _n(m["rest"])
            ex, pub, etc, tot = (float(m[k]) for k in ("ex", "pub", "etc", "sum"))
            if not _deposit_ok(t, f, r) or abs(ex + pub + etc - tot) > 0.05:
                continue
            pre = m["pre"].split()
            kind = next((w for w in pre if w in ("일반", "주거약자", "우선")), "")
            units = next((int(w) for w in reversed(pre) if w.isdigit() and int(w) < 2000), None)
            ctx = flat[max(last_end, m.start() - 80) : m.start("total")].strip()
            rows.append(JeonseRow(t, ex, tot, units, kind, ctx, pno))
            last_end = m.end()
    return rows


# ------------------------------------------------------------------ 대출


def parse_loan_terms(pages: list[str]) -> list[dict]:
    """대출·LTV 관련 문장과 핵심 숫자."""
    out = []
    pat = re.compile(r"LTV|담보인정비율|디딤돌|모기지|중도금\s*대출|잔금\s*대출|주택도시기금.{0,10}대출")
    for pno, page in enumerate(pages, start=1):
        flat = _flat(page)
        for m in pat.finditer(flat):
            s = max(0, flat.rfind(".", 0, m.start()) + 1, m.start() - 160)
            e = flat.find(". ", m.end())
            e = len(flat) if e < 0 else min(e + 1, m.end() + 220)
            quote = flat[s:e].strip()
            if any(q["quote"] == quote for q in out):
                continue
            item = {"page": pno, "quote": quote}
            for key, rx in {
                "ltv_max_pct": r"LTV\s*최대\s*(\d{1,3})\s*%",
                "limit_eok": r"최대\s*(\d+(?:\.\d+)?)\s*억\s*원?\s*한도",
                "max_years": r"만기\s*최장\s*(\d+)\s*년",
                "rate": r"금리\s*(\d+(?:\.\d+)?\s*~\s*\d+(?:\.\d+)?\s*%)",
            }.items():
                if mm := re.search(rx, quote):
                    item[key] = mm.group(1)
            out.append(item)
    return out


def sale_rows_to_dicts(rows: list[SaleRow]) -> list[dict]:
    return [asdict(r) for r in rows]
