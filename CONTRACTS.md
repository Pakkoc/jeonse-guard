# jeonse-guard 모듈 계약 (빌드 하네스용)

모든 모듈은 `jeonse_guard/models.py`의 타입만으로 소통한다. 이 문서의 시그니처와 다르게 구현하지 않는다.
공통 규칙: **stdlib only**(외부 의존성 금지), 타입 힌트 필수, docstring 한국어, 네트워크는 반드시 `jeonse_guard.net.get_json` 경유, 실패는 `jeonse_guard.errors.SourceError` 계열로만.

테스트 규칙: `tests/` 아래 pytest. 네트워크 금지 — `net.get_json`(각 모듈이 import한 이름)을 monkeypatch해서 `tests/fixtures/*.json`을 주입한다. fixture는 실제 프록시 응답 형태를 따른다.

k-skill(MIT) 헬퍼를 참조·개작할 때는 파일 상단 docstring에 출처를 남긴다:
`참고: NomaDamas/k-skill <스킬명> (MIT)`.

## 프록시 API 명세 (실측 검증됨)

base = `jeonse_guard.config.proxy_base()` (기본 `https://k-skill-proxy.nomadamas.org`)

1. **카카오 지오코딩**: `GET {base}/v1/kakao-local/geocode?q=<주소>&limit=2`
   응답의 address 객체에서 `b_code`(10자리 법정동), 본번/부번, 산 여부(`mountain_yn` "Y"/"N")를 얻는다.
   결과가 0건이거나 2건 이상이거나 필지 정보가 없으면 **추정하지 말고** `AmbiguousAddressError`(후보 목록 포함).
2. **건축물대장 표제부**: `GET {base}/v1/building-register/title?sigunguCd=..&bjdongCd=..&platGbCd=..&bun=..&ji=..`
   bun/ji는 4자리 zero-pad. 주요 필드: `mainPurpsCdNm`, `etcPurps`, `totArea`, `grndFlrCnt`, `ugrndFlrCnt`, `useAprDay`, `regstrKindCdNm`, `platPlc`. 여러 동이 반환되면 전부 raw에 보존하고 대표(연면적 최대) 1건으로 요약.
3. **실거래 매매**: `GET {base}/v1/real-estate/{asset}/trade?lawd_cd=<5자리>&deal_ymd=<YYYYMM>&num_of_rows=1000`
   asset ∈ apartment|villa|officetel|single-house. items[]: `name, district, area_m2, floor, price_10k, deal_date, build_year`.
4. **실거래 전월세**: `GET {base}/v1/real-estate/{asset}/rent?...` — items에 `deposit_10k, monthly_rent_10k, contract_type` 추가. `monthly_rent_10k == 0`이 전세.

금액 단위는 전부 **만원**. 프록시 오류: 400(파라미터), 503(키 미설정), 502(upstream). 데이터 없으면 빈 `items`.

## 모듈 소유권과 시그니처

### A1. `jeonse_guard/sources/address.py` + `jeonse_guard/sources/building.py`

```python
# address.py
def resolve_address(query: str, *, base: str, timeout: int = 25) -> ResolvedAddress: ...
# building.py  — k-skill building-register-search/scripts/building_register.py + building_register_xml.py (MIT) 참조
def fetch_title(addr: ResolvedAddress, *, base: str, timeout: int = 30) -> BuildingTitle: ...
```

