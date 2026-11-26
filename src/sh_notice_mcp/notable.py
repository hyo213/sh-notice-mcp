"""공고문에서 '청약 전략에 영향을 주는 특이 규칙' 문장을 찾는다.

예: 특별공급 1개 + 일반공급 1개 중복 신청 가능, 부부가 각각 청약 가능, 특별공급 간 중복 불가.
이런 문장은 100쪽이 넘는 공고문 곳곳에 흩어져 있어 요약에서 빠지기 쉽다. 그래서 서버가 규칙으로 찾아
원문 문장과 쪽 번호를 그대로 건네고, Claude는 요약의 '청약 전략·특이점'에 반드시 쓰게 한다.
"""

from __future__ import annotations

import re

# (항목, 정규식). PDF 줄바꿈을 공백으로 합친 텍스트에서 찾는다.
RULES: list[tuple[str, str]] = [
    ("특별공급+일반공급 동시 신청",
     r"특별공급\s*1\s*개\s*,?\s*일반공급\s*1\s*개\s*중복\s*신청이?\s*가능"),
    ("특별공급끼리는 중복 불가",
     r"특별공급\s*간\s*중복\s*청약할\s*수\s*없"),
    ("부부 각각 청약 가능",
     r"부부[^.]{0,40}각각\s*청약할\s*수\s*있"),
    ("다른 세대원 청약 시 부적격",
     r"신청자\s*본인\s*외\s*다른\s*세대구성원이[^.]{0,60}부적격"),
    ("사전청약 당첨자 재청약 가능",
     r"지위\s*포기\s*후[^.]{0,60}한\s*번\s*더\s*청약"),
]

_SENT_END = re.compile(r"(?<=[다요음함])\.\s|(?<=니다)\s*\.|[•※■]")


def _sentence(text: str, start: int, end: int, limit: int = 220) -> str:
    """매치를 포함한 문장 하나를 잘라낸다 (앞뒤 문장부호·글머리표 기준)."""
    left = max(text.rfind(m, 0, start) for m in ("다. ", "• ", "※ ", "■ ", "니다."))
    left = 0 if left < 0 else left
    while left < start and text[left] in "다니.•※■ ":
        left += 1
    m = _SENT_END.search(text, end)
    right = m.end() if m else len(text)
    s = text[left:right].strip(" •※■")
    return s if len(s) <= limit else s[:limit] + "…"


def find_notable_rules(pages: list[str]) -> list[dict]:
    """항목마다 처음 나온 문장 하나와 PDF 쪽 번호."""
    out = []
    flat = [re.sub(r"\s+", " ", p or "") for p in pages]
    for name, pattern in RULES:
        rx = re.compile(pattern)
        for i, text in enumerate(flat):
            m = rx.search(text)
            if m:
                out.append({"rule": name, "page": i + 1, "quote": _sentence(text, m.start(), m.end())})
                break
    return out
