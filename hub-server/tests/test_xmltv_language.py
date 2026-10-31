from __future__ import annotations

import gzip
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.adapters.language.inferrer import LanguageChain, normalise_code
from app.adapters.xmltv.parser import iter_file, parse_bytes, parse_xmltv_time
from app.domain.entities import ChannelInfo, LanguageSource, ProgrammeInfo

FIX = Path(__file__).parent / "fixtures" / "sample_epg.xml"


# ------------------------------------------------------------------ XMLTV
def test_parse_xmltv_time_offsets() -> None:
    assert parse_xmltv_time("20261031120000 +0100") == datetime(2026, 10, 31, 11, 0, tzinfo=UTC)
    assert parse_xmltv_time("20261031120000") == datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert parse_xmltv_time("20261031120000 -0500") == datetime(2026, 10, 31, 17, 0, tzinfo=UTC)
    assert parse_xmltv_time("garbage") is None


def test_parse_programmes_utc_and_filter() -> None:
    progs = parse_bytes(FIX.read_bytes(), wanted={"bbc1.uk", "tf1.fr"})
    assert [p.title for p in progs] == ["News at Noon", "Afternoon Show", "Le Journal"]
    news = progs[0]
    assert news.start == datetime(2026, 10, 31, 11, 0, tzinfo=UTC)
    assert news.stop == datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert news.description == "Headlines." and news.category == "News"


def test_missing_stop_defaults_to_30min() -> None:
    progs = parse_bytes(FIX.read_bytes())
    unwanted = next(p for p in progs if p.channel == "other.ch")
    assert (unwanted.stop - unwanted.start).total_seconds() == 1800


def test_gzip_file(tmp_path: Path) -> None:
    gz = tmp_path / "epg.xml.gz"
    gz.write_bytes(gzip.compress(FIX.read_bytes()))
    items = [x for x in iter_file(gz) if isinstance(x, ProgrammeInfo)]
    assert len(items) == 4
    channels = [x for x in iter_file(gz) if isinstance(x, tuple)]
    assert channels == [("bbc1.uk", ["BBC One"], "http://l/bbc.png")]


def test_old_programmes_skipped(tmp_path: Path) -> None:
    items = [
        x for x in iter_file(FIX, not_before=datetime(2026, 10, 31, 11, 45, tzinfo=UTC)) if isinstance(x, ProgrammeInfo)
    ]
    assert "Le Journal" not in [p.title for p in items]


# ------------------------------------------------------------------ language chain
@pytest.fixture
def chain() -> LanguageChain:
    lc = LanguageChain()
    lc.load_db(
        [
            {"id": "BBCOne.uk", "languages": ["eng"], "country": "UK"},
            {"id": "TV5Monde.fr", "languages": [], "country": "FR"},
            {"id": "AlJazeera.qa", "languages": ["ara"], "country": "QA"},
        ]
    )
    return lc


def ch(**kw: object) -> ChannelInfo:
    base: dict[str, object] = {"ext_id": "x", "name": "Channel", "group": "Misc"}
    base.update(kw)
    return ChannelInfo(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kw", "code", "source"),
    [
        ({"tvg_language": "French"}, "fra", LanguageSource.TAG),
        ({"tvg_language": "de"}, "deu", LanguageSource.TAG),
        ({"tvg_language": "ger"}, "deu", LanguageSource.TAG),
        ({"tvg_id": "bbcone.uk"}, "eng", LanguageSource.DB),
        ({"tvg_id": "AlJazeera.qa@HD"}, "ara", LanguageSource.DB),
        ({"tvg_id": "TV5Monde.fr"}, "fra", LanguageSource.COUNTRY),  # db hit without languages → its country
        ({"tvg_country": "BR"}, "por", LanguageSource.COUNTRY),
        ({"group": "EN| Movies"}, "eng", LanguageSource.GROUP),
        ({"group": "FR - Sport"}, "fra", LanguageSource.GROUP),
        ({"group": "|AR| Kids"}, "ara", LanguageSource.GROUP),
        ({"group": "DE: News"}, "deu", LanguageSource.GROUP),
        ({"group": "ARABIC CHANNELS"}, "ara", LanguageSource.GROUP),
        ({"group": "Sports ENGLISH"}, "eng", LanguageSource.GROUP),
        ({"group": "Movies | TR"}, "tur", LanguageSource.GROUP),
        ({"group": "Music 🇮🇹"}, "ita", LanguageSource.FLAG),
        ({"name": "🇵🇱 TVP 1"}, "pol", LanguageSource.FLAG),
        ({"name": "Hindi Movies 24"}, "hin", LanguageSource.NAME),
        ({"name": "UK: BBC Two"}, "eng", LanguageSource.NAME),
        ({"group": "HD Channels", "name": "Movies HD"}, "und", None),
    ],
)
def test_language_chain(chain: LanguageChain, kw: dict[str, object], code: str, source: LanguageSource | None) -> None:
    guess = chain.infer(ch(**kw))
    assert (guess.code, guess.source) == (code, source)


def test_chain_priority_tag_beats_everything(chain: LanguageChain) -> None:
    g = chain.infer(ch(tvg_language="spa", tvg_id="bbcone.uk", tvg_country="FR", group="DE: x", name="ARABIC"))
    assert (g.code, g.source) == ("spa", LanguageSource.TAG)


def test_chain_db_beats_country_and_group(chain: LanguageChain) -> None:
    g = chain.infer(ch(tvg_id="BBCOne.uk", tvg_country="FR", group="DE: x"))
    assert (g.code, g.source) == ("eng", LanguageSource.DB)


def test_normalise_code() -> None:
    assert normalise_code("en") == "eng"
    assert normalise_code("en-US") == "eng"
    assert normalise_code("Français") == "fra"
    assert normalise_code("xx") is None


async def test_iptv_org_db_download_cached(respx_mock: object, tmp_path: Path) -> None:
    import httpx
    import respx

    from app.domain.ports import AdapterContext
    from app.infra.cache import MemoryCache

    assert isinstance(respx_mock, respx.MockRouter)
    route = respx_mock.get("https://iptv-org.example/channels.json").mock(
        return_value=httpx.Response(200, json=[{"id": "CNN.us", "languages": ["eng"], "country": "US", "name": "CNN"}])
    )
    ctx = AdapterContext(http_factory=lambda **kw: httpx.AsyncClient(), cache=MemoryCache(), data_dir=tmp_path)
    lc = LanguageChain(ctx, "https://iptv-org.example/channels.json")
    await lc.prepare()
    assert lc.infer(ch(tvg_id="cnn.us")).source == LanguageSource.DB
    lc2 = LanguageChain(ctx, "https://iptv-org.example/channels.json")
    await lc2.prepare()  # served from cache
    assert route.call_count == 1 and lc2.db_size == 1


async def test_iptv_org_failure_is_non_fatal(respx_mock: object, tmp_path: Path) -> None:
    import httpx

    from app.domain.ports import AdapterContext
    from app.infra.cache import MemoryCache

    respx_mock.get("https://iptv-org.example/channels.json").mock(return_value=httpx.Response(500))  # type: ignore[attr-defined]
    ctx = AdapterContext(http_factory=lambda **kw: httpx.AsyncClient(), cache=MemoryCache(), data_dir=tmp_path)
    lc = LanguageChain(ctx, "https://iptv-org.example/channels.json")
    await lc.prepare()
    assert lc.db_size == 0
    assert lc.infer(ch(group="EN| x")).code == "eng"
