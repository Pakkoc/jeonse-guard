"""검토 신호 평가 — 근거 수치·원문이 있을 때만 신호를 만들고 단정하지 않는다.

5종 고정 (CONTRACTS.md A3 — 이 외 추가 금지):
high_jeonse_ratio / nongsaeng_building / no_price_reference / section_unavailable /
partial_month_coverage.
"안전", "위험 확정", "사기" 같은 단정 표현은 쓰지 않는다. 사실 서술만.
"""

from __future__ import annotations

from typing import Optional

from .models import BuildingTitle, Metrics, SectionResult, Signal

# 전세가율 경계 (deposit_used / trade_median)
RATIO_THRESHOLD = 0.80
# 이 미만이면 가격 참고치 부족으로 본다
MIN_TRADE_SAMPLE = 3


def evaluate(
    *,
    metrics: Optional[Metrics],
    building: Optional[BuildingTitle],
    sections: dict[str, SectionResult],
) -> list[Signal]:
    """산출 지표·건축물대장·섹션 상태에서 검토 신호 목록을 만든다."""
    found: list[Signal] = []

    # 1. high_jeonse_ratio (warn): 전세가율 0.80 이상
    if (
        metrics is not None
        and metrics.jeonse_ratio is not None
        and metrics.jeonse_ratio >= RATIO_THRESHOLD
        and metrics.deposit_used_10k is not None
        and metrics.trade_median_10k is not None
    ):
        found.append(
            Signal(
                id="high_jeonse_ratio",
                level="warn",
                title="전세가율 80% 이상",
                basis=(
                    f"전세가율 {metrics.jeonse_ratio * 100:.1f}%"
                    f" = 보증금 {metrics.deposit_used_10k:,}만원"
                    f" ÷ 매매 중위 {metrics.trade_median_10k:,}만원"
                    f" ({metrics.match_scope}, 표본 {metrics.trade_sample}건)"
                ),
            )
        )

    # 2. nongsaeng_building (warn): 대장 용도에 "근린생활" 포함
    if building is not None:
        main_purps = building.main_purps or ""
        etc_purps = building.etc_purps or ""
        if "근린생활" in main_purps or "근린생활" in etc_purps:
            basis = f"건축물대장 주용도 원문: '{main_purps}'"
            if etc_purps:
                basis += f", 기타용도 원문: '{etc_purps}'"
            found.append(
                Signal(
                    id="nongsaeng_building",
                    level="warn",
                    title="건축물대장 용도에 근린생활시설 포함",
                    basis=basis,
                )
            )

    # 3. no_price_reference (info): 매매 표본 3건 미만
    if metrics is not None and metrics.trade_sample < MIN_TRADE_SAMPLE:
        found.append(
            Signal(
                id="no_price_reference",
                level="info",
                title="매매 실거래 표본 부족",
                basis=(
                    f"매매 실거래 표본 {metrics.trade_sample}건"
                    f" ({metrics.match_scope}, 최근 {metrics.months_covered}개월 조회)"
                ),
            )
        )

    # 4. section_unavailable (info): 확인 불가 섹션 1개 이상
    unavailable = [(name, sec) for name, sec in sections.items() if sec.status == "unavailable"]
    if unavailable:
        listed = " / ".join(f"{name}: {sec.note or '사유 미상'}" for name, sec in unavailable)
        found.append(
            Signal(
                id="section_unavailable",
                level="info",
                title="일부 데이터 섹션 확인 불가",
                basis=f"확인 불가 섹션 {len(unavailable)}개 — {listed}",
            )
        )

    # 5. partial_month_coverage (info): 월별 실거래 수집이 절반 이상 실패
    #    부분 실패를 허용하는 설계의 대가 — 중위값이 적은 달로만 계산됐음을 알린다.
    if metrics is not None and metrics.months_covered > 0:
        degraded = []
        for label, ok in (("매매", metrics.trade_months_ok), ("전세", metrics.rent_months_ok)):
            if ok is not None and (metrics.months_covered - ok) * 2 >= metrics.months_covered:
                degraded.append(f"{label} {ok}/{metrics.months_covered}개월")
        if degraded:
            found.append(
                Signal(
                    id="partial_month_coverage",
                    level="info",
                    title="실거래 월별 수집 절반 이상 실패",
                    basis=(
                        f"요청한 최근 {metrics.months_covered}개월 중 수집 성공: "
                        + ", ".join(degraded)
                        + " — 이 실행의 중위값·표본 수는 수집된 달 기준이라 신뢰도가 낮습니다."
                    ),
                )
            )

    return found
