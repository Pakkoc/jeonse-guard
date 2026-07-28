"""카카오 지오코딩으로 주소를 필지 1건으로 확정하는 어댑터.

결과가 0건이거나 2건 이상이거나 필지 정보가 없으면 **추정하지 않고**
AmbiguousAddressError(후보 목록 포함)로 중단한다.

참고: NomaDamas/k-skill building-register-search (MIT)
"""

from __future__ import annotations

from .. import net
from ..errors import AmbiguousAddressError, SourceError
from ..models import ResolvedAddress


def resolve_address(query: str, *, base: str, timeout: int = 25) -> ResolvedAddress:
    """주소 문자열을 카카오 geocode(프록시 경유)로 조회해 필지 1건으로 확정한다.

    - 0건 / 2건 이상 → AmbiguousAddressError (후보 목록 포함)
    - 지번(b_code·본번) 정보 누락 → AmbiguousAddressError
    - 네트워크/응답 형식 오류 → SourceError
    """
    payload = net.get_json(
        f"{base}/v1/kakao-local/geocode", {"q": query, "limit": 2}, timeout=timeout
    )
    documents = payload.get("documents")
    if not isinstance(documents, list):
        raise SourceError(f"지오코딩 응답에 documents 목록이 없습니다: {str(payload)[:200]}")

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
