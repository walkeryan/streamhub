"""The TV app build this server can hand out, as described by a manifest next to the APK.

``tv-release.json`` lives in the web client's ``downloads/`` directory, the one the APK is
already served from. No manifest, or one that does not check out, means no update service.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MANIFEST = "tv-release.json"
MAX_BYTES = 16 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}")
_APK = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.apk")


def read_release(downloads: Path) -> dict[str, Any] | None:
    """The release the manifest in ``downloads`` describes, or None when it describes none.

    The APK is named, never addressed: a bare file name that must exist beside the manifest,
    so the manifest cannot point a TV at anything but a file this server already serves.
    """
    path = downloads / MANIFEST
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    code, name, apk, digest = (data.get(k) for k in ("version_code", "version_name", "apk", "sha256"))
    if isinstance(code, bool) or not isinstance(code, int) or code < 1:
        return None
    if not isinstance(name, str) or not name.strip() or len(name) > 64:
        return None
    if not isinstance(apk, str) or not _APK.fullmatch(apk) or not (downloads / apk).is_file():
        return None
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest.strip().lower()):
        return None
    return {
        "version_code": code,
        "version_name": name.strip(),
        "apk_url": f"/downloads/{apk}",
        "sha256": digest.strip().lower(),
    }
