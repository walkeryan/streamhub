"""Settings that would quietly weaken a deployment are refused, or default to closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.container import build_container
from app.infra.cache import MemoryCache
from app.infra.settings import MIN_SECRET_KEY, AppSettings
from tests.conftest import http_factory


def build(tmp_path: Path, **values: object) -> AppSettings:
    settings = AppSettings(config_dir=tmp_path / "config", enable_scheduler=False, **values)  # type: ignore[arg-type]
    build_container(settings, http_factory=http_factory, cache=MemoryCache())
    return settings


def test_a_short_secret_key_stops_the_server_from_starting(tmp_path: Path) -> None:
    assert MIN_SECRET_KEY == 32
    for short in ("q7", "change-me-long-random", "k" * 31):
        with pytest.raises(ValueError, match="SECRET_KEY") as refused:
            build(tmp_path, secret_key=short)
        assert short not in str(refused.value)
    build(tmp_path, secret_key="k" * 32)


def test_the_generated_key_is_long_and_always_accepted(tmp_path: Path) -> None:
    settings = build(tmp_path, secret_key=None)
    stored = (tmp_path / "config" / "secret.key").read_text().strip()
    assert settings.resolved_secret_key == stored and len(stored) >= 48
    # A key file from an older install is the generated one whatever its length: it is not refused,
    # because changing it would make the stored source credentials unreadable.
    (tmp_path / "config" / "secret.key").write_text("an-older-short-key")
    assert build(tmp_path, secret_key=None).resolved_secret_key == "an-older-short-key"


def test_an_existing_install_can_keep_a_short_key_on_purpose(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        settings = build(tmp_path, secret_key="k" * 20, secret_key_allow_short=True)
    assert settings.resolved_secret_key == "k" * 20
    assert any("SECRET_KEY" in r.getMessage() for r in caplog.records) and "k" * 20 not in caplog.text


def test_no_credentialed_cross_origin_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert AppSettings(config_dir=tmp_path).cors_origin_list == []
    assert AppSettings(config_dir=tmp_path, cors_origins=" https://hub.example.test ,").cors_origin_list == [
        "https://hub.example.test"
    ]
