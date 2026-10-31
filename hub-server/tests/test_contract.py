"""Every path+method in contracts/openapi.yaml must be implemented under /api/v1."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import Api

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
METHODS = ("get", "post", "put", "patch", "delete")


def contract_ops() -> set[tuple[str, str]]:
    yaml = pytest.importorskip("yaml")
    if not CONTRACT.exists():
        pytest.skip("contract not present")
    spec = yaml.safe_load(CONTRACT.read_text())
    return {(m.upper(), p) for p, item in spec["paths"].items() for m in METHODS if m in item}


async def test_all_contract_paths_implemented(api: Api) -> None:
    spec = (await api.client.get("/api/openapi.json")).json()
    served = {
        (m.upper(), re.sub(r"^/api/v1", "", p)) for p, item in spec["paths"].items() for m in METHODS if m in item
    }
    missing = contract_ops() - served
    assert not missing, f"not implemented: {sorted(missing)}"


async def test_docs_served(api: Api) -> None:
    assert (await api.client.get("/api/docs")).status_code == 200


async def test_spa_fallback(api: Api) -> None:
    dist = api.c.settings.web_dist
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "a.js").write_text("console.log(1)")
    assert (await api.client.get("/")).text == "<html>app</html>"
    assert (await api.client.get("/live/guide")).text == "<html>app</html>"
    r = await api.client.get("/assets/a.js")
    assert r.text == "console.log(1)" and "immutable" in r.headers["cache-control"]
    assert (await api.client.get("/../../etc/passwd")).text == "<html>app</html>"
    assert (await api.client.get("/api/v1/nope")).status_code == 404


async def test_qr_svg(api: Api) -> None:
    r = await api.client.get("/api/v1/qr", params={"data": "http://hub.local/pair?code=ABC123"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml" and b"<svg" in r.content
