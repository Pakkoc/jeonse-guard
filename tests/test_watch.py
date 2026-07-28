"""watch 모듈 테스트 — 네트워크·실제 scan 없이 monkeypatch로 검증한다.

pipeline.scan은 가짜 ScanResult로, report.render는 고정 문자열로 대체한다.
"""

from __future__ import annotations

import importlib
import json
import sys
import tomllib
import types
from pathlib import Path

import pytest


def _ensure_importable(name: str) -> None:
    """병렬 빌드 중 미구현 스텁(raise NotImplementedError) 모듈을 빈 모듈로 대체한다.

    실제 모듈이 import 가능하면 아무것도 하지 않는다 — 통합 후에는 실물이 쓰인다.
    """
    try:
        importlib.import_module(name)
    except Exception:
        module = types.ModuleType(name)
        sys.modules[name] = module
        parent_name, _, child = name.rpartition(".")
        setattr(importlib.import_module(parent_name), child, module)


_ensure_importable("jeonse_guard.pipeline")
_ensure_importable("jeonse_guard.report")

from jeonse_guard import models, watch  # noqa: E402
from jeonse_guard.errors import SourceError  # noqa: E402

ADDRESS = models.ResolvedAddress(
    raw="서울 마포구 성산동 200-1",
    jibun="서울 마포구 성산동 200-1",
    road=None,
    b_code="1144010700",
    mountain=False,
    bun="0200",
    ji="0001",
    region_name="서울특별시 마포구",
)


def make_deal(name="성산시영", date="2026-07-01", price=52000, floor=5):
    """매매 실거래 가짜 1건."""
    return models.Deal(
        name=name, district="성산동", area_m2=50.0, floor=floor,
        price_10k=price, deal_date=date, build_year=1986,
    )


def make_building(purps="공동주택", apr_day="19860401"):
    """건축물대장 표제부 가짜 1건."""
    return models.BuildingTitle(
        main_purps=purps, etc_purps=None, tot_area=1000.0,
        grnd_flr_cnt=5, ugrnd_flr_cnt=1, use_apr_day=apr_day,
        regstr_kind="일반", plat_plc="서울 마포구 성산동 200-1",
    )


def make_result(
    *, deals=(), ratio=None, median=None, deposit_used=None,
    building=None, address=ADDRESS, scanned_at="2026-07-28T07:00:00+09:00",
    trades_unavailable=False,
):
    """watch가 소비할 가짜 ScanResult를 만든다."""
    if address is None:
        # 주소 미확정 → 후속 섹션 전부 skipped (pipeline의 무결과 형태)
        sections = {"address": models.SectionResult(status="unavailable", note="다중 후보로 확정 불가")}
        for key in ("building", "trades", "rents"):
            sections[key] = models.SectionResult(status="skipped", note="주소 미확정")
        return models.ScanResult(
            query={}, scanned_at=scanned_at, address=None,
            sections=sections, metrics=None, signals=[],
        )
    sections = {
        "address": models.SectionResult(status="ok", data=address),
        "building": (
            models.SectionResult(status="ok", data=building)
            if building is not None
            else models.SectionResult(status="unavailable", note="대장 조회 실패")
        ),
        "trades": (
            models.SectionResult(status="unavailable", note="실거래 조회 실패")
            if trades_unavailable
            else models.SectionResult(status="ok", data=list(deals))
        ),
        "rents": models.SectionResult(status="ok", data=[]),
    }
    metrics = models.Metrics(
        trade_median_10k=median, trade_sample=len(deals),
        deposit_median_10k=None, jeonse_sample=0,
        deposit_used_10k=deposit_used,
        deposit_basis="입력값" if deposit_used is not None else "산출 불가",
        jeonse_ratio=ratio, match_scope="단지", months_covered=12,
    )
    return models.ScanResult(
        query={}, scanned_at=scanned_at, address=address,
        sections=sections, metrics=metrics, signals=[],
    )


@pytest.fixture
def env(tmp_path):
    """watchlist 1건짜리 임시 작업 환경."""
    watchlist = tmp_path / "watchlist.toml"
    watchlist.write_text(
        "[[entry]]\n"
        'id = "home"\n'
        'address = "서울 마포구 성산동 200-1"\n'
        "deposit_10k = 35000\n"
        'unit_name = "성산시영"\n'
        "area_m2 = 50.0\n",
        encoding="utf-8",
    )
    return {
        "watchlist": watchlist,
        "snapshots": tmp_path / "snapshots",
        "reports": tmp_path / "reports",
    }


