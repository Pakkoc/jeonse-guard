"""report.render 테스트 — 정상 케이스와 주소 미확정 케이스 모두 리포트가 완성돼야 한다."""

from __future__ import annotations

from jeonse_guard import report
from jeonse_guard.models import (
    BuildingTitle,
    Metrics,
    ResolvedAddress,
    ScanResult,
    SectionResult,
    Signal,
)

DISCLAIMER = (
    "이 리포트는 공공데이터 기반 참고 자료이며 계약 판단의 근거가 아닙니다. "
    "반드시 등기부등본과 전문가 확인을 거치세요."
)

CHECKLIST_KEYWORDS = ["근저당", "선순위", "전입세대", "보증보험", "완납"]


def make_full_result() -> ScanResult:
    address = ResolvedAddress(
        raw="서울 마포구 성산동 200-1",
        jibun="서울특별시 마포구 성산동 200-1",
        road="서울특별시 마포구 월드컵로 100",
        b_code="1144012700",
        mountain=False,
        bun="0200",
        ji="0001",
        region_name="서울특별시 마포구",
    )
    building = BuildingTitle(
        main_purps="공동주택(아파트)", etc_purps=None, tot_area=51234.56,
        grnd_flr_cnt=14, ugrnd_flr_cnt=1, use_apr_day="19860630",
        regstr_kind="집합건축물대장", plat_plc="서울특별시 마포구 성산동 200-1",
    )
    metrics = Metrics(
        trade_median_10k=105000, trade_sample=29,
        deposit_median_10k=29000, jeonse_sample=39,
        deposit_used_10k=35000, deposit_basis="입력값",
        jeonse_ratio=0.3333, match_scope="단지+면적", months_covered=12,
    )
    found = [
        Signal(
            id="high_jeonse_ratio", level="warn", title="전세가율 80% 이상",
            basis="전세가율 88.0% = 보증금 88,000만원 ÷ 매매 중위 100,000만원 (단지+면적, 표본 29건)",
        ),
    ]
    sections = {
        "address": SectionResult(status="ok", data=address),
        "building": SectionResult(status="ok", data=building),
        "trades": SectionResult(status="ok", data=[]),
        "rents": SectionResult(status="ok", data=[]),
    }
    return ScanResult(
        query={"address": "서울 마포구 성산동 200-1", "deposit_10k": 35000},
        scanned_at="2026-07-28T09:00:00+09:00",
        address=address, sections=sections, metrics=metrics, signals=found,
    )


def test_full_report_contains_all_sections():
    markdown = report.render(make_full_result())
    assert isinstance(markdown, str)
    # 제목 = 확정 주소
    assert "서울특별시 마포구 성산동 200-1" in markdown.splitlines()[0]
    # 생성시각·출처
    assert "2026-07-28T09:00:00+09:00" in markdown
    assert "국토교통부" in markdown
    # 요약 카운트
    assert "검토 신호 1건" in markdown
    # 대장 사실
    assert "공동주택(아파트)" in markdown
    assert "19860630" in markdown
    # 전세가율 산출식 (수치 포함)
    assert "35,000만원" in markdown
    assert "105,000만원" in markdown
    assert "33.3%" in markdown
    # 신호 본문
    assert "전세가율 80% 이상" in markdown
    assert "표본 29건" in markdown


def test_full_report_has_checklist_and_disclaimer():
    markdown = report.render(make_full_result())
    assert DISCLAIMER in markdown
    for keyword in CHECKLIST_KEYWORDS:
        assert keyword in markdown
    # 체크리스트는 체크박스 형식
    assert "- [ ]" in markdown


def test_report_completes_without_metrics_and_address():
    """주소 미확정 케이스 — metrics/address 가 None 이어도 리포트를 완성한다."""
    sections = {
        "address": SectionResult(status="unavailable", note="후보 2건 — 확정 불가"),
        "building": SectionResult(status="skipped", note="주소 미확정"),
        "trades": SectionResult(status="skipped", note="주소 미확정"),
        "rents": SectionResult(status="skipped", note="주소 미확정"),
    }
    result = ScanResult(
        query={"address": "서울 어딘가 1-1", "deposit_10k": None},
        scanned_at="2026-07-28T09:00:00+09:00",
        address=None, sections=sections, metrics=None,
        signals=[
            Signal(
                id="section_unavailable", level="info", title="일부 데이터 섹션 확인 불가",
                basis="확인 불가 섹션 1개 — address: 후보 2건 — 확정 불가",
            )
        ],
    )
    markdown = report.render(result)
    assert isinstance(markdown, str)
    # 입력 원문 + 미확정 표시가 제목에 남는다
    assert "서울 어딘가 1-1" in markdown
    assert "주소 미확정" in markdown
    # 미확정이어도 고정 안내는 그대로
    assert DISCLAIMER in markdown
    for keyword in CHECKLIST_KEYWORDS:
        assert keyword in markdown
    # 확인 불가 사유 노출
    assert "후보 2건" in markdown
    assert "확인 불가 섹션 1건" in markdown


def test_report_no_signals_message():
    """신호 0건이어도 빈칸이 아니라 안내 문구가 있어야 한다."""
    result = make_full_result()
    result.signals = []
    markdown = report.render(result)
    assert "검토 신호 0건" in markdown
    assert "생성된 검토 신호가 없습니다" in markdown
    assert DISCLAIMER in markdown
