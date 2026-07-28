"""리포트 렌더러 — ScanResult 하나를 Markdown 리포트로 완성한다.

metrics/address 가 None 인 주소 미확정 케이스에서도 리포트를 항상 완성한다.
면책 문구·직접 확인 체크리스트는 CONTRACTS.md A3의 고정 문구다.
"""

from __future__ import annotations

from typing import Optional

from .models import BuildingTitle, Metrics, ScanResult, SectionResult

# 고정 면책 문구 (CONTRACTS.md — 문구 변경 금지)
DISCLAIMER = (
    "이 리포트는 공공데이터 기반 참고 자료이며 계약 판단의 근거가 아닙니다. "
    "반드시 등기부등본과 전문가 확인을 거치세요."
)

# 자동화 불가 — 계약 전 반드시 직접 확인해야 하는 고정 체크리스트
CHECKLIST = [
    "등기부등본 을구 근저당·가압류 등 권리관계 확인 (인터넷등기소 열람)",
    "선순위보증금(먼저 계약한 임차인들의 보증금) 규모 확인",
    "전입세대 열람 (주민센터 또는 정부24)",
    "전세보증금 반환보증보험 가입 가능 여부 확인 (HUG·HF·SGI)",
    "임대인 국세·지방세 완납증명(납세증명서) 확인",
]

DATA_SOURCES = (
    "국토교통부 실거래가 공개시스템, 건축물대장 표제부(공공데이터포털 15134735), "
    "카카오 로컬 지오코딩 — k-skill-proxy 경유"
)

_SECTION_LABELS = {
    "address": "주소 확정",
    "building": "건축물대장",
    "trades": "매매 실거래",
    "rents": "전월세 실거래",
}


