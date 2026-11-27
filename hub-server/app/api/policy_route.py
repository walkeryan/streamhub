"""Final response gate supplements SQL filtering for external metadata discovery."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse, Response

from app.services.errors import Forbidden, NotFound
from app.services.household_library import filter_metadata


class PolicyCatalogRoute(APIRoute):
    def get_route_handler(self) -> Any:
        original = super().get_route_handler()

        async def protected(request: Request) -> Response:
            response = await original(request)
            c = request.app.state.container
            principal = getattr(request.state, "principal", None)
            hidden: frozenset[str] = getattr(request.state, "removed_media", frozenset())
            if (
                principal is None
                or response.status_code != 200
                or (not hidden and not c.content_policy.applies_to(principal))
            ):
                return response
            if "application/json" not in response.headers.get("content-type", ""):
                return response
            payload = json.loads(bytes(response.body))
            filtered = filter_metadata(payload, hidden) if hidden else payload
            if filtered is None:
                raise NotFound("Title removed from the household library")
            if c.content_policy.applies_to(principal):
                filtered = await c.content_policy.filter_payload(principal.profile_id, filtered)
            if filtered is None:
                raise Forbidden("This content is blocked for the current profile", code="content_restricted")
            headers = {key: value for key, value in response.headers.items() if key.lower() != "content-length"}
            headers["Cache-Control"] = "private, no-store"
            return JSONResponse(filtered, headers=headers)

        return protected
