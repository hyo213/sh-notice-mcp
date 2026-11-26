"""공고문 PDF → 페이지별 텍스트 → 섹션.

섹션 나누기 전략 (실제 '제51차 장기전세주택 입주자 모집공고' 64쪽 PDF를 분석해 정함)
  1) 목차 우선: SH 공고문은 앞쪽에 CONTENTS가 있다. "1. 공급일정 ....... 9", "<별표2> 소득 및 자산 ... 54".
     본문 장 제목은 "01 | 공급 일정"처럼 목차와 같은 쪽에 있다. 목차가 있으면 이걸로 자른다.
  2) 목차 앞쪽(표지·공고 요약·입주자격 기준 요약)은 '표지·공고 요약' 섹션 하나로 묶는다.
     판단에 가장 쓸모 있는 요약표가 여기 있다.
  3) 목차가 없으면 제목 패턴(01 | 제목, Ⅰ. 제목, 1. 제목, ■ 제목)으로 자른다.

요약과 자격 판단은 Claude가 한다. 서버는 '읽기 좋게 잘라서 건네주는' 역할만 한다.
공고문은 30~80쪽이라 통째로 넘기면 대화 맥락을 많이 차지하므로
  1) 목차(섹션 제목·페이지·분량)를 먼저 보여주고
  2) 필요한 섹션만 골라 읽게 한다.
신청자격·공급일정·소득자산 기준처럼 판단에 꼭 필요한 섹션은 '핵심 섹션'으로 표시한다.
"""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass

from pypdf import PdfReader

MAX_PDF_BYTES = 40 * 1024 * 1024

# 섹션 제목으로 보이는 줄: "Ⅰ. 공급개요", "1. 신청자격", "■ 공급일정", "제1장 ..." 등
_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"\d{2}\s*\|"  # "03 | 공급현황" (SH 공고문 장 제목)
    r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.\s]"  # 로마숫자
    r"|제\s*\d+\s*[장절]"
    r"|\d{1,2}\s*\.\s*(?!\d)"  # "1. " (날짜 2026. 와 구분하려고 1~2자리만)
    r"|[■□◆◇●]"
    r")\s*(?P<title>[^\n]{2,40})$"
)

# 목차 한 줄: "1. 공급일정 ......... 9", "<별표2> 소득 및 자산 ... 54", "단지별 유의사항(공통) ..... 56"
_TOC_RE = re.compile(
    r"^\s*(?:(?P<num>\d{1,2})\s*\.\s*|(?P<annex><\s*별표\s*\d+\s*>)\s*)?"
    r"(?P<title>[^\d.…·][^…]*?)"
    r"(?:\s*[.…·ㆍ]{3,}\s*|\s+)(?P<page>\d{1,3})\s*$"
)

SUMMARY_TAG = "요약"

KEY_SECTIONS = {
    SUMMARY_TAG: ("표지", "공고 요약"),
    # '당첨자 선정방법'·'계약 시 구비서류'는 일정이 아니다 → 일정·기간을 뜻하는 말만
    "공급일정": ("일정", "접수기간", "신청기간", "청약신청 기간", "공급일정"),
    "신청자격": ("신청자격", "입주자격", "자격요건", "무주택", "청약자격"),
    "소득·자산": ("소득", "자산", "자동차", "총자산"),
    "공급대상": ("공급대상", "공급개요", "공급현황", "공급호수", "공급규모", "공급내역", "단지"),
    "순위·배점": ("순위", "배점", "가점", "선정방법", "입주자 선정", "입주자선정"),
    "특별공급": ("특별공급", "신혼부부", "생애최초", "청년", "신생아", "다자녀"),
}


class PdfError(RuntimeError):
    pass


@dataclass
class Section:
    index: int
    title: str
    page_from: int  # 1부터
    page_to: int
    text: str
    tags: list[str]

    def outline(self) -> dict:
        d = asdict(self)
        d.pop("text")
        d["chars"] = len(self.text)
        return d


