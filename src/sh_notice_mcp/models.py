"""캘린더 일정 모델 + 검증.

일정은 두 경로로 만들어진다.
  1) 규칙 기반 파서(schedule_parser)가 본문에서 뽑은 것
  2) LLM(Claude)이 본문을 읽고 MCP 도구 인자로 넘긴 것
어느 쪽이든 캘린더에 쓰기 전에 같은 검증을 통과해야 한다.
특히 2)는 모델이 날짜를 잘못 읽거나 지어낼 수 있으므로, 도구 쪽에서 막는 게 핵심이다.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field, model_validator

MAX_EVENT_SPAN = timedelta(days=60)


class CalendarEvent(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    start: datetime
    end: datetime
    all_day: bool = False
    notes: str = Field(default="", max_length=4000)
    url: str = ""
    # 어떤 공고에서 나온 일정인지 (중복 방지 키에 쓰인다)
    board_id: str = ""
    seq: str = ""
    label: str = ""
    # 원문 근거 출처 (예: "공고문 p.9"). 중복 판단 키에는 포함하지 않는다.
    evidence: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _check(self) -> "CalendarEvent":
        if self.end < self.start:
            raise ValueError(f"종료({self.end})가 시작({self.start})보다 빠릅니다.")
        if self.end - self.start > MAX_EVENT_SPAN:
            raise ValueError("일정 기간이 60일을 넘습니다. 날짜를 잘못 읽었을 가능성이 큽니다.")
        this_year = date.today().year
        if not (this_year - 1 <= self.start.year <= this_year + 2):
            raise ValueError(f"연도({self.start.year})가 허용 범위를 벗어났습니다.")
        if self.url and not self.url.startswith("https://www.i-sh.co.kr/"):
            raise ValueError("url은 SH 공식 사이트 주소만 허용합니다.")
        return self

    @property
    def dedup_key(self) -> str:
        """같은 공고의 같은 일정은 몇 번 실행해도 한 번만 등록되도록 하는 키 (멱등성)."""
        raw = f"{self.board_id}|{self.seq}|{self.label or self.title}|{self.start.isoformat()}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    @classmethod
    def all_day_event(cls, start: date, end: date, **kw) -> "CalendarEvent":
        return cls(
            start=datetime.combine(start, datetime.min.time()),
            end=datetime.combine(end, datetime.min.time()),
            all_day=True,
            **kw,
        )
