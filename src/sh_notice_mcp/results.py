"""당첨자 발표: 발표 글 찾기, 명단 PDF 파싱, 내 접수번호 대조.

SH는 발표일에 '○○ 당첨자 발표 및 서류제출 안내' 글을 올리고 아래 PDF를 붙인다 (2026-04 마곡 17단지 본청약 확인).
  - 당첨자 명단:      '연번 주택형 접수번호 신청구분 당첨자'
  - 예비입주자 명단:  '순위 주택형 접수번호 신청구분 예비입주자'
  - 당첨하한선, 서류 제출 안내 …
이름은 일부를 가리므로 접수번호로만 찾는다. 나중에 부적격·포기자를 뺀 '당첨자 확정 명단' 글이 따로 올라온다.

판정은 참고용이다. SH도 명단 PDF는 착오가 있을 수 있다며 SH인터넷청약시스템·ARS(1600-3456)에서
본인이 확인하라고 안내하므로, 결과에는 늘 그 안내를 붙인다.
"""

from __future__ import annotations

import re

from .attachments import Attachment

# 연번/순위, 주택형(059.0000H), 접수번호, 신청구분(공백 없는 한 단어: 생애최초, 사전청약-청년, 일반 …)
_ROW = re.compile(r"^\s*(\d{1,5})\s+(\d{2,3}\.\d{2,4}[A-Z]?\d?)\s+(\d{5,8})\s+(\S+)")

OFFICIAL_CHECK = ("명단 PDF로 찾은 참고 결과입니다. 최종 확인은 SH인터넷청약시스템(대상자 조회·제출 → 당첨자 조회) "
                  "또는 ARS 1600-3456에서 본인이 직접 하세요.")


def house_type_label(code: str) -> str:
    """'059.0000H' → '59㎡'."""
    m = re.match(r"0*(\d+)\.", code)
    return f"{m.group(1)}㎡" if m else code


def parse_result_list(pages: list[str]) -> list[dict]:
    """명단 PDF의 각 쪽 텍스트 → [{order, house_type, receipt_no, category, page}]. 쪽 경계에서 잘린 줄은 버린다."""
    rows = []
    for page_no, text in enumerate(pages, start=1):
        for line in (text or "").splitlines():
            m = _ROW.match(line)
            if m:
                rows.append({"order": int(m.group(1)), "house_type": house_type_label(m.group(2)),
                             "house_code": m.group(2), "receipt_no": m.group(3),
                             "category": m.group(4), "page": page_no})
    return rows


def supply_group(category: str) -> str:
    """신청구분 → 공급구분. 예비 순번은 주택형 × 공급구분(특별/일반)마다 1번부터 다시 센다."""
    return "일반공급" if category.split("-")[-1].startswith("일반") else "특별공급"


def list_kind(file_name: str) -> str | None:
    """첨부파일 이름으로 명단 종류를 정한다. 명단이 아니면 None."""
    n = file_name.replace(" ", "")
    if "명단" not in n or not n.lower().endswith(".pdf"):
        return None
    if "예비" in n:
        return "예비"
    if "당첨" in n:
        return "당첨"
    return None


def result_lists(attachments: list[Attachment]) -> list[tuple[str, Attachment]]:
    return [(k, a) for a in attachments if (k := list_kind(a.name))]


def notice_key(title: str) -> str:
    """'[정정] 고덕강일지구 3단지 토지임대부 분양주택 입주자모집공고' → '고덕강일지구 3단지 토지임대부 분양주택'."""
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title)
    t = re.split(r"(?:본청약\s*)?(?:입주자\s*)?모\s*집\s*공\s*고", t)[0]
    return re.sub(r"\s+", " ", t).strip()


def is_result_post_for(post_title: str, notice_title: str) -> bool:
    """발표 글이 이 공고의 것인가. 단어 대부분이 겹치면 같은 공고로 본다 ('고덕강일3단지'처럼 붙여 쓴 경우 포함)."""
    if "당첨자" not in post_title or ("명단" not in post_title and "발표" not in post_title):
        return False
    if "사전" in post_title and "사전" not in notice_title:  # 사전청약 발표는 본청약 공고의 결과가 아니다
        return False
    key = notice_key(notice_title)
    compact = post_title.replace(" ", "")
    if key.replace(" ", "") in compact:
        return True
    words = [w for w in key.split() if len(w) >= 2]
    if not words:
        return False
    hits = sum(1 for w in words if w in compact or (w.endswith("지구") and w[:-2] in compact))
    return hits / len(words) >= 0.75


def judge(receipt_no: str, found: dict[str, list[dict]]) -> dict:
    """found = {'당첨': rows, '예비': rows} → 내 접수번호의 결과."""
    rn = re.sub(r"\D", "", str(receipt_no))
    win = [r for r in found.get("당첨", []) if r["receipt_no"] == rn]
    if win:
        r = win[0]
        return {"result": "당첨", "house_type": r["house_type"], "category": r["category"],
                "order": r["order"], "page": r["page"]}
    wait = [r for r in found.get("예비", []) if r["receipt_no"] == rn]
    if wait:
        r = wait[0]
        group = supply_group(r["category"])
        total = sum(1 for x in found["예비"]
                    if x["house_type"] == r["house_type"] and supply_group(x["category"]) == group)
        return {"result": "예비", "rank": r["order"], "of": total, "group": f"{r['house_type']} {group}",
                "house_type": r["house_type"], "category": r["category"], "page": r["page"],
                "note": "예비 순번은 주택형·공급구분별로 따로 매깁니다. 발표 글 본문에 서류를 낼 예비 순번 범위가 "
                        "나오면 그 안에 드는지 확인하세요. 당첨자 부적격·포기가 생기면 순번대로 연락이 옵니다."}
    if not found.get("당첨") and not found.get("예비"):
        return {"result": "확인 불가", "note": "명단 PDF에서 표를 읽지 못했습니다(스캔본일 수 있음)."}
    return {"result": "명단에 없음",
            "note": "당첨자·예비입주자 명단에서 이 접수번호를 찾지 못했습니다. 낙첨일 가능성이 높지만, 번호 오타나 "
                    "명단 형식 문제일 수도 있으니 SH에서 확인하세요."}
