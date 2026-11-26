"""캘린더 쓰기 백엔드.

  - MacCalendarBackend : macOS '캘린더' 앱에 AppleScript(osascript)로 직접 추가
  - ICSBackend         : .ics 파일 생성 (어느 OS에서나 동작, 더블클릭으로 가져오기, 테스트용)

백엔드를 인터페이스로 분리해 두었기 때문에 Google Calendar API 백엔드를 추가해도
MCP 서버·파이프라인 코드는 바뀌지 않는다.

안전 설계: 이 모듈에는 '추가'만 있고 '삭제/수정'은 없다.
AI가 호출하는 도구에 되돌리기 어려운 기능은 처음부터 노출하지 않는다는 원칙.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from .models import CalendarEvent

CALENDAR_NAME = os.environ.get("SH_NOTICE_CALENDAR", "SH 공고")


class CalendarBackend(Protocol):
    name: str

    def add_event(self, ev: CalendarEvent) -> str:
        """일정을 추가하고 식별자(파일 경로나 UID)를 돌려준다."""
        ...


# ------------------------------------------------------------------ .ics


def _ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def to_ics(ev: CalendarEvent) -> str:
    if ev.all_day:
        # iCalendar의 종일 일정 DTEND는 '다음 날'(배타적)
        dtstart = f"DTSTART;VALUE=DATE:{ev.start:%Y%m%d}"
        dtend = f"DTEND;VALUE=DATE:{(ev.end + timedelta(days=1)):%Y%m%d}"
    else:
        dtstart = f"DTSTART:{ev.start:%Y%m%dT%H%M%S}"
        dtend = f"DTEND:{ev.end:%Y%m%dT%H%M%S}"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//sh-notice-mcp//KO",
        "BEGIN:VEVENT",
        f"UID:{ev.dedup_key}@sh-notice-mcp",
        dtstart,
        dtend,
        f"SUMMARY:{_ics_escape(ev.title)}",
        f"DESCRIPTION:{_ics_escape(ev.notes)}",
    ]
    if ev.url:
        lines.append(f"URL:{ev.url}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


class ICSBackend:
    name = "ics"

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def add_event(self, ev: CalendarEvent) -> str:
        safe = re.sub(r"[^\w가-힣\-]+", "_", ev.title)[:60]
        path = self.out_dir / f"{ev.start:%Y%m%d}_{safe}_{ev.dedup_key[:6]}.ics"
        path.write_text(to_ics(ev), encoding="utf-8")
        return str(path)


# ------------------------------------------------------------------ macOS Calendar

# 값은 문자열 치환이 아니라 argv로 넘긴다 → 공고 제목에 따옴표가 있어도
# AppleScript 코드가 깨지거나 주입(injection)되지 않는다.
_APPLESCRIPT = r'''
on mkDate(y, mo, d, h, mi)
    set dt to current date
    set day of dt to 1
    set year of dt to y
    set month of dt to mo
    set day of dt to d
    set hours of dt to h
    set minutes of dt to mi
    set seconds of dt to 0
    return dt
end mkDate

on run argv
    set calName to item 1 of argv
    set evTitle to item 2 of argv
    set sd to mkDate((item 3 of argv) as integer, (item 4 of argv) as integer, (item 5 of argv) as integer, (item 6 of argv) as integer, (item 7 of argv) as integer)
    set ed to mkDate((item 8 of argv) as integer, (item 9 of argv) as integer, (item 10 of argv) as integer, (item 11 of argv) as integer, (item 12 of argv) as integer)
    set isAllDay to ((item 13 of argv) is "1")
    set notesText to item 14 of argv
    set urlText to item 15 of argv
    tell application "Calendar"
        if not (exists calendar calName) then
            make new calendar with properties {name:calName}
        end if
        tell calendar calName
            set ev to make new event at end with properties {summary:evTitle, start date:sd, end date:ed, allday event:isAllDay, description:notesText, url:urlText}
        end tell
        return uid of ev
    end tell
end run
'''


class MacCalendarBackend:
    name = "mac"

    def __init__(self, calendar_name: str = CALENDAR_NAME):
        if platform.system() != "Darwin":
            raise RuntimeError("macOS 캘린더 백엔드는 macOS에서만 동작합니다. SH_NOTICE_BACKEND=ics를 쓰세요.")
        self.calendar_name = calendar_name

    def add_event(self, ev: CalendarEvent) -> str:
        end = ev.end + timedelta(days=1) if ev.all_day else ev.end
        s, e = ev.start, end
        args = [
            self.calendar_name, ev.title,
            *map(str, (s.year, s.month, s.day, s.hour, s.minute)),
            *map(str, (e.year, e.month, e.day, e.hour, e.minute)),
            "1" if ev.all_day else "0",
            ev.notes, ev.url,
        ]
        r = subprocess.run(
            ["osascript", "-", *args], input=_APPLESCRIPT, capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            raise RuntimeError(
                "캘린더 추가 실패: " + r.stderr.strip()
                + "\n(처음 실행이면 시스템 설정 > 개인정보 보호 및 보안 > 자동화/캘린더 권한을 허용하세요.)"
            )
        return r.stdout.strip()


def get_backend(data_dir: Path) -> CalendarBackend:
    choice = os.environ.get("SH_NOTICE_BACKEND", "mac" if platform.system() == "Darwin" else "ics")
    if choice == "mac":
        return MacCalendarBackend()
    return ICSBackend(data_dir / "ics")
