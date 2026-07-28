"""건축물대장 표제부 어댑터 — 이중 모드 (프록시 우선, 실패 시 data.go.kr 직접 XML).

① 프록시 `/v1/building-register/title` 를 먼저 시도한다 (JSON).
② 실패(예: 라우트 미배포 404) 시 환경변수 KSKILL_BUILDING_REGISTER_API_KEY →
   DATA_GO_KR_API_KEY 순으로 키를 찾아, 있으면 data.go.kr BldRgstHubService
   getBrTitleInfo 를 직접 호출해 XML을 파싱한다.
③ 키도 없으면 발급 안내를 담은 SourceError로 중단한다.

여러 동이 반환되면 전부 raw에 보존하고 연면적(totArea) 최대 1건으로 요약한다.

참고: NomaDamas/k-skill building-register-search (MIT)
"""

from __future__ import annotations

import os
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Optional

from .. import net
from ..errors import SourceError
from ..models import BuildingTitle, ResolvedAddress

DIRECT_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
NO_KEY_MSG = (
    "프록시 미배포 + API 키 없음 — data.go.kr에서 무료 키 발급 후 "
    "DATA_GO_KR_API_KEY 설정 (데이터셋 15134735 활용신청 필요)"
)


def fetch_title(addr: ResolvedAddress, *, base: str, timeout: int = 30) -> BuildingTitle:
    """확정된 필지의 건축물대장 표제부를 조회해 대표 1건으로 요약한다.

    프록시 실패 시 직접 모드로 폴백하고, 표제부가 아예 없으면 SourceError.
    """
    params = {
        "sigunguCd": addr.sigungu_cd,
        "bjdongCd": addr.bjdong_cd,
        "platGbCd": addr.plat_gb_cd,
        "bun": addr.bun,
        "ji": addr.ji,
        "numOfRows": 100,
    }
    try:
        payload = net.get_json(
            f"{base}/v1/building-register/title", params, timeout=timeout
        )
    except SourceError as proxy_exc:
        items = _fetch_direct(params, timeout=timeout, proxy_exc=proxy_exc)
        return _summarize(items, mode="direct")
    return _summarize(_extract_proxy_items(payload), mode="proxy")


def _api_key() -> Optional[str]:
    """직접 모드용 API 키 — KSKILL_BUILDING_REGISTER_API_KEY 우선."""
    for name in ("KSKILL_BUILDING_REGISTER_API_KEY", "DATA_GO_KR_API_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def _fetch_direct(params: dict, *, timeout: int, proxy_exc: SourceError) -> list[dict]:
    """직접 모드: data.go.kr getBrTitleInfo XML 호출·파싱. 키 없으면 안내 후 중단."""
    key = _api_key()
    if key is None:
        raise SourceError(NO_KEY_MSG) from proxy_exc
    direct_params = dict(params)
    # data.go.kr "Encoding" 형태 키(%2B 등 포함)는 urlencode를 거치며 이중 인코딩되어
    # 인증에 실패하므로, 원문(Decoding) 형태로 되돌려 전달한다.
    direct_params["serviceKey"] = urllib.parse.unquote(key) if "%" in key else key
    xml_text = net.get_text(DIRECT_URL, direct_params, timeout=timeout)
    return _parse_title_xml(xml_text)


def _extract_proxy_items(payload: dict) -> list[dict]:
    """프록시 JSON 응답에서 items 목록을 꺼낸다 (단건 dict도 목록으로 정규화)."""
    items = payload.get("items") or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise SourceError(f"건축물대장 프록시 응답 items 형식이 올바르지 않습니다: {str(payload)[:200]}")
    return [item for item in items if isinstance(item, dict)]


def _parse_title_xml(xml_text: str) -> list[dict]:
    """공공데이터포털 표준 XML 응답을 item dict 목록으로 파싱한다."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SourceError(f"건축물대장 XML 파싱 실패: {xml_text[:200]}") from exc
    # 인증 실패 등은 별도 봉투(OpenAPI_ServiceResponse)로 온다
    if root.tag == "OpenAPI_ServiceResponse":
        reason = (
            root.findtext("./cmmMsgHeader/returnAuthMsg")
            or root.findtext("./cmmMsgHeader/errMsg")
            or ""
        ).strip()
        raise SourceError(f"건축물대장 API 인증/서비스 오류: {reason or '원인 미상'}")
    result_code = (root.findtext("./header/resultCode") or "").strip()
    if result_code not in {"", "0", "00"}:
        result_msg = (root.findtext("./header/resultMsg") or "").strip()
        raise SourceError(f"건축물대장 API 오류: {result_msg or result_code}")
    body = root.find("./body")
    if body is None:
        raise SourceError("건축물대장 API 응답에 body가 없습니다.")
    return [
        {child.tag: (child.text or "").strip() for child in item}
        for item in body.findall("./items/item")
    ]


def _summarize(items: list[dict], *, mode: str) -> BuildingTitle:
    """여러 동을 raw에 보존하고 연면적 최대 1건을 대표로 요약한다."""
    if not items:
        raise SourceError("표제부 없음 — 해당 필지의 건축물대장 표제부가 없습니다.")
    rep = max(items, key=lambda item: _to_float(item.get("totArea")) or 0.0)
    return BuildingTitle(
        main_purps=str(rep.get("mainPurpsCdNm") or "").strip(),
        etc_purps=_opt_str(rep.get("etcPurps")),
        tot_area=_to_float(rep.get("totArea")),
        grnd_flr_cnt=_to_int(rep.get("grndFlrCnt")),
        ugrnd_flr_cnt=_to_int(rep.get("ugrndFlrCnt")),
        use_apr_day=_opt_str(rep.get("useAprDay")),
        regstr_kind=_opt_str(rep.get("regstrKindCdNm")),
        plat_plc=_opt_str(rep.get("platPlc")),
        raw={"mode": mode, "items": items},
    )


def _opt_str(value: object) -> Optional[str]:
    """빈 문자열/None을 None으로 정규화."""
    text = str(value or "").strip()
    return text or None


def _to_float(value: object) -> Optional[float]:
    """숫자 문자열을 float로, 실패하면 None (필드 누락 관대 처리)."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_int(value: object) -> Optional[int]:
    """숫자 문자열을 int로, 실패하면 None (필드 누락 관대 처리)."""
    number = _to_float(value)
    return int(number) if number is not None else None
