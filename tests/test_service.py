from datetime import datetime
from pathlib import Path

from sh_notice_mcp.calendar_backends import ICSBackend
from sh_notice_mcp.service import NoticeService
from sh_notice_mcp.store import Store

from .conftest import fixture_text, wrap_body


TODAY = datetime(2026, 10, 21, 17, 0)


def make_service(tmp_path, client, today=TODAY):
    return NoticeService(client=client, store=Store(tmp_path / "s.db"),
                         backend=ICSBackend(tmp_path / "ics"), base_dir=tmp_path,
                         now=lambda: today)


def test_first_run_creates_baseline_without_reporting_old_posts(tmp_path, fake_client):
    fake_client.add("sale", "100", "예전 공고", "")
    s = make_service(tmp_path, fake_client)
    r = s.check_new(["sale"])["boards"]["sale"]
    assert r["baseline_created"] is True
    assert r["new_notices"] == []


def test_new_post_detected_then_processed_once(tmp_path, fake_client):
    fake_client.add("sale", "100", "예전 공고", "")
    s = make_service(tmp_path, fake_client)
    s.check_new(["sale"])  # baseline

    title = "고덕강일지구 3단지 토지임대부 분양주택 입주자모집공고"
    fake_client.add("sale", "311656", title, wrap_body(title, fixture_text("body_sale.txt")))
    new = s.check_new(["sale"])["boards"]["sale"]["new_notices"]
    assert [n["seq"] for n in new] == ["311656"]

    p = s.propose_events("sale", "311656")
    assert len(p["events"]) == 9  # 모집공고일(10/16)은 지난 일정이라 제외
    assert p["skipped"][0]["reason"] == "이미 지난 일정"

    first = [s.add_event(e)["status"] for e in p["events"]]
    second = [s.add_event(e)["status"] for e in p["events"]]
    assert set(first) == {"added"}
    assert set(second) == {"skipped_duplicate"}  # 멱등성
    assert len(list((tmp_path / "ics").glob("*.ics"))) == 9

    s.mark_processed("sale", "311656")
    assert s.check_new(["sale"])["boards"]["sale"]["new_notices"] == []


def test_unprocessed_notice_is_retried(tmp_path, fake_client):
    """새 글을 발견한 뒤 처리 도중 죽어도, 다음 실행에서 다시 나와야 한다."""
    s = make_service(tmp_path, fake_client)
    s.check_new(["sale"])
    fake_client.add("sale", "200", "새 공고", "")
    assert len(s.check_new(["sale"])["boards"]["sale"]["new_notices"]) == 1
    # mark_processed 없이 다시 확인
    assert [n["seq"] for n in s.check_new(["sale"])["boards"]["sale"]["new_notices"]] == ["200"]


def _event(s, label, start, end, all_day=False):
    from sh_notice_mcp.scraper import NoticeDetail
    d = NoticeDetail("rent_jeonse", "309467", "제51차 장기전세주택", "2026-08-31", "", [],
                     "https://www.i-sh.co.kr/x/view.do?seq=309467")
    return s.make_event(d, label, start, end, all_day)


def test_only_today_or_later_events_are_registered(tmp_path, fake_client):
    s = make_service(tmp_path, fake_client)
    past = _event(s, "서류심사대상자 발표", datetime(2026, 10, 14, 16), datetime(2026, 10, 14, 17))
    ongoing = _event(s, "서류제출기간", datetime(2026, 10, 19), datetime(2026, 10, 21), all_day=True)
    today_later = _event(s, "오늘 늦게", datetime(2026, 10, 21, 20), datetime(2026, 10, 21, 21))
    future = _event(s, "당첨자 발표", datetime(2027, 3, 5, 16), datetime(2027, 3, 5, 17))

    r = s.add_event(past)
    assert r["status"] == "skipped_past" and "2026-10-21" in r["reason"]
    assert s.add_event(past, dry_run=True)["status"] == "skipped_past"  # 미리보기에서도 동일
    assert s.add_event(ongoing)["status"] == "added"  # 이미 시작했지만 오늘 끝나는 기간
    assert s.add_event(today_later)["status"] == "added"
    assert s.add_event(future)["status"] == "added"

    assert len(list((tmp_path / "ics").glob("*.ics"))) == 3
    assert not s.store.has_event(past.dedup_key)  # 기록도 남지 않아야 함


def test_jeonse_notice_only_keeps_upcoming(tmp_path, fake_client):
    """실제 제51차 공고(2026-10-21 기준): 지난 일정은 skipped, 진행 중·미래 일정만 후보."""
    from .conftest import fixture_text as ft
    fake_client.bodies[("rent_jeonse", "309467")] = ft("detail_jeonse.html")
    s = make_service(tmp_path, fake_client)
    p = s.propose_events("rent_jeonse", "309467")
    assert [e.label for e in p["events"]] == ["서류심사대상자 서류제출기간", "당첨자 발표"]
    assert {sk["reason"] for sk in p["skipped"]} == {"이미 지난 일정"}
    assert len(p["skipped"]) == 6


