"""MCP 서버 (MCP Python SDK 2.x의 MCPServer, stdio).

Claude 데스크톱 앱 같은 MCP 클라이언트가 이 프로세스를 실행하고, 표준입출력(stdin/stdout)으로
JSON-RPC 메시지를 주고받는다. 그래서 stdout에 print()를 하면 프로토콜이 깨진다 → 로그는 stderr로.

작업 흐름
  check_new_notices → read_notice_pdf(모집공고문 PDF) → Claude가 요약
  → get_my_profile → Claude가 내 조건과 대조해 해당 일정 선택
  → propose_registration(검증·필터, 쓰기 없음) → confirm_registration(사람 확인 후 쓰기)

도구 설계 원칙
  1. 작게 나눈다. AI가 단계마다 판단하고, 사람이 중간에 끼어들 수 있다.
  2. 요약·자격 판단은 Claude가, 데이터 수집·검증·쓰기는 서버가. 서버에 AI API 키가 필요 없다.
  3. 캘린더 쓰기는 confirm_registration 하나뿐이고, 그 안에서 사람에게 직접 확인받는다.
  4. 삭제 도구는 없다. 되돌리기 어려운 동작은 AI에게 맡기지 않는다.
  5. AI가 넘긴 값은 믿지 않는다: 제목·링크는 원문에서 다시 가져오고, 날짜는 검증한다.
"""

from __future__ import annotations

import functools
import inspect
import logging
import sys

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError

from .config import RENT_TYPES
from .pdf_text import PdfError, find_section
from .scraper import ScrapeError
from .prompts import SUMMARY_FORMAT
from .service import NoticeService

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

mcp = MCPServer("sh-notice")

# 클라이언트에게 도구 성격을 알려주는 힌트. 읽기 도구는 바로, 쓰기 도구는 확인 후 실행하도록 판단할 수 있다.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
STATE_ONLY = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)
CALENDAR_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)

MAX_CHARS = 12_000  # 한 번에 돌려줄 PDF 텍스트 양 (대화 맥락 절약)

_service: NoticeService | None = None


def svc() -> NoticeService:
    global _service
    if _service is None:
        _service = NoticeService()
    return _service


def _as_tool_error(e: Exception) -> ToolError:
    if isinstance(e, ValidationError):
        return ToolError("; ".join(err["msg"] for err in e.errors()))
    return ToolError(str(e))


_EXPECTED = (ValidationError, ValueError, ScrapeError, PdfError)


def _explain_errors(fn):
    """예상 가능한 오류는 ToolError로 바꿔 AI가 이유를 보고 고쳐서 다시 시도할 수 있게 한다.
    그 밖의 예외는 SDK가 일반 오류로 감추고 상세는 서버 로그에만 남긴다 (내부 정보 노출 방지)."""
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def awrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except _EXPECTED as e:
                raise _as_tool_error(e) from e

        return awrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _EXPECTED as e:
            raise _as_tool_error(e) from e

    return wrapper


