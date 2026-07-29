"""카카오 지오코딩으로 주소를 필지 1건으로 확정하는 어댑터.

결과가 0건이거나 2건 이상이거나 필지 정보가 없으면 **추정하지 않고**
AmbiguousAddressError(후보 목록 포함)로 중단한다.

이중 모드 (프록시 우선, 실패 시 카카오 REST API 직접 호출):
① 프록시 `/v1/kakao-local/geocode`를 먼저 시도한다.
② 실패(네트워크 오류 등) 시 환경변수 KAKAO_REST_API_KEY가 있으면
   dapi.kakao.com 주소 검색(search/address.json)을 직접 호출한다.
   프록시가 갖는 "주소 검색 0건 시 키워드 검색 재시도" 폴백은 재현하지 않는다 —
   직접 모드는 프록시 장애 시 비상 경로이므로 정확한 지번 주소 입력을 전제로 한다.
③ 키도 없으면 발급 안내를 담은 SourceError로 중단한다.

참고: NomaDamas/k-skill building-register-search (MIT)
"""

from __future__ import annotations

import os

from .. import net
from ..errors import AmbiguousAddressError, SourceError
from ..models import ResolvedAddress

DIRECT_URL = "https://dapi.kakao.com/v2/local/search/address.json"
NO_KEY_MSG = (
    "프록시 호출 실패 + API 키 없음 — 카카오 개발자센터(https://developers.kakao.com)에서 "
    "REST API 키 발급 후 KAKAO_REST_API_KEY 환경변수로 설정하세요"
)


def resolve_address(query: str, *, base: str, timeout: int = 25) -> ResolvedAddress:
    """주소 문자열을 지오코딩(프록시 우선, 실패 시 카카오 직접 호출)해 필지 1건으로 확정한다.

    - 0건 / 2건 이상 → AmbiguousAddressError (후보 목록 포함)
    - 지번(b_code·본번) 정보 누락 → AmbiguousAddressError
    - 네트워크/응답 형식 오류 → SourceError
    """
    try:
        payload = net.get_json(
            f"{base}/v1/kakao-local/geocode", {"q": query, "limit": 2}, timeout=timeout
        )
    except SourceError as proxy_exc:
        payload = _fetch_direct(query, timeout=timeout, proxy_exc=proxy_exc)

    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise SourceError(f"지오코딩 응답에 documents 목록이 없습니다: {str(payload)[:200]}")

    return _resolve_from_documents(query, documents)


def _fetch_direct(query: str, *, timeout: int, proxy_exc: SourceError) -> dict:
    """직접 모드: 카카오 주소 검색 API 호출. 키 없으면 안내 후 중단."""
    key = (os.environ.get("KAKAO_REST_API_KEY") or "").strip()
    if not key:
        raise SourceError(NO_KEY_MSG) from proxy_exc
    return net.get_json(
        DIRECT_URL,
        {"query": query, "size": 2},
        timeout=timeout,
        headers={"authorization": f"KakaoAK {key}"},
    )


def _resolve_from_documents(query: str, documents: list) -> ResolvedAddress:
    """documents 목록에서 필지 1건을 확정한다 (프록시·직접 모드 공용 파싱)."""
    candidates = [
        str(doc.get("address_name") or "").strip()
        for doc in documents
        if isinstance(doc, dict) and doc.get("address_name")
    ]
    if len(documents) == 0:
        raise AmbiguousAddressError(f"주소 검색 결과가 없습니다: {query!r}", [])
    if len(documents) > 1:
        raise AmbiguousAddressError(
            f"주소가 하나로 확정되지 않습니다 ({len(documents)}건 이상): {query!r}", candidates
        )

    doc = documents[0]
    address = doc.get("address") if isinstance(doc, dict) else None
    if not isinstance(address, dict):
        raise AmbiguousAddressError(
            "주소 결과에 지번(법정동 필지) 정보가 없습니다 — 번지까지 포함한 주소를 입력하세요.",
            candidates,
        )

    b_code = str(address.get("b_code") or "").strip()
    main = str(address.get("main_address_no") or "").strip()
    sub = str(address.get("sub_address_no") or "").strip() or "0"
    mountain = str(address.get("mountain_yn") or "N").strip().upper() == "Y"

    if len(b_code) != 10 or not b_code.isdigit():
        raise AmbiguousAddressError(
            "주소 결과에 10자리 법정동 코드(b_code)가 없습니다.", candidates
        )
    if not main.isdigit() or len(main) > 4:
        raise AmbiguousAddressError(
            "주소 결과에 본번이 없어 필지를 확정할 수 없습니다.", candidates
        )
    if not sub.isdigit() or len(sub) > 4:
        raise AmbiguousAddressError(
            "주소 결과의 부번이 올바르지 않아 필지를 확정할 수 없습니다.", candidates
        )

    road_address = doc.get("road_address")
    road = None
    if isinstance(road_address, dict):
        road = str(road_address.get("address_name") or "").strip() or None

    region_parts = (
        str(address.get("region_1depth_name") or "").strip(),
        str(address.get("region_2depth_name") or "").strip(),
    )
    region_name = " ".join(part for part in region_parts if part)

    jibun = (
        str(address.get("address_name") or "").strip()
        or str(doc.get("address_name") or "").strip()
    )

    return ResolvedAddress(
        raw=query,
        jibun=jibun,
        road=road,
        b_code=b_code,
        mountain=mountain,
        bun=main.zfill(4),
        ji=sub.zfill(4),
        region_name=region_name,
    )
