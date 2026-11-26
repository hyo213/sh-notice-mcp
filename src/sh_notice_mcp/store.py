"""SQLite 상태 저장소.

세 가지를 기억한다.
  - boards  : 기준선을 만든 게시판 (→ 첫 실행 판단)
  - notices : 이미 본 게시글 (→ '새 글' 판단)
  - events  : 이미 캘린더에 넣은 일정 (→ 같은 일정 중복 등록 방지 = 멱등성)

'본 글'과 '처리 완료한 글'을 구분한 이유:
  목록에서 새 글을 발견한 직후 프로그램이 죽으면, 그 글은 '봤지만 일정은 안 넣은' 상태가 된다.
  processed_at이 비어 있는 글은 다음 실행 때 다시 처리하므로 일정이 빠지지 않는다.

'게시판 초기화'를 따로 기록하는 이유:
  '게시글이 하나라도 있으면 초기화된 것'으로 판단하면, 첫 실행 때 게시판이 비어 있던 경우
  다음 실행도 첫 실행으로 오인해 새 글을 기준선으로 삼켜버린다. (테스트로 발견한 버그)

연결을 작업마다 새로 여는 이유:
  MCP SDK는 동기 도구 함수를 작업 스레드에서 실행하는데, sqlite3 연결은 만든 스레드에서만 쓸 수 있다.
  또 cron 실행과 MCP 서버가 같은 DB를 동시에 쓸 수 있어서, 짧게 열고 닫는 편이 안전하다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS boards (
    board_id       TEXT PRIMARY KEY,
    initialized_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notices (
    board_id      TEXT NOT NULL,
    seq           TEXT NOT NULL,
    title         TEXT NOT NULL,
    posted_at     TEXT,
    first_seen_at TEXT NOT NULL,
    processed_at  TEXT,
    PRIMARY KEY (board_id, seq)
);
CREATE TABLE IF NOT EXISTS events (
    dedup_key   TEXT PRIMARY KEY,
    board_id    TEXT,
    seq         TEXT,
    title       TEXT NOT NULL,
    start       TEXT NOT NULL,
    backend     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    board_id    TEXT NOT NULL,
    seq         TEXT NOT NULL,
    supply_type TEXT NOT NULL,
    title       TEXT NOT NULL,
    applied_at  TEXT NOT NULL,
    PRIMARY KEY (board_id, seq)
);
CREATE TABLE IF NOT EXISTS receipts (
    receipt_no  TEXT PRIMARY KEY,
    board_id    TEXT NOT NULL,
    seq         TEXT NOT NULL,
    title       TEXT NOT NULL,
    applicant   TEXT NOT NULL,
    supply_type TEXT,
    house_type  TEXT,
    created_at  TEXT NOT NULL,
    result      TEXT,
    result_json TEXT,
    checked_at  TEXT
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        with self._tx() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:  # 블록이 끝나면 commit, 예외면 rollback
                yield conn

    # --- boards
    def is_initialized(self, board_id: str) -> bool:
        with self._tx() as c:
            return c.execute("SELECT 1 FROM boards WHERE board_id=?", (board_id,)).fetchone() is not None

    def mark_initialized(self, board_id: str):
        with self._tx() as c:
            c.execute("INSERT OR IGNORE INTO boards VALUES (?,?)", (board_id, _now()))

    # --- notices
    def known_seqs(self, board_id: str) -> set[str]:
        with self._tx() as c:
            return {r["seq"] for r in c.execute("SELECT seq FROM notices WHERE board_id=?", (board_id,))}

    def unprocessed_seqs(self, board_id: str) -> set[str]:
        with self._tx() as c:
            rows = c.execute(
                "SELECT seq FROM notices WHERE board_id=? AND processed_at IS NULL", (board_id,)
            )
            return {r["seq"] for r in rows}

    def remember(self, board_id: str, seq: str, title: str, posted_at: str, processed: bool = False):
        with self._tx() as c:
            c.execute(
                """INSERT INTO notices(board_id, seq, title, posted_at, first_seen_at, processed_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(board_id, seq) DO NOTHING""",
                (board_id, seq, title, posted_at, _now(), _now() if processed else None),
            )

    def mark_processed(self, board_id: str, seq: str):
        with self._tx() as c:
            c.execute(
                "UPDATE notices SET processed_at=? WHERE board_id=? AND seq=?", (_now(), board_id, seq)
            )

    # --- events
    def has_event(self, dedup_key: str) -> bool:
        with self._tx() as c:
            return c.execute("SELECT 1 FROM events WHERE dedup_key=?", (dedup_key,)).fetchone() is not None

    def record_event(self, dedup_key: str, board_id: str, seq: str, title: str, start: str, backend: str):
        with self._tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                (dedup_key, board_id, seq, title, start, backend, _now()),
            )

    def recent_events(self, limit: int = 20) -> list[dict]:
        with self._tx() as c:
            rows = c.execute(
                "SELECT title, start, backend, created_at FROM events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in rows]

    # --- 내가 접수한 공고
    def record_application(self, board_id: str, seq: str, supply_type: str, title: str):
        with self._tx() as c:
            c.execute("INSERT OR REPLACE INTO applications VALUES (?,?,?,?,?)",
                      (board_id, seq, supply_type, title, _now()))

    def applications(self) -> list[dict]:
        with self._tx() as c:
            rows = c.execute("SELECT * FROM applications ORDER BY applied_at DESC")
            return [dict(r) for r in rows]

    # --- 접수번호 (당첨 조회용). 이 DB는 내 컴퓨터에만 있다.
    def save_receipt(self, receipt_no: str, board_id: str, seq: str, title: str, applicant: str,
                     supply_type: str | None, house_type: str | None):
        with self._tx() as c:
            c.execute(
                "INSERT INTO receipts (receipt_no, board_id, seq, title, applicant, supply_type, house_type, created_at)"
                " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(receipt_no) DO UPDATE SET board_id=excluded.board_id,"
                " seq=excluded.seq, title=excluded.title, applicant=excluded.applicant,"
                " supply_type=excluded.supply_type, house_type=excluded.house_type",
                (receipt_no, board_id, seq, title, applicant, supply_type, house_type, _now()))

    def receipts(self, board_id: str | None = None, seq: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM receipts", []
        if board_id and seq:
            q, args = q + " WHERE board_id=? AND seq=?", [board_id, seq]
        with self._tx() as c:
            return [dict(r) for r in c.execute(q + " ORDER BY created_at", args)]

    def set_receipt_result(self, receipt_no: str, result: str, result_json: str):
        with self._tx() as c:
            c.execute("UPDATE receipts SET result=?, result_json=?, checked_at=? WHERE receipt_no=?",
                      (result, result_json, _now(), receipt_no))
