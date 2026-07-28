"""공용 데이터 모델 — 모든 모듈이 이 타입만으로 소통한다 (단일 진실원천)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ResolvedAddress:
    """카카오 geocode로 확정된 지번 주소 한 건."""

    raw: str                      # 사용자가 입력한 원문
    jibun: str                    # 정규화된 지번 주소 전체
    road: Optional[str]           # 도로명 주소 (없으면 None)
    b_code: str                   # 10자리 법정동 코드
    mountain: bool                # 산 지번 여부
    bun: str                      # 본번 4자리 (zero-pad)
    ji: str                       # 부번 4자리 (zero-pad)
    region_name: str              # 예: "서울특별시 마포구"

    @property
    def sigungu_cd(self) -> str:
        return self.b_code[:5]

    @property
    def bjdong_cd(self) -> str:
        return self.b_code[5:]

    @property
    def lawd_cd(self) -> str:
        """실거래가 API용 5자리 시군구 코드."""
        return self.b_code[:5]

    @property
    def plat_gb_cd(self) -> str:
        """건축물대장 API 토지구분 (0=일반, 1=산)."""
        return "1" if self.mountain else "0"

    @property
    def pnu(self) -> str:
        """19자리 PNU (토지구분: 1=일반, 2=산)."""
        return f"{self.b_code}{'2' if self.mountain else '1'}{self.bun}{self.ji}"


@dataclass
class BuildingTitle:
    """건축물대장 표제부 핵심 필드."""

    main_purps: str               # 주용도 (mainPurpsCdNm)
    etc_purps: Optional[str]      # 기타용도 (etcPurps)
    tot_area: Optional[float]     # 연면적 ㎡
    grnd_flr_cnt: Optional[int]   # 지상 층수
    ugrnd_flr_cnt: Optional[int]  # 지하 층수
    use_apr_day: Optional[str]    # 사용승인일 YYYYMMDD
    regstr_kind: Optional[str]    # 대장 종류 (regstrKindCdNm)
    plat_plc: Optional[str]       # 대지위치
    raw: dict = field(default_factory=dict)


@dataclass
class Deal:
    """매매 실거래 1건. 금액 단위: 만원."""

    name: str                     # 단지/건물명
    district: str                 # 법정동 이름
    area_m2: Optional[float]
    floor: Optional[int]
    price_10k: int
    deal_date: str                # YYYY-MM-DD
    build_year: Optional[int]


@dataclass
class RentDeal:
    """전월세 실거래 1건. 금액 단위: 만원. monthly_rent_10k == 0 이면 전세."""

    name: str
    district: str
    area_m2: Optional[float]
    floor: Optional[int]
    deposit_10k: int
    monthly_rent_10k: int
    deal_date: str
    contract_type: Optional[str]  # 신규/갱신 (없으면 None)


@dataclass
class SectionResult:
    """파이프라인 각 섹션의 결과. 실패해도 전체를 막지 않는다 (실패 강등)."""

    status: str                   # "ok" | "unavailable" | "skipped"
    data: Any = None
    note: str = ""                # unavailable/skipped 사유


@dataclass
class Metrics:
    """전세가율 등 산출 지표. 산출 불가 필드는 None."""

    trade_median_10k: Optional[int]
    trade_sample: int
    deposit_median_10k: Optional[int]
    jeonse_sample: int
    deposit_used_10k: Optional[int]   # 전세가율 분자로 쓴 값
    deposit_basis: str                # "입력값" | "전세 실거래 중위" | "산출 불가"
    jeonse_ratio: Optional[float]     # deposit_used / trade_median (0.0~)
    match_scope: str                  # "단지+면적" | "단지" | "면적" | "지역구"
    months_covered: int


@dataclass
class Signal:
    """근거가 있을 때만 생성되는 검토 신호. 단정 라벨이 아니다."""

    id: str                       # 예: "high_jeonse_ratio"
    level: str                    # "warn" | "info"
    title: str                    # 한 줄 제목
    basis: str                    # 산출 근거·수치·원문 (필수)


@dataclass
class ScanResult:
    """진단 1회의 전체 결과."""

    query: dict                   # 입력 파라미터 기록 (address, deposit_10k, unit_name, ...)
    scanned_at: str               # ISO8601 KST
    address: Optional[ResolvedAddress]
    sections: dict[str, SectionResult]  # keys: "address" | "building" | "trades" | "rents"
    metrics: Optional[Metrics]
    signals: list[Signal] = field(default_factory=list)