def run(env):
    return watch.run_watch(
        watchlist_path=env["watchlist"],
        snapshots_dir=env["snapshots"],
        reports_dir=env["reports"],
    )


def patch_scan(monkeypatch, result, render_text="# fake report\n"):
    """watch.pipeline.scan / watch.report.render를 가짜로 대체하고 호출 기록을 반환."""
    calls = []

    def fake_scan(address, **kwargs):
        calls.append((address, kwargs))
        return result

    monkeypatch.setattr(watch.pipeline, "scan", fake_scan, raising=False)
    monkeypatch.setattr(watch.report, "render", lambda r: render_text, raising=False)
    return calls


def read_snapshot(env, entry_id="home"):
    return json.loads(
        (env["snapshots"] / f"{entry_id}.json").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------- 첫 실행


def test_first_run_saves_baseline_without_events(env, monkeypatch):
    """첫 실행(스냅샷 없음)은 기준선 저장 + 리포트 갱신만 하고 이벤트가 없다."""
    result = make_result(
        deals=[make_deal()], ratio=0.7, median=52000, deposit_used=35000,
        building=make_building(),
    )
    calls = patch_scan(monkeypatch, result)

    events = run(env)

    assert events == []
    snapshot = read_snapshot(env)
    assert snapshot["date"] == "2026-07-28"
    assert len(snapshot["deal_keys"]) == 1
    assert snapshot["trade_median_10k"] == 52000
    assert snapshot["jeonse_ratio"] == 0.7
    assert snapshot["building_fingerprint"] == "공동주택|19860401"
    assert (env["reports"] / "home-latest.md").read_text(encoding="utf-8") == "# fake report\n"
    # watchlist 항목 파라미터가 scan에 그대로 전달됐는지
    address, kwargs = calls[0]
    assert address == "서울 마포구 성산동 200-1"
    assert kwargs["deposit_10k"] == 35000
    assert kwargs["unit_name"] == "성산시영"
    assert kwargs["area_m2"] == 50.0
    assert kwargs["asset_type"] == "apartment"


# ---------------------------------------------------------------- 신규 거래


def test_new_trade_detected(env, monkeypatch):
    """직전 스냅샷에 없던 deal_key가 나타나면 new_trade 이벤트 1건으로 묶인다."""
    patch_scan(monkeypatch, make_result(deals=[make_deal()], ratio=0.7, median=52000))
    run(env)

    second = make_result(
        deals=[make_deal(), make_deal(date="2026-07-20", price=58000, floor=9)],
        ratio=0.7, median=55000,
    )
    patch_scan(monkeypatch, second)
    events = run(env)

    assert [e.kind for e in events] == ["new_trade"]
    event = events[0]
    assert event.entry_id == "home"
    assert "1건" in event.title
    assert "58,000만원" in event.detail
    assert "2026-07-20" in event.detail
    assert len(read_snapshot(env)["deal_keys"]) == 2


def test_trades_unavailable_no_false_new_trade_after_recovery(env, monkeypatch):
    """trades 섹션 강등 실행이 baseline을 파괴해 복구 후 오탐을 내면 안 된다.

    실패 실행은 deal_keys를 null로 기록하고, null이 낀 비교는 건너뛴다.
    복구 실행이 baseline을 재수립한 뒤에는 정상 감지가 재개된다.
    """
    patch_scan(monkeypatch, make_result(deals=[make_deal()], ratio=0.7, median=52000))
    run(env)

    # 실거래 조회만 실패한 실행: 이벤트 없음, deal_keys는 null로 기록
    patch_scan(monkeypatch, make_result(trades_unavailable=True))
    assert run(env) == []
    assert read_snapshot(env)["deal_keys"] is None

    # 복구 실행: 기존 거래가 그대로 다시 보여도 신규로 오탐하지 않는다
    patch_scan(monkeypatch, make_result(deals=[make_deal()], ratio=0.7, median=52000))
    assert run(env) == []
    assert read_snapshot(env)["deal_keys"] is not None

    # baseline 재수립 후에는 진짜 신규 거래를 정상 감지한다
    patch_scan(
        monkeypatch,
        make_result(
            deals=[make_deal(), make_deal(date="2026-07-25", price=60000, floor=3)],
            ratio=0.7, median=56000,
        ),
    )
    assert [e.kind for e in run(env)] == ["new_trade"]


def test_empty_ok_trades_still_detects_first_deal(env, monkeypatch):
    """조회 성공·거래 0건([])은 실패(null)와 달리 정상 baseline — 첫 거래는 new_trade."""
    patch_scan(monkeypatch, make_result(deals=[]))
    run(env)
    assert read_snapshot(env)["deal_keys"] == []

    patch_scan(monkeypatch, make_result(deals=[make_deal()], median=52000))
    events = run(env)
    assert [e.kind for e in events] == ["new_trade"]


def test_same_deals_no_event(env, monkeypatch):
    """거래 목록이 그대로면 이벤트가 없다."""
    result = make_result(deals=[make_deal()], ratio=0.7, median=52000)
    patch_scan(monkeypatch, result)
    run(env)
    patch_scan(monkeypatch, result)
    assert run(env) == []


# ---------------------------------------------------------------- 전세가율 경계


def test_ratio_crossing_up_emits_event(env, monkeypatch):
    """전세가율이 0.80 경계를 아래→위로 통과하면 ratio_crossed."""
    patch_scan(
        monkeypatch,
        make_result(deals=[make_deal()], ratio=0.75, median=52000, deposit_used=39000),
    )
    run(env)

    patch_scan(
        monkeypatch,
        make_result(deals=[make_deal()], ratio=0.85, median=46000, deposit_used=39000),
    )
    events = run(env)

    assert [e.kind for e in events] == ["ratio_crossed"]
    event = events[0]
    assert "0.85" in event.title
    assert "0.75" in event.detail
    assert "39,000만원" in event.detail  # 산출 근거 포함


def test_ratio_not_crossing_no_event(env, monkeypatch):
    """이미 경계 위에 머무르거나 위→아래로 내려가는 경우는 이벤트가 아니다."""
    patch_scan(monkeypatch, make_result(deals=[make_deal()], ratio=0.85, median=46000))
    run(env)

    # 위에서 위로 (0.85 → 0.86): 통과 아님
    patch_scan(monkeypatch, make_result(deals=[make_deal()], ratio=0.86, median=45500))
    assert run(env) == []

    # 위에서 아래로 (0.86 → 0.75): 하향은 이벤트 아님
    patch_scan(monkeypatch, make_result(deals=[make_deal()], ratio=0.75, median=52000))
    assert run(env) == []


def test_ratio_none_no_event(env, monkeypatch):
    """이전 또는 현재 ratio가 None이면 통과 판정을 하지 않는다."""
    patch_scan(monkeypatch, make_result(deals=[make_deal()], ratio=None, median=52000))
    run(env)
    patch_scan(monkeypatch, make_result(deals=[make_deal()], ratio=0.85, median=46000))
    assert run(env) == []


# ---------------------------------------------------------------- 대장 변화


def test_building_changed_detected(env, monkeypatch):
    """건축물대장 지문(주용도|사용승인일) 변화 시 building_changed."""
    patch_scan(monkeypatch, make_result(deals=[make_deal()], building=make_building()))
    run(env)

    patch_scan(
        monkeypatch,
        make_result(deals=[make_deal()], building=make_building(purps="근린생활시설")),
    )
    events = run(env)

    assert [e.kind for e in events] == ["building_changed"]
    assert "근린생활시설" in events[0].detail
    assert "공동주택" in events[0].detail


def test_building_unavailable_not_a_change(env, monkeypatch):
    """대장 조회가 실패해 지문이 비면 변화로 보지 않는다 (소음 방지)."""
    patch_scan(monkeypatch, make_result(deals=[make_deal()], building=make_building()))
    run(env)
    patch_scan(monkeypatch, make_result(deals=[make_deal()], building=None))
    assert run(env) == []


# ---------------------------------------------------------------- 스캔 실패


def test_scan_failed_preserves_snapshot_and_report(env, monkeypatch):
    """주소 미확정 무결과 → scan_failed 이벤트, 스냅샷·리포트는 그대로 보존."""
    patch_scan(monkeypatch, make_result(deals=[make_deal()], ratio=0.7, median=52000))
    run(env)
    snapshot_before = (env["snapshots"] / "home.json").read_bytes()

    # 실패 실행에서는 render가 다른 내용을 돌려주게 해, 리포트가 안 덮였음을 검증
    patch_scan(monkeypatch, make_result(address=None), render_text="# must not be written\n")
    events = run(env)

    assert [e.kind for e in events] == ["scan_failed"]
    assert events[0].entry_id == "home"
    assert "다중 후보" in events[0].detail
    assert (env["snapshots"] / "home.json").read_bytes() == snapshot_before
    assert (env["reports"] / "home-latest.md").read_text(encoding="utf-8") == "# fake report\n"


def test_scan_exception_becomes_scan_failed(env, monkeypatch):
    """scan이 예외를 던져도 scan_failed 이벤트로 흡수하고 스냅샷을 보존한다."""
    patch_scan(monkeypatch, make_result(deals=[make_deal()], ratio=0.7, median=52000))
    run(env)
    snapshot_before = (env["snapshots"] / "home.json").read_bytes()

    def boom(address, **kwargs):
        raise SourceError("네트워크 오류: 프록시 응답 없음")

    monkeypatch.setattr(watch.pipeline, "scan", boom, raising=False)
    events = run(env)

    assert [e.kind for e in events] == ["scan_failed"]
    assert "네트워크 오류" in events[0].detail
    assert (env["snapshots"] / "home.json").read_bytes() == snapshot_before


def test_first_run_failure_no_snapshot_created(env, monkeypatch):
    """첫 실행부터 실패하면 스냅샷 파일 자체를 만들지 않는다."""
    patch_scan(monkeypatch, make_result(address=None))
    events = run(env)
    assert [e.kind for e in events] == ["scan_failed"]
    assert not (env["snapshots"] / "home.json").exists()


# ---------------------------------------------------------------- watchlist 파싱


def test_example_watchlist_parses():
    """저장소의 watchlist.example.toml이 계약 스키마대로 파싱된다."""
    path = Path(__file__).resolve().parents[1] / "watchlist.example.toml"
    with open(path, "rb") as fp:
        data = tomllib.load(fp)
    assert data["entry"], "예시 watchlist에 [[entry]]가 최소 1건 있어야 한다"
    for entry in data["entry"]:
        assert entry["id"] and entry["address"]


def test_watchlist_missing_required_field_raises(tmp_path, monkeypatch):
    """id/address가 빠진 entry는 즉시 ValueError."""
    bad = tmp_path / "watchlist.toml"
    bad.write_text('[[entry]]\nid = "home"\n', encoding="utf-8")
    patch_scan(monkeypatch, make_result(deals=[make_deal()]))
    with pytest.raises(ValueError):
        watch.run_watch(
            watchlist_path=bad,
            snapshots_dir=tmp_path / "snapshots",
            reports_dir=tmp_path / "reports",
        )


def test_watchlist_unsafe_or_duplicate_id_raises(tmp_path, monkeypatch):
    """경로 문자가 든 id와 중복 id는 파일명 오염을 막기 위해 즉시 ValueError."""
    patch_scan(monkeypatch, make_result(deals=[make_deal()]))
    for body in (
        '[[entry]]\nid = "a/b"\naddress = "서울 마포구 성산동 200-1"\n',
        '[[entry]]\nid = "home"\naddress = "주소1"\n\n'
        '[[entry]]\nid = "home"\naddress = "주소2"\n',
    ):
        bad = tmp_path / "watchlist.toml"
        bad.write_text(body, encoding="utf-8")
        with pytest.raises(ValueError):
            watch.run_watch(
                watchlist_path=bad,
                snapshots_dir=tmp_path / "snapshots",
                reports_dir=tmp_path / "reports",
            )


def test_non_dict_snapshot_treated_as_baseline_reset(env, monkeypatch):
    """스냅샷 파일이 dict가 아닌 유효 JSON(리스트 등)이면 손상으로 보고 기준선을 재수립한다."""
    patch_scan(monkeypatch, make_result(deals=[make_deal()]))
    env["snapshots"].mkdir(parents=True, exist_ok=True)
    (env["snapshots"] / "home.json").write_text("[1, 2, 3]", encoding="utf-8")
    events = run(env)
    assert events == []  # 기준선 재수립이므로 이벤트 없음
    assert isinstance(read_snapshot(env), dict)


def test_snapshot_tracks_only_matched_scope_deals(env, monkeypatch):
    """스냅샷 deal_keys는 지역구 전체가 아니라 매칭 범위(내 단지)의 거래만 담는다."""
    result = make_result(
        deals=[make_deal(name="성산시영(대우)"), make_deal(name="무관한단지", price=90000)],
    )
    result.query = {"unit_name": "성산시영", "area_m2": None}
    patch_scan(monkeypatch, result)
    run(env)
    assert len(read_snapshot(env)["deal_keys"]) == 1
