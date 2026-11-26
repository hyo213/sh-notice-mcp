"""비즈니스 로직 계층.

MCP 서버(AI가 호출)와 CLI(스케줄러가 호출)가 같은 로직을 공유하도록 여기 모았다.
MCP 도구는 이 클래스의 메서드를 얇게 감싸기만 한다.
"""

from __future__ import annotations

import json
import re
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from .attachments import select_notice_pdf
from .calendar_backends import CalendarBackend, get_backend
from .config import BOARDS, RESULT_POSTS, Board, data_dir, watched_boards
from .confirm import Chooser, Confirmer, ConfirmResult, default_chooser, default_confirmer, describe
from .grounding import Evidence, SourceIndex
from .models import CalendarEvent
from .notable import find_notable_rules
from .pdf_text import MAX_PDF_BYTES, PdfError, Section, extract_pages, split_sections
from . import costs
from .profile import gaps_for_notice, load_profile, update_profile, validate_change
from .schedule_parser import extract_schedules
from .status import STAGES, application_status, is_followup
from .scraper import NoticeDetail, ScrapeError, SHClient
from .store import Store
from . import results as res

PLAN_TTL_SEC = 60 * 60

log = logging.getLogger(__name__)


def short_title(title: str, limit: int = 40) -> str:
    return title if len(title) <= limit else title[: limit - 1] + "…"


def parse_when(value: str) -> tuple[datetime, bool]:
    """'2026-09-14' → (자정, 종일=True), '2026-09-14T10:00' → (그 시각, False)."""
    v = value.strip()
    if "T" in v or " " in v:
        return datetime.fromisoformat(v.replace(" ", "T")), False
    return datetime.combine(date.fromisoformat(v), datetime.min.time()), True


