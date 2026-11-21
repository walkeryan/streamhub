"""Exercise the exact FFmpeg and ID3 wire format used by native HLS and the web player."""

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.proxy.canvas import PICTURE_OWNER, packed_audio
from app.proxy.stream_proxy import canvas_args


@pytest.mark.parametrize("live,with_audio", [(True, True), (False, False)])
def test_real_canvas_segments(tmp_path: Path, live: bool, with_audio: bool) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    source = tmp_path / "source.mkv"
    inputs = ["-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24"]
    if with_audio:
        inputs += ["-f", "lavfi", "-i", "sine=frequency=440"]
    subprocess.run([ffmpeg, "-loglevel", "error", *inputs, "-t", "4.3", "-c:v", "ffv1", str(source)], check=True)
    subprocess.run(
        canvas_args(ffmpeg, str(source), {}, tmp_path, live=live, vod=None, silent=not with_audio),
        check=True,
        timeout=20,
    )
    segments = sorted(tmp_path.glob("seg_*.ts"))
    assert len(segments) >= 2
    for path in segments:
        packed = packed_audio(path)
        assert packed.startswith(b"ID3\x04") and b"com.apple.streaming.transportStreamTimestamp\0" in packed
        jpeg = packed.index(PICTURE_OWNER + b"\0") + len(PICTURE_OWNER) + 5
        # The ID3 tag ends at the last JPEG byte. What follows is an AAC ADTS stream.
        end = 10 + sum(b << shift for b, shift in zip(packed[6:10], (21, 14, 7, 0), strict=True))
        sheet = Image.open(io.BytesIO(packed[jpeg:end]))
        assert sheet.size == (160 * 4, 90 * 6)
        assert packed[end] == 0xFF and packed[end + 1] & 0xF0 == 0xF0
    first = packed_audio(segments[0])
    decoded = subprocess.run(
        [ffmpeg, "-v", "error", "-f", "aac", "-i", "pipe:", "-f", "s16le", "-"],
        input=first,
        capture_output=True,
        check=True,
    ).stdout
    assert len(decoded) > 100_000 and bool(any(decoded)) == with_audio


def test_live_canvas_reads_its_source_at_playback_speed(tmp_path: Path) -> None:
    """A source that sends faster than it plays (a start-up backlog, a provider's looping filler clip)
    must not roll the six-segment live window away before a viewer can fetch from it."""
    import time

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    source = tmp_path / "source.mkv"
    subprocess.run(
        [ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24", "-f", "lavfi", "-i",
         "sine=frequency=440", "-t", "60", "-c:v", "libx264", "-preset", "ultrafast", str(source)],
        check=True,
    )  # fmt: skip
    proc = subprocess.Popen(canvas_args(ffmpeg, str(source), {}, tmp_path, live=True, vod=None))
    try:
        time.sleep(5)
        newest = max(int(path.stem[4:]) for path in tmp_path.glob("seg_*.ts"))
    finally:
        proc.kill()
        proc.wait()
    # Six seconds of start-up burst and five of playback: about five segments, never the whole minute.
    assert 2 <= newest <= 8


def test_incomplete_segment_cannot_return_a_picture(tmp_path: Path) -> None:
    path = tmp_path / "broken.ts"
    path.write_bytes(b"not a complete transport packet")
    with pytest.raises(ValueError):
        packed_audio(path)


@pytest.mark.parametrize(("height", "width"), [(720, 1280), (1080, 1920)])
def test_hd_canvas_uses_four_phone_sized_sheets(tmp_path: Path, height: int, width: int) -> None:
    from app.proxy.stream_proxy import VodPlan

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is not installed")
    source = tmp_path / "hd.mkv"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=1920x1080:rate=12",
            "-t",
            "3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(source),
        ],
        check=True,
    )
    args = canvas_args(
        ffmpeg, str(source), {}, tmp_path, live=False, vod=VodPlan(video="canvas", max_height=height), silent=True
    )
    subprocess.run(args, check=True, timeout=30)
    data = packed_audio(tmp_path / "seg_00000.ts", hd=True)
    pos = data.index(PICTURE_OWNER + b"\0") + len(PICTURE_OWNER) + 5
    assert data[pos] == 4
    pos += 1
    for _ in range(4):
        length = int.from_bytes(data[pos : pos + 4], "big")
        pos += 4
        with Image.open(io.BytesIO(data[pos : pos + length])) as sheet:
            assert sheet.size == (width * 2, height * 3)
        pos += length
    assert data[pos : pos + 1] == b"\xff"  # first AAC frame
