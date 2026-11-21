"""Canvas HLS segments: AAC with a timed JPEG contact sheet in an ID3 private frame.

FFmpeg writes our fixed-layout transport stream atomically. The HLS route packages only
a segment the viewer is authorized to read. Native HLS and hls.js put its metadata on
the audio clock, including across pauses, seeks, and live playlist updates.
"""

from pathlib import Path

# Two seconds at 12 fps. A sheet stays within 4096 pixels in either dimension.
COLUMNS, ROWS, FPS = 4, 6, 12
MAX_SEGMENT_BYTES = 16 * 1024 * 1024
PICTURE_OWNER = b"streamhub.canvas.v1"


def _priv(owner: bytes, payload: bytes) -> bytes:
    content = owner + b"\0" + payload
    return b"PRIV" + _size(len(content)) + b"\0\0" + content


def _size(n: int) -> bytes:
    return bytes((n >> shift) & 127 for shift in (21, 14, 7, 0))


def packed_audio(path: Path, *, hd: bool = False) -> bytes:
    """Extract PID 256 (sheet) and 257 (selected audio, or silence for a silent film)."""
    with path.open("rb") as source:
        segment = source.read(MAX_SEGMENT_BYTES + 1)
    if len(segment) > MAX_SEGMENT_BYTES or len(segment) % 188:
        raise ValueError("Invalid canvas segment size")
    picture, audio = bytearray(), bytearray()
    sheets: list[bytearray] = []
    first_pts: dict[int, int] = {}
    view = memoryview(segment)
    for start in range(0, len(segment), 188):
        packet = view[start : start + 188]
        if packet[0] != 0x47:
            raise ValueError("Invalid transport packet")
        pid = ((packet[1] & 31) << 8) | packet[2]
        if pid not in (256, 257) or not packet[3] & 16:
            continue
        offset = 5 + packet[4] if packet[3] & 32 else 4
        if packet[1] & 64:
            if hd and pid == 256 and picture:
                sheets.append(picture)
                picture = bytearray()
            pes = packet[offset:]
            if len(pes) < 14 or pes[:3] != b"\0\0\1" or not pes[7] & 128:
                raise ValueError("Missing canvas timestamp")
            if pid not in first_pts:
                p = pes[9:14]
                first_pts[pid] = (p[0] & 14) << 29 | p[1] << 22 | (p[2] & 254) << 14 | p[3] << 7 | p[4] >> 1
            offset += 9 + pes[8]
        (picture if pid == 256 else audio).extend(packet[offset:])
    sheets.append(picture)
    if not audio or not all(sheet.startswith(b"\xff\xd8") and sheet.endswith(b"\xff\xd9") for sheet in sheets):
        raise ValueError("Incomplete canvas segment")
    audio_pts = first_pts[257]
    delta = ((first_pts[256] - audio_pts + (1 << 32)) % (1 << 33)) - (1 << 32)
    timing = round(delta / 90).to_bytes(4, "big", signed=True)
    tag = _priv(b"com.apple.streaming.transportStreamTimestamp", audio_pts.to_bytes(8, "big"))
    # HD carries up to four half-second sheets to keep decoded phone bitmaps below 4096px.
    if hd:
        if len(sheets) > 4:
            raise ValueError("Too many canvas sheets")
        payload = bytes([len(sheets)]) + b"".join(len(sheet).to_bytes(4, "big") + sheet for sheet in sheets)
    else:
        payload = bytes(picture)
    tag += _priv(PICTURE_OWNER, timing + payload)
    return b"ID3\x04\0\0" + _size(len(tag)) + tag + audio