class NoticeService:
    def __init__(
        self,
        client: SHClient | None = None,
        store: Store | None = None,
        backend: CalendarBackend | None = None,
        base_dir: Path | None = None,
        now: Callable[[], datetime] | None = None,
        confirmer: Confirmer | None = None,
        chooser: Chooser | None = None,
    ):
        base = base_dir or data_dir()
        self.client = client or SHClient()
        self.store = store or Store(base / "state.db")
        self._backend = backend
        self._base = base
        self._now = now or datetime.now  # 테스트에서 '오늘'을 고정할 수 있도록 주입
        self._confirmer = confirmer or default_confirmer
        self._chooser = chooser or default_chooser
        self._plans: dict[str, dict] = {}
        self._docs: dict[tuple[str, str, str], list[Section]] = {}
        self._pages: dict[tuple[str, str, str], list[str]] = {}

    @property
    def backend(self) -> CalendarBackend:
        if self._backend is None:  # 실제로 쓸 때 생성 (macOS가 아니어도 서버는 뜨도록)
            self._backend = get_backend(self._base)
        return self._backend

    def is_upcoming(self, end: datetime) -> bool:
        """오늘 이후 일정인지. 기준은 '끝나는 날'이 오늘이거나 그 이후인가.

        시작일이 아니라 종료일로 보는 이유: 서류제출 10/19~10/21처럼 이미 시작했지만
        아직 끝나지 않은 기간은 오늘 기준으로 여전히 챙겨야 하는 일정이기 때문이다.
        """
        return end.date() >= self._now().date()

    def watched(self) -> list[Board]:
        """프로필 [watch]에 따라 지금 조회할 게시판들 (설정이 없으면 분양·장기전세)."""
        try:
            data = load_profile(self._base, self._now().date())["profile"]
        except ValueError:  # 프로필 형식 오류여도 공고 조회는 기본값으로 계속
            data = {}
        return watched_boards(data)

    def watched_ids(self) -> list[str]:
        return [b.id for b in self.watched()]

    def board(self, board_id: str) -> Board:
        """board_id → Board. 감시 중이면 프로필의 제목 필터까지 적용된 것, 아니면 기본 정의."""
        for b in self.watched():
            if b.id == board_id:
                return b
        if board_id not in BOARDS:
            raise ValueError(f"알 수 없는 게시판: {board_id}. 가능한 값: {', '.join(BOARDS)}")
        return BOARDS[board_id]

    # ------------------------------------------------------------ 새 글 감지
    def check_new(self, board_ids: list[str] | None = None) -> dict:
        """게시판 1페이지를 읽어 처음 보는 글과 아직 처리 안 된 글을 돌려준다.

        첫 실행(기록이 전혀 없는 게시판)에서는 지금 있는 글을 '기준선'으로 저장만 하고
        새 글로 취급하지 않는다. 그러지 않으면 과거 공고 수십 개가 한꺼번에 캘린더에 들어간다.
        """
        result: dict = {"boards": {}}
        for bid in board_ids or self.watched_ids():
            b = self.board(bid)
            items = [it for it in self.client.fetch_list(b) if not b.is_excluded(it.title, it.department)]
            first_run = not self.store.is_initialized(bid)
            known = self.store.known_seqs(bid)
            new = []
            for it in items:
                if it.seq in known:
                    continue
                self.store.remember(bid, it.seq, it.title, it.posted_at, processed=first_run)
                if not first_run:
                    new.append(it.to_dict())
            if first_run:
                self.store.mark_initialized(bid)
            pending = self.store.unprocessed_seqs(bid) - {n["seq"] for n in new}
            for it in items:
                if it.seq in pending:
                    new.append(it.to_dict())
            result["boards"][bid] = {
                "name": b.name,
                "baseline_created": first_run,
                "checked": len(items),
                "new_notices": new,
            }
        return result

    def find_notices(self, keyword: str | None = None, board_id: str | None = None) -> dict:
        """게시판 1페이지(검색어별)의 공고를 제목 키워드로 찾는다. 새 글 여부와 상관없이 지금 올라와 있는 공고."""
        words = [w for w in (keyword or "").replace(",", " ").split() if w]
        out = []
        for bid in [board_id] if board_id else self.watched_ids():
            b = self.board(bid)
            for it in self.client.fetch_list(b):
                if b.is_excluded(it.title, it.department):
                    continue
                compact = it.title.replace(" ", "")
                if all(w.replace(" ", "") in compact for w in words):
                    out.append({"board_id": bid, "board": b.name, **it.to_dict()})
        return {"keyword": keyword or "", "count": len(out), "notices": out}

    def recent_notices(self, board_id: str | None = None, per_board: int = 5) -> dict:
        """게시판마다 최근 모집공고 per_board개와 접수 상태(본문 일정 기준, PDF는 읽지 않음).

        status: 접수 예정 / 접수 중 / 접수 마감 / 확인 필요(본문에 접수 일정 없음).
        마감된 공고는 PDF를 읽지 않고 '이런 일정이 있었다'만 알려주기 위한 도구.
        """
        now = self._now()
        out = []
        for bid in [board_id] if board_id else self.watched_ids():
            b = self.board(bid)
            items = [it for it in self.client.fetch_list(b) if not b.is_excluded(it.title, it.department)]
            for it in items[:per_board]:
                row = {"board_id": bid, "board": b.name, **it.to_dict()}
                try:
                    sch = extract_schedules(self.detail(bid, it.seq).body)
                except ScrapeError as e:
                    out.append({**row, "status": "확인 필요", "reason": str(e)})
                    continue
                row.update(application_status(sch, now))
                out.append(row)
        order = {"접수 중": 0, "접수 예정": 1, "확인 필요": 2, "접수 마감": 3}
        out.sort(key=lambda r: r.get("posted_at", ""), reverse=True)  # 같은 상태 안에서는 최신 공고 먼저
        out.sort(key=lambda r: order.get(r["status"], 9))
        return {
            "today": now.date().isoformat(),
            "notices": out,
            "how_to_answer": "접수 중·예정 공고만 자세히 안내하고(요약을 원하는지 물어봄), 접수 마감 공고는 "
                             "PDF를 읽지 말고 제목과 schedule(있었던 일정)만 한두 줄로 알려준다. "
                             "마감 공고라도 사용자가 원하면 그때 요약한다.",
        }

    # ------------------------------------------------------------ 상세 / 일정 추출
    def detail(self, board_id: str, seq: str) -> NoticeDetail:
        return self.client.fetch_detail(self.board(board_id), seq)

    def propose_events(
        self, board_id: str, seq: str, include_past: bool = False, now: datetime | None = None
    ) -> dict:
        d = self.detail(board_id, seq)
        today = (now or self._now()).date()
        events, skipped = [], []
        for s in extract_schedules(d.body):
            if not include_past and s.end.date() < today:
                skipped.append({**s.to_dict(), "reason": "이미 지난 일정"})
                continue
            try:
                ev = self.make_event(d, s.label, s.start, s.end, s.all_day)
            except ValueError as e:
                skipped.append({**s.to_dict(), "reason": str(e)})
                continue
            events.append(ev)
        return {"notice": d, "events": events, "skipped": skipped}

    @staticmethod
    def make_event(
        d: NoticeDetail, label: str, start, end, all_day: bool, evidence: list[Evidence] | None = None
    ) -> CalendarEvent:
        basis = ""
        if evidence:
            basis = "\n근거:\n" + "\n".join(f"- [{e.source}] {e.quote}" for e in evidence)
        return CalendarEvent(
            title=f"[SH] {short_title(d.title)} · {label}",
            start=start,
            end=end,
            all_day=all_day,
            notes=f"{label}\n공고: {d.title}\n원문: {d.url}{basis}\n(자동 등록: sh-notice-mcp)",
            url=d.url,
            board_id=d.board_id,
            seq=d.seq,
            label=label,
            evidence=evidence[0].source if evidence else "",
        )

    # ------------------------------------------------------------ 캘린더 등록
    def add_event(self, ev: CalendarEvent, dry_run: bool = False) -> dict:
        # 등록 직전 마지막 관문. 규칙 파서 경로든 AI가 직접 넘긴 경로든 모두 여기를 지난다.
        if not self.is_upcoming(ev.end):
            return {
                "status": "skipped_past",
                "title": ev.title,
                "start": ev.start.isoformat(),
                "reason": f"오늘({self._now():%Y-%m-%d}) 이전에 끝난 일정이라 등록하지 않았습니다.",
            }
        if self.store.has_event(ev.dedup_key):
            return {"status": "skipped_duplicate", "title": ev.title}
        if dry_run:
            return {"status": "dry_run", "title": ev.title, "start": ev.start.isoformat()}
        ref = self.backend.add_event(ev)
        self.store.record_event(
            ev.dedup_key, ev.board_id, ev.seq, ev.title, ev.start.isoformat(), self.backend.name
        )
        return {"status": "added", "title": ev.title, "start": ev.start.isoformat(), "ref": ref}

    # ------------------------------------------------------------ 공고문 PDF
    def notice_document(self, board_id: str, seq: str, file_seq: str | None = None) -> dict:
        """모집공고문 PDF를 골라 읽고 섹션으로 나눈다. 같은 파일은 디스크에 캐시해 다시 받지 않는다."""
        d = self.detail(board_id, seq)
        sel = select_notice_pdf(d.attachments)
        if file_seq:
            match = [a for a in d.attachments if a.file_seq == str(file_seq)]
            if not match:
                raise ValueError(f"file_seq={file_seq} 첨부파일이 없습니다.")
            att, reason = match[0], "직접 지정한 파일입니다."
        elif sel.chosen is None:
            return {"notice": {"title": d.title, "url": d.url}, "status": "need_choice",
                    "reason": sel.reason, "candidates": [a.to_dict() for a in sel.candidates]}
        else:
            att, reason = sel.chosen, sel.reason

        key = (board_id, seq, att.file_seq)
        if key not in self._docs:
            cache = self._base / "pdf" / f"{board_id}_{seq}_{att.file_seq}.json"
            if cache.exists():
                pages = json.loads(cache.read_text(encoding="utf-8"))
            else:
                pages = extract_pages(self.client.fetch_file(att, MAX_PDF_BYTES))
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
            self._pages[key] = pages
            self._docs[key] = split_sections(pages)
        sections = self._docs[key]
        return {
            "notice": {"title": d.title, "url": d.url},
            "status": "ok",
            "file": {**att.to_dict(), "why_this_file": reason},
            "other_attachments": [a.name for a in d.attachments if a.file_seq != att.file_seq],
            "pages": max((s.page_to for s in sections), default=0),
            "sections": sections,
            "notable_rules": find_notable_rules(self._pages[key]),
            "_key": key,
        }

    def _sources(self, board_id: str, seq: str, d: NoticeDetail) -> tuple[list[tuple[str, str]], str]:
        """날짜 근거를 찾을 원문: 게시글 본문 + (가능하면) 모집공고문 PDF 각 쪽."""
        docs = [("게시글 본문", d.body)]
        try:
            doc = self.notice_document(board_id, seq)
        except (ScrapeError, PdfError, ValueError) as e:
            return docs, f"공고문 PDF를 읽지 못해 게시글 본문에서만 확인했습니다: {e}"
        if doc["status"] != "ok":
            return docs, f"공고문 PDF를 고르지 못해 게시글 본문에서만 확인했습니다: {doc['reason']}"
        pages = self._pages[doc["_key"]]
        docs += [(f"공고문 p.{i}", text) for i, text in enumerate(pages, start=1)]
        return docs, f"게시글 본문과 공고문 PDF {len(pages)}쪽에서 확인했습니다."

    # ------------------------------------------------------------ 확인 후 등록
    def propose_registration(self, board_id: str, seq: str, items: list[dict], summary: str = "") -> dict:
        """등록할 일정 묶음(플랜)을 만든다. 이 단계에서는 캘린더에 아무것도 쓰지 않는다.

        여기서 걸러내는 것: 형식이 틀린 날짜, 검증 실패, 오늘 이전에 끝난 일정, 이미 등록한 일정,
        그리고 원문(게시글 본문·공고문 PDF)에서 찾을 수 없는 날짜.
        """
        self._gc_plans()
        d = self.detail(board_id, seq)
        docs, source_note = self._sources(board_id, seq, d)
        index = SourceIndex.build(docs)
        ready, rejected, grounds = [], [], []
        for it in items:
            label = str(it.get("label", "")).strip()
            try:
                s, all_day = parse_when(str(it["start"]))
                e = parse_when(str(it["end"]))[0] if it.get("end") else (s if all_day else s + timedelta(hours=1))
                ev = self.make_event(d, label, s, e, all_day)
            except (KeyError, ValueError, ValidationError) as err:
                msg = "; ".join(x["msg"] for x in err.errors()) if isinstance(err, ValidationError) else str(err)
                rejected.append({"item": it, "reason": msg})
                continue
            if not self.is_upcoming(ev.end):
                rejected.append({"item": it, "reason": "오늘 이전에 끝난 일정"})
            elif self.store.has_event(ev.dedup_key):
                rejected.append({"item": it, "reason": "이미 등록된 일정"})
            else:
                evidence, warnings = index.ground(label, ev.start, ev.end, ev.all_day)
                if not evidence:
                    why = warnings[0] if warnings else f"{ev.start:%Y-%m-%d}을 원문에서 찾지 못했습니다."
                    rejected.append({"item": it, "reason": f"원문 근거 없음: {why} 공고에 있는 날짜인지 다시 확인하세요."})
                    continue
                grounded = self.make_event(d, label, ev.start, ev.end, ev.all_day, evidence)
                if warnings:  # Mac 확인 창에서도 보이도록 표시
                    grounded = grounded.model_copy(update={"evidence": f"⚠ {grounded.evidence} 확인 필요"})
                ready.append(grounded)
                grounds.append({"evidence": [e.to_dict() for e in evidence], "warnings": warnings})
        plan_id = secrets.token_hex(4)
        if ready:
            self._plans[plan_id] = {"created": time.time(), "notice": d, "events": ready, "summary": summary}
        return {
            "plan_id": plan_id if ready else None,
            "notice": d.title,
            "to_register": [{"no": i + 1, "label": e.label, "start": e.start.isoformat(),
                             "end": e.end.isoformat(), "all_day": e.all_day, "line": describe(e), **g}
                            for i, (e, g) in enumerate(zip(ready, grounds))],
            "rejected": rejected,
            "checked_sources": source_note,
            "next": "confirm_registration(plan_id)을 호출하면 사용자에게 최종 확인을 받은 뒤 등록합니다."
                    if ready else "등록할 일정이 없습니다.",
        }

    async def confirm_registration(self, plan_id: str, ctx=None) -> dict:
        self._gc_plans()
        plan = self._plans.pop(plan_id, None)
        if plan is None:
            raise ValueError("플랜이 없거나 만료되었습니다 (1시간). propose_registration부터 다시 하세요.")
        events: list[CalendarEvent] = plan["events"]
        title = f"[SH] {short_title(plan['notice'].title, 60)}"
        res: ConfirmResult = await self._confirmer(ctx, title, plan["summary"], events)
        results = []
        for i, ev in enumerate(events):
            if i in res.approved:
                results.append(self.add_event(ev))
            else:
                results.append({"status": "not_approved", "title": ev.title})
        return {
            "confirmed_by": res.method,
            "note": res.note,
            "approved": len(res.approved),
            "results": results,
        }

    def _gc_plans(self):
        now = time.time()
        for k in [k for k, v in self._plans.items() if now - v["created"] > PLAN_TTL_SEC]:
            self._plans.pop(k, None)

    def profile(self) -> dict:
        return load_profile(self._base, self._now().date())

    async def update_profile(self, changes: dict, reason: str = "", ctx=None) -> dict:
        """내 조건을 바꾼다. 무엇을 바꿀지 사람에게 직접 확인받고, 체크한 항목만 저장한다.

        Claude가 대화를 잘못 알아들어 조건을 바꾸면 이후 모든 자격 판단이 틀어진다.
        그래서 캘린더 등록과 같은 확인 창을 거친다.
        """
        if not changes:
            raise ValueError("바꿀 항목이 없습니다.")
        clean = {k: validate_change(k, v) for k, v in changes.items()}
        current = self.profile()["profile"]
        items = []
        for k, v in clean.items():
            sec, key = k.split(".")
            old = current.get(sec, {}).get(key, "(모름)")
            items.append(f"{k}: {old} → {v}")
        prompt = "내 조건을 다음과 같이 저장할까요? 저장할 항목만 고르세요."
        if reason:
            prompt += f"\n\n(이유: {reason})"
        res = await self._chooser(ctx, "SH 공고 → 내 조건 저장", prompt, "선택한 항목 저장", items)
        approved = {k: v for i, (k, v) in enumerate(clean.items()) if i in res.approved}
        if approved:
            update_profile(self._base, approved)
        return {"confirmed_by": res.method, "note": res.note, "saved": approved,
                "not_saved": [k for k in clean if k not in approved]}

    def housing_costs(self, board_id: str, seq: str, prof: dict | None = None) -> dict:
        """공고문 가격표에서 평형별 비용을 계산한다(계산은 costs.py). 모든 숫자에 근거가 붙는다.

        prof를 주면 프로필 파일 대신 그 조건으로 계산한다 (원격 서버는 프로필을 저장하지 않는다).
        """
        doc = self.notice_document(board_id, seq)
        if doc["status"] != "ok":
            return {"status": doc["status"], "reason": doc["reason"], "candidates": doc["candidates"]}
        return costs.housing_costs(self._pages[doc["_key"]], doc["notice"]["title"],
                                   prof if prof is not None else self.profile(), self._now().date())

    def record_application(self, board_id: str, seq: str, supply_type: str,
                           items: list[dict] | None = None, stage: str = "접수") -> dict:
        """진행 단계를 기록하고, 그 단계에서 챙길 일정으로 등록 플랜을 만든다.

        stage='접수': '접수했어' → 발표 일정만 (당첨자 발표, 서류심사대상자 발표).
        stage='당첨': '당첨됐어' / '서류심사대상자 됐어' → 서류 제출·계약·사전점검·입주.
        items를 주지 않으면 게시글 본문에서 찾는다. 등록은 confirm_registration에서 사람이 확인한 뒤에만 한다.
        """
        if stage not in STAGES:
            raise ValueError(f"stage는 {', '.join(STAGES)} 중 하나여야 합니다.")
        d = self.detail(board_id, seq)
        self.store.record_application(board_id, seq, f"{supply_type} · {stage}", d.title)
        if items is None:
            items = []
            for s in extract_schedules(d.body):
                if not is_followup(s.label, supply_type, stage):
                    continue
                if s.all_day:
                    items.append({"label": s.label, "start": s.start.date().isoformat(),
                                  "end": s.end.date().isoformat()})
                else:
                    items.append({"label": s.label, "start": s.start.isoformat(timespec="minutes"),
                                  "end": s.end.isoformat(timespec="minutes")})
        plan = self.propose_registration(
            board_id, seq, items, summary=f"{supply_type} {stage} 완료. 이 단계에서 챙길 일정: {STAGES[stage]}.")
        plan["stage"] = {"supply_type": supply_type, "stage": stage, "title": d.title,
                         "next_stage": "당첨되면 stage='당첨'으로 다시 호출해 서류 제출·계약 일정을 넣는다."
                         if stage == "접수" else None}
        if not items:
            plan["next"] = (f"본문에서 '{stage}' 단계 일정({STAGES[stage]})을 찾지 못했습니다. read_notice_pdf로 "
                            "공급일정 섹션을 읽고 items로 넘기세요.")
        return plan

    # ------------------------------------------------------------ 접수번호와 당첨 조회
    def save_receipts(self, board_id: str, seq: str, receipts: list[dict]) -> dict:
        """'접수했어'와 함께 받은 접수번호를 저장한다 (내 컴퓨터의 DB에만). 캘린더와는 관계없다."""
        if not receipts:
            raise ValueError("receipts가 비어 있습니다.")
        d = self.detail(board_id, seq)
        saved = []
        for r in receipts:
            no = re.sub(r"\D", "", str(r.get("receipt_no", "")))
            if not 5 <= len(no) <= 8:
                raise ValueError(f"접수번호는 숫자 5~8자리여야 합니다: {r.get('receipt_no')!r}")
            applicant = (r.get("applicant") or "본인").strip()
            self.store.save_receipt(no, board_id, seq, d.title, applicant,
                                    r.get("supply_type"), r.get("house_type"))
            saved.append({"receipt_no": no, "applicant": applicant, "supply_type": r.get("supply_type"),
                          "house_type": r.get("house_type")})
        return {"notice": d.title, "saved": saved,
                "next": "당첨자 발표 글이 올라오면 check_my_results로 명단에서 이 번호를 찾는다."}

    def my_receipts(self) -> list[dict]:
        out = []
        for r in self.store.receipts():
            r = dict(r)
            r["result_detail"] = json.loads(r.pop("result_json")) if r.get("result_json") else None
            out.append(r)
        return out

    def result_posts(self, notice_title: str) -> list[dict]:
        """이 공고의 당첨자 발표·확정 명단 글 (최신 글 먼저)."""
        # '당첨자'로 찾은 1페이지는 다른 공고 발표로 금방 밀리므로, 단지 이름('고덕강일')으로도 찾아 합친다.
        words = res.notice_key(notice_title).split()
        place = words[0].removesuffix("지구") if words else ""
        board = replace(RESULT_POSTS, search_words=("당첨자", place) if len(place) >= 2 else ("당첨자",))
        posts = self.client.fetch_list(board)
        return [p.to_dict() for p in posts if res.is_result_post_for(p.title, notice_title)]

    def _result_pages(self, seq: str, att) -> list[str]:
        cache = self._base / "pdf" / f"results_{seq}_{att.file_seq}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        pages = extract_pages(self.client.fetch_file(att, MAX_PDF_BYTES))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
        return pages

    def check_results(self, board_id: str | None = None, seq: str | None = None) -> dict:
        """저장한 접수번호를 당첨자·예비입주자 명단에서 찾는다. 공고를 주면 그 공고만."""
        mine = self.store.receipts(board_id, seq)
        if not mine:
            return {"status": "no_receipts",
                    "message": "저장된 접수번호가 없습니다. '접수했어'라고 할 때 접수번호를 받아 save_receipt_numbers로 저장하세요."}
        by_notice: dict[tuple[str, str], list[dict]] = {}
        for r in mine:
            by_notice.setdefault((r["board_id"], r["seq"]), []).append(r)
        report = [self._check_notice(rows[0]["title"], rows, save=True) for rows in by_notice.values()]
        return {"results": report, "official_check": res.OFFICIAL_CHECK,
                "next": "당첨이면 record_application(stage='당첨')으로 서류 제출·계약 일정을 제안한다."}

    def check_results_for(self, board_id: str, seq: str, receipt_numbers: list[str]) -> dict:
        """저장하지 않고, 넘겨받은 접수번호만 이 공고의 명단에서 찾는다 (원격 서버용)."""
        nos = []
        for x in receipt_numbers:
            no = re.sub(r"\D", "", str(x))
            if not 5 <= len(no) <= 8:
                raise ValueError(f"접수번호는 숫자 5~8자리여야 합니다: {x!r}")
            nos.append(no)
        if not nos:
            raise ValueError("receipt_numbers가 비어 있습니다.")
        d = self.detail(board_id, seq)
        rows = [{"receipt_no": n, "applicant": None, "supply_type": None, "house_type": None} for n in nos]
        return {**self._check_notice(d.title, rows, save=False), "official_check": res.OFFICIAL_CHECK}

    def _check_notice(self, title: str, rows: list[dict], save: bool) -> dict:
        posts = self.result_posts(title)
        entry = {"notice": title, "result_posts": posts, "receipts": []}
        if not posts:
            entry["status"] = "not_yet"
            entry["message"] = "아직 이 공고의 당첨자 발표 글이 없습니다. 공고문의 발표일(시각) 이후 다시 확인하세요."
            entry["receipts"] = [{"receipt_no": r["receipt_no"], "applicant": r["applicant"],
                                  "supply_type": r["supply_type"], "house_type": r["house_type"]} for r in rows]
            return entry
        post = posts[0]  # 가장 최근 글 (확정 명단이 있으면 그것)
        d = self.client.fetch_detail(RESULT_POSTS, post["seq"])
        lists = res.result_lists(d.attachments)
        found: dict[str, list[dict]] = {"당첨": [], "예비": []}
        read, problems = [], []
        for kind, att in lists:
            try:
                found[kind] += res.parse_result_list(self._result_pages(post["seq"], att))
                read.append(att.name)
            except (ScrapeError, PdfError) as e:
                problems.append(f"{att.name}: {e}")
        notes = [ln.strip() for ln in d.body.splitlines() if "예비" in ln and any(c.isdigit() for c in ln)]
        entry.update({"status": "checked", "checked_post": {"title": d.title, "url": d.url, "posted_at": post.get("posted_at"),
                                                            "reserve_notes": notes[:10]},
                      "lists_read": read, "list_sizes": {k: len(v) for k, v in found.items()},
                      "other_attachments": [a.name for a in d.attachments if not res.list_kind(a.name)]})
        if problems:
            entry["problems"] = problems
        if not lists:
            entry["status"] = "no_lists"
            entry["message"] = "발표 글에 명단 PDF가 없습니다. 본문 안내대로 SH인터넷청약시스템·ARS에서 확인하세요."
        for r in rows:
            j = res.judge(r["receipt_no"], found) if lists else {"result": "확인 불가"}
            if save:
                self.store.set_receipt_result(r["receipt_no"], j["result"], json.dumps(j, ensure_ascii=False))
            entry["receipts"].append({"receipt_no": r["receipt_no"], "applicant": r["applicant"],
                                      "supply_type": r["supply_type"], "house_type": r["house_type"], **j})
        return entry

    def profile_gaps(self, board_id: str, seq: str) -> dict:
        """이 공고의 자격·조건·배점 판단에 필요한데 내 조건에 비어 있는 항목."""
        d = self.detail(board_id, seq)
        docs, note = self._sources(board_id, seq, d)
        gaps = gaps_for_notice(self.profile(), docs)
        return {"notice": d.title, "checked_sources": note, "ready": not gaps, "questions": gaps}

    def mark_processed(self, board_id: str, seq: str):
        self.board(board_id)
        self.store.mark_processed(board_id, seq)

    # ------------------------------------------------------------ 한 번에 확인 (CLI용)
    def scan(self) -> list[dict]:
        """새 공고를 찾아 '읽을 PDF'와 '본문 일정 후보'를 정리한다. 캘린더에는 쓰지 않는다.

        사용자가 요청한 흐름(공고문 요약 → 내 조건 확인 → 사람의 최종 확인 → 등록)에서
        자동 등록은 맞지 않으므로, cron 실행은 '알려주기'까지만 한다.
        """
        report = []
        checked = self.check_new()
        for bid, info in checked["boards"].items():
            if info["baseline_created"]:
                report.append({"board": bid, "baseline_created": True, "count": info["checked"]})
            for n in info["new_notices"]:
                try:
                    d = self.detail(bid, n["seq"])
                    sel = select_notice_pdf(d.attachments)
                    proposal = self.propose_events(bid, n["seq"])
                    report.append({
                        "board": bid, "seq": n["seq"], "title": n["title"], "url": d.url,
                        "notice_pdf": sel.chosen.name if sel.chosen else None,
                        "pdf_note": sel.reason,
                        "upcoming_schedules": [describe(e) for e in proposal["events"]],
                    })
                except (ScrapeError, PdfError, ValueError) as e:
                    log.exception("처리 실패: %s %s", bid, n["seq"])
                    report.append({"board": bid, "title": n["title"], "error": str(e)})
        return report
