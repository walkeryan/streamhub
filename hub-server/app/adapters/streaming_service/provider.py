"""Streaming-service (hand-off) 'source'. Holds no catalog; it only marks which
services the user subscribes to, for availability filtering and deep links."""

from __future__ import annotations

from typing import Any

from app.domain.entities import SourceRef, TestResult
from app.domain.ports import AdapterContext, CatalogSink


class StreamingServiceSource:
    def __init__(self, ref: SourceRef, config: dict[str, Any], ctx: AdapterContext) -> None:
        self.ref = ref
        self._cfg = config

    def config_summary(self) -> dict[str, Any]:
        return {"service_key": self.ref.service_key}

    def secrets(self) -> list[str]:
        return []

    async def test(self) -> TestResult:
        return TestResult(
            bool(self.ref.service_key), "Hand-off service" if self.ref.service_key else "missing service_key"
        )

    async def sync(self, sink: CatalogSink) -> None:
        return None


def factory(ref: SourceRef, config: dict[str, Any], ctx: AdapterContext) -> StreamingServiceSource:
    return StreamingServiceSource(ref, config, ctx)
