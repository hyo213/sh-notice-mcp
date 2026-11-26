"""내 조건(프로필) 파일.

자격 판단에 필요한 개인 정보를 내 컴퓨터의 로컬 파일 하나에만 둔다.
  - 이 프로젝트는 이 파일을 어떤 외부 서버에도 보내거나 저장하지 않는다.
  - 다만 Claude가 자격을 판단하려면 대화 중에 내용을 읽어야 하므로, 판단에 필요한 항목만 적는다.
  - 주민등록번호·계좌번호 같은 식별정보는 받지 않는다 (판단에 필요 없음).

'모름'의 표현: 값을 적지 않은(주석 처리된) 항목은 모르는 것으로 본다.
  예전에는 0을 기본값으로 두었는데, 0이 '없음'인지 '모름'인지 구분할 수 없었다(자녀 0명 vs 모름).
  그래서 모르는 항목은 아예 비워 두고, Claude에게 missing 목록으로 알려 물어보게 한다.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

from .config import RENT_TYPES, rent_type_name

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

MARITAL = ("미혼", "예비신혼", "기혼", "한부모")


# 섹션 → 키 → (타입, 설명). 타입: "date" | "year" | int | bool | str | "list" | ("enum", 값들)
SCHEMA: dict[str, dict[str, tuple[object, str]]] = {
    "basic": {
        "birth_date": ("date", "생년월일 (YYYY-MM-DD)"),
        "household_head": (bool, "세대주 여부"),
        "homeless_household": (bool, "무주택세대구성원 여부 (세대원 전원 무주택)"),
        "seoul_resident": (bool, "현재 서울 거주"),
        "seoul_since": ("year", "서울 전입 연도 (계속 거주한 경우)"),
        "household_size": (int, "세대원 수 (본인 포함)"),
        "marital_status": (("enum", MARITAL), "미혼 / 예비신혼 / 기혼 / 한부모"),
        "marriage_date": ("date", "혼인신고일 (YYYY-MM-DD). 신혼부부 기간(보통 7년) 판단용"),
        "children": (int, "자녀 수 (태아 포함 여부는 공고마다 다름)"),
        "minor_children": (int, "미성년 자녀 수"),
        "recent_births": (int, "2023-03-28 이후 출생 자녀(태아 포함) 수. 소득·자산 기준 가산 대상"),
        "dependents": (int, "부양가족 수 (본인 제외, 배우자·직계존비속 등. 공고 기준 확인)"),
        "homeless_years": (int, "무주택 기간 (년). 기산일은 공고마다 다름"),
        "first_home_buyer": (bool, "세대원 모두 과거에 주택을 소유한 적 없음 (생애최초 특별공급)"),
        "disability": (bool, "세대원 중 장애인 있음"),
        "vulnerable": (bool, "사회취약계층 해당 (수급자·차상위 등)"),
        "recent_jeonse_contract": (bool, "최근 5년 이내 장기전세주택 임대차계약 이력 (감점 대상)"),
    },
    "income_assets": {
        "monthly_household_income": (int, "세대 합산 월평균 소득 (만원)"),
        "dual_income": (bool, "맞벌이 여부 (맞벌이는 소득 기준이 완화되는 경우가 많음)"),
        "total_assets": (int, "총자산 (만원). 부동산+금융+자동차 등, 산정 방식은 공고마다 다름"),
        "car_value": (int, "자동차가액 (만원). 차가 없으면 0"),
        "available_cash": (int, "가용 현금 (만원). 대출 없이 계약금·잔금·보증금에 바로 쓸 수 있는 돈"),
        "income_tax_5y": (bool, "5년 이상 소득세 납부 (생애최초 특별공급 요건)"),
    },
    "savings_account": {
        "payments": (int, "주택청약종합저축 납입 횟수"),
        "months": (int, "가입 기간 (개월)"),
        "recognized_amount": (int, "납입 인정 금액 (만원)"),
    },
    "wishes": {
        "regions": ("list", "희망 지역. 예: [\"강동구\", \"강서구\"]"),
        "area": (str, "희망 면적. 예: \"60㎡ 이하\""),
    },
    "watch": {
        "sale": (bool, "주택분양 모집공고를 볼지 (적지 않으면 true)"),
        "rent": (bool, "주택임대 모집공고를 볼지 (적지 않으면 true)"),
        "rent_types": ("rent_types", "주택임대 중 볼 공급유형. 비우면 전체. 가능: " + ", ".join(RENT_TYPES)),
        "include_words": ("list", "제목에 이 단어 중 하나라도 있는 공고만 (예: [\"강동\", \"고덕\"]). 비우면 전부"),
        "exclude_words": ("list", "제목에 이 단어가 있으면 제외 (예: [\"잔여세대\"])"),
    },
    "loan": {
        "expected_rate_pct": ("rate", "내 예상 대출 금리 (연 %, 예: 2.5). 있으면 비용표 기준 금리로 쓰고, 공고 범위(최저~최고)도 함께 보여줌"),
    },
    "memo": {
        "note": (str, "기타 (예: \"청년 특별공급 관심\")"),
    },
}


def _example(kind) -> str:
    return {
        "date": '"1990-01-01"', "year": "2000", int: "0", bool: "true", str: '""', "list": "[]", "rate": "2.5",
    }.get(kind) or ('["장기전세주택", "행복주택"]' if kind == "rent_types" else '"기혼"')


def _template() -> str:
    lines = [
        "# SH 공고 자격 판단용 '내 조건' 파일",
        "# - 이 파일은 내 컴퓨터에만 있고, 이 프로그램은 외부로 보내지 않습니다.",
        "#   (Claude가 자격을 판단할 때 대화 중에 읽습니다.)",
        "# - 아는 항목만 앞의 '#'을 지우고 값을 적으세요. '#'이 붙은 항목은 '모름'으로 봅니다.",
        "# - 주민등록번호 같은 식별정보는 적지 마세요. 금액 단위: 만원",
        "",
    ]
    for sec, keys in SCHEMA.items():
        lines.append(f"[{sec}]")
        for key, (kind, desc) in keys.items():
            lines.append(f"# {key} = {_example(kind)}   # {desc}")
        lines.append("")
    return "\n".join(lines)


TEMPLATE = _template()


def profile_path(data_dir: Path) -> Path:
    return data_dir / "profile.toml"


def ensure_template(data_dir: Path) -> Path:
    p = profile_path(data_dir)
    if not p.exists():
        p.write_text(TEMPLATE, encoding="utf-8")
    return p


def _derived(data: dict, today: date) -> dict:
    """판단에 자주 쓰는 값을 미리 계산해 둔다 (AI의 날짜 계산 실수 방지)."""
    out = {}
    b = data.get("basic", {})
    if bd := b.get("birth_date"):
        d = date.fromisoformat(bd)
        out["age"] = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
    if md := b.get("marriage_date"):
        d = date.fromisoformat(md)
        months = (today.year - d.year) * 12 + today.month - d.month - (today.day < d.day)
        out["marriage_months"] = months
        out["newlywed_within_7y"] = months < 84
        try:
            out["newlywed_7y_until"] = d.replace(year=d.year + 7).isoformat()
        except ValueError:  # 2월 29일
            out["newlywed_7y_until"] = date(d.year + 7, 3, 1).isoformat()
    if (since := b.get("seoul_since")) and b.get("seoul_resident", True):
        out["seoul_years_if_continuous"] = today.year - int(since)
    return out


def load_profile(data_dir: Path, today: date | None = None) -> dict:
    """프로필을 읽는다. 적힌 값, 모르는 항목(missing), 계산값(derived)을 함께 돌려준다."""
    p = ensure_template(data_dir)
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"프로필 파일 형식 오류 ({p}): {e}") from e
    missing = [f"{s}.{k}" for s, keys in SCHEMA.items() for k in keys if k not in data.get(s, {})]
    filled = any(data.get(s) for s in SCHEMA)
    return {
        "path": str(p),
        "filled": filled,
        "profile": data,
        "derived": _derived(data, today or date.today()),
        "missing": missing,
        "fields": {f"{s}.{k}": desc for s, keys in SCHEMA.items() for k, (_, desc) in keys.items()},
    }


# ---------------------------------------------------------------- 수정


def validate_change(dotted: str, value):
    """'basic.marital_status' 같은 키와 값을 검사해 정규화된 값을 돌려준다."""
    if "." not in dotted:
        raise ValueError(f"키는 '섹션.항목' 형식이어야 합니다: {dotted}")
    sec, key = dotted.split(".", 1)
    if sec not in SCHEMA or key not in SCHEMA[sec]:
        raise ValueError(f"알 수 없는 항목: {dotted}. 가능한 항목: {', '.join(f'{s}.{k}' for s in SCHEMA for k in SCHEMA[s])}")
    kind = SCHEMA[sec][key][0]
    if kind == "date":
        if not isinstance(value, str):
            raise ValueError(f"{dotted}: 날짜는 'YYYY-MM-DD' 문자열이어야 합니다.")
        d = date.fromisoformat(value)
        if not (1900 <= d.year <= date.today().year + 1):
            raise ValueError(f"{dotted}: 날짜가 이상합니다: {value}")
        return d.isoformat()
    if kind == "year":
        y = int(value)
        if not (1900 <= y <= date.today().year):
            raise ValueError(f"{dotted}: 연도가 이상합니다: {value}")
        return y
    if kind is bool:
        if isinstance(value, bool):
            return value
        raise ValueError(f"{dotted}: true/false 값이어야 합니다.")
    if kind == "rate":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"{dotted}: 숫자(연 %)여야 합니다. 예: 2.5")
        v = float(str(value).replace("%", "").strip())
        if not (0 < v < 20):
            raise ValueError(f"{dotted}: 0~20 사이 연 %여야 합니다: {value}")
        return v
    if kind is int:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value != int(value):
            raise ValueError(f"{dotted}: 0 이상의 정수여야 합니다.")
        return int(value)
    if kind == "list":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError(f"{dotted}: 문자열 목록이어야 합니다.")
        return [_safe_str(dotted, x) for x in value]
    if kind == "rent_types":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ValueError(f"{dotted}: 공급유형 이름의 목록이어야 합니다. 예: [\"행복주택\"]")
        return list(dict.fromkeys(rent_type_name(x) for x in value))
    if isinstance(kind, tuple) and kind[0] == "enum":
        if value not in kind[1]:
            raise ValueError(f"{dotted}: {', '.join(kind[1])} 중 하나여야 합니다.")
        return value
    return _safe_str(dotted, value)


def _safe_str(dotted: str, v) -> str:
    if not isinstance(v, str) or "\n" in v or "#" in v or len(v) > 200:
        raise ValueError(f"{dotted}: 한 줄짜리 문자열(200자 이하, '#' 제외)이어야 합니다.")
    return v


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(json.dumps(x, ensure_ascii=False) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)


def set_values(text: str, changes: dict[str, object]) -> str:
    """주석을 보존하면서 값만 바꾼다. 항목 줄이 없으면 섹션 끝에 추가한다."""
    lines = text.splitlines()
    for dotted, value in changes.items():
        sec, key = dotted.split(".", 1)
        desc = SCHEMA[sec][key][1]
        new_line = f"{key} = {_toml(value)}   # {desc}"
        start = next((i for i, ln in enumerate(lines) if ln.strip() == f"[{sec}]"), None)
        if start is None:
            lines += ["", f"[{sec}]", new_line]
            continue
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        pat = re.compile(rf"^\s*#?\s*{re.escape(key)}\s*=")
        hit = next((i for i in range(start + 1, end) if pat.match(lines[i])), None)
        if hit is not None:
            lines[hit] = new_line
        else:
            insert_at = end
            while insert_at > start + 1 and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines.insert(insert_at, new_line)
    return "\n".join(lines) + "\n"


def update_profile(data_dir: Path, changes: dict[str, object]) -> dict:
    p = ensure_template(data_dir)
    clean = {k: validate_change(k, v) for k, v in changes.items()}
    new_text = set_values(p.read_text(encoding="utf-8"), clean)
    tomllib.loads(new_text)  # 쓰기 전에 형식 확인
    p.write_text(new_text, encoding="utf-8")
    return clean


# ---------------------------------------------------------------- 공고별로 필요한 항목

# 공고문에 이 표현이 있으면 그 항목이 자격·조건·배점 판단에 쓰인다.
NEEDED_BY = {
    "basic.birth_date": r"만\s*\d+\s*세|나이|연령",
    "basic.household_head": r"세대주",
    "basic.homeless_household": r"무주택세대구성원",
    "basic.seoul_since": r"거주\s*기간|계속\s*거주|연속\s*거주",
    "basic.household_size": r"가구원\s*수|세대원\s*수",
    "basic.marital_status": r"신혼부부|혼인|배우자",
    "basic.marriage_date": r"신혼부부|혼인\s*기간",
    "basic.children": r"자녀",
    "basic.minor_children": r"미성년\s*자녀|다자녀",
    "basic.recent_births": r"2023\s*\.\s*0?3\s*\.\s*28|2023년\s*3월\s*28일",
    "basic.dependents": r"부양가족",
    "basic.homeless_years": r"무주택\s*기간",
    "basic.first_home_buyer": r"생애\s*최초",
    "basic.disability": r"장애인",
    "basic.vulnerable": r"사회취약|수급자|차상위",
    "basic.recent_jeonse_contract": r"장기전세주택\s*임대차\s*계약\s*사실|임대차계약\s*사실",
    "income_assets.monthly_household_income": r"월평균\s*소득",
    "income_assets.dual_income": r"맞벌이",
    "income_assets.total_assets": r"총\s*자산",
    "income_assets.car_value": r"자동차",
    "income_assets.income_tax_5y": r"소득세",
    "savings_account.payments": r"납입\s*(?:회차|횟수)|\d+\s*회\s*이상",
    "savings_account.months": r"가입\s*기간|가입하여\s*\d+\s*(?:년|개월)",
    "savings_account.recognized_amount": r"납입\s*인정\s*금액|예치\s*금액|예치금",
}


def gaps_for_notice(profile: dict, docs: list[tuple[str, str]]) -> list[dict]:
    """공고문(출처별 텍스트)이 판단에 쓰는데 내 조건에 비어 있는 항목과, 그 근거 문장."""
    data = profile.get("profile", {})
    out = []
    for dotted, pattern in NEEDED_BY.items():
        sec, key = dotted.split(".")
        if key in data.get(sec, {}):
            continue
        rx = re.compile(pattern)
        for source, text in docs:
            hit = next((ln.strip() for ln in text.splitlines() if rx.search(ln)), None)
            if hit:
                out.append({
                    "field": dotted,
                    "question": SCHEMA[sec][key][1],
                    "why": {"source": source, "quote": hit if len(hit) <= 120 else hit[:119] + "…"},
                })
                break
    return out
