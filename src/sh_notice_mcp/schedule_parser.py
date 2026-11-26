"""공고 본문에서 일정(접수·발표·서류제출 등)을 뽑아내는 규칙 기반 파서.

왜 LLM만 쓰지 않고 규칙 파서를 두었나?
  - SH 공고 본문은 '■ 항목: 2026. 9. 14.(월) 10:00 ~ ...' 처럼 형식이 꽤 일정하다.
  - 규칙 파서는 결정적(같은 입력 → 같은 출력)이고, 테스트 가능하고, 비용이 없다.
  - LLM은 규칙이 못 잡는 예외 문장을 보완하는 용도로 쓰고, 결과는 models.CalendarEvent 검증을 거친다.

지원하는 표기 예:
  2026. 9. 14.(월) 10:00 ~ 2026. 9. 15.(화) 17:00
  2026. 9. 16.(수) 10:00 ~ 17:00
  2026.10.26.(월) 10:00 ~ 10.27.(화) 17:00
  2026.11.30.(월) ~ 12.09.(수)
  2026. 10. 14.(수) 16:00 이후
  2026. 08. 31.(월)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

_WD = r"(?:\s*\([월화수목금토일]\))?"
_FULL = re.compile(
    r"(?P<y>20\d{2})\s*\.\s*(?P<m>\d{1,2})\s*\.\s*(?P<d>\d{1,2})\s*\.?" + _WD
)
_PARTIAL = re.compile(r"(?P<m>\d{1,2})\s*\.\s*(?P<d>\d{1,2})\s*\.?" + _WD)
_TIME = re.compile(r"\s*(?P<hh>\d{1,2}):(?P<mm>\d{2})")
_TILDE = re.compile(r"\s*[~∼～]\s*")
_MARKERS = "■□○●◎◇◆-–·※▶☞*"
_HEADER_MARKERS = {"■", "□"}  # set으로 비교: "" in "■□" 는 True라서 문자열 비교는 버그

# 이 단어가 라벨에 있어야 일정으로 인정 (본문 속 잡다한 날짜를 걸러낸다)
KEYWORDS = ("접수", "신청", "발표", "제출", "계약", "청약", "순위", "공급", "공고일", "입주", "추첨")


@dataclass
class ParsedSchedule:
    label: str
    start: datetime
    end: datetime
    all_day: bool

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "all_day": self.all_day,
        }


def _strip_marker(line: str) -> tuple[str, str]:
    s = line.strip()
    marker = ""
    while s and s[0] in _MARKERS:
        marker = marker or s[0]
        s = s[1:].lstrip()
    return marker, s


def _clean_label(text: str) -> str:
    text = text.strip().rstrip(":：").strip()
    return " ".join(text.split())


def _parse_range(s: str, pos: int) -> tuple[datetime, datetime, bool] | None:
    """s[pos:]에서 '시작 [시각] [~ 끝]'을 읽는다."""
    m = _FULL.match(s, pos)
    if not m:
        return None
    sd = date(int(m["y"]), int(m["m"]), int(m["d"]))
    pos = m.end()
    st: time | None = None
    if tm := _TIME.match(s, pos):
        st = time(int(tm["hh"]), int(tm["mm"]))
        pos = tm.end()

    ed, et = sd, None
    if tl := _TILDE.match(s, pos):
        p = tl.end()
        if em := _FULL.match(s, p):
            ed = date(int(em["y"]), int(em["m"]), int(em["d"]))
            p = em.end()
        elif (em := _PARTIAL.match(s, p)) and not _TIME.match(s, p):
            # 연도가 생략된 끝 날짜: 12.09. → 시작 연도를 쓰되, 월이 작아지면 다음 해
            y = sd.year + (1 if int(em["m"]) < sd.month else 0)
            ed = date(y, int(em["m"]), int(em["d"]))
            p = em.end()
        if etm := _TIME.match(s, p):
            et = time(int(etm["hh"]), int(etm["mm"]))

    if st is None and et is None:  # 시각 정보 없음 → 종일 일정
        return datetime.combine(sd, time()), datetime.combine(ed, time()), True
    start = datetime.combine(sd, st or time(9, 0))
    end = datetime.combine(ed, et) if et else start + timedelta(hours=1)
    return start, end, False


def extract_schedules(body: str) -> list[ParsedSchedule]:
    section = ""  # 가장 최근의 ■ 제목
    group = ""  # ○/- 로 시작하는 짧은 소제목 (예: '인터넷접수', '일반공급')
    results: list[ParsedSchedule] = []
    seen: set[tuple[str, date]] = set()

    for raw in body.splitlines():
        marker, line = _strip_marker(raw)
        if not line:
            continue
        m = _FULL.search(line)

        if m is None:
            if marker in _HEADER_MARKERS:
                section, group = _clean_label(line), ""
            elif len(line) <= 20 and not line.endswith(("다.", "다")):
                group = _clean_label(line)
            continue

        prefix = line[: m.start()].strip()
        # '라벨: 날짜' 또는 '[1순위] 날짜' 형태만 일정으로 인정.
        # '(2026. 10. 21.자 우체국 소인...)' 같은 문장 속 날짜는 버린다.
        if prefix.endswith((":", "：")):
            own = _clean_label(prefix)
        elif re.fullmatch(r"\[[^\]]+\]", prefix):
            own = prefix
        else:
            continue

        if marker in _HEADER_MARKERS:
            section, group = own, ""
            parts = [own]
        else:
            parts = [p for p in (section, group, own) if p]
        label = " · ".join(dict.fromkeys(parts))  # 순서 유지 중복 제거

        if not any(k in label for k in KEYWORDS):
            continue
        parsed = _parse_range(line, m.start())
        if parsed is None:
            continue
        start, end, all_day = parsed
        key = (label, start.date())
        if end < start or key in seen:
            continue
        seen.add(key)
        results.append(ParsedSchedule(label=label, start=start, end=end, all_day=all_day))

    return results
