"""지표 산출 — 실거래 표본을 매칭 사다리로 좁혀 전세가율 등 Metrics를 계산한다.

매칭 사다리(좁은 순): 단지+면적 → 단지 → 면적 → 지역구.
좁힌 결과 표본이 0건이면 한 단계 되돌린다 (CONTRACTS.md A3).
"""

from __future__ import annotations

import re
import statistics
from typing import Optional, Sequence, Union

from .models import Deal, Metrics, RentDeal

# 괄호(반각·전각)와 그 안의 내용, 모든 공백을 제거해 단지명을 정규화한다.
_PAREN_RE = re.compile(r"[(（][^)）]*[)）]")
_SPACE_RE = re.compile(r"\s+")

# 면적대 매칭 허용 오차 (±15%)
AREA_TOLERANCE = 0.15


def _normalize_name(name: Optional[str]) -> str:
    """단지명 정규화 — 괄호 내용과 공백을 제거한다.

    예: "성산시영(대우)" → "성산시영", "성산 시영" → "성산시영".
    """
    if not name:
        return ""
    return _SPACE_RE.sub("", _PAREN_RE.sub("", name))


def _name_matches(unit_norm: str, name: Optional[str]) -> bool:
    """정규화 후 부분일치 — 어느 한쪽이 다른 쪽을 포함하면 매칭."""
    item_norm = _normalize_name(name)
    if not unit_norm or not item_norm:
        return False
    return unit_norm in item_norm or item_norm in unit_norm


def _area_matches(target: float, area: Optional[float]) -> bool:
    """±15% 면적대 매칭. 면적 미상 항목은 매칭하지 않는다."""
    if area is None:
        return False
    return target * (1 - AREA_TOLERANCE) <= area <= target * (1 + AREA_TOLERANCE)


def _build_ladder(unit_norm: str, area_m2: Optional[float]) -> list[str]:
    """입력에 따라 적용 가능한 매칭 사다리를 만든다 (좁은 순)."""
    if unit_norm and area_m2 is not None:
        return ["단지+면적", "단지", "면적", "지역구"]
    if unit_norm:
        return ["단지", "지역구"]
    if area_m2 is not None:
        return ["면적", "지역구"]
    return ["지역구"]


def _filter_by_scope(
    items: Sequence[Union[Deal, RentDeal]],
    scope: str,
    unit_norm: str,
    area_m2: Optional[float],
) -> list:
    """scope 수준으로 표본을 좁힌다."""
    if scope == "단지+면적":
        assert area_m2 is not None
        return [
            x for x in items
            if _name_matches(unit_norm, x.name) and _area_matches(area_m2, x.area_m2)
        ]
    if scope == "단지":
        return [x for x in items if _name_matches(unit_norm, x.name)]
    if scope == "면적":
        assert area_m2 is not None
        return [x for x in items if _area_matches(area_m2, x.area_m2)]
    return list(items)  # 지역구 — 필터 없음


def compute_metrics(
    trades: list[Deal],
    rents: list[RentDeal],
    *,
    unit_name: Optional[str],
    area_m2: Optional[float],
    deposit_10k: Optional[int],
    months_covered: int,
    district: str = "",
    trade_months_ok: Optional[int] = None,
    rent_months_ok: Optional[int] = None,
) -> Metrics:
    """매칭 사다리로 좁힌 표본에서 매매·전세 중위값과 전세가율을 산출한다.

    - 전세 표본은 monthly_rent_10k == 0 인 건만 쓴다.
    - 사다리의 각 단계에서 (매매 + 전세) 표본 합이 0건이면 한 단계 되돌린다.
    - deposit_used_10k 는 입력값 우선, 없으면 전세 실거래 중위 (deposit_basis에 명기).
    - jeonse_ratio = deposit_used / trade_median — 둘 중 하나라도 없으면 None.
    - district 는 기록·확장용 파라미터로 산출에는 쓰지 않는다.
    """
    jeonse_all = [r for r in rents if r.monthly_rent_10k == 0]
    unit_norm = _normalize_name(unit_name)

    ladder = _build_ladder(unit_norm, area_m2)
    scope = ladder[-1]
    matched_trades: list[Deal] = []
    matched_jeonse: list[RentDeal] = []
    for candidate in ladder:
        matched_trades = _filter_by_scope(trades, candidate, unit_norm, area_m2)
        matched_jeonse = _filter_by_scope(jeonse_all, candidate, unit_norm, area_m2)
        if matched_trades or matched_jeonse:
            scope = candidate
            break

    trade_prices = [t.price_10k for t in matched_trades]
    jeonse_deposits = [r.deposit_10k for r in matched_jeonse]

    trade_median = round(statistics.median(trade_prices)) if trade_prices else None
    deposit_median = round(statistics.median(jeonse_deposits)) if jeonse_deposits else None

    if deposit_10k is not None:
        deposit_used: Optional[int] = deposit_10k
        deposit_basis = "입력값"
    elif deposit_median is not None:
        deposit_used = deposit_median
        deposit_basis = "전세 실거래 중위"
    else:
        deposit_used = None
        deposit_basis = "산출 불가"

    jeonse_ratio: Optional[float] = None
    if deposit_used is not None and trade_median:
        jeonse_ratio = round(deposit_used / trade_median, 4)

    return Metrics(
        trade_median_10k=trade_median,
        trade_sample=len(trade_prices),
        deposit_median_10k=deposit_median,
        jeonse_sample=len(jeonse_deposits),
        deposit_used_10k=deposit_used,
        deposit_basis=deposit_basis,
        jeonse_ratio=jeonse_ratio,
        match_scope=scope,
        trade_months_ok=trade_months_ok,
        rent_months_ok=rent_months_ok,
        months_covered=months_covered,
    )


def filter_matched(
    items: Sequence[Union[Deal, RentDeal]],
    *,
    match_scope: str,
    unit_name: Optional[str],
    area_m2: Optional[float],
) -> list:
    """compute_metrics가 확정한 match_scope와 동일한 기준으로 목록을 좁힌다.

    워치독이 "내 단지" 거래만 추적하도록 스냅샷 단계에서 재사용한다.
    scope가 요구하는 입력이 빠진 비정상 조합이면 좁히지 않고 전체를 반환한다 —
    잘못 좁혀 빈 목록을 만드는 것보다 안전하다.
    """
    unit_norm = _normalize_name(unit_name)
    need_name = match_scope in ("단지+면적", "단지")
    need_area = match_scope in ("단지+면적", "면적")
    if (need_name and not unit_norm) or (need_area and area_m2 is None):
        return list(items)
    return _filter_by_scope(items, match_scope, unit_norm, area_m2)
