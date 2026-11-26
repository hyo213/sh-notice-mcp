"""AI 없이 실행하는 도구 (cron / 터미널용).

  sh-notice-run                         # 새 공고 확인 → 읽을 PDF·일정 후보를 보고만 함 (캘린더에 안 씀)
  sh-notice-run --preview sale 311656   # 특정 공고의 본문 일정 후보 보기
  sh-notice-run --pdf sale 311656       # 모집공고문 PDF 목차(섹션) 보기
  sh-notice-run --review sale 311656    # 일정 후보를 하나씩 보여주고, 터미널에서 y를 누른 것만 등록

등록은 반드시 사람이 확인한 뒤에만 한다: --review(터미널 y/n) 또는 Claude의 confirm_registration(대화상자).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .confirm import describe
from .service import NoticeService


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sh-notice-run")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--preview", nargs=2, metavar=("BOARD_ID", "SEQ"), help="본문 일정 후보 보기")
    g.add_argument("--pdf", nargs=2, metavar=("BOARD_ID", "SEQ"), help="모집공고문 PDF 목차 보기")
    g.add_argument("--review", nargs=2, metavar=("BOARD_ID", "SEQ"), help="확인하며 등록")
    p.add_argument("--include-past", action="store_true", help="--preview에서 지난 일정도 표시")
    args = p.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")

    s = NoticeService()
    if args.preview or args.review:
        bid, seq = args.preview or args.review
        r = s.propose_events(bid, seq, include_past=bool(args.preview and args.include_past))
        print(f"# {r['notice'].title}\n{r['notice'].url}\n")
        if args.preview:
            for e in r["events"]:
                print(f"- {describe(e)}")
            for sk in r["skipped"]:
                print(f"  (제외) {sk['label']}: {sk['start'][:10]} — {sk['reason']}")
            return 0
        for e in r["events"]:
            ans = input(f"등록할까요? {describe(e)} [y/N] ").strip().lower()
            if ans == "y":
                print("  →", s.add_event(e)["status"])
        return 0

    if args.pdf:
        doc = s.notice_document(*args.pdf)
        if doc["status"] != "ok":
            print(doc["reason"])
            for c in doc["candidates"]:
                print(f"  - file_seq={c['file_seq']}  {c['name']}  ({c['size_mb']}MB)")
            return 1
        print(f"# {doc['file']['name']} ({doc['pages']}쪽) — {doc['file']['why_this_file']}")
        for sec in doc["sections"]:
            tags = f"  [{', '.join(sec.tags)}]" if sec.tags else ""
            print(f"{sec.index:>3}. p.{sec.page_from}-{sec.page_to}  {sec.title}  ({len(sec.text)}자){tags}")
        return 0

    report = s.scan()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if any("error" in r for r in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())
