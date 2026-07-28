"""실행 설정. 프록시 기본 경로는 k-skill-proxy hosted, 환경변수로 교체 가능."""

from __future__ import annotations

import os

DEFAULT_PROXY_BASE = "https://k-skill-proxy.nomadamas.org"
USER_AGENT = "jeonse-guard/0.1 (+https://github.com/qkrtjdgh751014/jeonse-guard)"
DEFAULT_TIMEOUT = 25  # seconds


def proxy_base() -> str:
    return os.environ.get("KSKILL_PROXY_BASE_URL", DEFAULT_PROXY_BASE).rstrip("/")
