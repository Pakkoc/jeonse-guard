"""address.resolve_address 테스트 — 네트워크 없이 net.get_json monkeypatch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jeonse_guard import net
from jeonse_guard.errors import AmbiguousAddressError
from jeonse_guard.sources import address

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://proxy.test"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def patch_geocode(monkeypatch, payload: dict, calls: list | None = None) -> None:
    """net.get_json을 fixture 반환 함수로 대체한다 (호출 인자 기록 옵션)."""

    def fake_get_json(url, params=None, *, timeout=25):
        if calls is not None:
            calls.append((url, params))
        return payload

    monkeypatch.setattr(net, "get_json", fake_get_json)


def test_resolve_single_confirms_parcel(monkeypatch):
    """정상 1건 — 실제 지오코딩 fixture로 필지가 확정된다."""
    calls: list = []
    patch_geocode(monkeypatch, load_fixture("geocode_seongsan.json"), calls)

    resolved = address.resolve_address("서울 마포구 성산동 200-1", base=BASE)

    assert resolved.raw == "서울 마포구 성산동 200-1"
    assert resolved.jibun == "서울 마포구 성산동 200-1"
    assert resolved.road == "서울 마포구 월드컵북로 190"
    assert resolved.b_code == "1144012500"
    assert resolved.mountain is False
    assert resolved.bun == "0200"
    assert resolved.ji == "0001"
    assert resolved.region_name == "서울 마포구"
    # 파생 프로퍼티
    assert resolved.sigungu_cd == "11440"
    assert resolved.bjdong_cd == "12500"
    assert resolved.lawd_cd == "11440"
    assert resolved.plat_gb_cd == "0"
    assert resolved.pnu == "1144012500102000001"
    # 프록시 지오코딩 경로·파라미터 (limit=2)
    url, params = calls[0]
    assert url == f"{BASE}/v1/kakao-local/geocode"
    assert params == {"q": "서울 마포구 성산동 200-1", "limit": 2}


def test_no_result_raises_ambiguous(monkeypatch):
    """0건 — 추정하지 않고 AmbiguousAddressError."""
    patch_geocode(
        monkeypatch,
        {"documents": [], "meta": {"is_end": True, "pageable_count": 0, "total_count": 0}},
    )
    with pytest.raises(AmbiguousAddressError) as exc_info:
        address.resolve_address("서울 어딘가 없는주소 999", base=BASE)
    assert exc_info.value.candidates == []


def test_multiple_candidates_raises_ambiguous(monkeypatch):
    """2건 이상 — 후보 목록을 담은 AmbiguousAddressError."""
    patch_geocode(monkeypatch, load_fixture("geocode_ambiguous.json"))
    with pytest.raises(AmbiguousAddressError) as exc_info:
        address.resolve_address("서울 마포구 성산동 200", base=BASE)
    assert "서울 마포구 성산동 200-1" in exc_info.value.candidates
    assert "서울 마포구 성산동 200-2" in exc_info.value.candidates


def test_missing_parcel_object_raises_ambiguous(monkeypatch):
    """도로명만 매칭되어 address(지번) 객체가 없으면 AmbiguousAddressError."""
    payload = load_fixture("geocode_seongsan.json")
    payload["documents"][0]["address"] = None
    patch_geocode(monkeypatch, payload)
    with pytest.raises(AmbiguousAddressError):
        address.resolve_address("서울 마포구 월드컵북로 190", base=BASE)


def test_missing_main_address_no_raises_ambiguous(monkeypatch):
    """본번이 없으면 필지를 확정할 수 없다 — AmbiguousAddressError."""
    payload = load_fixture("geocode_seongsan.json")
    payload["documents"][0]["address"]["main_address_no"] = ""
    patch_geocode(monkeypatch, payload)
    with pytest.raises(AmbiguousAddressError):
        address.resolve_address("서울 마포구 성산동", base=BASE)


def test_mountain_and_empty_sub_address(monkeypatch):
    """산 지번 + 부번 없음 — mountain=True, ji='0000'으로 정규화."""
    payload = load_fixture("geocode_seongsan.json")
    addr = payload["documents"][0]["address"]
    addr["mountain_yn"] = "Y"
    addr["sub_address_no"] = ""
    patch_geocode(monkeypatch, payload)

    resolved = address.resolve_address("서울 마포구 성산동 산200", base=BASE)

    assert resolved.mountain is True
    assert resolved.ji == "0000"
    assert resolved.plat_gb_cd == "1"
    assert resolved.pnu == "1144012500202000000"
