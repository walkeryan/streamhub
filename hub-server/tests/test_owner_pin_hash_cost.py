"""One real production-cost hash roundtrip, never reduced fixture parameters."""

import hmac
import time

from app.services.owner_pin import OwnerPinService


def test_production_scrypt_roundtrip(record_property):
    start = time.perf_counter()
    hashed = OwnerPinService._hash("629417")
    checked = OwnerPinService._hash("629417", hashed)
    elapsed = time.perf_counter() - start
    record_property("production_hash_roundtrip_seconds", round(elapsed, 3))
    assert hashed.startswith("scrypt-v1$131072$8$1$")
    assert hmac.compare_digest(hashed, checked)
    assert "629417" not in hashed
    assert elapsed < 30, "Production hash roundtrip exceeded bounded test budget"
