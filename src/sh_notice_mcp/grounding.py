"""AI가 고른 일정 날짜를 원문에서 확인한다 (grounding).

Claude가 공고문을 잘못 읽거나(9/14 → 9/15) 공고에 없는 날짜를 만들어도, 형식만 맞으면
캘린더에 들어갈 수 있다. 그래서 플랜을 만들 때 날짜마다 원문(게시글 본문, 공고문 PDF 각 쪽)에서
같은 날짜가 실제로 적힌 줄을 찾아 근거로 붙이고, 못 찾으면 등록 후보에서 뺀다.

원문 날짜 표기 (실제 SH 공고에서 확인한 것)
  2026. 9. 14.(월)   2026.09.14.   2026-09-14   2026년 9월 14일
  ’26.09.14.(월)     '26.10.19.             ← 공고문 표지 일정표
  2026.10.26.(월) 10:00 ~ 10.27.(화) 17:00   ← 범위의 끝은 연도 생략
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

_FULL = re.compile(
    r"(?:(?P<y4>20\d{2})|[’‘'`](?P<y2>\d{2}))\s*[.\-/년]\s*(?P<m>\d{1,2})\s*[.\-/월]\s*(?P<d>\d{1,2})(?!\d)"
)
# 범위의 끝: "~ 10.27." / "~09.15." (앞에 나온 전체 날짜의 연도를 이어받는다)
_SHORT_END = re.compile(r"[~∼～]\s*(?P<m>\d{1,2})\s*\.\s*(?P<d>\d{1,2})\s*\.")
_TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def dates_in_line(line: str) -> set[date]:
    found: set[date] = set()
    last_year = None
    last_month = None
    events = sorted(
        [(m.start(), "full", m) for m in _FULL.finditer(line)]
        + [(m.start(), "short", m) for m in _SHORT_END.finditer(line)],
        key=lambda x: x[0],
    )
    for _, kind, m in events:
        if kind == "full":
            y = int(m["y4"]) if m["y4"] else 2000 + int(m["y2"])
            d = _mk(y, int(m["m"]), int(m["d"]))
            if d:
                found.add(d)
                last_year, last_month = d.year, d.month
        elif last_year:
            mo = int(m["m"])
            y = last_year + (1 if last_month and mo < last_month else 0)
            d = _mk(y, mo, int(m["d"]))
            if d:
                found.add(d)
    return found


@dataclass
class Evidence:
    source: str  # "게시글 본문" 또는 "공고문 p.9"
    quote: str

    def short(self) -> str:
        return self.source

    def to_dict(self) -> dict:
        return {"source": self.source, "quote": self.quote}


@dataclass
class _Line:
    source: str
    text: str
    dates: set[date]


@dataclass
class SourceIndex:
    lines: list[_Line] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, docs: list[tuple[str, str]]) -> "SourceIndex":
        idx = cls()
        for source, text in docs:
            idx.sources.append(source)
            for raw in text.splitlines():
                line = " ".join(raw.split())
                if not line:
                    continue
                ds = dates_in_line(line)
                if ds:
                    idx.lines.append(_Line(source, line, ds))
        return idx

    def ground(self, label: str, start: datetime, end: datetime, all_day: bool) -> tuple[list[Evidence], list[str]]:
        """(근거 목록, 경고 목록). 근거가 비어 있으면 원문에서 날짜를 찾지 못한 것."""
        d1, d2 = start.date(), end.date()
        with_start = [ln for ln in self.lines if d1 in ln.dates]
        if not with_start:
            return [], []
        if d2 != d1 and not any(d2 in ln.dates for ln in self.lines):
            return [], [f"종료일 {d2:%Y-%m-%d}을 원문에서 찾지 못했습니다."]

        words = [w for w in re.split(r"[^\w가-힣]+", label) if len(w) >= 2]

        def score(ln: _Line) -> tuple:
            return (
                d2 in ln.dates,  # 시작~끝이 한 줄에 있는 범위 표기
                sum(w in ln.text for w in words),  # 라벨 단어가 많이 겹칠수록
                ln.source == "게시글 본문",  # 본문은 문장이 온전해 읽기 좋다
            )

        best = sorted(with_start, key=score, reverse=True)[:2]
        warnings = []
        # 날짜는 원문에 있지만 다른 일정의 날짜일 수 있다 (예: '당첨자 발표'에 모집공고일을 붙임).
        # 근거 문장(과 같은 출처의 바로 윗줄 제목)에 라벨 단어가 하나도 없으면 알린다.
        if words and not any(w in ln.text for ln in best for w in words):
            warnings.append("근거 문장에 일정 이름의 단어가 없습니다. 다른 일정의 날짜가 아닌지 확인하세요.")
        if not all_day:
            hhmm = f"{start.hour}:{start.minute:02d}"
            times = {f"{int(h)}:{mm}" for ln in best for h, mm in _TIME.findall(ln.text)}
            if times and hhmm not in times:
                warnings.append(f"시작 시각 {hhmm}이 근거 문장의 시각({', '.join(sorted(times))})과 다릅니다.")
        return [Evidence(ln.source, _clip(ln.text)) for ln in best], warnings


def _clip(s: str, n: int = 140) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"