def _clip(text: str, limit: int = MAX_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n…(이하 {len(text) - limit}자 생략)"


# ---------------------------------------------------------------- 공고 찾기·읽기


@mcp.tool(annotations=READ_ONLY)
@_explain_errors
def list_boards() -> dict:
    """지금 조회 중인 공고(프로필 [watch])와 설정할 수 있는 값을 보여준다.

    보는 범위를 바꾸려면 update_my_profile을 쓴다. 예: 행복주택만 → {"watch.sale": false, "watch.rent": true,
    "watch.rent_types": ["행복주택"]}. rent_types를 비우면 임대 전체.
    """
    watched = svc().watched()
    return {
        "watching": [{"board_id": b.id, "name": b.name, "include_words": list(b.include_words),
                      "exclude_words": list(b.exclude_words)} for b in watched],
        "settings": {"watch.sale": "주택분양 true/false", "watch.rent": "주택임대 true/false",
                     "watch.rent_types": list(RENT_TYPES), "watch.include_words": "제목에 하나라도 있어야",
                     "watch.exclude_words": "제목에 있으면 제외"},
    }


@mcp.tool(annotations=STATE_ONLY)
@_explain_errors
def check_new_notices(board_id: str | None = None) -> dict:
    """게시판을 확인해 아직 처리하지 않은 새 모집공고 목록을 돌려준다.

    board_id를 생략하면 모든 게시판을 확인한다. 처음 실행한 게시판은 현재 글들을 기준선으로
    저장만 하고(baseline_created=true) 새 글로 보고하지 않는다.
    """
    return svc().check_new([board_id] if board_id else None)


@mcp.tool(annotations=READ_ONLY)
@_explain_errors
def find_notices(keyword: str | None = None, board_id: str | None = None) -> dict:
    """지금 게시판에 올라와 있는 모집공고를 제목 키워드로 찾는다 (새 글이 아니어도 된다).

    사용자가 '고덕강일 공고 요약해 줘', '마곡 17단지', '51차 장기전세'처럼 이름으로 말하면 이 도구로
    board_id와 seq를 찾는다. 키워드는 띄어쓰기를 무시하고, 여러 단어면 모두 포함한 제목만 찾는다.
    keyword를 비우면 최근 공고 목록 전체. 결과가 여러 개면 사용자에게 어느 공고인지 묻는다.
    """
    return svc().find_notices(keyword, board_id)


@mcp.tool(annotations=READ_ONLY)
@_explain_errors
def recent_notices(board_id: str | None = None, per_board: int = 5) -> dict:
    """'최근 뜬 공고 있어?' 같은 질문에 쓴다. 게시판마다 최근 모집공고와 접수 상태를 돌려준다.

    status는 게시글 본문 일정으로 판단한다: 접수 중 / 접수 예정 / 접수 마감 / 확인 필요.
    - 접수 중·예정: 제목·접수 기간을 알려주고 요약(PDF 분석)을 원하는지 묻는다.
    - 접수 마감: PDF를 읽지 말고 schedule로 '이런 일정이 있었다'만 한두 줄. 사용자가 원할 때만 요약한다.
    - 확인 필요: 본문에 일정이 없다는 뜻. 원하면 read_notice_pdf로 공급일정을 확인한다.
    새 글 여부(check_new_notices)와 무관하게 지금 게시판에 있는 공고를 본다. 기록을 바꾸지 않는다.
    """
    return svc().recent_notices(board_id, per_board)


@mcp.tool(annotations=READ_ONLY)
@_explain_errors
def get_notice(board_id: str, seq: str) -> dict:
    """게시글 본문(요약 안내문)과 첨부파일 목록을 가져온다. 공고의 세부 내용은 read_notice_pdf로 읽는다."""
    d = svc().detail(board_id, seq).to_dict()
    d["body"] = _clip(d["body"], 6000)
    return d


@mcp.tool(annotations=READ_ONLY)
@_explain_errors
def read_notice_pdf(
    board_id: str,
    seq: str,
    section: str | None = None,
    file_seq: str | None = None,
) -> dict:
    """첨부파일 중 '입주자 모집공고문' PDF를 골라 읽는다. 위임장·동의서·팸플릿 같은 서식은 건너뛴다.

    section을 생략하면 목차(섹션 제목·페이지·분량·태그)와 핵심 섹션 본문을 돌려준다.
    핵심 섹션 태그: 요약(표지·입주자격 기준 요약), 공급일정, 신청자격, 소득·자산, 공급대상, 순위·배점, 특별공급.
    섹션은 공고문 목차(CONTENTS) 기준이며, 페이지 번호는 PDF 쪽 번호다.
    notable_rules: 청약 전략에 영향을 주는 특이 규칙(특별공급+일반공급 동시 신청, 부부 각각 청약, 특별공급 간 중복 불가 등)의
    원문 문장과 쪽. 요약의 '청약 전략·특이점'에 반드시 넣는다.
    section에 섹션 번호('3')·제목 일부('신청자격')·태그를 주면 그 섹션 본문을 돌려준다.
    모집공고문을 확신할 수 없으면 status='need_choice'와 후보를 돌려준다. 이때는 사용자에게
    어떤 파일인지 물은 뒤 file_seq를 지정해 다시 호출한다.
    """
    doc = svc().notice_document(board_id, seq, file_seq)
    if doc["status"] != "ok":
        return doc
    sections = doc.pop("sections")
    doc.pop("_key", None)
    # 136쪽 공고문은 섹션이 100개가 넘는다 → 목차는 한 줄 문자열로 짧게
    doc["outline"] = [
        f"{s.index}. p.{s.page_from}-{s.page_to} {s.title} ({len(s.text)}자)" + (f" [{','.join(s.tags)}]" if s.tags else "")
        for s in sections
    ]

    if section is None:
        key = [s for s in sections if s.tags]
        budget, picked = MAX_CHARS, []
        priority = ["요약", "공급일정", "신청자격", "소득·자산"]

        def rank(s):
            return (min((priority.index(t) for t in s.tags if t in priority), default=len(priority)), s.index)

        read_next = []
        for i, s in enumerate(sorted(key, key=rank)):
            # 섹션을 중간에 자르면 자격 조건이 반쯤 잘려 오판할 수 있다 → 통째로 들어갈 때만 넣는다.
            # (가장 중요한 첫 섹션이 너무 길면 그것만은 잘라서라도 준다.)
            if len(s.text) <= budget or i == 0:
                picked.append({"index": s.index, "title": s.title, "pages": f"{s.page_from}-{s.page_to}",
                               "tags": s.tags, "text": _clip(s.text, budget)})
                budget -= len(picked[-1]["text"])
            else:
                read_next.append({"section": str(s.index), "title": s.title,
                                  "pages": f"{s.page_from}-{s.page_to}", "tags": s.tags})
        doc["key_sections"] = picked
        doc["read_next"] = read_next
        doc["hint"] = ("read_next의 섹션은 section 인자로 이어서 읽으세요. 자격 판단 전에는 신청자격·소득·자산 섹션을 "
                       "반드시 읽고, 근거를 말할 때는 페이지를 함께 인용하세요.")
        return doc

    if section.isdigit():
        hits = [s for s in sections if s.index == int(section)]
    else:
        hits = find_section(sections, section)
    if not hits:
        raise ValueError(f"'{section}' 섹션을 찾지 못했습니다. outline의 index나 title을 쓰세요.")
    doc.pop("notable_rules", None)  # 섹션을 이어 읽을 때는 반복하지 않는다
    text = "\n\n".join(f"[{s.index}. {s.title} · p.{s.page_from}-{s.page_to}]\n{s.text}" for s in hits)
    doc["section_text"] = _clip(text)
    return doc


@mcp.tool(annotations=READ_ONLY)
@_explain_errors
def extract_schedule(board_id: str, seq: str, include_past: bool = False) -> dict:
    """게시글 본문에서 규칙 기반으로 일정 후보(접수·발표·서류제출 등)를 뽑는다. 캘린더에는 쓰지 않는다.

    오늘 이전에 끝난 일정은 candidates가 아니라 skipped에 들어간다.
    공고문 PDF의 공급일정 섹션과 비교해 빠지거나 다른 일정이 없는지 확인하는 데 쓴다.
    """
    p = svc().propose_events(board_id, seq, include_past=include_past)
    return {
        "title": p["notice"].title,
        "url": p["notice"].url,
        "candidates": [
            {"label": e.label, "start": e.start.isoformat(), "end": e.end.isoformat(),
             "all_day": e.all_day, "already_added": svc().store.has_event(e.dedup_key)}
            for e in p["events"]
        ],
        "skipped": p["skipped"],
    }


@mcp.tool(annotations=READ_ONLY)
@_explain_errors
def get_housing_costs(board_id: str, seq: str) -> dict:
    """공고문 가격표를 서버가 직접 파싱·계산한 평형별 비용표. 요약표의 금액은 반드시 이 숫자를 쓴다.

    분양: 대표 타입(청약 신청 단위)별 세대수, 평균·최저·최고 분양가, 평균 발코니 확장비와 합계,
          대출 가능액(loan_man), 최소 필요 현금(min_cash_man), 취득세 추정(acquisition_tax, 생애최초 감면 반영),
          취득세 포함 최소 필요 현금(min_cash_with_tax_man), 월 대출 상환액(monthly_loan_won),
          토지임대부면 월 토지임대료와 월 총부담(monthly_total_won), '최대 보증금 전환 시'(max_conversion)의
          보증금·월 토지임대료·최소 필요 현금·월 총부담, 그리고 내 가용 현금 기준 전환(my_conversion):
          보증금 = 가용 현금 − 최소 필요 현금(전환 단위 내림, 최대 한도까지), 전환 후 월 토지임대료·월 총부담·남는 현금.
    장기전세: 행마다 전세금·전용면적·호수·공급구분·단지명 매칭용 문맥(context), 전세대출 가능액·최소 필요 현금·월 이자.
    그 밖의 임대(행복주택 등)는 아직 가격표 형식을 모르므로 kind='알 수 없음'을 돌려준다 → read_notice_pdf로 표를 읽는다.
    by_term[만기].by_rate[금리]: 만기(30년·공고 최장) × 금리(내 예상 금리, 공고 상단)별 월 이자·원금·총부담·내 현금 전환 시 총부담.
    best_case: 공고 하단 금리일 때 월 총부담.
    기준 금리는 프로필 loan.expected_rate_pct(내 예상 금리)가 있으면 그것, 없으면 공고 범위 상단(보수적).
    loan_basis: 대출 계산 근거. from_notice=true면 공고문 조건, false면 일반 기준(추정) — 요약에 반드시 어느 쪽인지 적는다.
    분양의 일반 기준은 내 조건의 생애최초 여부로 LTV가 달라지고, 전세는 소득·신혼 여부로 대출 상품이 정해진다.
    직접 계산하거나 반올림하지 말고 이 숫자를 그대로 쓴다. 없으면 '공고문에 없음'으로 쓴다.
    """
    return svc().housing_costs(board_id, seq)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
@_explain_errors
def get_my_profile() -> dict:
    """사용자의 '내 조건' 파일(거주·무주택·세대·소득·자산·청약통장·희망지역)을 읽는다.

    이 파일은 사용자 컴퓨터에만 있다. missing은 '모름'인 항목이다(0과 다르다). 추측하지 말고 묻는다.
    derived에 나이·혼인 기간(개월)·신혼 7년 해당 여부·서울 거주 연수가 계산되어 있으니 직접 계산하지 말고 쓴다.
    자격 판단은 참고용이며, 최종 자격은 공고문 원문·청약홈에서 확인해야 한다고 함께 알린다.
    """
    return svc().profile()


@mcp.tool(annotations=READ_ONLY)
@_explain_errors
def check_profile_gaps(board_id: str, seq: str) -> dict:
    """이 공고의 지원자격·소득자산 조건·배점 판단에 필요한데 내 조건에 비어 있는 항목을 찾는다.

    공고 요약을 보여주기 전에 호출한다. questions가 있으면 요약 전에 사용자에게 한 번에 묻는다
    (항목마다 공고문 근거 why를 함께 보여주고, '모름'으로 넘어가도 된다고 알린다).
    답을 받으면 update_my_profile로 저장한다.
    """
    return svc().profile_gaps(board_id, seq)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True,
                                      open_world_hint=False))