def extract_pages(data: bytes) -> list[str]:
    if not data.startswith(b"%PDF"):
        raise PdfError("PDF 파일이 아닙니다 (HWP·이미지 등일 수 있습니다).")
    if len(data) > MAX_PDF_BYTES:
        raise PdfError(f"PDF가 너무 큽니다 ({len(data) / 1_048_576:.1f}MB).")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception as e:  # pypdf는 손상된 PDF에서 다양한 예외를 낸다
        raise PdfError(f"PDF를 읽지 못했습니다: {e}") from e
    pages = [_normalize(p) for p in pages]
    if sum(len(p.strip()) for p in pages) < 50:
        raise PdfError("PDF에서 글자를 거의 뽑지 못했습니다. 스캔(이미지)본일 수 있습니다.")
    return pages


def _normalize(text: str) -> str:
    text = text.replace(" ", " ").replace("　", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _tags_for(title: str, text: str) -> list[str]:
    tags = []
    for tag, kws in KEY_SECTIONS.items():
        if any(k in title for k in kws):
            tags.append(tag)
    return tags


def _is_heading(line: str) -> str | None:
    m = _HEADING_RE.match(line)
    if not m or len(line) > 45:
        return None
    title = m.group("title").strip()
    if line.rstrip().endswith(("다.", "다", "요.", "것", "음", "함")):  # 문장·목록 항목
        return None  # 문장
    if re.search(r"[:：]\s*\S", title):
        return None  # '■ 공급호수: 1,381세대' 같은 항목:값 줄은 제목이 아니다
    if re.search(r"\d{4}\s*\.\s*\d{1,2}\s*\.", title):
        return None  # 날짜가 든 줄은 일정 항목
    return title.strip(" :：")


def parse_toc(pages: list[str], look: int = 8) -> list[tuple[str, int, str | None]]:
    """앞쪽 페이지에서 목차를 찾는다. (제목, 인쇄된 쪽, 장 번호) 목록. 못 찾으면 빈 목록."""
    for page in pages[:look]:
        entries = []
        for line in page.splitlines():
            m = _TOC_RE.match(line)
            if not m or not (m["num"] or m["annex"] or "....." in line or "…" in line):
                continue
            title = " ".join(((m["annex"] or "") + " " + m["title"]).split()).strip(" .·…")
            entries.append((title, int(m["page"]), m["num"]))
        pages_seq = [e[1] for e in entries]
        if len(entries) >= 4 and pages_seq == sorted(pages_seq):
            return entries
    return []


def _toc_offset(pages: list[str], toc: list[tuple[str, int, str | None]]) -> int:
    """인쇄된 쪽 번호와 PDF 쪽 번호의 차이. 본문의 'NN | 제목' 위치로 계산한다."""
    votes: dict[int, int] = {}
    for _, printed, num in toc:
        if not num:
            continue
        pat = re.compile(rf"^\s*0?{int(num)}\s*\|", re.M)
        for pno, page in enumerate(pages, start=1):
            if pat.search(page):
                votes[pno - printed] = votes.get(pno - printed, 0) + 1
                break
    return max(votes, key=votes.get) if votes else 0


def _sections_from_toc(pages: list[str], toc) -> list[Section]:
    off = _toc_offset(pages, toc)
    n = len(pages)
    starts = [min(max(printed + off, 1), n) for _, printed, _ in toc]
    sections: list[Section] = []

    def add(title: str, a: int, b: int, extra_tags: list[str] | None = None):
        text = "\n\n".join(pages[a - 1 : b]).strip()
        if text:
            tags = (extra_tags or []) + [t for t in _tags_for(title, text) if t not in (extra_tags or [])]
            sections.append(Section(len(sections), title, a, b, text, tags))

    if starts[0] > 1:
        add("표지·공고 요약", 1, starts[0] - 1, [SUMMARY_TAG])
    for i, (title, _, num) in enumerate(toc):
        a = starts[i]
        b = max(a, (starts[i + 1] - 1) if i + 1 < len(toc) else n)
        add(f"{num}. {title}" if num else title, a, b)
    return sections


def _boilerplate_keys(pages: list[str]) -> set[str]:
    """쪽마다 반복되는 머리글·꼬리글 줄을 찾는다.

    실제 분양 공고문(pypdf 추출)은 쪽 아래에 '44 | 고덕강일지구 3단지 … 입주자모집공고'가 반복되는데,
    이 줄이 장 제목 패턴('01 | 공급 일정')과 같아 짝수 쪽마다 가짜 섹션이 생겼다.
    숫자를 지운 형태가 전체 쪽의 20% 이상에서 나오면 반복 줄로 본다.
    한글이 4자 이상인 줄만 대상: 분양가 행('301 201 059.0000B 59B 371,544 …')은 숫자를 지우면 모두 같아 보이지만
    지우면 안 되는 데이터다.
    """
    if len(pages) < 5:
        return set()
    counts: dict[str, int] = {}
    for page in pages:
        seen = set()
        for line in page.splitlines():
            if len(re.findall(r"[가-힣]", line)) < 4:
                continue
            key = _bp_key(line)
            if key and key not in seen:
                seen.add(key)
                counts[key] = counts.get(key, 0) + 1
    need = max(3, int(len(pages) * 0.2))
    return {k for k, c in counts.items() if c >= need}


def _bp_key(line: str) -> str:
    return re.sub(r"[\d\s|·.,]+", "", line)


def strip_boilerplate(pages: list[str]) -> list[str]:
    keys = _boilerplate_keys(pages)
    if not keys:
        return pages
    return [
        "\n".join(ln for ln in page.splitlines() if _bp_key(ln) not in keys or len(re.findall(r"[가-힣]", ln)) < 4)
        for page in pages
    ]


def split_sections(pages: list[str]) -> list[Section]:
    pages = strip_boilerplate(pages)
    toc = parse_toc(pages)
    if toc:
        return _sections_from_toc(pages, toc)
    return _split_by_headings(pages)


def _split_by_headings(pages: list[str]) -> list[Section]:
    """제목 패턴으로 나눈다. '■ 신청자격' 같은 소제목에는 상위 번호 제목을 붙인다.

    실제 분양 공고문은 '1. 청년 특별공급 / ■ 신청자격', '2. 신혼부부 특별공급 / ■ 신청자격'처럼
    같은 소제목이 유형마다 반복된다. 상위 제목 없이 '신청자격'만 남기면 어느 유형인지 알 수 없다.
    """
    sections: list[Section] = []
    cur_title, cur_from, last_page, buf = "머리말", 1, 1, []
    parent = ""

    def flush():
        text = "\n".join(buf).strip()
        if text:
            sections.append(
                Section(len(sections), cur_title, cur_from, last_page, text, _tags_for(cur_title, text))
            )

    for pno, page in enumerate(pages, start=1):
        for line in page.splitlines():
            title = _is_heading(line)
            if title:
                flush()
                if re.match(r"^\s*[■□◆◇●]", line):
                    full = f"{parent} › {title}" if parent else title
                else:  # 번호·장 제목이 새 상위 제목이 된다
                    num = re.match(r"^\s*(\d{1,2})\s*[.|]", line)
                    parent = f"{int(num.group(1))}. {title}" if num else title
                    full = parent
                cur_title, cur_from, last_page, buf = full, pno, pno, [line]
            else:
                buf.append(line)
                last_page = pno
    flush()
    return sections


def find_section(sections: list[Section], query: str) -> list[Section]:
    q = query.replace(" ", "")
    hits = [s for s in sections if q in s.title.replace(" ", "") or query in s.tags]
    return hits
