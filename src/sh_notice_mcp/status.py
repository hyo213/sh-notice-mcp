"""게시글 본문 일정으로 '접수 상태'와 '단계별로 챙길 일정'을 판단한다 (AI 없이, 규칙으로)."""

from __future__ import annotations

from datetime import datetime

_APPLY_WORDS = ("접수", "신청", "청약")
_NOT_APPLY = ("발표", "서류", "계약", "당첨", "입주", "공고일", "포기", "결과", "가능여부")
# 단계별로 챙길 일정. 접수했다고 서류제출·계약까지 넣으면, 떨어진 사람의 캘린더가 쓸모없는 일정으로 찬다.
STAGES = {
    "접수": "발표 일정만 (당첨자 발표, 서류심사대상자 발표)",
    "당첨": "당첨자(또는 서류심사대상자)가 할 일 (서류 제출, 계약, 사전점검, 입주)",
}
_WIN_WORDS = ("서류", "계약", "사전점검", "입주")


def is_application_window(label: str) -> bool:
    return any(w in label for w in _APPLY_WORDS) and not any(w in label for w in _NOT_APPLY)


def is_followup(label: str, supply_type: str = "", stage: str = "접수") -> bool:
    """이 단계에서 챙길 일정인가. 사전청약 당첨자 전용 일정은 사전청약으로 신청한 경우만.

    접수: 발표 일정만. 당첨: 서류 제출·계약·사전점검·입주 (발표 일정 제외).
    """
    if stage not in STAGES:
        raise ValueError(f"stage는 {', '.join(STAGES)} 중 하나여야 합니다.")
    if "사전청약" in label and "사전청약" not in supply_type:
        return False
    if is_application_window(label) or "공고일" in label:
        return False
    is_announce = "발표" in label
    if stage == "접수":
        return is_announce
    return not is_announce and any(w in label for w in _WIN_WORDS)


def application_status(schedules, now: datetime) -> dict:
    """본문 일정으로 접수 상태를 판단한다."""
    windows = [s for s in schedules if is_application_window(s.label)]
    sched = [{"label": s.label, "date": s.start.date().isoformat()
              + ("" if s.end.date() == s.start.date() else f" ~ {s.end.date().isoformat()}")} for s in schedules]
    upcoming = [x for x, s in zip(sched, schedules) if s.end.date() >= now.date()]
    if not windows:
        return {"status": "확인 필요", "reason": "게시글 본문에 접수 일정이 없습니다. 공고문 PDF에서 확인해야 합니다.",
                "schedule": sched, "upcoming": upcoming}
    start = min(s.start for s in windows)
    end = max(s.end for s in windows)
    if now.date() > end.date():
        status = "접수 마감"
    elif now < start:
        status = "접수 예정"
    else:
        status = "접수 중"
    return {"status": status, "apply_from": start.isoformat(timespec="minutes"),
            "apply_to": end.isoformat(timespec="minutes"), "schedule": sched, "upcoming": upcoming}