@_explain_errors
async def update_my_profile(changes: dict[str, str | int | float | bool | list[str]], reason: str, ctx: Context) -> dict:
    """사용자가 말한 내 조건을 프로필 파일에 저장한다. 저장 전에 사용자에게 직접 확인받는다.

    changes 키는 '섹션.항목' 형식이다. 예: {"basic.marital_status": "기혼", "basic.marriage_date": "2021-06-12",
    "income_assets.monthly_household_income": 500, "savings_account.payments": 120, "loan.expected_rate_pct": 2.5}
    가능한 항목과 뜻은 get_my_profile의 fields에 있다. 금액은 만원, 날짜는 YYYY-MM-DD.
    사용자가 실제로 말한 값만 넣는다. 추측한 값은 넣지 않는다. reason에는 어떤 말을 근거로 했는지 쓴다.
    macOS에서는 Mac 화면에 '이전 값 → 새 값' 목록이 뜨고, 사용자가 체크한 항목만 저장된다.
    """
    return await svc().update_profile(changes, reason, ctx)


# ---------------------------------------------------------------- 확인 후 등록


class ScheduleItem(BaseModel):
    label: str = Field(description="일정 이름. 예: '1순위 청약접수', '당첨자 발표'")
    start: str = Field(description="종일이면 'YYYY-MM-DD', 시각이 있으면 'YYYY-MM-DDTHH:MM'")
    end: str | None = Field(default=None, description="생략하면 종일은 같은 날, 시각 일정은 1시간")


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True))
@_explain_errors
def propose_registration(board_id: str, seq: str, items: list[ScheduleItem], summary: str) -> dict:
    """사용자에게 해당하는 일정만 골라 등록 플랜을 만든다. 이 단계에서는 캘린더에 쓰지 않는다.

    summary에는 사용자가 확인 창에서 읽을 2~4줄 요약을 쓴다: 공고 핵심, 사용자의 해당 여부와
    그 근거(공고문 페이지), 왜 이 일정들을 골랐는지.
    서버가 형식 오류·오늘 이전 일정·이미 등록한 일정을 걸러 rejected로 알려준다.
    또 날짜마다 게시글 본문과 공고문 PDF에서 같은 날짜가 적힌 줄을 찾아 evidence(출처·문장)로 붙이고,
    원문에서 찾을 수 없는 날짜는 '원문 근거 없음'으로 거부한다. warnings(시각 불일치 등)가 있으면
    사용자에게 알리고 날짜·시각을 다시 확인한다.
    결과(근거 포함)를 사용자에게 보여준 뒤 confirm_registration(plan_id)을 호출한다.
    """
    return svc().propose_registration(board_id, seq, [i.model_dump() for i in items], summary)


