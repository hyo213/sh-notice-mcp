"""캘린더 등록 전 '사람의 최종 확인'.

왜 별도 모듈인가?
  Claude 앱에도 도구 실행 승인 창이 있지만, 사용자가 '항상 허용'을 눌러 두면 사라진다.
  또 AI가 "사용자가 확인했다"고 말하는 것만으로는 확인이 아니다.
  그래서 등록 도구가 직접 사람에게 묻고, 사람이 고른 일정만 쓴다.

확인 방식 (SH_NOTICE_CONFIRM)
  dialog : macOS 기본 대화상자(choose from list). Claude가 클릭할 수 없어 가장 확실하다. (macOS 기본값)
  elicit : MCP elicitation. 채팅 화면 안에서 묻는다. 다만 사양상 클라이언트가 AI 에이전트이면
           사람 대신 자동 응답할 수 있어서 보장 수준은 dialog보다 낮다. (macOS가 아닐 때 기본값)
  deny   : 확인 수단이 없으면 등록하지 않는다.
"""

from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import create_model

from .models import CalendarEvent


@dataclass
class ConfirmResult:
    approved: list[int]  # 승인된 일정의 인덱스
    method: str
    note: str = ""


def describe(ev: CalendarEvent) -> str:
    if ev.all_day:
        when = f"{ev.start:%m/%d}" if ev.start == ev.end else f"{ev.start:%m/%d}~{ev.end:%m/%d}"
    else:
        when = f"{ev.start:%m/%d %H:%M}"
        if ev.end.date() != ev.start.date():
            when += f"~{ev.end:%m/%d %H:%M}"
    text = f"{when}  {ev.label or ev.title}"
    return f"{text}  (근거: {ev.evidence})" if ev.evidence else text


# ------------------------------------------------------------------ macOS 대화상자

_DIALOG = r'''
on run argv
    set dialogTitle to item 1 of argv
    set promptText to item 2 of argv
    set okName to item 3 of argv
    set itemList to items 4 thru -1 of argv
    tell application "System Events" to activate
    tell application "System Events"
        set picked to choose from list itemList with title dialogTitle with prompt promptText ¬
            default items itemList OK button name okName cancel button name "취소" ¬
            with multiple selections allowed and empty selection allowed
    end tell
    if picked is false then return "__CANCEL__"
    set AppleScript's text item delimiters to linefeed
    return picked as text
end run
'''


def mac_choose(window: str, prompt: str, ok: str, items: list[str]) -> ConfirmResult:
    """Mac 기본 선택 창. 사람이 체크한 항목의 인덱스를 돌려준다."""
    labels = [f"{i + 1}. {t}" for i, t in enumerate(items)]
    try:
        r = subprocess.run(
            ["osascript", "-", window, prompt[:900], ok, *labels],
            input=_DIALOG, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return ConfirmResult([], "dialog", "10분 동안 응답이 없어 취소했습니다.")
    out = r.stdout.strip()
    if r.returncode != 0 or out == "__CANCEL__":
        return ConfirmResult([], "dialog", "사용자가 취소했습니다." if out == "__CANCEL__" else r.stderr.strip())
    chosen = {ln.strip() for ln in out.splitlines() if ln.strip()}
    return ConfirmResult([i for i, lb in enumerate(labels) if lb in chosen], "dialog")


# ------------------------------------------------------------------ MCP elicitation


async def elicit_choose(ctx, prompt: str, items: list[str]) -> ConfirmResult:
    # 항목마다 체크박스(bool) 하나. elicitation 스키마는 원시 타입만 허용한다.
    Model = create_model("Choose", **{f"e{i + 1}": (bool, True) for i in range(len(items))})
    for i, t in enumerate(items):
        Model.model_fields[f"e{i + 1}"].description = t
    Model.model_rebuild(force=True)
    lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(items))
    res = await ctx.elicit(f"{prompt}\n\n{lines}", Model)
    if res.action != "accept" or res.data is None:
        return ConfirmResult([], "elicit", f"사용자가 {res.action} 했습니다.")
    picked = res.data.model_dump()
    return ConfirmResult([i for i in range(len(items)) if picked.get(f"e{i + 1}")], "elicit")


# ------------------------------------------------------------------ 선택

Chooser = Callable[[object, str, str, str, list[str]], Awaitable[ConfirmResult]]
Confirmer = Callable[[object, str, str, list[CalendarEvent]], Awaitable[ConfirmResult]]


def _supports_elicitation(ctx) -> bool:
    try:
        caps = ctx.client_capabilities
        return bool(caps and getattr(caps, "elicitation", None) is not None)
    except Exception:
        return False


async def default_chooser(ctx, window: str, prompt: str, ok: str, items: list[str]) -> ConfirmResult:
    """사람에게 항목을 고르게 한다: macOS 선택 창 → elicitation → 둘 다 없으면 아무것도 고르지 않음."""
    mode = os.environ.get("SH_NOTICE_CONFIRM") or ("dialog" if platform.system() == "Darwin" else "elicit")
    if mode == "dialog" and platform.system() == "Darwin":
        return mac_choose(window, prompt, ok, items)
    if mode in ("elicit", "dialog") and ctx is not None and _supports_elicitation(ctx):
        return await elicit_choose(ctx, f"{window}\n\n{prompt}", items)
    return ConfirmResult([], "deny", "사람에게 확인할 수단이 없어 진행하지 않았습니다. (macOS 대화상자·elicitation 모두 불가)")


async def default_confirmer(ctx, title: str, summary: str, events: list[CalendarEvent]) -> ConfirmResult:
    prompt = f"{title}\n\n{summary}\n\n등록할 일정을 고르세요. (기본: 전체 선택)"
    return await default_chooser(ctx, "SH 공고 → 캘린더 등록", prompt, "선택한 일정 등록",
                                 [describe(e) for e in events])
