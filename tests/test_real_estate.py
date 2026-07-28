"""jeonse_guard.sources.real_estate 테스트 — 네트워크 없이 fixture 주입(monkeypatch)."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from jeonse_guard.errors import SourceError
from jeonse_guard.models import Deal, MonthlyFetch, RentDeal
from jeonse_guard.sources import real_estate

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://proxy.test"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _patch_get_json(monkeypatch, responses: dict[str, dict | Exception], calls: list | None = None):
    """deal_ymd → 응답(dict) 또는 예외 매핑으로 net.get_json을 대체한다."""

    def fake_get_json(url, params=None, *, timeout=25):
        if calls is not None:
            calls.append((url, dict(params or {})))
        result = responses[params["deal_ymd"]]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(real_estate.net, "get_json", fake_get_json)


# ---------------------------------------------------------------- recent_months


class TestRecentMonths:
    def test_당월_포함_최신월부터(self):
        assert real_estate.recent_months(3, today=datetime.date(2026, 7, 28)) == [
            "202607", "202606", "202605",
        ]

    def test_1월_경계에서_전년_12월로_넘어간다(self):
        assert real_estate.recent_months(3, today=datetime.date(2026, 1, 15)) == [
            "202601", "202512", "202511",
        ]

    def test_여러_해를_거슬러_올라간다(self):
        months = real_estate.recent_months(14, today=datetime.date(2026, 2, 1))
        assert months[0] == "202602"
        assert months[-1] == "202501"
        assert len(months) == 14

    def test_today_없으면_KST_오늘_기준(self):
        now_kst = datetime.datetime.now(real_estate.KST)
        months = real_estate.recent_months(1)
        assert months == [now_kst.strftime("%Y%m")]

    def test_n이_0이하면_빈_목록(self):
        assert real_estate.recent_months(0) == []
        assert real_estate.recent_months(-2) == []


# ---------------------------------------------------------------- fetch_trades


class TestFetchTrades:
    def test_실제_fixture_전건_파싱(self, monkeypatch):
        calls: list = []
        _patch_get_json(monkeypatch, {"202506": _load("trades_11440_202506.json")}, calls)
        fetch = real_estate.fetch_trades("11440", ["202506"], asset_type="apartment", base=BASE)

        assert isinstance(fetch, MonthlyFetch)
        assert fetch.months_requested == 1
        assert fetch.months_ok == 1
        assert fetch.failed_months == []
        deals = fetch.items
        assert len(deals) == 666  # fixture 전건에 price_10k가 있다
        assert all(isinstance(d, Deal) for d in deals)
        first = deals[0]
        assert first.name == "삼성"
        assert first.district == "도화동"
        assert first.price_10k == 178000
        assert first.deal_date == "2025-06-21"
        # 호출 URL·파라미터 계약 확인
        url, params = calls[0]
        assert url == f"{BASE}/v1/real-estate/apartment/trade"
        assert params == {"lawd_cd": "11440", "deal_ymd": "202506", "num_of_rows": 1000}

    def test_월별_합산과_필드_관대_처리(self, monkeypatch):
        _patch_get_json(monkeypatch, {
            "202506": _load("trades_11440_202506.json"),
            "202505": _load("trades_11440_202505_small.json"),
        })
        fetch = real_estate.fetch_trades(
            "11440", ["202506", "202505"], asset_type="apartment", base=BASE
        )
        deals = fetch.items
        # 666건 + 소형 fixture 4건 중 price 누락 1건 버림 → 3건
        assert len(deals) == 666 + 3
        # price 누락 행은 버려졌다
        assert all(d.name != "가격누락행" for d in deals)
        # 다른 필드 누락은 None으로 관대 처리
        lenient = next(d for d in deals if d.name == "필드누락행")
        assert lenient.price_10k == 71000
        assert lenient.area_m2 is None
        assert lenient.floor is None
        assert lenient.build_year is None

    def test_일부_달_실패는_건너뛰고_합산하고_통계에_기록(self, monkeypatch):
        _patch_get_json(monkeypatch, {
            "202506": SourceError("HTTP 502: upstream"),
            "202505": _load("trades_11440_202505_small.json"),
        })
        fetch = real_estate.fetch_trades(
            "11440", ["202506", "202505"], asset_type="apartment", base=BASE
        )
        assert len(fetch.items) == 3  # 실패한 달은 제외, 성공한 달만 합산
        # 부분 실패가 통계에 정직하게 남는다
        assert fetch.months_requested == 2
        assert fetch.months_ok == 1
        assert fetch.months_failed == 1
        assert fetch.failed_months == ["202506"]

    def test_전부_실패하면_SourceError에_실패_달_수(self, monkeypatch):
        _patch_get_json(monkeypatch, {
            "202506": SourceError("HTTP 502: upstream"),
            "202505": SourceError("HTTP 503: 키 미설정"),
        })
        with pytest.raises(SourceError) as exc_info:
            real_estate.fetch_trades(
                "11440", ["202506", "202505"], asset_type="apartment", base=BASE
            )
        message = str(exc_info.value)
        assert "2개월" in message
        assert "202506" in message and "202505" in message

    def test_정수값_float_price는_버리지_않는다(self, monkeypatch):
        # 프록시가 숫자를 float로 내려도(92000.0) price가 있는 행은 유지해야 한다.
        _patch_get_json(monkeypatch, {"202506": {"items": [
            {"name": "실수형", "district": "성산동", "price_10k": 92000.0,
             "floor": 9.0, "deal_date": "2025-06-01"},
            {"name": "소수점가격", "district": "성산동", "price_10k": 92000.5,
             "deal_date": "2025-06-02"},
        ]}})
        fetch = real_estate.fetch_trades("11440", ["202506"], asset_type="apartment", base=BASE)
        # 정수값 float는 유지, 소수부 있는 가격은 파싱 불가로 버림
        assert [d.name for d in fetch.items] == ["실수형"]
        assert fetch.items[0].price_10k == 92000
        assert fetch.items[0].floor == 9

    def test_빈_items는_빈_목록(self, monkeypatch):
        _patch_get_json(monkeypatch, {"202506": {"items": []}})
        fetch = real_estate.fetch_trades(
            "11440", ["202506"], asset_type="apartment", base=BASE
        )
        assert fetch.items == []
        assert fetch.months_ok == 1  # "성공했지만 거래 0건"은 실패가 아니다

    def test_잘못된_asset_type은_SourceError(self, monkeypatch):
        _patch_get_json(monkeypatch, {"202506": {"items": []}})
        with pytest.raises(SourceError):
            real_estate.fetch_trades("11440", ["202506"], asset_type="hotel", base=BASE)


# ---------------------------------------------------------------- fetch_rents


class TestFetchRents:
    def test_실제_fixture_전세_월세_구분(self, monkeypatch):
        calls: list = []
        _patch_get_json(monkeypatch, {"202506": _load("rents_11440_202506.json")}, calls)
        fetch = real_estate.fetch_rents("11440", ["202506"], asset_type="apartment", base=BASE)

        rents = fetch.items
        assert len(rents) == 1000  # fixture 전건에 deposit_10k가 있다
        assert all(isinstance(r, RentDeal) for r in rents)
        jeonse = [r for r in rents if r.monthly_rent_10k == 0]
        wolse = [r for r in rents if r.monthly_rent_10k > 0]
        assert len(jeonse) == 563
        assert len(wolse) == 437
        url, _ = calls[0]
        assert url == f"{BASE}/v1/real-estate/apartment/rent"

    def test_보증금_누락_버림과_contract_type_정규화(self, monkeypatch):
        _patch_get_json(monkeypatch, {"202505": _load("rents_11440_202505_small.json")})
        fetch = real_estate.fetch_rents("11440", ["202505"], asset_type="apartment", base=BASE)

        rents = fetch.items
        # 3건 중 deposit 누락 1건 버림 → 2건
        assert len(rents) == 2
        assert all(r.name != "보증금누락행" for r in rents)
        jeonse = next(r for r in rents if r.name == "성산시영(대우)")
        assert jeonse.monthly_rent_10k == 0       # 전세
        assert jeonse.contract_type is None       # 빈 문자열 → None
        wolse = next(r for r in rents if r.name == "신촌금호")
        assert wolse.monthly_rent_10k == 100      # 월세
        assert wolse.contract_type == "신규"

    def test_일부_달_실패는_건너뛰고_합산하고_통계에_기록(self, monkeypatch):
        _patch_get_json(monkeypatch, {
            "202506": _load("rents_11440_202505_small.json"),
            "202505": SourceError("네트워크 오류: timeout"),
        })
        fetch = real_estate.fetch_rents(
            "11440", ["202506", "202505"], asset_type="apartment", base=BASE
        )
        assert len(fetch.items) == 2
        assert fetch.months_ok == 1
        assert fetch.failed_months == ["202505"]

    def test_전부_실패하면_SourceError에_실패_달_수(self, monkeypatch):
        _patch_get_json(monkeypatch, {
            "202506": SourceError("HTTP 400: 파라미터"),
        })
        with pytest.raises(SourceError) as exc_info:
            real_estate.fetch_rents("11440", ["202506"], asset_type="apartment", base=BASE)
        assert "1개월" in str(exc_info.value)
