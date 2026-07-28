"""building.fetch_title 테스트 — 이중 모드(프록시/직접 XML)를 네트워크 없이 검증."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jeonse_guard import net
from jeonse_guard.errors import SourceError
from jeonse_guard.models import ResolvedAddress
from jeonse_guard.sources import building

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://proxy.test"


def make_addr() -> ResolvedAddress:
    return ResolvedAddress(
        raw="서울 마포구 성산동 200-1",
        jibun="서울 마포구 성산동 200-1",
        road="서울 마포구 월드컵북로 190",
        b_code="1144012500",
        mountain=False,
        bun="0200",
        ji="0001",
        region_name="서울 마포구",
    )


def forbid_get_text(url, params=None, *, timeout=30):
    raise AssertionError("프록시 성공 경로에서 get_text(직접 모드)가 호출되면 안 된다")


def raise_404(url, params=None, *, timeout=30):
    raise SourceError('HTTP 404: {"error":"not_found","message":"Route /v1/building-register/title not found"}')


def clear_keys(monkeypatch) -> None:
    monkeypatch.delenv("KSKILL_BUILDING_REGISTER_API_KEY", raising=False)
    monkeypatch.delenv("DATA_GO_KR_API_KEY", raising=False)


def test_proxy_ok_picks_max_area_and_keeps_all_dongs(monkeypatch):
    """프록시 정상 + 다동 건물 — 연면적 최대 동을 대표로, 전 동은 raw에 보존."""
    payload = json.loads((FIXTURES / "title_seongsan_proxy.json").read_text(encoding="utf-8"))
    calls: list = []

    def fake_get_json(url, params=None, *, timeout=30):
        calls.append((url, params))
        return payload

    monkeypatch.setattr(net, "get_json", fake_get_json)
    monkeypatch.setattr(net, "get_text", forbid_get_text)

    title = building.fetch_title(make_addr(), base=BASE)

    # 프록시 경로·파라미터 (bun/ji 4자리 zero-pad)
    url, params = calls[0]
    assert url == f"{BASE}/v1/building-register/title"
    assert params["sigunguCd"] == "11440"
    assert params["bjdongCd"] == "12500"
    assert params["platGbCd"] == "0"
    assert params["bun"] == "0200"
    assert params["ji"] == "0001"
    # 대표 = 연면적 최대 동(14동, 12849.98㎡)
    assert title.main_purps == "공동주택"
    assert title.etc_purps == "아파트"
    assert title.tot_area == 12849.98
    assert title.grnd_flr_cnt == 14
    assert title.ugrnd_flr_cnt == 1
    assert title.use_apr_day == "19860630"
    assert title.regstr_kind == "표제부"
    assert title.plat_plc == "서울특별시 마포구 성산동 200-1"
    # 전 동 보존
    assert title.raw["mode"] == "proxy"
    assert len(title.raw["items"]) == 2
    assert {item["dongNm"] for item in title.raw["items"]} == {"1동", "14동"}


def test_proxy_404_without_key_raises_guidance(monkeypatch):
    """프록시 404 + 키 없음 — 키 발급 안내 SourceError."""
    clear_keys(monkeypatch)
    monkeypatch.setattr(net, "get_json", raise_404)
    monkeypatch.setattr(net, "get_text", forbid_get_text)

    with pytest.raises(SourceError) as exc_info:
        building.fetch_title(make_addr(), base=BASE)

    message = str(exc_info.value)
    assert "API 키 없음" in message
    assert "DATA_GO_KR_API_KEY" in message
    assert "15134735" in message


def test_proxy_404_with_key_falls_back_to_direct_xml(monkeypatch):
    """프록시 404 + 키 있음 — data.go.kr 직접 모드로 XML 파싱."""
    clear_keys(monkeypatch)
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "test-service-key")
    monkeypatch.setattr(net, "get_json", raise_404)

    xml_text = (FIXTURES / "title_seongsan_direct.xml").read_text(encoding="utf-8")
    calls: list = []

    def fake_get_text(url, params=None, *, timeout=30):
        calls.append((url, params))
        return xml_text

    monkeypatch.setattr(net, "get_text", fake_get_text)

    title = building.fetch_title(make_addr(), base=BASE)

    url, params = calls[0]
    assert url == "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
    assert params["serviceKey"] == "test-service-key"
    assert params["sigunguCd"] == "11440"
    assert params["bjdongCd"] == "12500"
    assert params["platGbCd"] == "0"
    assert params["bun"] == "0200"
    assert params["ji"] == "0001"
    assert params["numOfRows"] == 100
    # XML에서도 다동 보존 + 연면적 최대 대표
    assert title.main_purps == "공동주택"
    assert title.tot_area == 12849.98
    assert title.grnd_flr_cnt == 14
    assert title.use_apr_day == "19860630"
    assert title.raw["mode"] == "direct"
    assert len(title.raw["items"]) == 2


def test_key_priority_prefers_kskill_key(monkeypatch):
    """키 탐색 순서 — KSKILL_BUILDING_REGISTER_API_KEY가 DATA_GO_KR_API_KEY보다 우선."""
    monkeypatch.setenv("KSKILL_BUILDING_REGISTER_API_KEY", "kskill-key")
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "datagokr-key")
    monkeypatch.setattr(net, "get_json", raise_404)

    seen: dict = {}

    def fake_get_text(url, params=None, *, timeout=30):
        seen.update(params or {})
        return (FIXTURES / "title_seongsan_direct.xml").read_text(encoding="utf-8")

    monkeypatch.setattr(net, "get_text", fake_get_text)

    building.fetch_title(make_addr(), base=BASE)
    assert seen["serviceKey"] == "kskill-key"


def test_empty_items_raises_no_title(monkeypatch):
    """대장 빈 결과 — SourceError('표제부 없음')."""
    monkeypatch.setattr(
        net, "get_json",
        lambda url, params=None, *, timeout=30: {"page": 1, "page_size": 100, "total_count": 0, "items": []},
    )
    monkeypatch.setattr(net, "get_text", forbid_get_text)

    with pytest.raises(SourceError) as exc_info:
        building.fetch_title(make_addr(), base=BASE)
    assert "표제부 없음" in str(exc_info.value)


def test_direct_mode_xml_error_code_raises(monkeypatch):
    """직접 모드 — resultCode가 정상(00)이 아니면 SourceError."""
    clear_keys(monkeypatch)
    monkeypatch.setenv("DATA_GO_KR_API_KEY", "test-service-key")
    monkeypatch.setattr(net, "get_json", raise_404)
    error_xml = (
        "<response><header><resultCode>03</resultCode>"
        "<resultMsg>NODATA_ERROR</resultMsg></header><body/></response>"
    )
    monkeypatch.setattr(net, "get_text", lambda url, params=None, *, timeout=30: error_xml)

    with pytest.raises(SourceError) as exc_info:
        building.fetch_title(make_addr(), base=BASE)
    assert "NODATA_ERROR" in str(exc_info.value)