@mcp.tool(annotations=CALENDAR_WRITE)
@_explain_errors
async def confirm_registration(plan_id: str, ctx: Context) -> dict:
    """플랜의 일정을 사용자에게 직접 확인받고, 사용자가 고른 일정만 캘린더에 등록한다.

    macOS에서는 Mac 화면에 선택 창이 뜨고, 사용자가 체크한 일정만 등록된다(AI가 대신 누를 수 없음).
    사용자가 취소하면 아무것도 등록하지 않는다. 결과의 confirmed_by로 확인 방식을 알 수 있다.
    """
    return await svc().confirm_registration(plan_id, ctx)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True))
@_explain_errors
def record_application(board_id: str, seq: str, supply_type: str, stage: str = "접수",
                       items: list[ScheduleItem] | None = None) -> dict:
    """청약 진행 단계를 기록하고, 그 단계에서 챙길 일정으로 등록 플랜을 만든다. 캘린더에는 아직 쓰지 않는다.

    stage='접수': 사용자가 '접수했어'라고 하면. 발표 일정만 넣는다(당첨자 발표, 장기전세는 서류심사대상자 발표 포함).
                  이때 접수번호도 물어 save_receipt_numbers로 저장해 두면, 발표 후 check_my_results로 당첨 여부를 찾을 수 있다.
                  서류 제출·계약은 당첨자만 하는 일이라 이 단계에서는 넣지 않는다.
    stage='당첨': '당첨됐어', '서류심사대상자 됐어'라고 하면. 서류 제출·계약·사전점검·입주 일정을 넣는다.
    supply_type: 접수한 유형. 예: '생애최초 특별공급', '일반공급 1순위'. 모르면 물어본다.
    items를 생략하면 게시글 본문에서 자동으로 찾는다. 본문에 없으면(next 안내) 공고문 PDF 공급일정 섹션에서 찾아
    items로 다시 호출한다. 결과를 보여준 뒤 confirm_registration(plan_id)으로 사용자 확인을 받아 등록한다.
    """
    return svc().record_application(board_id, seq, supply_type,
                                    [i.model_dump() for i in items] if items else None, stage)