def _cell(text: object) -> str:
    """Markdown 표 셀 값 정화 — '|'와 줄바꿈이 표 레이아웃을 깨지 않게 한다."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _fmt_10k(value: Optional[int]) -> str:
    """만원 단위 금액 표기. None 은 '산출 불가'."""
    if value is None:
        return "산출 불가"
    return f"{value:,}만원"


def _building_rows(building: BuildingTitle) -> list[tuple[str, str, str]]:
    """건축물대장 표제부 → 사실 표 행."""
    src = "건축물대장 표제부"
    rows: list[tuple[str, str, str]] = [("대장 주용도", building.main_purps or "미상", src)]
    if building.etc_purps:
        rows.append(("대장 기타용도", building.etc_purps, src))
    if building.tot_area is not None:
        rows.append(("연면적", f"{building.tot_area:,.2f}㎡", src))
    if building.grnd_flr_cnt is not None or building.ugrnd_flr_cnt is not None:
        rows.append(
            ("층수", f"지상 {building.grnd_flr_cnt or 0}층 / 지하 {building.ugrnd_flr_cnt or 0}층", src)
        )
    if building.use_apr_day:
        rows.append(("사용승인일", building.use_apr_day, src))
    if building.regstr_kind:
        rows.append(("대장 종류", building.regstr_kind, src))
    if building.plat_plc:
        rows.append(("대지위치", building.plat_plc, src))
    return rows


def _metrics_rows(metrics: Metrics) -> list[tuple[str, str, str]]:
    """산출 지표 → 사실 표 행 (전세가율 산출식 포함)."""

    def src(months_ok) -> str:
        # 월별 수집 성공 수를 함께 표기해 "12개월"이 과장이 되지 않게 한다
        base = f"실거래 최근 {metrics.months_covered}개월"
        if months_ok is None:
            return base
        return f"{base} ({months_ok}/{metrics.months_covered}개월 수집)"

    rows = [
        ("매칭 범위", metrics.match_scope, "매칭 사다리: 단지+면적 → 단지 → 면적 → 지역구"),
        ("매매 중위가", f"{_fmt_10k(metrics.trade_median_10k)} (표본 {metrics.trade_sample}건)", src(metrics.trade_months_ok)),
        ("전세 중위 보증금", f"{_fmt_10k(metrics.deposit_median_10k)} (표본 {metrics.jeonse_sample}건)", src(metrics.rent_months_ok)),
        ("적용 보증금", f"{_fmt_10k(metrics.deposit_used_10k)} (기준: {metrics.deposit_basis})", "전세가율 분자"),
    ]
    if metrics.jeonse_ratio is not None:
        formula = (
            f"{_fmt_10k(metrics.deposit_used_10k)} ÷ {_fmt_10k(metrics.trade_median_10k)}"
            f" = {metrics.jeonse_ratio * 100:.1f}%"
        )
        rows.append(("전세가율", formula, "적용 보증금 ÷ 매매 중위가"))
    else:
        rows.append(("전세가율", "산출 불가 (매매 중위가 또는 적용 보증금 없음)", "적용 보증금 ÷ 매매 중위가"))
    return rows


def render(result: ScanResult) -> str:
    """ScanResult 를 Markdown 리포트 문자열로 렌더한다."""
    lines: list[str] = []

    # 제목 — 주소 미확정이면 입력 원문에 표시를 붙인다
    if result.address is not None:
        title_addr = result.address.jibun
    else:
        title_addr = f"{result.query.get('address') or '입력 없음'} (주소 미확정)"
    lines.append(f"# 전세 안전진단 리포트 — {title_addr}")
    lines.append("")
    lines.append(f"- 생성시각: {result.scanned_at}")
    lines.append(f"- 데이터 출처: {DATA_SOURCES}")
    lines.append("")

    # 요약
    warn_count = sum(1 for s in result.signals if s.level == "warn")
    info_count = sum(1 for s in result.signals if s.level == "info")
    unavailable = [
        (name, sec) for name, sec in result.sections.items() if sec.status == "unavailable"
    ]
    skipped = [(name, sec) for name, sec in result.sections.items() if sec.status == "skipped"]
    lines.append("## 요약")
    lines.append("")
    lines.append(
        f"검토 신호 {len(result.signals)}건 (warn {warn_count}건 / info {info_count}건), "
        f"확인 불가 섹션 {len(unavailable)}건."
    )
    if unavailable or skipped:
        lines.append("")
        for name, sec in unavailable:
            label = _SECTION_LABELS.get(name, name)
            lines.append(f"- 확인 불가 — {label}({name}): {sec.note or '사유 미상'}")
        for name, sec in skipped:
            label = _SECTION_LABELS.get(name, name)
            lines.append(f"- 건너뜀 — {label}({name}): {sec.note or '사유 미상'}")
    lines.append("")

    # 확인된 사실 표
    lines.append("## 확인된 사실")
    lines.append("")
    rows: list[tuple[str, str, str]] = []
    if result.address is not None:
        rows.append(("확정 주소(지번)", result.address.jibun, "카카오 지오코딩"))
        if result.address.road:
            rows.append(("도로명 주소", result.address.road, "카카오 지오코딩"))
        rows.append(("법정동 코드", result.address.b_code, "카카오 지오코딩"))
    building_sec = result.sections.get("building")
    if building_sec is not None and building_sec.status == "ok" and building_sec.data is not None:
        rows.extend(_building_rows(building_sec.data))
    if result.metrics is not None:
        rows.extend(_metrics_rows(result.metrics))
    if rows:
        lines.append("| 항목 | 내용 | 출처·비고 |")
        lines.append("| --- | --- | --- |")
        for item, value, source in rows:
            lines.append(f"| {_cell(item)} | {_cell(value)} | {_cell(source)} |")
    else:
        lines.append("확인된 사실이 없습니다 — 주소가 확정되지 않아 공공데이터 조회를 진행하지 못했습니다.")
    lines.append("")

    # 검토 신호
    lines.append("## 검토 신호")
    lines.append("")
    if result.signals:
        for signal in result.signals:
            lines.append(f"- **[{signal.level}] {signal.title}** (`{signal.id}`)")
            lines.append(f"  - 근거: {signal.basis}")
    else:
        lines.append(
            "- 생성된 검토 신호가 없습니다. 신호가 없다는 것이 문제 없음을 뜻하지는 않습니다 — "
            "아래 체크리스트를 반드시 직접 확인하세요."
        )
    lines.append("")

    # 직접 확인 체크리스트 (고정)
    lines.append("## 직접 확인 체크리스트 (자동 조회 불가 — 계약 전 반드시 직접 확인)")
    lines.append("")
    for item in CHECKLIST:
        lines.append(f"- [ ] {item}")
    lines.append("")

    # 면책 고정 문구
    lines.append("---")
    lines.append("")
    lines.append(f"> {DISCLAIMER}")
    lines.append("")

    return "\n".join(lines)
