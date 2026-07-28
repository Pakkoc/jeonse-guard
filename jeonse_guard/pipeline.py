"""진단 오케스트레이터 — 섹션별 실패 강등(degrade)으로 리포트를 항상 완성한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from . import analysis, signals
from .config import proxy_base
from .errors import AmbiguousAddressError, SourceError
from .models import ScanResult, SectionResult
from .sources import address as address_source
from .sources import building as building_source
from .sources import real_estate

KST = timezone(timedelta(hours=9))


def now_kst_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def scan(
    address_query: str,
    *,
    deposit_10k: Optional[int] = None,
    unit_name: Optional[str] = None,
    area_m2: Optional[float] = None,
    asset_type: str = "apartment",
    months: int = 12,
    base: Optional[str] = None,
) -> ScanResult:
    """주소 하나로 전세 안전진단 1회를 수행한다.

    어느 섹션이 실패해도 전체를 막지 않는다. 해당 섹션만
    status="unavailable" + 사유로 강등하고 나머지로 결과를 완성한다.
    """
    base = base or proxy_base()
    sections: dict[str, SectionResult] = {}
    query = {
        "address": address_query,
        "deposit_10k": deposit_10k,
        "unit_name": unit_name,
        "area_m2": area_m2,
        "asset_type": asset_type,
        "months": months,
    }

    # 1. 주소 정규화 — 유일하게 실패 시 후속 섹션이 모두 막히는 단계
    resolved = None
    try:
        resolved = address_source.resolve_address(address_query, base=base)
        sections["address"] = SectionResult(status="ok", data=resolved)
    except AmbiguousAddressError as exc:
        note = str(exc)
        if exc.candidates:
            note += " 후보: " + " / ".join(exc.candidates[:5])
        sections["address"] = SectionResult(status="unavailable", note=note)
    except SourceError as exc:
        sections["address"] = SectionResult(status="unavailable", note=str(exc))

    if resolved is None:
        for key in ("building", "trades", "rents"):
            sections[key] = SectionResult(status="skipped", note="주소 미확정")
        return ScanResult(
            query=query, scanned_at=now_kst_iso(), address=None,
            sections=sections, metrics=None,
            signals=signals.evaluate(metrics=None, building=None, sections=sections),
        )

    # 2. 건축물대장 표제부
    title = None
    try:
        title = building_source.fetch_title(resolved, base=base)
        sections["building"] = SectionResult(status="ok", data=title)
    except SourceError as exc:
        sections["building"] = SectionResult(status="unavailable", note=str(exc))

    # 3. 매매 실거래 / 4. 전월세 실거래
    month_list = real_estate.recent_months(months)
    trades: list = []
    try:
        trades = real_estate.fetch_trades(
            resolved.lawd_cd, month_list, asset_type=asset_type, base=base
        )
        sections["trades"] = SectionResult(status="ok", data=trades)
    except SourceError as exc:
        sections["trades"] = SectionResult(status="unavailable", note=str(exc))

    rents: list = []
    try:
        rents = real_estate.fetch_rents(
            resolved.lawd_cd, month_list, asset_type=asset_type, base=base
        )
        sections["rents"] = SectionResult(status="ok", data=rents)
    except SourceError as exc:
        sections["rents"] = SectionResult(status="unavailable", note=str(exc))

    # 5. 지표 산출 + 6. 신호 평가 (섹션이 비어 있어도 정직하게 산출 불가로 처리)
    metrics = analysis.compute_metrics(
        trades, rents,
        unit_name=unit_name, area_m2=area_m2,
        deposit_10k=deposit_10k, months_covered=months,
        district=resolved.jibun,
    )
    found = signals.evaluate(metrics=metrics, building=title, sections=sections)

    return ScanResult(
        query=query, scanned_at=now_kst_iso(), address=resolved,
        sections=sections, metrics=metrics, signals=found,
    )
