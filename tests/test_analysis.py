"""analysis.compute_metrics 테스트 — 실측 fixture 기반, 네트워크 없음."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import pytest

from jeonse_guard import analysis
from jeonse_guard.models import Deal, RentDeal

FIXTURES = Path(__file__).parent / "fixtures"


def load_trades() -> list[Deal]:
    """실측 매매 fixture(2025-06 마포구)를 Deal 목록으로 변환."""
    items = json.loads((FIXTURES / "trades_11440_202506.json").read_text(encoding="utf-8"))["items"]
    return [
        Deal(
            name=item["name"],
            district=item["district"],
            area_m2=item.get("area_m2"),
            floor=item.get("floor"),
            price_10k=item["price_10k"],
            deal_date=item["deal_date"],
            build_year=item.get("build_year"),
        )
        for item in items
    ]


def load_rents() -> list[RentDeal]:
    """실측 전월세 fixture(2025-06 마포구)를 RentDeal 목록으로 변환."""
    items = json.loads((FIXTURES / "rents_11440_202506.json").read_text(encoding="utf-8"))["items"]
    return [
        RentDeal(
            name=item["name"],
            district=item["district"],
            area_m2=item.get("area_m2"),
            floor=item.get("floor"),
            deposit_10k=item["deposit_10k"],
            monthly_rent_10k=item["monthly_rent_10k"],
            deal_date=item["deal_date"],
            contract_type=item.get("contract_type") or None,
        )
        for item in items
    ]


def in_band(area, target=50.0, tol=0.15):
    return area is not None and target * (1 - tol) <= area <= target * (1 + tol)


def test_unit_and_area_scope_with_paren_names():
    """실데이터: 사용자 입력 '성산시영'이 '성산시영(대우)' 등 괄호 이름과 매칭돼야 한다."""
    trades, rents = load_trades(), load_rents()
    metrics = analysis.compute_metrics(
        trades, rents,
        unit_name="성산시영", area_m2=50.0,
        deposit_10k=35000, months_covered=12, district="서울특별시 마포구",
    )
    assert metrics.match_scope == "단지+면적"

    expected_prices = [
        t.price_10k for t in trades if "성산시영" in t.name and in_band(t.area_m2)
    ]
    assert metrics.trade_sample == len(expected_prices) > 0
    assert metrics.trade_median_10k == round(statistics.median(expected_prices))

    expected_deposits = [
        r.deposit_10k for r in rents
        if "성산시영" in r.name and in_band(r.area_m2) and r.monthly_rent_10k == 0
    ]
    assert metrics.jeonse_sample == len(expected_deposits) > 0
    assert metrics.deposit_median_10k == round(statistics.median(expected_deposits))

    assert metrics.deposit_used_10k == 35000
    assert metrics.deposit_basis == "입력값"
    assert metrics.jeonse_ratio == pytest.approx(35000 / metrics.trade_median_10k, abs=1e-3)
    assert metrics.months_covered == 12


def test_name_normalization_ignores_spaces():
    """공백이 낀 입력 '성산 시영'도 정규화 부분일치로 매칭돼야 한다."""
    trades, rents = load_trades(), load_rents()
    metrics = analysis.compute_metrics(
        trades, rents,
        unit_name="성산 시영", area_m2=None,
        deposit_10k=None, months_covered=12,
    )
    assert metrics.match_scope == "단지"
    assert metrics.trade_sample == sum(1 for t in trades if "성산시영" in t.name)
    assert metrics.jeonse_sample == sum(
        1 for r in rents if "성산시영" in r.name and r.monthly_rent_10k == 0
    )


def test_backoff_unknown_unit_falls_to_area():
    """단지명이 어떤 표본과도 안 맞으면 단지+면적 → 단지 → 면적으로 되돌린다."""
    trades, rents = load_trades(), load_rents()
    metrics = analysis.compute_metrics(
        trades, rents,
        unit_name="존재하지않는단지명제로", area_m2=50.0,
        deposit_10k=None, months_covered=12,
    )
    assert metrics.match_scope == "면적"
    assert metrics.trade_sample == sum(1 for t in trades if in_band(t.area_m2))


def test_backoff_unknown_unit_no_area_falls_to_district():
    """면적 입력이 없으면 단지 → 지역구로 되돌린다."""
    trades, rents = load_trades(), load_rents()
    metrics = analysis.compute_metrics(
        trades, rents,
        unit_name="존재하지않는단지명제로", area_m2=None,
        deposit_10k=None, months_covered=12,
    )
    assert metrics.match_scope == "지역구"
    assert metrics.trade_sample == len(trades)


def test_area_only_scope():
    """단지명 없이 면적만 있으면 면적 매칭부터 시작한다."""
    trades, rents = load_trades(), load_rents()
    metrics = analysis.compute_metrics(
        trades, rents,
        unit_name=None, area_m2=50.0,
        deposit_10k=None, months_covered=6,
    )
    assert metrics.match_scope == "면적"
    assert metrics.months_covered == 6


def test_deposit_falls_back_to_jeonse_median():
    """입력 보증금이 없으면 전세 실거래 중위를 분자로 쓴다 (basis 명기)."""
    trades, rents = load_trades(), load_rents()
    metrics = analysis.compute_metrics(
        trades, rents,
        unit_name="성산시영", area_m2=50.0,
        deposit_10k=None, months_covered=12,
    )
    assert metrics.deposit_basis == "전세 실거래 중위"
    assert metrics.deposit_used_10k == metrics.deposit_median_10k
    assert metrics.jeonse_ratio == pytest.approx(
        metrics.deposit_median_10k / metrics.trade_median_10k, abs=1e-3
    )


def test_empty_data_yields_honest_nones():
    """표본이 전혀 없으면 None/0/'산출 불가'로 정직하게 채운다."""
    metrics = analysis.compute_metrics(
        [], [],
        unit_name="성산시영", area_m2=50.0,
        deposit_10k=None, months_covered=12,
    )
    assert metrics.match_scope == "지역구"  # 사다리 끝까지 되돌림
    assert metrics.trade_median_10k is None
    assert metrics.trade_sample == 0
    assert metrics.deposit_median_10k is None
    assert metrics.jeonse_sample == 0
    assert metrics.deposit_used_10k is None
    assert metrics.deposit_basis == "산출 불가"
    assert metrics.jeonse_ratio is None


def test_monthly_rent_excluded_from_jeonse_sample():
    """monthly_rent_10k > 0 인 월세 건은 전세 표본에서 제외한다."""
    rents = [
        RentDeal("테스트단지", "성산동", 50.0, 3, 30000, 0, "2025-06-01", None),
        RentDeal("테스트단지", "성산동", 50.0, 5, 28000, 0, "2025-06-02", None),
        RentDeal("테스트단지", "성산동", 50.0, 7, 10000, 80, "2025-06-03", None),
    ]
    metrics = analysis.compute_metrics(
        [], rents,
        unit_name="테스트단지", area_m2=None,
        deposit_10k=None, months_covered=12,
    )
    assert metrics.jeonse_sample == 2
    assert metrics.deposit_median_10k == 29000


def test_scope_kept_when_only_jeonse_matches():
    """단지 수준에서 매매 0건이어도 전세 표본이 있으면 되돌리지 않는다 (지역구 중위 오염 방지)."""
    trades = [Deal("다른단지", "성산동", 84.9, 10, 150000, "2025-06-01", 2010)]
    rents = [RentDeal("테스트단지", "성산동", 50.0, 3, 30000, 0, "2025-06-01", None)]
    metrics = analysis.compute_metrics(
        trades, rents,
        unit_name="테스트단지", area_m2=None,
        deposit_10k=None, months_covered=12,
    )
    assert metrics.match_scope == "단지"
    assert metrics.trade_sample == 0
    assert metrics.trade_median_10k is None
    assert metrics.jeonse_sample == 1
    assert metrics.jeonse_ratio is None  # 매매 중위가 없으므로 산출 불가
