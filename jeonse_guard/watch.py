"""보증금 워치독 — watchlist 항목을 재스캔해 직전 스냅샷과 비교하고 변화 이벤트를 만든다.

동작 (CONTRACTS.md A4):
    항목마다 pipeline.scan 실행 → snapshots/<id>.json의 직전 스냅샷과 비교 →
    이벤트 생성 → 새 스냅샷 저장(원자적 쓰기) → 리포트를 reports/<id>-latest.md로 갱신.

규칙:
    - 첫 실행(스냅샷 없음)은 기준선 저장만 하고 이벤트를 만들지 않는다.
    - scan_failed 시 기존 스냅샷·리포트를 절대 덮어쓰지 않는다.
    - 스냅샷 쓰기는 임시 파일에 쓴 뒤 os.replace로 원자적 교체.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import analysis, pipeline, report
from .models import ScanResult

# 전세가율 경계 — 직전 값이 이 값 미만이었다가 이상이 되면 ratio_crossed
RATIO_THRESHOLD = 0.80


@dataclass
class Event:
    """watch 1회 실행에서 감지된 변화 1건. GitHub Issue 1건의 재료가 된다."""

    kind: str        # "new_trade" | "ratio_crossed" | "building_changed" | "scan_failed"
    entry_id: str    # watchlist 항목 id
    title: str       # Issue 제목으로 쓸 한 줄
    detail: str      # Issue 본문 (Markdown, 근거 포함)


_ENTRY_ID_RE = re.compile(r"^[0-9A-Za-z가-힣._-]+$")


def _load_watchlist(path: Path) -> list[dict]:
    """watchlist.toml을 파싱해 [[entry]] 목록을 반환한다. id/address 누락은 즉시 오류.

    id는 스냅샷·리포트 파일명으로 쓰이므로 안전한 문자만 허용하고 중복을 금지한다.
    """
    with open(path, "rb") as fp:
        data = tomllib.load(fp)
    entries = data.get("entry", [])
    if not entries:
        raise ValueError(f"watchlist에 [[entry]] 항목이 없습니다: {path}")
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not entry.get("id") or not entry.get("address"):
            raise ValueError(f"watchlist entry #{index}: id와 address는 필수입니다")
        entry_id = str(entry["id"])
        if not _ENTRY_ID_RE.match(entry_id):
            raise ValueError(
                f"watchlist entry #{index}: id '{entry_id}'는 파일명으로 쓸 수 없습니다"
                " (허용 문자: 영문·숫자·한글·마침표·밑줄·하이픈)"
            )
        if entry_id in seen_ids:
            raise ValueError(f"watchlist entry #{index}: id '{entry_id}'가 중복됩니다")
        seen_ids.add(entry_id)
    return entries


def _fmt_10k(value: Optional[int]) -> str:
    """만원 단위 금액을 사람이 읽는 문자열로 (예: 58,000만원)."""
    return "산출 불가" if value is None else f"{value:,}만원"


def _deal_key(deal) -> str:
    """매매 실거래 1건의 동일성 해시 — name|date|price|floor 조합의 sha1 앞 16자리."""
    raw = f"{deal.name}|{deal.deal_date}|{deal.price_10k}|{deal.floor}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _build_snapshot(result: ScanResult) -> tuple[dict, dict]:
    """ScanResult에서 스냅샷 dict와 deal_key→Deal 매핑을 만든다.

    trades 섹션이 강등(unavailable)된 실행은 deal_keys를 None으로 기록한다 —
    []로 쓰면 "조회 성공·거래 0건"과 구분되지 않아, 복구된 다음 실행에서
    기존 거래 전부가 신규로 오탐되기 때문이다 (소음 방지).
    """
    trades_section = result.sections.get("trades")
    trades_ok = trades_section is not None and trades_section.status == "ok"
    deals = list(trades_section.data) if trades_ok and trades_section.data else []
    # 지역구 전체가 아니라 metrics가 매칭한 범위(내 단지·면적대)의 거래만 추적한다 —
    # 필터 없이 키잉하면 무관한 동네 거래마다 new_trade 알림이 발생한다.
    if deals and result.metrics is not None:
        deals = analysis.filter_matched(
            deals,
            match_scope=result.metrics.match_scope,
            unit_name=result.query.get("unit_name"),
            area_m2=result.query.get("area_m2"),
        )
    key_map = {_deal_key(deal): deal for deal in deals}

    fingerprint = ""
    building_section = result.sections.get("building")
    if building_section is not None and building_section.status == "ok" and building_section.data:
        title = building_section.data
        fingerprint = f"{title.main_purps}|{title.use_apr_day or ''}"

    metrics = result.metrics
    snapshot = {
        "date": result.scanned_at[:10],
        "deal_keys": sorted(key_map) if trades_ok else None,
        "trade_median_10k": metrics.trade_median_10k if metrics else None,
        "jeonse_ratio": metrics.jeonse_ratio if metrics else None,
        "building_fingerprint": fingerprint,
    }
    return snapshot, key_map


def _read_snapshot(path: Path) -> Optional[dict]:
    """직전 스냅샷을 읽는다. 없거나 손상이면 None(기준선 재수립)."""
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_snapshot_atomic(path: Path, snapshot: dict) -> None:
    """임시 파일에 쓴 뒤 os.replace로 원자적 교체 — 중단돼도 기존 스냅샷이 깨지지 않는다."""
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp_path, path)


def _diff_events(
    entry_id: str, prev: dict, curr: dict, key_map: dict, result: ScanResult
) -> list[Event]:
    """직전 스냅샷과 현재 스냅샷을 비교해 변화 이벤트를 만든다."""
    events: list[Event] = []
    metrics = result.metrics
    prev_date = prev.get("date") or "?"

    # 1) 새 deal_key 등장 → new_trade (한 실행의 신규 건은 이슈 1건으로 묶는다)
    #    어느 쪽이든 deal_keys가 None(trades 조회 실패 실행)이면 비교하지 않는다 — 소음 방지.
    prev_keys_list = prev.get("deal_keys")
    curr_keys_list = curr["deal_keys"]
    new_keys: list[str] = []
    if prev_keys_list is not None and curr_keys_list is not None:
        prev_keys = set(prev_keys_list)
        new_keys = [key for key in curr_keys_list if key not in prev_keys]
    if new_keys:
        lines = []
        for key in new_keys:
            deal = key_map.get(key)
            if deal is None:
                continue
            floor_text = f" | {deal.floor}층" if deal.floor is not None else ""
            lines.append(
                f"- {deal.name} | {deal.deal_date} | {_fmt_10k(deal.price_10k)}{floor_text}"
            )
        detail = (
            f"직전 스냅샷({prev_date}) 이후 새로 확인된 매매 실거래입니다.\n\n"
            + "\n".join(lines)
            + "\n\n"
            + f"- 조회 주소: {result.address.jibun if result.address else '?'}\n"
            + f"- 현재 매매 중위가: {_fmt_10k(curr['trade_median_10k'])}"
            + (f" (표본 {metrics.trade_sample}건)" if metrics else "")
        )
        events.append(
            Event(
                kind="new_trade",
                entry_id=entry_id,
                title=f"[{entry_id}] 신규 매매 실거래 {len(new_keys)}건 감지",
                detail=detail,
            )
        )

    # 2) 전세가율이 경계를 아래→위로 통과 → ratio_crossed
    prev_ratio = prev.get("jeonse_ratio")
    curr_ratio = curr["jeonse_ratio"]
    if (
        prev_ratio is not None
        and curr_ratio is not None
        and prev_ratio < RATIO_THRESHOLD <= curr_ratio
    ):
        basis = ""
        if metrics and metrics.deposit_used_10k is not None and metrics.trade_median_10k is not None:
            basis = (
                f"- 산출: 보증금 {_fmt_10k(metrics.deposit_used_10k)}"
                f" ÷ 매매 중위 {_fmt_10k(metrics.trade_median_10k)}"
                f" ({metrics.match_scope}, 표본 {metrics.trade_sample}건)\n"
            )
        detail = (
            f"전세가율이 {RATIO_THRESHOLD:.2f} 경계를 아래에서 위로 통과했습니다.\n\n"
            f"- 이전({prev_date}): {prev_ratio:.2f}\n"
            f"- 현재({curr['date']}): {curr_ratio:.2f}\n"
            + basis
        )
        events.append(
            Event(
                kind="ratio_crossed",
                entry_id=entry_id,
                title=f"[{entry_id}] 전세가율 {curr_ratio:.2f} — {RATIO_THRESHOLD:.2f} 경계 상향 통과",
                detail=detail,
            )
        )

    # 3) 건축물대장 지문(주용도+사용승인일) 변화 → building_changed
    #    한쪽이라도 비어 있으면(대장 조회 실패 등) 비교하지 않는다 — 소음 방지.
    prev_fp = prev.get("building_fingerprint") or ""
    curr_fp = curr["building_fingerprint"]
    if prev_fp and curr_fp and prev_fp != curr_fp:
        detail = (
            "건축물대장 지문(주용도|사용승인일)이 직전 스냅샷과 다릅니다.\n\n"
            f"- 이전({prev_date}): {prev_fp}\n"
            f"- 현재({curr['date']}): {curr_fp}\n"
        )
        events.append(
            Event(
                kind="building_changed",
                entry_id=entry_id,
                title=f"[{entry_id}] 건축물대장 표제부 변화 감지",
                detail=detail,
            )
        )

    return events


def _failure_event(entry_id: str, address: str, reason: str) -> Event:
    """scan 무결과·예외를 scan_failed 이벤트로 만든다 (스냅샷·리포트는 건드리지 않음)."""
    detail = (
        "정기 스캔이 유효한 결과를 만들지 못했습니다. 기존 스냅샷과 리포트는 보존됩니다.\n\n"
        f"- 주소: {address}\n"
        f"- 사유: {reason}\n"
    )
    return Event(
        kind="scan_failed",
        entry_id=entry_id,
        title=f"[{entry_id}] 정기 스캔 실패",
        detail=detail,
    )


def run_watch(
    *, watchlist_path: Path, snapshots_dir: Path, reports_dir: Path
) -> list[Event]:
    """watchlist 전체를 재스캔해 직전 스냅샷 대비 변화 이벤트 목록을 반환한다.

    항목별로: pipeline.scan → 스냅샷 diff → 이벤트 → 스냅샷 원자적 저장 →
    reports/<id>-latest.md 갱신. 실패한 항목은 scan_failed 이벤트만 남기고
    스냅샷·리포트를 보존한다.
    """
    snapshots_dir = Path(snapshots_dir)
    reports_dir = Path(reports_dir)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    events: list[Event] = []
    for entry in _load_watchlist(Path(watchlist_path)):
        entry_id = entry["id"]
        address = entry["address"]
        snapshot_path = snapshots_dir / f"{entry_id}.json"

        result: Optional[ScanResult] = None
        reason = ""
        try:
            result = pipeline.scan(
                address,
                deposit_10k=entry.get("deposit_10k"),
                unit_name=entry.get("unit_name"),
                area_m2=entry.get("area_m2"),
                asset_type=entry.get("asset_type", "apartment"),
            )
        except Exception as exc:  # scan은 강등 설계지만, 예외도 이벤트로 흡수한다
            reason = f"{type(exc).__name__}: {exc}"

        if result is None or result.address is None:
            if result is not None:
                section = result.sections.get("address")
                reason = (section.note if section and section.note else "") or "주소 미확정"
            events.append(_failure_event(entry_id, address, reason))
            continue  # 기존 스냅샷·리포트를 절대 덮어쓰지 않는다

        curr, key_map = _build_snapshot(result)
        prev = _read_snapshot(snapshot_path)
        if prev is not None:
            events.extend(_diff_events(entry_id, prev, curr, key_map, result))
        # 첫 실행(prev 없음)은 기준선 저장만 하고 이벤트를 만들지 않는다

        _write_snapshot_atomic(snapshot_path, curr)
        report_path = reports_dir / f"{entry_id}-latest.md"
        report_path.write_text(report.render(result), encoding="utf-8")

    return events
