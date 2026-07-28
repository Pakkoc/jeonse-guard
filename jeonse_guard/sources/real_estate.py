"""국토교통부 실거래가 어댑터 — k-skill-proxy 경유로 매매/전월세를 월별 조회한다.

참고: NomaDamas/k-skill real-estate-search (MIT)

프록시 명세 (CONTRACTS.md):
- 매매:   GET {base}/v1/real-estate/{asset}/trade?lawd_cd=..&deal_ymd=YYYYMM&num_of_rows=1000
- 전월세: GET {base}/v1/real-estate/{asset}/rent?...  (deposit_10k, monthly_rent_10k, contract_type 추가)
금액 단위는 전부 만원. 데이터가 없으면 빈 items.
"""

from __future__ import annotations

import datetime
from typing import Any, Callable, Optional

from .. import net
from ..errors import SourceError
from ..models import Deal, MonthlyFetch, RentDeal

KST = datetime.timezone(datetime.timedelta(hours=9))

ASSET_TYPES = ("apartment", "villa", "officetel", "single-house")


def recent_months(n: int, *, today: datetime.date | None = None) -> list[str]:
    """KST 기준 당월 포함, 최신월부터 n개의 "YYYYMM" 목록을 반환한다.

    실거래 신고 지연(계약 후 30일)을 고려해 당월을 포함한다.
    today=None이면 KST 오늘 날짜를 쓴다.
    """
    if n <= 0:
        return []
    if today is None:
        today = datetime.datetime.now(KST).date()
    months: list[str] = []
    year, month = today.year, today.month
    for _ in range(n):
        months.append(f"{year:04d}{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return months


def fetch_trades(
    lawd_cd: str, months: list[str], *, asset_type: str, base: str, timeout: int = 25
) -> MonthlyFetch:
    """매매 실거래를 월별로 순회 조회해 합산한다 (items는 Deal 목록).

    일부 달이 실패해도 나머지 달로 계속하고, 전부 실패하면 SourceError.
    부분 실패는 MonthlyFetch의 months_ok/failed_months에 정직하게 기록된다.
    price_10k 없는 행은 버리고, 나머지 필드 누락은 None으로 관대하게 처리한다.
    """
    return _fetch_months("trade", lawd_cd, months, asset_type, base, timeout, _parse_deal)


def fetch_rents(
    lawd_cd: str, months: list[str], *, asset_type: str, base: str, timeout: int = 25
) -> MonthlyFetch:
    """전월세 실거래를 월별로 순회 조회해 합산한다 (items는 RentDeal 목록).

    일부 달이 실패해도 나머지 달로 계속하고, 전부 실패하면 SourceError.
    부분 실패는 MonthlyFetch의 months_ok/failed_months에 정직하게 기록된다.
    deposit_10k 없는 행은 버린다. monthly_rent_10k == 0이 전세다.
    """
    return _fetch_months("rent", lawd_cd, months, asset_type, base, timeout, _parse_rent)


# ---------------------------------------------------------------- 내부 헬퍼


def _fetch_months(
    kind: str,
    lawd_cd: str,
    months: list[str],
    asset_type: str,
    base: str,
    timeout: int,
    parse: Callable[[dict], Any],
) -> MonthlyFetch:
    """월별 호출을 순회하며 items를 파싱·합산한다. 전 달 실패 시 SourceError."""
    if asset_type not in ASSET_TYPES:
        raise SourceError(f"지원하지 않는 asset_type: {asset_type} (허용: {', '.join(ASSET_TYPES)})")
    url = f"{base}/v1/real-estate/{asset_type}/{kind}"
    rows: list = []
    failed: list[str] = []
    for deal_ymd in months:
        try:
            payload = net.get_json(
                url,
                {"lawd_cd": lawd_cd, "deal_ymd": deal_ymd, "num_of_rows": 1000},
                timeout=timeout,
            )
        except SourceError:
            failed.append(deal_ymd)
            continue
        items = payload.get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            row = parse(item)
            if row is not None:
                rows.append(row)
    if months and len(failed) == len(months):
        raise SourceError(
            f"실거래 {kind} 조회 전체 실패: {len(failed)}개월 모두 실패 ({', '.join(failed)})"
        )
    return MonthlyFetch(
        items=rows,
        months_requested=len(months),
        months_ok=len(months) - len(failed),
        failed_months=failed,
    )


def _parse_deal(item: dict) -> Optional[Deal]:
    """매매 항목 1건 파싱. price_10k 없으면 None(버림)."""
    price = _to_int(item.get("price_10k"))
    if price is None:
        return None
    return Deal(
        name=str(item.get("name") or ""),
        district=str(item.get("district") or ""),
        area_m2=_to_float(item.get("area_m2")),
        floor=_to_int(item.get("floor")),
        price_10k=price,
        deal_date=str(item.get("deal_date") or ""),
        build_year=_to_int(item.get("build_year")),
    )


def _parse_rent(item: dict) -> Optional[RentDeal]:
    """전월세 항목 1건 파싱. deposit_10k 없으면 None(버림)."""
    deposit = _to_int(item.get("deposit_10k"))
    if deposit is None:
        return None
    contract_type = item.get("contract_type")
    return RentDeal(
        name=str(item.get("name") or ""),
        district=str(item.get("district") or ""),
        area_m2=_to_float(item.get("area_m2")),
        floor=_to_int(item.get("floor")),
        deposit_10k=deposit,
        monthly_rent_10k=_to_int(item.get("monthly_rent_10k")) or 0,
        deal_date=str(item.get("deal_date") or ""),
        contract_type=str(contract_type) if contract_type else None,
    )


def _to_int(value: Any) -> Optional[int]:
    """정수 변환. 실패·빈 값은 None (관대 처리).

    정수값 실수(178000.0, "178000.0")는 정수로 받아들이되,
    소수부가 있는 값(84.97)은 잘못된 데이터로 보고 None 처리한다.
    """
    if value is None or value == "":
        return None
    text = str(value).replace(",", "").strip()
    try:
        return int(text)
    except (TypeError, ValueError):
        pass
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return None


def _to_float(value: Any) -> Optional[float]:
    """실수 변환. 실패·빈 값은 None (관대 처리)."""
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
