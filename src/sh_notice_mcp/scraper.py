"""SH 게시판 크롤러.

설계 포인트: '가져오기(HTTP)'와 '해석(파싱)'을 분리했다.
파싱 함수는 HTML 문자열만 받기 때문에 네트워크 없이 저장된 HTML로 테스트할 수 있고,
사이트 구조가 바뀌면 테스트가 먼저 깨져서 바로 알 수 있다.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass

import httpx
from bs4 import BeautifulSoup

from .attachments import Attachment, parse_attachments
from .config import REQUEST_DELAY_SEC, REQUEST_TIMEOUT, USER_AGENT, Board

log = logging.getLogger(__name__)

_SEQ_RE = re.compile(r"getDetailView\('(\d+)'\)")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class ScrapeError(RuntimeError):
    """사이트 구조가 예상과 다르거나 요청이 실패했을 때."""


@dataclass
class NoticeSummary:
    board_id: str
    seq: str
    title: str
    department: str
    posted_at: str  # YYYY-MM-DD
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NoticeDetail:
    board_id: str
    seq: str
    title: str
    posted_at: str
    body: str
    attachments: list[Attachment]
    url: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["attachments"] = [a.to_dict() for a in self.attachments]
        return d


# ---------------------------------------------------------------- 파싱 (순수 함수)


def parse_list(html: str, board: Board) -> list[NoticeSummary]:
    """목록 페이지 HTML → 게시글 요약 목록."""
    soup = BeautifulSoup(html, "html.parser")
    table = None
    for t in soup.find_all("table"):
        cap = t.find("caption")
        if cap and "목록" in cap.get_text():
            table = t
            break
    if table is None:
        raise ScrapeError("목록 테이블을 찾지 못했습니다. 사이트 구조가 바뀌었을 수 있습니다.")

    items: list[NoticeSummary] = []
    for tr in table.select("tbody tr"):
        a = tr.find("a", onclick=_SEQ_RE)
        if a is None:  # '검색 결과가 없습니다' 같은 행
            continue
        seq = _SEQ_RE.search(a["onclick"]).group(1)
        tds = tr.find_all("td")
        texts = [td.get_text(" ", strip=True) for td in tds]
        posted = next((m.group(0) for t in texts if (m := _DATE_RE.fullmatch(t))), "")
        # 열 순서: 번호 | 제목 | 담당부서 | 등록일 | 조회수
        dept = texts[2] if len(texts) >= 5 else ""
        items.append(
            NoticeSummary(
                board_id=board.id,
                seq=seq,
                title=" ".join(a.get_text().split()),
                department=dept,
                posted_at=posted,
                url=board.detail_link(seq),
            )
        )
    return items


_BLOCK_TAGS = ["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "dd", "dt", "section"]


def block_text(el) -> str:
    """블록 태그(p·div·li…)와 <br>에서만 줄을 바꾸고, 같은 문단 안의 <span>들은 이어 붙인다.

    SH 본문은 한 문단이 글자 단위 <span>으로 잘게 쪼개져 있다
    (예: <span>[1</span><span>순위</span><span>] 2026. 9. 14.(</span><span>월</span>…).
    get_text("\n")처럼 태그마다 줄을 바꾸면 '[1순위] 2026. 9. 14.' 한 줄이 여러 줄로 흩어져 일정을 놓친다.
    """
    for br in el.find_all("br"):
        br.replace_with("\n")
    for tag in el.find_all(_BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")
    text = el.get_text("").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def parse_detail(html: str, board: Board, seq: str) -> NoticeDetail:
    """상세 페이지 HTML → 제목·본문·첨부파일."""
    soup = BeautifulSoup(html, "html.parser")
    cont = soup.select_one("td.cont")
    if cont is None:
        raise ScrapeError(f"seq={seq} 본문(td.cont)을 찾지 못했습니다.")
    table = cont.find_parent("table")
    th = table.find("th") if table else None
    title = " ".join(th.get_text().split()) if th else ""

    posted = ""
    if table:
        m = re.search(r"등록일\s*:\s*(\d{4}-\d{2}-\d{2})", table.get_text(" "))
        posted = m.group(1) if m else ""

    # 첨부파일은 화면의 링크가 아니라 페이지 스크립트의 initParam.downList(JSON)에서 읽는다.
    # 링크에는 파일 번호가 없고, JSON에는 다운로드에 필요한 값이 모두 있다.
    attachments = parse_attachments(html)

    # 본문 안의 표(조회방법 등)는 일정과 무관하고 노이즈가 많아 제거
    for inner in cont.find_all("table"):
        inner.decompose()
    body = block_text(cont)

    return NoticeDetail(
        board_id=board.id,
        seq=seq,
        title=title,
        posted_at=posted,
        body=body,
        attachments=attachments,
        url=board.detail_link(seq),
    )


def merge_notices(lists: list[list[NoticeSummary]]) -> list[NoticeSummary]:
    seen: dict[str, NoticeSummary] = {}
    for items in lists:
        for it in items:
            seen.setdefault(it.seq, it)
    # seq는 사이트 전체에서 증가하는 글 번호라 클수록 최신이다
    return sorted(seen.values(), key=lambda it: int(it.seq), reverse=True)


# ---------------------------------------------------------------- HTTP


class SHClient:
    def __init__(self, client: httpx.Client | None = None, retries: int = 2):
        self._client = client or httpx.Client(
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        self._retries = retries

    def _post(self, url: str, data: dict[str, str]) -> str:
        last: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                r = self._client.post(url, data=data)
                r.raise_for_status()
                return r.text
            except httpx.HTTPError as e:  # 네트워크 오류·5xx는 잠시 후 재시도
                last = e
                wait = 2**attempt
                log.warning("요청 실패(%s), %ss 후 재시도: %s", attempt + 1, wait, e)
                time.sleep(wait)
        raise ScrapeError(f"요청 실패: {url}: {last}")

    def fetch_list(self, board: Board, page: int = 1) -> list[NoticeSummary]:
        """검색어마다 1페이지씩 받아 합친다(OR). 같은 글은 한 번만, 최신 글이 앞에 오게."""
        results = []
        for i, word in enumerate(board.search_words):
            if i:
                time.sleep(REQUEST_DELAY_SEC)
            data = {"page": str(page), "seq": "", **board.form, "srchWord": word}
            results.append(parse_list(self._post(board.list_url, data), board))
        return merge_notices(results)

    def fetch_detail(self, board: Board, seq: str) -> NoticeDetail:
        time.sleep(REQUEST_DELAY_SEC)
        return parse_detail(self._post(board.view_url, {"seq": seq}), board, seq)

    def fetch_file(self, att: Attachment, max_bytes: int) -> bytes:
        """첨부파일을 받는다. 서버가 HEAD를 막아 두었으므로 GET 스트리밍으로 받으며 크기를 제한한다."""
        if att.size and att.size > max_bytes:
            raise ScrapeError(f"첨부파일이 너무 큽니다: {att.name} ({att.size / 1_048_576:.1f}MB)")
        time.sleep(REQUEST_DELAY_SEC)
        chunks, total = [], 0
        try:
            with self._client.stream("GET", att.url) as r:
                r.raise_for_status()
                for chunk in r.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ScrapeError(f"첨부파일이 너무 큽니다: {att.name}")
                    chunks.append(chunk)
        except httpx.HTTPError as e:
            raise ScrapeError(f"첨부파일 다운로드 실패: {att.name}: {e}") from e
        return b"".join(chunks)