**building.py 이중 모드 (중요)**: 프로덕션 프록시에 `/v1/building-register/title` 라우트가 아직 배포되지 않아 404("Route ... not found")가 온다 (2026-07-28 실측).
① 프록시 경로를 먼저 시도한다. ② 실패 시 환경변수 `KSKILL_BUILDING_REGISTER_API_KEY` → `DATA_GO_KR_API_KEY` 순으로 키를 찾아, 있으면 **직접 모드**: `https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo`에 `serviceKey`+`sigunguCd/bjdongCd/platGbCd/bun/ji&numOfRows=100`으로 `net.get_text` 호출 → XML을 `xml.etree.ElementTree`로 파싱 (resultCode 확인, item들에서 mainPurpsCdNm 등 추출). ③ 키도 없으면 `SourceError("프록시 미배포 + API 키 없음 — data.go.kr에서 무료 키 발급 후 DATA_GO_KR_API_KEY 설정 (데이터셋 15134735 활용신청 필요)")`.
테스트: `tests/test_address.py`, `tests/test_building.py` (+ fixtures). 실제 지오코딩 fixture가 `tests/fixtures/geocode_seongsan.json`에 있다 — 응답 구조(documents[].address.b_code/main_address_no/sub_address_no/mountain_yn)를 이 파일로 확인하라. 직접 모드 XML fixture는 공공데이터포털 표준 응답 형태로 직접 작성.
케이스 필수: 정상 1건 확정 / 다중 후보 → AmbiguousAddressError / 필지 누락 → AmbiguousAddressError / 프록시 404 + 키 없음 → SourceError 안내 / 프록시 404 + 키 있음 → 직접 모드 파싱 / 대장 빈 결과 → SourceError("표제부 없음") / 다동 건물 대표(연면적 최대) 선정.

### A2. `jeonse_guard/sources/real_estate.py`

```python
def recent_months(n: int, *, today: datetime.date | None = None) -> list[str]:  # 최신월부터 n개 "YYYYMM"
def fetch_trades(lawd_cd: str, months: list[str], *, asset_type: str, base: str, timeout: int = 25) -> list[Deal]: ...
def fetch_rents(lawd_cd: str, months: list[str], *, asset_type: str, base: str, timeout: int = 25) -> list[RentDeal]: ...
```
월별 호출을 순회 합산한다. 한 달 호출이 실패해도 나머지 달로 계속하되, **전부** 실패하면 SourceError. 항목 필드 누락은 관대하게(None) 처리하되 price/deposit 없는 행은 버린다. `today=None`이면 KST 오늘. 신고 지연을 고려해 당월 포함.
테스트: `tests/test_real_estate.py` — recent_months 경계(1월→전년 12월), 부분 실패 합산, 전실패 SourceError, 전세/월세 구분.

### A3. `jeonse_guard/analysis.py` + `jeonse_guard/signals.py` + `jeonse_guard/report.py`

```python
# analysis.py
def compute_metrics(trades: list[Deal], rents: list[RentDeal], *,
                    unit_name: str | None, area_m2: float | None,
                    deposit_10k: int | None, months_covered: int,
                    district: str = "") -> Metrics: ...
```
매칭 규칙(순서대로 좁힌다, 좁힌 결과 표본 0이면 한 단계 되돌림):
① unit_name이 있으면 정규화 부분일치(공백·괄호 제거)로 단지 필터 ② area_m2가 있으면 ±15% 면적대 필터 ③ 남으면 지역구 전체. 실제 적용된 수준을 `match_scope`에 기록.
전세 표본은 `monthly_rent_10k == 0`만. 중위값은 `statistics.median` 반올림.
`deposit_used_10k` = 입력 deposit_10k 우선, 없으면 전세 중위(`deposit_basis`에 명기). `jeonse_ratio` = deposit_used / trade_median (둘 중 하나 없으면 None).

```python
# signals.py
def evaluate(*, metrics: Metrics | None, building: BuildingTitle | None,
             sections: dict[str, SectionResult]) -> list[Signal]: ...
```
신호 규칙 (MVP 4종 — 이 외 추가 금지, 각 basis에 산출 수치·원문 필수):
- `high_jeonse_ratio` (warn): ratio ≥ 0.80. basis에 "전세가율 N% = 보증금 X만원 ÷ 매매 중위 Y만원 (match_scope, 표본 N건)" 형식.
- `nongsaeng_building` (warn): building.main_purps 또는 etc_purps에 "근린생활" 포함. basis에 대장 주용도 원문.
- `no_price_reference` (info): trade_sample < 3. basis에 표본 수와 조회 범위.
- `section_unavailable` (info): unavailable 섹션이 1개 이상. basis에 섹션명과 사유 나열.
단정 표현 금지: "안전", "위험 확정", "사기" 등을 쓰지 않는다. 사실 서술만.

