"""sh-notice-agent: MCP 서버를 쓰는 AI 에이전트 (에이전트 루프를 직접 구현).

  sh-notice-agent --daily            # 매일 아침용: 새 공고 없으면 아무것도 안 함(AI 호출 0회)
  sh-notice-agent "고덕강일 정리해줘"   # 아무 요청이나: 시연·학습용

에이전트 루프의 본질 (이 파일의 run_agent):
  1. MCP 서버의 도구 목록을 Claude에게 넘긴다.
  2. Claude가 "이 도구를 이 인자로 불러줘"(tool_use)라고 답하면, 이 코드가 MCP 서버에 실제로 호출한다.
  3. 결과(tool_result)를 Claude에게 돌려준다.
  4. Claude가 더 부를 도구가 없다고(end_turn) 할 때까지 2~3을 반복한다.

안전장치
  - 허용 목록(ALLOWED_TOOLS)에 있는 도구만 Claude에게 보여준다. 프로필 수정은 사람이 있을 때만(대화형 모드).
  - 최대 반복 횟수(MAX_STEPS)와 토큰 예산(MAX_TOKENS_TOTAL)을 넘으면 멈춘다.
  - 캘린더 쓰기(confirm_registration)는 서버 안에서 Mac 확인 창을 띄운다. 에이전트가 대신 누를 수 없다.
  - API 키는 코드·설정 파일에 두지 않는다: 환경변수 → macOS 키체인 순서로 읽는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime

from mcp import ClientSession, StdioServerParameters, stdio_client

from .config import data_dir

MODEL = os.environ.get("SH_AGENT_MODEL", "claude-sonnet-5-5")
MAX_STEPS = 25
MAX_TOKENS_TOTAL = 400_000  # 입력+출력 누적 상한 (비용 폭주 방지)
MAX_RESULT_CHARS = 80_000  # 도구 결과 하나를 Claude에게 넘기는 최대 길이
KEYCHAIN_SERVICE = "sh-notice-anthropic"

# 혼자 도는 모드에서 쓸 수 있는 도구. update_my_profile(내 조건 수정)은 빠져 있다.
DAILY_TOOLS = {
    "find_notices", "recent_notices", "get_notice", "read_notice_pdf", "extract_schedule",
    "get_my_profile", "check_profile_gaps", "get_housing_costs",
    "propose_registration", "confirm_registration", "mark_notice_processed",
    "check_my_results", "my_receipts",
}
INTERACTIVE_TOOLS = DAILY_TOOLS | {"update_my_profile", "record_application", "check_new_notices",
                                   "save_receipt_numbers"}

SYSTEM = (
    "너는 사용자의 SH(서울주택도시개발공사) 청약 비서다. 도구로 공고를 읽고, 사용자의 조건과 대조해 요약한다. "
    "금액은 get_housing_costs 숫자만 쓰고 직접 계산하지 않는다. 판단에는 공고문 쪽 번호를 붙인다. "
    "모르는 조건은 추측하지 않고 '확인이 필요한 것'에 적는다. 한국어로, 표를 써서 짧게."
)


# ------------------------------------------------------------------ 준비


def api_key() -> str | None:
    """환경변수 → macOS 키체인. 키를 파일에 평문으로 두지 않기 위해."""
    if k := os.environ.get("ANTHROPIC_API_KEY"):
        return k
    if platform.system() == "Darwin" and shutil.which("security"):
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def notify(title: str, text: str):
    """맥 알림 센터에 알림. 맥이 아니면 터미널에만."""
    print(f"[알림] {title}: {text}", file=sys.stderr)
    if platform.system() == "Darwin":
        script = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run'
        subprocess.run(["osascript", "-e", script, title, text], check=False)


def server_params() -> StdioServerParameters:
    """이 파이썬과 같은 환경에서 MCP 서버를 하위 프로세스로 띄운다."""
    return StdioServerParameters(command=sys.executable, args=["-m", "sh_notice_mcp.server"],
                                 env={"SH_NOTICE_DATA_DIR": str(data_dir())})


def _short(obj, n: int = 120) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"


# ------------------------------------------------------------------ 에이전트 루프


async def run_agent(task: str, allowed: set[str], log=print, client=None, params=None) -> str:
    """Claude가 도구를 고르고, 이 코드가 MCP 서버에서 실행하고, 결과를 돌려주는 반복."""
    if client is None:
        import anthropic  # 에이전트를 쓸 때만 필요한 의존성

        key = api_key()
        if not key:
            raise SystemExit("Anthropic API 키가 없습니다. README의 '에이전트' 절을 보고 키체인이나 환경변수에 넣으세요.")
        client = anthropic.AsyncAnthropic(api_key=key)

    async with stdio_client(params or server_params()) as (read, write):
        async with ClientSession(read, write) as mcp:
            await mcp.initialize()
            listed = (await mcp.list_tools()).tools
            tools = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                     for t in listed if t.name in allowed]
            log(f"[준비] MCP 도구 {len(listed)}개 중 {len(tools)}개 허용, 모델 {MODEL}")

            messages = [{"role": "user", "content": task}]
            used = 0
            for step in range(1, MAX_STEPS + 1):
                resp = await client.messages.create(model=MODEL, max_tokens=8000, system=SYSTEM,
                                                    tools=tools, messages=messages)
                used += resp.usage.input_tokens + resp.usage.output_tokens
                messages.append({"role": "assistant", "content": resp.content})

                calls = [b for b in resp.content if b.type == "tool_use"]
                if not calls:  # end_turn: 더 부를 도구가 없다 → 최종 답
                    text = "".join(b.text for b in resp.content if b.type == "text")
                    log(f"[완료] {step}단계, 누적 토큰 {used:,}")
                    return text

                results = []
                for c in calls:
                    log(f"[{step}] Claude → {c.name}({_short(c.input)})")
                    if c.name not in allowed:  # 목록에 없는 도구는 실행하지 않는다
                        out, err = f"허용되지 않은 도구: {c.name}", True
                    else:
                        r = await mcp.call_tool(c.name, c.input)
                        out = "\n".join(getattr(x, "text", "") for x in r.content)
                        err = bool(r.is_error)
                    if len(out) > MAX_RESULT_CHARS:
                        out = out[:MAX_RESULT_CHARS] + "\n…(길어서 잘림)"
                    log(f"[{step}] 결과 ← {'오류: ' if err else ''}{len(out):,}자")
                    results.append({"type": "tool_result", "tool_use_id": c.id, "content": out, "is_error": err})
                messages.append({"role": "user", "content": results})

                if used > MAX_TOKENS_TOTAL:
                    log(f"[중단] 토큰 예산 {MAX_TOKENS_TOTAL:,} 초과")
                    return "토큰 예산을 넘어 중단했습니다. 지금까지의 결과는 로그를 확인하세요."
            log(f"[중단] 최대 {MAX_STEPS}단계 도달")
            return "최대 반복 횟수에 도달해 중단했습니다."


# ------------------------------------------------------------------ 매일 아침 모드


def daily_task(new: list[dict]) -> str:
    from .prompts import SUMMARY_FORMAT

    lines = "\n".join(f"- board_id={n['board_id']}, seq={n['seq']}, 제목: {n['title']}" for n in new)
    return (
        "오늘 아침 SH 게시판에서 새 모집공고를 발견했다. 사용자는 지금 자리에 없을 수 있다.\n"
        f"{lines}\n\n"
        "공고마다 다음을 해라.\n"
        "1) recent_notices로 접수 상태를 확인한다. '접수 마감'이면 PDF를 읽지 말고 일정만 한 줄 적는다.\n"
        "2) 접수 중·예정이면 read_notice_pdf(신청자격·소득·자산·선정방식은 section으로 끝까지), check_profile_gaps, "
        "get_my_profile, get_housing_costs를 호출해 아래 형식으로 요약한다. 빠진 조건은 묻지 말고 '확인이 필요한 것'에 적는다.\n"
        "3) 내가 신청할 유형(특별공급은 하나만)의 오늘 이후 '접수 일정'만 propose_registration으로 플랜을 만들고 "
        "confirm_registration을 호출한다. 그러면 사용자의 맥에 확인 창이 뜨고, 사용자가 고른 것만 등록된다.\n"
        "4) mark_notice_processed로 처리 완료를 표시한다.\n"
        "마지막에 공고별 요약 전체를 마크다운으로 답한다.\n\n[요약 형식]\n" + SUMMARY_FORMAT
    )


def run_daily(service=None, agent=None) -> int:
    """새 공고 확인은 AI 없이 한다. 없으면 여기서 끝(비용 0원)."""
    from .service import NoticeService

    s = service or NoticeService()
    res = s.check_new()
    new = [n for b in res["boards"].values() for n in b.get("new_notices", [])]
    baseline = [bid for bid, b in res["boards"].items() if b.get("baseline_created")]
    if baseline:
        print(f"처음 실행한 게시판 {baseline}: 지금 있는 글을 기준선으로 저장했습니다.", file=sys.stderr)
    if not new:
        print(f"{datetime.now():%Y-%m-%d %H:%M} 새 공고 없음 — 아무것도 하지 않습니다.", file=sys.stderr)
        return 0

    titles = ", ".join(n["title"][:20] for n in new)
    notify(f"SH 새 공고 {len(new)}건", f"{titles} — 요약을 만드는 중입니다.")
    report = asyncio.run((agent or run_agent)(daily_task(new), DAILY_TOOLS, log=lambda m: print(m, file=sys.stderr)))

    out_dir = data_dir() / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{datetime.now():%Y-%m-%d_%H%M}.md"
    path.write_text(report, encoding="utf-8")
    notify("SH 공고 요약 완료", f"{path.name} — 캘린더 등록은 확인 창에서 고르세요.")
    if platform.system() == "Darwin":
        subprocess.run(["open", str(path)], check=False)
    print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sh-notice-agent", description="SH 공고 AI 에이전트")
    p.add_argument("task", nargs="?", help="에이전트에게 시킬 일 (예: '고덕강일 정리해줘')")
    p.add_argument("--daily", action="store_true", help="매일 아침용: 새 공고가 있을 때만 요약")
    args = p.parse_args(argv)
    if args.daily:
        return run_daily()
    if not args.task:
        p.error("task 또는 --daily가 필요합니다.")
    print(asyncio.run(run_agent(args.task, INTERACTIVE_TOOLS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
