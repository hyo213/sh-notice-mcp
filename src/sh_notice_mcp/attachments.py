"""첨부파일 목록 파싱 + '모집공고문 PDF' 고르기.

상세 페이지 HTML 안에 첨부파일 정보가 JSON으로 들어 있다.
    initParam.downList = [{"brdId":"GS0401","seq":"311656","fileSeq":"1",
                           "fileSize":"7540395","oriFileNm":"...입주자모집공고문.pdf","fileTp":"A"}, ...];
다운로드 주소는 이 값으로 만든다.
    /main/com/file/innoFD.do?brdId=GS0401&seq=311656&fileTp=A&fileSeq=1
(이 주소는 GET만 허용하고 HEAD는 401을 돌려준다. 쿠키·리퍼러는 필요 없다.)

장기전세 공고는 첨부가 보통 1개지만, 분양 공고는 위임장·동의서·팸플릿 등 서식이 함께 올라온다.
모집공고문 하나만 골라야 요약·자격 판단이 정확해진다.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

DOWNLOAD_BASE = "https://www.i-sh.co.kr/main/com/file/innoFD.do"

_DOWNLIST_RE = re.compile(r"initParam\.downList\s*=\s*(\[.*?\])\s*;", re.S)

# 파일명에 있으면 모집공고문일 가능성이 높은 단어 (앞쪽일수록 강한 신호)
POSITIVE = ("입주자모집공고문", "입주자 모집공고문", "모집공고문", "입주자모집공고", "모집 공고", "모집공고", "공고문")
# 서식·부속 자료 (이 단어가 있으면 후보에서 제외)
NEGATIVE = (
    "위임장", "동의서", "팸플릿", "팜플렛", "리플릿", "브로셔", "계획서", "신청서", "확인서",
    "서약서", "각서", "서식", "양식", "별지", "체크리스트", "배치도", "평면도", "조감도",
    "당첨자", "결과", "명단", "예비입주자",
)
# '정정'·'변경'은 넣지 않는다: 정정 공고에서는 '[정정] ...모집공고문.pdf'가 바로 읽어야 할 파일이다.


@dataclass
class Attachment:
    file_seq: str
    name: str
    size: int
    brd_id: str
    seq: str
    file_tp: str

    @property
    def is_pdf(self) -> bool:
        return self.name.lower().endswith(".pdf")

    @property
    def url(self) -> str:
        return (
            f"{DOWNLOAD_BASE}?brdId={self.brd_id}&seq={self.seq}"
            f"&fileTp={self.file_tp}&fileSeq={self.file_seq}"
        )

    def to_dict(self) -> dict:
        return {**asdict(self), "url": self.url, "size_mb": round(self.size / 1_048_576, 2)}


@dataclass
class Selection:
    chosen: Attachment | None
    reason: str
    candidates: list[Attachment]

    def to_dict(self) -> dict:
        return {
            "chosen": self.chosen.to_dict() if self.chosen else None,
            "reason": self.reason,
            "candidates": [a.to_dict() for a in self.candidates],
        }


def parse_attachments(html: str) -> list[Attachment]:
    m = _DOWNLIST_RE.search(html)
    if not m:
        return []
    try:
        raw = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out = []
    for r in raw:
        try:
            out.append(
                Attachment(
                    file_seq=str(r["fileSeq"]),
                    name=str(r.get("oriFileNm", "")).strip(),
                    size=int(r.get("fileSize") or 0),
                    brd_id=str(r["brdId"]),
                    seq=str(r["seq"]),
                    file_tp=str(r.get("fileTp", "A")),
                )
            )
        except (KeyError, ValueError):
            continue
    return out


def _score(name: str) -> int:
    n = name.replace(" ", "")
    for i, kw in enumerate(POSITIVE):
        if kw.replace(" ", "") in n:
            return len(POSITIVE) - i
    return 0


def select_notice_pdf(attachments: list[Attachment]) -> Selection:
    """모집공고문 PDF 하나를 고른다. 확신할 수 없으면 chosen=None으로 돌려 사람(또는 Claude)이 고르게 한다."""
    pdfs = [a for a in attachments if a.is_pdf]
    if not pdfs:
        return Selection(None, "PDF 첨부파일이 없습니다.", attachments)

    usable = [a for a in pdfs if not any(k in a.name.replace(" ", "") for k in NEGATIVE)]
    scored = sorted(usable, key=lambda a: (_score(a.name), a.size), reverse=True)

    if scored and _score(scored[0].name) > 0:
        top = scored[0]
        ties = [a for a in scored[1:] if _score(a.name) == _score(top.name)]
        if ties:
            return Selection(
                None,
                "모집공고문으로 보이는 PDF가 여러 개입니다. 어떤 파일을 읽을지 선택이 필요합니다.",
                [top, *ties],
            )
        return Selection(top, f"파일명에 '모집공고' 관련 단어가 있습니다: {top.name}", pdfs)

    # 장기전세처럼 첨부가 하나뿐인 경우. 단, '당첨자 명단.pdf'처럼 서식·결과 파일이면 고르지 않는다.
    if len(usable) == 1:
        why = "첨부된 PDF가 하나뿐입니다." if len(pdfs) == 1 else "서식 파일을 제외하면 PDF가 하나 남습니다."
        return Selection(usable[0], why, pdfs)
    if not usable:
        return Selection(None, "모집공고문이 아니라 서식·결과 파일만 첨부되어 있습니다.", pdfs)
    return Selection(None, "모집공고문을 확신할 수 없습니다. 후보 중에서 선택이 필요합니다.", pdfs)
