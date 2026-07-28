"""signals.evaluate 테스트 — 4종 신호 각각 발화/비발화, 근거 수치 포함, 단정 표현 금지."""

from __future__ import annotations

from jeonse_guard import signals
from jeonse_guard.models import BuildingTitle, Metrics, SectionResult, Signal

ALLOWED_IDS = {"high_jeonse_ratio", "nongsaeng_building", "no_price_reference", "section_unavailable"}


def make_metrics(**overrides) -> Metrics:
    """발화 조건이 하나도 걸리지 않는 기본 Metrics."""
    base = dict(
        trade_median_10k=100000,
        trade_sample=10,
        deposit_median_10k=30000,
        jeonse_sample=5,
        deposit_used_10k=30000,
        deposit_basis="입력값",
        jeonse_ratio=0.3,
        match_scope="단지+면적",
        months_covered=12,
    )
    base.update(overrides)
    return Metrics(**base)


def make_building(main_purps="공동주택(아파트)", etc_purps=None) -> BuildingTitle:
    return BuildingTitle(
        main_purps=main_purps, etc_purps=etc_purps, tot_area=1000.0,
        grnd_flr_cnt=14, ugrnd_flr_cnt=1, use_apr_day="19860630",
        regstr_kind="집합건축물대장", plat_plc="서울특별시 마포구 성산동 200-1",
    )


def ok_sections() -> dict[str, SectionResult]:
    return {key: SectionResult(status="ok") for key in ("address", "building", "trades", "rents")}


def ids(found: list[Signal]) -> set[str]:
    return {s.id for s in found}


def test_no_signals_when_everything_normal():
    found = signals.evaluate(metrics=make_metrics(), building=make_building(), sections=ok_sections())
    assert found == []


def test_high_jeonse_ratio_fires_at_threshold():
    metrics = make_metrics(jeonse_ratio=0.80, deposit_used_10k=80000, trade_median_10k=100000)
    found = signals.evaluate(metrics=metrics, building=None, sections=ok_sections())
    assert ids(found) == {"high_jeonse_ratio"}
    signal = found[0]
    assert signal.level == "warn"
    # basis에 산출 수치가 전부 있어야 한다
    assert "80.0%" in signal.basis
    assert "80,000만원" in signal.basis
    assert "100,000만원" in signal.basis
    assert "단지+면적" in signal.basis
    assert "표본 10건" in signal.basis


def test_high_jeonse_ratio_not_fired_below_threshold_or_none():
    below = make_metrics(jeonse_ratio=0.79)
    assert "high_jeonse_ratio" not in ids(
        signals.evaluate(metrics=below, building=None, sections=ok_sections())
    )
    none_ratio = make_metrics(jeonse_ratio=None)
    assert "high_jeonse_ratio" not in ids(
        signals.evaluate(metrics=none_ratio, building=None, sections=ok_sections())
    )


def test_nongsaeng_building_fires_on_main_purps():
    building = make_building(main_purps="제2종근린생활시설")
    found = signals.evaluate(metrics=make_metrics(), building=building, sections=ok_sections())
    assert ids(found) == {"nongsaeng_building"}
    assert found[0].level == "warn"
    assert "제2종근린생활시설" in found[0].basis  # 대장 원문 필수


def test_nongsaeng_building_fires_on_etc_purps():
    building = make_building(main_purps="공동주택", etc_purps="근린생활시설(소매점)")
    found = signals.evaluate(metrics=make_metrics(), building=building, sections=ok_sections())
    assert ids(found) == {"nongsaeng_building"}
    assert "근린생활시설(소매점)" in found[0].basis


def test_nongsaeng_building_not_fired_for_apartment():
    found = signals.evaluate(metrics=make_metrics(), building=make_building(), sections=ok_sections())
    assert "nongsaeng_building" not in ids(found)


def test_no_price_reference_fires_below_three_samples():
    metrics = make_metrics(trade_sample=2, trade_median_10k=90000, jeonse_ratio=0.3)
    found = signals.evaluate(metrics=metrics, building=None, sections=ok_sections())
    assert ids(found) == {"no_price_reference"}
    signal = found[0]
    assert signal.level == "info"
    assert "2건" in signal.basis
    assert "12개월" in signal.basis  # 조회 범위 필수


def test_no_price_reference_not_fired_at_three_samples():
    metrics = make_metrics(trade_sample=3)
    assert "no_price_reference" not in ids(
        signals.evaluate(metrics=metrics, building=None, sections=ok_sections())
    )


def test_section_unavailable_fires_with_names_and_reasons():
    sections = ok_sections()
    sections["building"] = SectionResult(status="unavailable", note="프록시 404")
    sections["rents"] = SectionResult(status="unavailable", note="HTTP 502")
    found = signals.evaluate(metrics=make_metrics(), building=None, sections=sections)
    assert ids(found) == {"section_unavailable"}
    signal = found[0]
    assert signal.level == "info"
    assert "building" in signal.basis and "프록시 404" in signal.basis
    assert "rents" in signal.basis and "HTTP 502" in signal.basis


def test_address_unresolved_case_only_section_signal():
    """pipeline이 주소 미확정 시 넘기는 형태: metrics/building 없음 + address unavailable."""
    sections = {
        "address": SectionResult(status="unavailable", note="후보 2건 — 확정 불가"),
        "building": SectionResult(status="skipped", note="주소 미확정"),
        "trades": SectionResult(status="skipped", note="주소 미확정"),
        "rents": SectionResult(status="skipped", note="주소 미확정"),
    }
    found = signals.evaluate(metrics=None, building=None, sections=sections)
    assert ids(found) == {"section_unavailable"}
    assert "address" in found[0].basis


def test_only_allowed_ids_and_no_verdict_wording():
    """신호는 계약의 4종뿐이며 '안전/위험/사기' 단정 표현을 쓰지 않는다."""
    metrics = make_metrics(jeonse_ratio=0.95, deposit_used_10k=95000, trade_sample=1)
    sections = ok_sections()
    sections["building"] = SectionResult(status="unavailable", note="표제부 없음")
    building = make_building(main_purps="제1종근린생활시설")
    found = signals.evaluate(metrics=metrics, building=building, sections=sections)
    assert ids(found) == ALLOWED_IDS
    for signal in found:
        text = signal.title + signal.basis
        for banned in ("안전", "위험", "사기"):
            assert banned not in text
        assert signal.basis.strip()  # 근거 필수


def test_high_ratio_with_missing_operands_does_not_crash():
    """ratio는 있는데 분자/분모가 None인 비정상 Metrics에도 크래시 없이 신호를 생략한다."""
    metrics = make_metrics(jeonse_ratio=0.95, deposit_used_10k=None)
    found = signals.evaluate(metrics=metrics, building=None, sections=ok_sections())
    assert "high_jeonse_ratio" not in ids(found)

    metrics = make_metrics(jeonse_ratio=0.95, trade_median_10k=None)
    found = signals.evaluate(metrics=metrics, building=None, sections=ok_sections())
    assert "high_jeonse_ratio" not in ids(found)
