from __future__ import annotations

import tracemalloc
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from app.adapters.m3u.parser import M3UParser, iter_lines, parse_attributes, split_extinf
from app.adapters.m3u.provider import build_catchup_url
from app.domain.entities import ChannelInfo, ProgrammeInfo

FIX = Path(__file__).parent / "fixtures" / "sample.m3u"


def parse_fixture() -> tuple[M3UParser, list[ChannelInfo]]:
    p = M3UParser()
    return p, list(p.parse_lines(FIX.read_text().splitlines()))


def test_header_epg_urls_and_catchup_defaults() -> None:
    p, chans = parse_fixture()
    assert p.header.epg_urls == ["http://epg.example/guide.xml.gz"]
    assert p.header.catchup == "default"
    assert len(chans) == 7


def test_attributes_and_names() -> None:
    _, chans = parse_fixture()
    bbc = chans[0]
    assert bbc.name == "BBC One HD"
    assert bbc.tvg_id == "bbc1.uk"
    assert bbc.tvg_name == "BBC One"
    assert bbc.logo == "http://logos.example/bbc1.png"
    assert bbc.tvg_language == "English"
    assert bbc.group == "UK | General"
    assert bbc.stream_url == "http://streams.example/live/bbc1.m3u8"
    assert bbc.catchup_type == "default" and bbc.catchup_days == 3
    assert chans[1].tvg_country == "FR"


def test_extvlcopt_headers() -> None:
    _, chans = parse_fixture()
    tf1 = chans[1]
    assert tf1.headers == {"User-Agent": "SuperPlayer/2.0", "Referer": "http://ref.example/"}
    # EXTVLCOPT must not leak to the next channel
    assert chans[2].headers == {}


def test_pipe_headers_and_extgrp() -> None:
    _, chans = parse_fixture()
    sky = chans[3]
    assert sky.stream_url == "http://streams.example/live/sky1.ts"
    assert sky.headers == {"User-Agent": "PipeUA", "Referer": "http://pipe.example/"}
    bare = chans[6]
    assert bare.name == "Bare Channel" and bare.group == "Extras"


def test_default_headers_overridden_by_vlcopt() -> None:
    p = M3UParser(default_headers={"User-Agent": "Default", "Referer": "http://d/"})
    chans = list(p.parse_lines(FIX.read_text().splitlines()))
    assert chans[0].headers == {"User-Agent": "Default", "Referer": "http://d/"}
    assert chans[1].headers["User-Agent"] == "SuperPlayer/2.0"


def test_stable_ids_are_unique_and_deterministic() -> None:
    _, a = parse_fixture()
    _, b = parse_fixture()
    assert [c.ext_id for c in a] == [c.ext_id for c in b]
    assert len({c.ext_id for c in a}) == len(a)


def test_quoted_comma_in_attribute() -> None:
    attrs, name = split_extinf('#EXTINF:-1 tvg-name="A, B" group-title="X",Real Name')
    assert name == "Real Name"
    assert parse_attributes(attrs)["tvg-name"] == "A, B"


def test_catchup_url_template() -> None:
    _, chans = parse_fixture()
    tf1 = chans[1]
    start = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)
    stop = datetime(2026, 10, 31, 11, 0, tzinfo=UTC)
    url = build_catchup_url(tf1, ProgrammeInfo("tf1.fr", start, stop, "x"), now=stop)
    assert url == f"http://streams.example/tf1/{int(start.timestamp())}-3600.ts"


async def test_large_playlist_streams_in_constant_memory() -> None:
    """200k channels in small chunks; peak memory must stay far below the playlist size."""
    n = 200_000
    entry = (
        '#EXTINF:-1 tvg-id="ch{i}.xx" tvg-logo="http://logo/{i}.png" group-title="Group {g}",Channel {i}\n'
        "#EXTVLCOPT:http-user-agent=UA{i}\n"
        "http://streams.example/live/{i}.ts\n"
    )
    approx_size = len(entry) * n

    async def chunks() -> AsyncIterator[bytes]:
        yield b"\xef\xbb\xbf#EXTM3U\n"  # BOM
        buf = []
        for i in range(n):
            buf.append(entry.format(i=i, g=i % 50))
            if len(buf) == 97:  # odd chunking to split lines across chunks
                yield "".join(buf).encode()
                buf = []
        if buf:
            yield "".join(buf).encode()

    tracemalloc.start()
    count = 0
    last = None
    parser = M3UParser()
    async for ch in parser.parse(iter_lines(chunks())):
        count += 1
        last = ch
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert count == n
    assert last is not None and last.name == f"Channel {n - 1}"
    assert last.headers == {"User-Agent": f"UA{n - 1}"}
    assert peak < approx_size / 10, (peak, approx_size)


async def test_iter_lines_handles_crlf_and_split_multibyte() -> None:
    data = "#EXTM3U\r\n#EXTINF:-1,Café\r\nhttp://x/1\r\n".encode()

    async def chunks() -> AsyncIterator[bytes]:
        for i in range(len(data)):
            yield data[i : i + 1]

    lines = [ln async for ln in iter_lines(chunks())]
    assert lines == ["#EXTM3U", "#EXTINF:-1,Café", "http://x/1"]
