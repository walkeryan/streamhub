"""Final response gate supplements SQL filtering for external metadata discovery."""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse, Response

from app.services.errors import Forbidden


class PolicyCatalogRoute(APIRoute):
    def get_route_handler(self) -> Any:
        original = super().get_route_handler()

        async def protected(request: Request) -> Response:
            response = await original(request)
            c = request.app.state.container
            principal = getattr(request.state, "principal", None)
            if not c.content_policy.enabled or principal is None or response.status_code != 200:
                return response
            if "application/json" not in response.headers.get("content-type", ""):
                return response
            payload = json.loads(bytes(response.body))
            filtered = await c.content_policy.filter_payload(principal.profile_id, payload)
            if filtered is None:
                raise Forbidden("This content is blocked for the current profile", code="content_restricted")
            headers = {key: value for key, value in response.headers.items() if key.lower() != "content-length"}
            headers["Cache-Control"] = "private, no-store"
            return JSONResponse(filtered, headers=headers)

        return protected