class ReceiptItem(BaseModel):
    receipt_no: str = Field(description="접수번호 (숫자). 청약 완료 화면·문자에 나오는 번호")
    applicant: str = Field("본인", description="누구 것인지: '본인', '배우자' 등")
    supply_type: str | None = Field(None, description="예: '생애최초 특별공급', '일반공급 1순위', '일반공급 2순위'")
    house_type: str | None = Field(None, description="신청한 주택형·타입. 예: '49D', '59E'")


@mcp.tool(annotations=STATE_ONLY)
@_explain_errors
def save_receipt_numbers(board_id: str, seq: str, receipts: list[ReceiptItem]) -> dict:
    """'접수했어'라고 할 때 접수번호를 저장한다. 나중에 당첨자 명단에서 이 번호로 결과를 찾는다.

    접수번호를 모르면 사용자에게 묻는다(청약 완료 화면·문자에 있음). 부부가 각각 넣었으면 applicant로 구분한다.
    번호는 이 컴퓨터의 로컬 DB에만 저장된다. 캘린더 발표 일정은 record_application(stage='접수')로 따로 제안한다.
    """
    return svc().save_receipts(board_id, seq, [r.model_dump() for r in receipts])


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True,
                                      open_world_hint=True))
@_explain_errors
def check_my_results(board_id: str | None = None, seq: str | None = None) -> dict:
    """'당첨됐는지 봐줘', '발표 났어?'에 쓴다. 저장한 접수번호를 SH 당첨자·예비입주자 명단 PDF에서 찾는다.

    발표 글(제목에 '당첨자 발표' 또는 '당첨자 확정 명단')을 찾아 명단을 읽고, 접수번호마다
    당첨(주택형·신청구분) / 예비(순번) / 명단에 없음 / 확인 불가를 돌려준다. 발표 전이면 status='not_yet'.
    결과에는 반드시 official_check 안내를 함께 전한다(최종 확인은 SH 시스템·ARS).
    '명단에 없음'은 낙첨일 가능성이 높다는 뜻이지 확정이 아니므로 단정하지 않는다.
    당첨이면 record_application(stage='당첨')으로 서류 제출·계약 일정을 제안한다. 당첨하한선 PDF가 있으면
    other_attachments에 있으니, 사용자가 원하면 read_notice_pdf로 읽는다(board_id는 'sale', file_seq 지정).
    """
    return svc().check_results(board_id, seq)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
