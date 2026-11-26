"""어떤 공고를 볼지 정의.

모든 조회는 SH '공고 및 공지 > 전체' 게시판(m_241) 한 곳에서 한다. 검색 폼 값만 바꿔
'주택분양 모집공고', '주택임대 모집공고 중 행복주택'처럼 좁힌다 (2026-10 실제 폼 분석).
  - multi_itm_seq : 공지분류 (1=주택분양, 2=주택임대, 512=주택매입 …)
  - itm_seq_1     : 공지유형 (0=전체공고, 1=모집공고)
  - splyTy        : 임대 공급유형 (03=장기전세, 07=행복주택 …). 주택임대일 때만 의미가 있다.
  - srchTp        : 검색 대상 (0=제목, 1=내용, 3=담당부서, 10=전체)
  - srchWord      : 검색어
상세 페이지(view.do?seq=N)는 seq가 사이트 전체 글 번호라 어느 게시판 경로로 열어도 같다.

무엇을 볼지는 사용자의 프로필 [watch]로 정한다: 주택분양 on/off, 주택임대 on/off, 임대 공급유형 목록.
공급유형 하나 = Board 하나. SH가 새 공급유형을 만들면 RENT_TYPES에 한 줄만 추가하면 된다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

BASE_URL = "https://www.i-sh.co.kr/main/lay2/program"
ALL_NOTICES_PATH = "S1T294C295/www/brd/m_241"  # 공고 및 공지 > 전체

# 모집공고가 아니라 그 결과·후속 안내인 글 (모집공고 필터에도 섞여 나온다)
RESULT_POST = r"경쟁률|당첨자|발표|서류\s*심사\s*대상|접수\s*결과|결과\s*및|계약\s*안내"


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    path: str  # 예: "S1T294C295/www/brd/m_241"
    form: dict[str, str] = field(default_factory=dict)
    # 제목 검색어. 여러 개면 각각 검색해 합친다(OR). 사이트 검색이 띄어쓰기를 구분하기 때문.
    search_words: tuple[str, ...] = ("",)
    # 검색 결과에 섞여도 버릴 글 (제목 정규식, 담당부서)
    exclude_title: str | None = None
    exclude_departments: tuple[str, ...] = ()
    # 프로필에서 오는 제목 필터: include는 하나라도 있어야, exclude는 하나라도 있으면 버림
    include_words: tuple[str, ...] = ()
    exclude_words: tuple[str, ...] = ()

    def is_excluded(self, title: str, department: str = "") -> bool:
        if department and department in self.exclude_departments:
            return True
        compact = title.replace(" ", "")
        if self.include_words and not any(w.replace(" ", "") in compact for w in self.include_words):
            return True
        if any(w.replace(" ", "") in compact for w in self.exclude_words):
            return True
        return bool(self.exclude_title and re.search(self.exclude_title, title))

    @property
    def list_url(self) -> str:
        return f"{BASE_URL}/{self.path}/list.do"

    @property
    def view_url(self) -> str:
        return f"{BASE_URL}/{self.path}/view.do"

    def detail_link(self, seq: str) -> str:
        """사람이 클릭해서 열 수 있는 상세 페이지 링크 (GET으로도 열린다)."""
        return f"{self.view_url}?seq={seq}"


def _rent(board_id: str, label: str, sply_ty: str) -> Board:
    form = {"multi_itm_seq": "2", "itm_seq_1": "1", "srchTp": "0"}
    if sply_ty:
        form["splyTy"] = sply_ty
    return Board(id=board_id, name=f"주택임대 · 모집공고 · {label}", path=ALL_NOTICES_PATH,
                 form=form, exclude_title=RESULT_POST)


SALE = Board(
    id="sale",
    name="주택분양 · 모집공고",
    path=ALL_NOTICES_PATH,
    form={"multi_itm_seq": "1", "itm_seq_1": "1", "srchTp": "0"},
    # 실제 제목 표기가 제각각이다 (2026-10 확인):
    #   '...입주자모집공고' / '...사전예약 입주자 모집공고' / '...입주자 모집 공고'
    # '입주자모집공고' 하나로만 검색하면 마곡16·위례A1-14·마곡10-2 공고가 빠진다.
    search_words=("입주자모집공고", "입주자 모집공고", "입주자 모집 공고"),
    # 토지·용지 분양(택지판매부)은 주택이 아니다. '토지임대부 분양주택'은 주택이므로 남긴다.
    # 주의: '택지'만으로 찾으면 '공공주택지구'(주택'지'구 → '택지')가 걸린다. 반드시 '분양'과 붙여 찾는다.
    exclude_title=r"(?:토지|용지|택지)\s*분양|" + RESULT_POST,
    exclude_departments=("택지판매부",),
)

RENT_ALL = _rent("rent_all", "전체 공급유형", "")

# 주택임대 공급유형: SH 검색 폼 '공급유형' 드롭다운의 이름 그대로 → (board_id, splyTy 코드)
RENT_TYPES: dict[str, tuple[str, str]] = {
    "국민공공임대주택": ("rent_national", "02"),
    "장기전세주택": ("rent_jeonse", "03"),
    "매입임대주택": ("rent_purchase", "04"),
    "장기안심주택": ("rent_anshim", "05"),
    "희망하우징": ("rent_hope", "06"),
    "행복주택": ("rent_happy", "07"),
    "청년안심주택": ("rent_youth", "10"),
    "전세임대": ("rent_lease", "20"),
    "영구임대주택": ("rent_permanent", "22"),
    "재개발임대주택": ("rent_redev", "23"),
    "두레주택": ("rent_dure", "11"),
    "사회주택": ("rent_social", "12"),
    "도시형생활주택": ("rent_urban", "13"),
    "수요자맞춤형": ("rent_custom", "14"),
}
RENT_BOARDS: dict[str, Board] = {name: _rent(bid, name, code) for name, (bid, code) in RENT_TYPES.items()}


def rent_type_name(value: str) -> str:
    """'장기전세'·'국민임대'처럼 줄여 써도 드롭다운 이름으로 맞춘다. 모르면 ValueError."""
    v = value.replace(" ", "")
    if v in RENT_TYPES:
        return v
    hits = [n for n in RENT_TYPES if v and (n.startswith(v) or v.replace("임대", "") in n and len(v) >= 2)]
    if len(hits) == 1:
        return hits[0]
    raise ValueError(f"알 수 없는 임대 공급유형: {value}. 가능한 값: {', '.join(RENT_TYPES)}")


# 당첨자 발표 글 찾기용: 전체 게시판, 모든 분류·유형, 제목에 '당첨자'. 감시 대상(BOARDS)이 아니다.
RESULT_POSTS = Board(id="results", name="당첨자 발표·명단", path=ALL_NOTICES_PATH,
                     form={"srchTp": "0"}, search_words=("당첨자",))

# board_id → Board (모든 유형). 저장된 기록·도구 인자의 board_id를 찾을 때 쓴다.
BOARDS: dict[str, Board] = {b.id: b for b in (SALE, RENT_ALL, *RENT_BOARDS.values())}


def watched_boards(profile_data: dict | None) -> list[Board]:
    """프로필 [watch] → 실제로 조회할 게시판 목록 (제목 필터 포함).

    [watch]
    sale = true                 # 주택분양 (기본 true)
    rent = true                 # 주택임대 (기본 true)
    rent_types = ["장기전세주택"]  # 주택임대 중 볼 공급유형. 비우면 전체
    include_words / exclude_words  # 제목 필터
    """
    w = (profile_data or {}).get("watch", {})
    boards: list[Board] = []
    if w.get("sale", True):
        boards.append(SALE)
    if w.get("rent", True):
        names = list(dict.fromkeys(rent_type_name(t) for t in (w.get("rent_types") or [])))
        boards += [RENT_BOARDS[n] for n in names] if names else [RENT_ALL]
    inc = tuple(w.get("include_words") or ())
    exc = tuple(w.get("exclude_words") or ())
    if inc or exc:
        boards = [replace(b, include_words=inc, exclude_words=exc) for b in boards]
    return boards


def data_dir() -> Path:
    """DB·ics 파일을 둘 폴더. SH_NOTICE_DATA_DIR로 바꿀 수 있다."""
    d = Path(os.environ.get("SH_NOTICE_DATA_DIR", Path.home() / ".sh-notice-mcp"))
    d.mkdir(parents=True, exist_ok=True)
    return d


# 사이트에 부담을 주지 않기 위한 설정
REQUEST_TIMEOUT = 15.0
REQUEST_DELAY_SEC = 1.0  # 상세 페이지를 연속으로 요청할 때 간격
USER_AGENT = "sh-notice-mcp/0.2 (unofficial notice-to-calendar tool)"