def test_land_sale_notices_are_never_reported(tmp_path, fake_client):
    s = make_service(tmp_path, fake_client)
    s.check_new(["sale"])  # 기준선
    fake_client.add("sale", "400", "강서구 외발산동 산27-2번지 토지 분양 공고", "")
    fake_client.lists["sale"][0].department = "택지판매부"
    fake_client.add("sale", "401", "OO지구 토지임대부 분양주택 입주자모집공고", "")
    new = s.check_new(["sale"])["boards"]["sale"]["new_notices"]
    assert [n["seq"] for n in new] == ["401"]


def test_find_notices_by_name_even_if_not_new(tmp_path, fake_client):
    s = make_service(tmp_path, fake_client)
    fake_client.add("sale", "301076", "마곡지구 17단지 토지임대부 분양주택 입주자모집공고", "")
    fake_client.add("sale", "311656", "고덕강일지구 3단지 토지임대부 분양주택 입주자모집공고", "")
    fake_client.add("rent_jeonse", "309467", "제51차 장기전세주택 입주자 모집공고(2026.08.31.공고)", "")
    s.check_new()  # 기준선: 위 공고들은 '이미 본 글'이 된다
    r = s.find_notices("고덕 강일")  # 띄어쓰기 무시
    assert [(n["board_id"], n["seq"]) for n in r["notices"]] == [("sale", "311656")]
    assert s.find_notices("51차 장기전세")["notices"][0]["seq"] == "309467"
    assert s.find_notices("토지임대부", "sale")["count"] == 2
    assert s.find_notices()["count"] == 3


def test_recent_notices_status_without_reading_pdf(tmp_path, fake_client):
    """'최근 뜬 공고 있어?': 본문 일정만으로 접수 상태를 판단하고 PDF는 받지 않는다."""
    title = "고덕강일지구 3단지 토지임대부 분양주택 입주자모집공고"
    fake_client.add("sale", "311656", title, wrap_body(title, fixture_text("body_sale.txt")))
    fake_client.add("rent_jeonse", "309467", "제51차 장기전세주택 입주자 모집공고", "")
    fake_client.bodies[("rent_jeonse", "309467")] = fixture_text("detail_jeonse.html")
    s = make_service(tmp_path, fake_client)
    r = s.recent_notices()
    by = {n["seq"]: n for n in r["notices"]}
    assert by["311656"]["status"] == "접수 예정"
    assert by["311656"]["apply_from"] == "2026-11-02T10:00" and by["311656"]["apply_to"] == "2026-11-06T17:00"
    assert by["309467"]["status"] == "접수 마감"
    assert any(x["label"] == "당첨자 발표" for x in by["309467"]["upcoming"])  # 마감이어도 남은 일정은 보임
    assert [n["seq"] for n in r["notices"]] == ["311656", "309467"]  # 접수 가능한 공고가 먼저
    assert fake_client.downloads == 0


def test_record_application_stages(tmp_path, fake_client):
    """'접수했어' → 발표만. '당첨됐어' → 서류제출(·계약). 사전청약 전용 일정은 빼고, 확인 전에는 쓰지 않는다."""
    title = "고덕강일지구 3단지 토지임대부 분양주택 입주자모집공고"
    fake_client.add("sale", "311656", title, wrap_body(title, fixture_text("body_sale.txt")))
    s = make_service(tmp_path, fake_client)
    applied = s.record_application("sale", "311656", "생애최초 특별공급")
    assert [e["label"] for e in applied["to_register"]] == ["당첨자 발표"]
    assert "생애최초" in s._plans[applied["plan_id"]]["summary"]
    won = s.record_application("sale", "311656", "생애최초 특별공급", stage="당첨")
    assert [e["label"] for e in won["to_register"]] == ["심사대상자 서류제출기간"]
    assert s.store.applications()[0]["supply_type"] == "생애최초 특별공급 · 당첨"
    assert not list((tmp_path / "ics").glob("*.ics"))  # 확인 전에는 캘린더에 쓰지 않음


def test_jeonse_stages_on_real_body(tmp_path, fake_client):
    fake_client.add("rent_jeonse", "309467", "제51차 장기전세주택", "")
    fake_client.bodies[("rent_jeonse", "309467")] = fixture_text("detail_jeonse_real.html")
    url = "https://www.i-sh.co.kr/main/com/file/innoFD.do?brdId=GS0401&seq=309467&fileTp=A&fileSeq=1"
    fake_client.files[url] = (Path(__file__).parent / "fixtures" / "notice_jeonse.pdf").read_bytes()
    s = make_service(tmp_path, fake_client, today=datetime(2026, 9, 1))
    labels = [e["label"] for e in s.record_application("rent_jeonse", "309467", "1순위")["to_register"]]
    assert labels == ["서류심사대상자 발표", "당첨자 발표"]
    won = [e["label"] for e in s.record_application("rent_jeonse", "309467", "1순위", stage="당첨")["to_register"]]
    assert won == ["서류심사대상자 서류제출기간"]
