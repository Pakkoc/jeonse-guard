"""공용 예외. 어댑터는 실패 시 SourceError 계열만 던진다."""

from __future__ import annotations


class SourceError(Exception):
    """데이터 소스 호출 실패 (HTTP 오류, 파싱 실패, 빈 응답 등)."""


class AmbiguousAddressError(SourceError):
    """주소가 하나로 확정되지 않음. 추정하지 않고 중단한다."""

    def __init__(self, message: str, candidates: list[str] | None = None):
        super().__init__(message)
        self.candidates = candidates or []
