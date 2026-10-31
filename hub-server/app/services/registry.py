"""SourceRegistry: SourceType → adapter factory (+ capability metadata).

Services ask the registry for a provider for a Source row; they never import
adapter modules. New source types register here at composition time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.entities import SourceRef, SourceType
from app.domain.ports import AdapterContext, SourceFactory, SourceProvider


@dataclass(frozen=True, slots=True)
class AdapterInfo:
    type: SourceType
    factory: SourceFactory
    in_app: bool
    requires_disclaimer: bool


class UnknownSourceType(LookupError):
    pass


class SourceRegistry:
    def __init__(self, ctx: AdapterContext) -> None:
        self._ctx = ctx
        self._adapters: dict[str, AdapterInfo] = {}

    @property
    def context(self) -> AdapterContext:
        return self._ctx

    def register(
        self,
        type_: SourceType | str,
        factory: SourceFactory,
        *,
        in_app: bool = True,
        requires_disclaimer: bool = False,
    ) -> None:
        st = SourceType(type_)
        self._adapters[st.value] = AdapterInfo(st, factory, in_app, requires_disclaimer)

    def info(self, type_: SourceType | str) -> AdapterInfo:
        try:
            return self._adapters[str(type_)]
        except KeyError as exc:
            raise UnknownSourceType(str(type_)) from exc

    def types(self) -> list[str]:
        return list(self._adapters)

    def requires_disclaimer(self, type_: SourceType | str) -> bool:
        return self.info(type_).requires_disclaimer

    def is_in_app(self, type_: SourceType | str) -> bool:
        try:
            return self.info(type_).in_app
        except UnknownSourceType:
            return False

    def create(self, ref: SourceRef, config: dict[str, Any]) -> SourceProvider:
        return self.info(ref.type).factory(ref, config, self._ctx)
