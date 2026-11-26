"""원격 MCP 서버 (Streamable HTTP): 휴대폰 등 어디서나 claude.ai '사용자 지정 커넥터'로 쓰기 위한 버전.

맥 버전(server.py)과 다른 점
  - 개인정보를 저장하지 않는다. 프로필·캘린더·접수번호 저장 도구가 없다.
    내 조건(가용 현금 등)과 접수번호는 대화에서 그때그때 인자로 받는다.
  - 캘린더에 쓰지 않는다(맥 확인 창을 띄울 수 없으므로). 일정은 extract_schedule로 알려주기만 한다.
  - 모든 요청에 토큰이 필요하다: 'Authorization: Bearer <SH_REMOTE_TOKEN>'.
    claude.ai 커넥터의 'Request headers'에 같은 값을 넣는다. 토큰 없이는 서버가 뜨지 않는다.
  - 공고문 PDF 텍스트 캐시는 SH_NOTICE_DATA_DIR(기본 /tmp/sh-notice)에만 둔다.

실행
  SH_REMOTE_TOKEN=... sh-notice-remote            # 0.0.0.0:$PORT(기본 8080)/mcp
"""

from __future__ import annotations

import hmac
import logging
import os
import sys

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from . import server as local
from .config import RENT_TYPES, watched_boards
from .service import NoticeService

log = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)

remote = MCPServer(
    "sh-notice",
    instructions=(
        "SH(서울주택도시개발공사) 모집공고 조회·요약용 도구. 개인정보를 저장하지 않으므로 사용자의 조건"
        "(가용 현금, 생애최초 여부, 예상 금리 등)과 접수번호는 대화에서 받은 값을 그때그때 인자로 넘긴다. "
        "캘린더 등록 기능은 없다. 금액은 get_housing_costs의 숫자만 쓰고 직접 계산하지 않는다. "
        "자격 판단과 당첨 조회 결과는 참고용이며, 최종 확인은 공고문 원문과 SH 청약시스템에서 하도록 안내한다."
    ),
)

# 맥 버전의 읽기 전용 도구를 그대로 쓴다 (같은 함수, 같은 설명). 서비스 객체만 원격용으로 바꿔 끼운다.
for fn in (local.find_notices, local.recent_notices, local.get_notice, local.read_notice_pdf, local.extract_schedule):
    remote.add_tool(fn, annotations=READ_ONLY)


@remote.tool(annotations=READ_ONLY)
@local._explain_errors
def list_types() -> dict:
    """조회할 수 있는 공고 유형. find_notices/recent_notices의 board_id로 쓴다 (예: 'sale', 'rent_jeonse', 'rent_happy')."""
    return {
        "board_ids": {"sale": "주택분양 모집공고", "rent_all": "주택임대 모집공고 전체",
                      **{bid: name for name, (bid, _) in RENT_TYPES.items()}},
        "default": [b.id for b in watched_boards({})],
        "note": "board_id를 생략하면 default(주택분양 + 주택임대 전체)를 본다.",
    }


@remote.tool(annotations=READ_ONLY)
@local._explain_errors
def get_housing_costs(
    board_id: str,
    seq: str,
    available_cash_man: int | None = Field(None, description="가용 현금(만원). 모르면 생략"),
    first_home_buyer: bool = Field(False, description="생애최초(세대원 전원 주택 소유 이력 없음)"),
    expected_rate_pct: float | None = Field(None, description="예상 대출 금리(연 %). 생략하면 공고 범위 상단"),
    monthly_household_income_man: int | None = Field(None, description="세대 월소득(만원, 전세대출 상품 판단용)"),
    newlywed_within_7y: bool = Field(False, description="혼인 7년 이내 (신혼부부 전세대출 판단용)"),
) -> dict:
    """공고문 가격표를 서버가 파싱·계산한 평형별 비용표. 요약표의 금액은 반드시 이 숫자를 쓴다.

    맥 버전과 같은 계산이다 (분양: 대출·필요 현금·취득세·월 부담·토지임대료 보증금 전환, 장기전세: 전세대출·필요 현금).
    내 조건은 저장하지 않으므로 사용자가 말한 값을 인자로 넘긴다. 모르는 값은 생략하고 '확인이 필요한 것'에 적는다.
    """
    prof = {
        "profile": {
            "basic": {"first_home_buyer": first_home_buyer},
            "income_assets": {k: v for k, v in (("available_cash", available_cash_man),
                                                 ("monthly_household_income", monthly_household_income_man))
                              if v is not None},
            "loan": {"expected_rate_pct": expected_rate_pct} if expected_rate_pct is not None else {},
        },
        "derived": {"newlywed_within_7y": newlywed_within_7y},
    }
    return local.svc().housing_costs(board_id, seq, prof)


@remote.tool(annotations=READ_ONLY)
@local._explain_errors
def check_results(
    board_id: str,
    seq: str,
    receipt_numbers: list[str] = Field(description="확인할 접수번호들 (숫자). 저장하지 않는다"),
) -> dict:
    """'당첨됐는지 봐줘'에 쓴다. 이 공고의 당첨자 발표 글에서 당첨자·예비입주자 명단 PDF를 읽어 접수번호를 찾는다.

    board_id·seq는 모집공고(발표 글이 아니라 원래 공고)다. 접수번호는 저장하지 않으니 대화에서 받은 값을 넘긴다.
    결과: 당첨 / 예비(순번) / 명단에 없음 / 확인 불가, 발표 전이면 status='not_yet'.
    official_check 안내를 반드시 함께 전하고, '명단에 없음'은 낙첨 확정이 아니라 가능성이 높다는 뜻으로 말한다.
    """
    return local.svc().check_results_for(board_id, seq, receipt_numbers)


class BearerAuth:
    """모든 요청에 'Authorization: Bearer <토큰>'을 요구하는 ASGI 미들웨어. 비교는 상수 시간으로."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            got = headers.get(b"authorization", b"")
            ok = got.startswith(b"Bearer ") and hmac.compare_digest(got[7:].strip(), self.token)
            if not ok:
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                        (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


def build_app(token: str):
    if len(token) < 24:
        raise SystemExit("SH_REMOTE_TOKEN은 24자 이상이어야 합니다 (예: python -c 'import secrets;print(secrets.token_urlsafe(32))').")
    os.environ.setdefault("SH_NOTICE_DATA_DIR", "/tmp/sh-notice")
    local._service = NoticeService()  # 맥 도구 함수들이 쓰는 svc()를 원격용 서비스로
    app = remote.streamable_http_app(
        stateless_http=True,
        json_response=True,
        host="0.0.0.0",
        # 공개 호스트에서 돌므로 Host 검사는 끄고, 대신 토큰으로 막는다.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return BearerAuth(app, token)


def main():
    import uvicorn

    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    token = os.environ.get("SH_REMOTE_TOKEN", "")
    if not token:
        raise SystemExit("SH_REMOTE_TOKEN 환경변수가 필요합니다. 토큰 없이 공개 서버를 띄우지 않습니다.")
    uvicorn.run(build_app(token), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), log_level="info")


if __name__ == "__main__":
    main()
