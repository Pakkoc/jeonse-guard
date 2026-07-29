"""HTTP 유틸 (stdlib only). 모든 어댑터는 이 함수로만 네트워크에 나간다.

테스트는 이 모듈의 get_json을 monkeypatch해서 fixture를 주입한다 — 네트워크 없는 테스트.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .config import DEFAULT_TIMEOUT, USER_AGENT
from .errors import SourceError


def get_json(
    url: str,
    params: dict | None = None,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    headers: dict | None = None,
) -> dict:
    """GET 요청 후 JSON dict 반환. 실패는 전부 SourceError로 정규화.

    headers는 기본 헤더(accept/user-agent) 위에 덮어써진다 — 인증키 직접 호출 모드용.
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": USER_AGENT, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise SourceError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceError(f"네트워크 오류: {exc}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise SourceError(f"JSON 파싱 실패: {body[:200]}") from exc
    if not isinstance(payload, dict):
        raise SourceError(f"예상 밖 응답 형태: {type(payload).__name__}")
    return payload


def get_text(url: str, params: dict | None = None, *, timeout: int = DEFAULT_TIMEOUT) -> str:
    """GET 요청 후 본문 텍스트 반환 (XML 등 비-JSON 응답용). 실패는 SourceError."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"accept": "application/xml, text/plain, */*", "user-agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise SourceError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceError(f"네트워크 오류: {exc}") from exc