@_explain_errors
def my_receipts() -> list[dict]:
    """저장된 접수번호와 마지막 조회 결과."""
    return svc().my_receipts()


@mcp.tool(annotations=STATE_ONLY)
@_explain_errors
def mark_notice_processed(board_id: str, seq: str) -> str:
    """공고 처리를 마쳤다고 표시한다(등록했든, 해당 없어서 건너뛰었든). 이후 check_new_notices에 다시 나오지 않는다."""
    svc().mark_processed(board_id, seq)
    return "ok"


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
@_explain_errors
def recent_calendar_events(limit: int = 20) -> list[dict]:
    """이 서버가 최근 캘린더에 등록한 일정 기록."""
    return svc().store.recent_events(limit)




@mcp.prompt()
def summarize_notice(board_id: str, seq: str) -> str:
    """특정 공고를 정해진 형식으로 요약한다 (자격표·비용표·대출·일정)."""
    return (
        f"SH 공고 board_id={board_id}, seq={seq}를 요약해 줘.\n"
        "1) read_notice_pdf로 공고문을 읽고(신청자격·소득·자산·선정방식 섹션은 section으로 끝까지),\n"
        "2) check_profile_gaps로 빠진 내 조건을 확인해 있으면 요약 전에 한 번에 물어보고 update_my_profile로 저장,\n"
        "3) get_my_profile, get_housing_costs, extract_schedule을 호출한 뒤,\n"
        "4) 아래 형식으로 요약해.\n\n" + SUMMARY_FORMAT
    )


@mcp.prompt()
def process_new_notices() -> str:
    """새 공고를 확인하고, 공고문을 요약하고, 내 조건에 맞는 일정만 확인받아 등록하는 표준 순서."""
    return (
        "SH 새 모집공고를 처리해 줘. 순서는 이렇게 해.\n"
        "1) check_new_notices로 새 공고를 확인해. 없으면 그렇다고만 말해.\n"
        "2) 공고마다 read_notice_pdf로 모집공고문을 읽어(필요한 섹션은 section으로 더 읽어). "
        "need_choice면 나에게 어떤 파일인지 물어봐.\n"
        "3) 요약하기 전에 check_profile_gaps를 호출해. questions가 있으면 요약을 보여주기 전에 나에게 한 번에 물어봐 "
        "(항목마다 공고문 근거를 함께, '모름'으로 넘어가도 된다고). 답은 update_my_profile로 저장해.\n"
        "4) get_my_profile(derived 값을 그대로 써)과 get_housing_costs를 호출하고, 아래 '요약 형식'대로 공고를 요약해. "
        "여전히 모르는 항목 때문에 판단이 갈리면 그렇다고 표시해.\n"
        "5) 해당하거나 애매하면, 내가 신청할 유형·순위에 맞는 오늘 이후 일정만 골라 propose_registration으로 플랜을 만들어. "
        "특별공급은 추천한 한 가지만 넣어(접수 일정까지만. 발표는 내가 '접수했어'라고 하면, 서류·계약은 '당첨됐어'라고 하면 record_application으로). "
        "extract_schedule 결과와 PDF 일정이 다르면 알려줘.\n"
        "6) 플랜의 일정마다 서버가 찾은 원문 근거(evidence)를 함께 보여주고, '원문 근거 없음'으로 거부된 일정이나 "
        "warnings가 있으면 공고문을 다시 확인해 고쳐. 그다음 confirm_registration으로 내 확인을 받아 등록해.\n"
        "7) 끝난 공고는 mark_notice_processed로 표시하고, 공고별 결과를 짧게 정리해 줘.\n"
        "자격 판단은 참고용이라는 점과, 최종 확인은 공고문 원문·청약홈에서 해야 한다는 점을 마지막에 한 줄로 알려줘.\n\n"
        "[요약 형식]\n" + SUMMARY_FORMAT
    )


def main():
    mcp.run()


if __name__ == "__main__":
    main()
