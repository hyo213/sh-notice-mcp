"""공고문 PDF 읽기 → 등록 플랜 → 사람 확인 → 등록 흐름."""
import asyncio
from datetime import datetime

import pytest

from sh_notice_mcp.calendar_backends import ICSBackend
from sh_notice_mcp.confirm import ConfirmResult
from sh_notice_mcp.service import NoticeService
from sh_notice_mcp.store import Store

from .conftest import FIX, downlist, fixture_text, wrap_body

TODAY = datetime(2026, 10, 21, 17)
SALE_TITLE = "고덕강일지구 3단지 토지임대부 분양주택 입주자모집공고"
FILES = ["고덕강일지구 3단지 토지임대부 분양주택 입주자모집공고문.pdf", "1_ 위임장.pdf",
         "2-1_ 개인정보 수집·이용 및 제3자 제공 동의서(사전청약 당첨자용).pdf"]


class FakeHuman:
    """확인 창에서 사람이 고르는 것을 흉내 낸다. pick=None이면 취소."""

    def __init__(self, pick):
        self.pick, self.seen = pick, None

    async def __call__(self, ctx, title, summary, events):
        self.seen = (title, summary, [e.label for e in events])
        if self.pick is None:
            return ConfirmResult([], "fake", "사용자가 취소했습니다.")
        return ConfirmResult(list(self.pick), "fake")


def make(tmp_path, fake_client, human=None):
    fake_client.add("sale", "311656", SALE_TITLE,
                    wrap_body(SALE_TITLE, fixture_text("body_sale.txt"), downlist("311656", FILES)))
    pdf_url = "https://www.i-sh.co.kr/main/com/file/innoFD.do?brdId=GS0401&seq=311656&fileTp=A&fileSeq=1"
    fake_client.files[pdf_url] = (FIX / "notice_jeonse.pdf").read_bytes()
    return NoticeService(client=fake_client, store=Store(tmp_path / "s.db"),
                         backend=ICSBackend(tmp_path / "ics"), base_dir=tmp_path,
                         now=lambda: TODAY, confirmer=human or FakeHuman([]))


def test_reads_only_the_notice_pdf_and_caches(tmp_path, fake_client):
    s = make(tmp_path, fake_client)
    doc = s.notice_document("sale", "311656")
    assert doc["status"] == "ok"
    assert doc["file"]["name"] == FILES[0]
    assert "위임장" in " ".join(doc["other_attachments"])
    assert [x.title for x in doc["sections"]][-1] == "공급일정"

    # 같은 프로세스에서 다시 읽으면 다운로드하지 않는다 + 디스크 캐시
    s.notice_document("sale", "311656")
    assert fake_client.downloads == 1
    assert (tmp_path / "pdf" / "sale_311656_1.json").exists()
    s2 = NoticeService(client=fake_client, store=s.store, backend=s.backend, base_dir=tmp_path)
    s2.notice_document("sale", "311656")
    assert fake_client.downloads == 1


def test_propose_filters_and_does_not_write(tmp_path, fake_client):
    s = make(tmp_path, fake_client)
    plan = s.propose_registration("sale", "311656", [
        {"label": "일반공급 1순위 접수", "start": "2026-11-04T10:00", "end": "2026-11-04T17:00"},
        {"label": "당첨자 발표", "start": "2026-11-26T16:00"},
        {"label": "모집공고일", "start": "2026-10-16"},                 # 지난 일정
        {"label": "이상한 날짜", "start": "2026-11-30", "end": "2026-11-01"},  # 종료 < 시작
        {"label": "형식 오류", "start": "11월 4일"},
    ], summary="1순위 해당. 신청자격 p.1, 순위 p.2")
    assert plan["plan_id"]
    assert [x["label"] for x in plan["to_register"]] == ["일반공급 1순위 접수", "당첨자 발표"]
    reasons = [r["reason"] for r in plan["rejected"]]
    assert reasons[0] == "오늘 이전에 끝난 일정"
    assert "종료" in reasons[1]
    assert len(reasons) == 3
    assert not (tmp_path / "ics").exists() or not list((tmp_path / "ics").glob("*.ics"))


def test_only_human_selected_events_are_written(tmp_path, fake_client):
    human = FakeHuman(pick=[1])  # 두 번째만 체크
    s = make(tmp_path, fake_client, human)
    plan = s.propose_registration("sale", "311656", [
        {"label": "일반공급 1순위 접수", "start": "2026-11-04T10:00", "end": "2026-11-04T17:00"},
        {"label": "당첨자 발표", "start": "2026-11-26T16:00"},
    ], summary="요약")
    r = asyncio.run(s.confirm_registration(plan["plan_id"]))
    assert human.seen[1] == "요약"
    assert [x["status"] for x in r["results"]] == ["not_approved", "added"]
    assert len(list((tmp_path / "ics").glob("*.ics"))) == 1

    # 플랜은 한 번만 쓸 수 있다 (같은 확인으로 두 번 등록 방지)
    with pytest.raises(ValueError, match="플랜이 없거나"):
        asyncio.run(s.confirm_registration(plan["plan_id"]))

    # 이미 등록한 일정은 다음 플랜에서 걸러진다
    again = s.propose_registration("sale", "311656",
                                   [{"label": "당첨자 발표", "start": "2026-11-26T16:00"}], "x")
    assert again["plan_id"] is None and again["rejected"][0]["reason"] == "이미 등록된 일정"