```python
# report.py
def render(result: ScanResult) -> str:  # Markdown
```
구조: 제목(주소) → 생성시각·데이터 출처 → 요약(신호 n건/확인불가 m건) → 확인된 사실 표(대장·실거래 요약·전세가율 산출식) → 검토 신호 → **직접 확인 체크리스트**(등기부 근저당·선순위보증금·전입세대 열람·보증보험 가입 가능 여부·임대인 세금완납증명 — 자동화 불가 항목 고정 안내) → 면책 고정 문구("이 리포트는 공공데이터 기반 참고 자료이며 계약 판단의 근거가 아닙니다. 반드시 등기부등본과 전문가 확인을 거치세요.").
테스트: `tests/test_analysis.py`, `tests/test_signals.py`, `tests/test_report.py` — 매칭 되돌림, 전세가율 산식, 신호 4종 각각 발화/비발화, 리포트에 면책 포함.

### A4. `jeonse_guard/watch.py` + `watchlist.example.toml`

```python
@dataclass
class Event:
    kind: str        # "new_trade" | "ratio_crossed" | "building_changed" | "scan_failed"
    entry_id: str    # watchlist 항목 id
    title: str       # Issue 제목으로 쓸 한 줄
    detail: str      # Issue 본문 (Markdown, 근거 포함)

def run_watch(*, watchlist_path: Path, snapshots_dir: Path, reports_dir: Path) -> list[Event]: ...
```
watchlist.toml (tomllib로 파싱):
```toml
[[entry]]
id = "home"                 # 스냅샷 파일명 키
address = "서울 마포구 성산동 200-1"
deposit_10k = 35000          # 선택
unit_name = "성산시영"        # 선택
area_m2 = 50.0               # 선택
asset_type = "apartment"     # 선택, 기본 apartment
```
동작: 항목마다 `pipeline.scan` 실행 → `snapshots/<id>.json`의 직전 스냅샷과 비교 → 이벤트 생성 → 새 스냅샷 저장(원자적 쓰기) → 리포트를 `reports/<id>-latest.md`로 갱신.
스냅샷 스키마: `{"date", "deal_keys": [name|date|price|floor 해시 목록], "trade_median_10k", "jeonse_ratio", "building_fingerprint": main_purps+use_apr_day}`.
diff 규칙: 새 deal_key 등장 → new_trade / ratio가 0.80 경계를 아래→위로 통과 → ratio_crossed / building_fingerprint 변화 → building_changed / scan 자체가 주소 미확정 등으로 무결과 → scan_failed(스냅샷은 갱신하지 않음).
첫 실행(스냅샷 없음)은 기준선 저장만 하고 이벤트를 만들지 않는다.
테스트: `tests/test_watch.py` — 첫 실행 무이벤트, 신규 거래 감지, 경계 통과, 실패 시 스냅샷 보존. pipeline.scan은 monkeypatch.

### A5. `README.md` + `.github/workflows/scan.yml` + `.github/workflows/watch.yml` + `.gitignore` + `LICENSE`

- README: 한국어. 순서 = 한 줄 소개 → 데모(샘플 리포트 링크 자리 `reports/`) → 3분 시작(`pip install -e .` 또는 `python -m jeonse_guard.cli scan "주소"`) → fork해서 쓰는 법(Actions) → 신호 설명 → 판정 철학(단정하지 않음)과 면책 → 데이터 출처(국토교통부 실거래가·건축물대장 15134735, k-skill-proxy 경유, NomaDamas/k-skill MIT 크레딧) → 프록시 사용 예절(개인 소량, 대량이면 공공데이터포털 키 자가 발급 안내).
- `scan.yml`: `workflow_dispatch` + inputs(address 필수, deposit/name/area 선택) → checkout → setup-python 3.12 → `python -m jeonse_guard.cli scan ...` → 리포트를 커밋(`github-actions[bot]`) 또는 artifact 업로드. fork 사용자가 브라우저만으로 진단 가능해야 함.
- `watch.yml`: cron `0 22 * * *`(KST 아침 7시) + workflow_dispatch → `python -m jeonse_guard.cli watch` → stdout 이벤트를 jq로 순회하며 `gh issue create`(GITHUB_TOKEN, `permissions: issues: write, contents: write`) → 스냅샷·리포트 커밋. 이벤트 0건이면 커밋만.
- LICENSE: MIT (2026).
- `.gitignore`: `__pycache__/`, `.pytest_cache/`, `dist/`, `.venv/`.
