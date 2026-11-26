from pathlib import Path

import json

import pytest

from sh_notice_mcp.config import BOARDS
from sh_notice_mcp.scraper import NoticeSummary, parse_detail

FIX = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class FakeClient:
    """네트워크 없이 서비스 계층을 테스트하기 위한 가짜 SH 클라이언트."""

    def __init__(self):
        self.lists: dict[str, list[NoticeSummary]] = {bid: [] for bid in BOARDS}
        self.bodies: dict[tuple[str, str], str] = {}
        self.files: dict[str, bytes] = {}  # 다운로드 URL → 내용
        self.downloads = 0

    def add(self, board_id: str, seq: str, title: str, body_html: str):
        b = BOARDS[board_id]
        self.lists[board_id].insert(
            0, NoticeSummary(board_id, seq, title, "부서", "2026-10-16", b.detail_link(seq))
        )
        self.bodies[(board_id, seq)] = body_html

    def fetch_list(self, board, page=1):
        if board.id == "rent_all":  # 실제 사이트처럼 '임대 전체'에는 모든 공급유형 글이 나온다
            items = [it for bid, lst in self.lists.items() if bid.startswith("rent_") for it in lst]
            return sorted(items, key=lambda it: int(it.seq), reverse=True)
        return list(self.lists[board.id])

    def fetch_detail(self, board, seq):
        # seq는 사이트 전체 글 번호라 어느 게시판 경로로 열어도 같은 글이다
        body = next(v for (_, sq), v in self.bodies.items() if sq == seq)
        return parse_detail(body, board, seq)

    def fetch_file(self, att, max_bytes):
        self.downloads += 1
        return self.files[att.url]


def downlist(seq: str, names: list[str], sizes: list[int] | None = None) -> list[dict]:
    sizes = sizes or [1000] * len(names)
    return [{"brdId": "GS0401", "seq": seq, "fileSeq": str(i + 1), "fileSize": str(sz),
             "oriFileNm": n, "fileTp": "A"} for i, (n, sz) in enumerate(zip(names, sizes))]


def wrap_body(title: str, body_text: str, files: list[dict] | None = None) -> str:
    paras = "".join(f"<p>{ln}</p>" for ln in body_text.splitlines())
    script = (f"<script>initParam.downList = {json.dumps(files, ensure_ascii=False)};</script>"
              if files else "")
    return (
        f"<table><tr><th>{title}</th></tr><tr><td>등록일 : 2026-10-16 조회수 : 1</td></tr>"
        f"<tr><td class='cont'>{paras}</td></tr></table>{script}"
    )


@pytest.fixture
def fake_client():
    return FakeClient()