def test_cancel_writes_nothing(tmp_path, fake_client):
    s = make(tmp_path, fake_client, FakeHuman(pick=None))
    plan = s.propose_registration("sale", "311656", [{"label": "당첨자 발표", "start": "2026-11-26T16:00"}], "")
    r = asyncio.run(s.confirm_registration(plan["plan_id"]))
    assert r["approved"] == 0 and r["note"] == "사용자가 취소했습니다."
    assert not list((tmp_path / "ics").glob("*.ics")) if (tmp_path / "ics").exists() else True


def test_no_confirm_channel_means_no_write(tmp_path, fake_client, monkeypatch):
    """확인 수단이 없는 환경(맥이 아니고 elicitation 미지원)에서는 등록하지 않는다."""
    from sh_notice_mcp import confirm
    monkeypatch.setattr(confirm.platform, "system", lambda: "Linux")
    s = make(tmp_path, fake_client)
    s._confirmer = confirm.default_confirmer
    plan = s.propose_registration("sale", "311656", [{"label": "당첨자 발표", "start": "2026-11-26T16:00"}], "")
    r = asyncio.run(s.confirm_registration(plan["plan_id"], ctx=None))
    assert r["confirmed_by"] == "deny" and r["approved"] == 0


def test_profile_template_created_and_unfilled(tmp_path, fake_client):
    s = make(tmp_path, fake_client)
    p = s.profile()
    assert p["filled"] is False
    assert "basic.birth_date" in p["missing"]
    text = (tmp_path / "profile.toml").read_text(encoding="utf-8")
    born = f"{s._now().year - 34}-01-01"  # 언제 돌려도 34세
    text = text.replace('# birth_date = "1990-01-01"', f'birth_date = "{born}"')
    (tmp_path / "profile.toml").write_text(text, encoding="utf-8")
    p = s.profile()
    assert p["filled"] is True and p["profile"]["basic"]["birth_date"] == born
    assert p["derived"]["age"] == 34 and "basic.birth_date" not in p["missing"]


def test_plan_rejects_dates_not_in_the_notice_and_shows_evidence(tmp_path, fake_client):
    s = make(tmp_path, fake_client)
    plan = s.propose_registration("sale", "311656", [
        {"label": "일반공급 1순위", "start": "2026-11-04T10:00", "end": "2026-11-04T17:00"},
        {"label": "당첨자 발표", "start": "2027-03-05T16:00"},  # 게시글 본문엔 없고 공고문 PDF p.3에만 있음
        {"label": "당첨자 발표(잘못 읽음)", "start": "2026-11-27T16:00"},  # 실제는 11/26
        {"label": "만들어낸 일정", "start": "2026-12-24"},
    ], summary="요약")
    reg = plan["to_register"]
    assert [x["label"] for x in reg] == ["일반공급 1순위", "당첨자 발표"]
    assert reg[0]["evidence"][0]["source"] == "게시글 본문"
    assert reg[1]["evidence"][0]["source"] == "공고문 p.3"
    assert "(근거: 공고문 p.3)" in reg[1]["line"]  # 확인 창에도 근거 표시
    assert [r["reason"].split(":")[0] for r in plan["rejected"]] == ["원문 근거 없음", "원문 근거 없음"]
    assert "공고문 PDF 3쪽" in plan["checked_sources"]


def test_evidence_is_written_into_calendar_notes(tmp_path, fake_client):
    s = make(tmp_path, fake_client, FakeHuman(pick=[0]))
    plan = s.propose_registration("sale", "311656", [{"label": "당첨자 발표", "start": "2026-11-26T16:00"}], "")
    asyncio.run(s.confirm_registration(plan["plan_id"]))
    [ics] = list((tmp_path / "ics").glob("*.ics"))
    text = ics.read_text(encoding="utf-8")
    assert "근거:" in text and "게시글 본문" in text


def test_falls_back_to_body_when_pdf_unreadable(tmp_path, fake_client):
    s = make(tmp_path, fake_client)
    url = next(iter(fake_client.files))
    fake_client.files[url] = b"not a pdf"
    plan = s.propose_registration("sale", "311656", [{"label": "당첨자 발표", "start": "2026-11-26T16:00"}], "")
    assert plan["to_register"] and "게시글 본문에서만" in plan["checked_sources"]


def test_warning_is_visible_in_confirm_dialog_line(tmp_path, fake_client):
    s = make(tmp_path, fake_client)
    plan = s.propose_registration("sale", "311656", [{"label": "당첨자 발표", "start": "2026-11-26T10:00"}], "")
    [item] = plan["to_register"]
    assert item["warnings"] and "⚠" in item["line"]
