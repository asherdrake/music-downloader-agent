from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from app.clients.discogs import (
    _MAX_SEARCH_ATTEMPTS,
    DiscogsAPIError,
    DiscogsClient,
    _build_search_attempts,
    _parse_duration,
    _romanized_match_score,
    _select_best_result,
)
from app.models.download_intent import DownloadIntent
from app.models.release import Release

# ---------------------------------------------------------------------------
# Fake API response data
# ---------------------------------------------------------------------------

_FAKE_SEARCH_RESULTS: dict[str, Any] = {
    "results": [
        {"id": 101, "year": 1979, "title": "Pink Floyd - The Wall"},
        {"id": 202, "year": 1990, "title": "Pink Floyd - The Wall (Remaster)"},
    ]
}

_FAKE_RELEASE_DETAIL: dict[str, Any] = {
    "id": 101,
    "title": "The Wall",
    "year": 1979,
    "artists": [{"name": "Pink Floyd"}],
    "genres": ["Rock"],
    "images": [
        {"type": "primary", "uri": "https://img.discogs.com/cover.jpg"},
    ],
    "tracklist": [
        {
            "type_": "track",
            "position": "A1",
            "title": "In The Flesh?",
            "duration": "3:16",
        },
        {"type_": "heading", "position": "", "title": "Side B", "duration": ""},
        {
            "type_": "track",
            "position": "B1",
            "title": "Comfortably Numb",
            "duration": "6:22",
        },
        {
            "type_": "track",
            "position": "B2",
            "title": "The Show Must Go On",
            "duration": "1:36",
        },
    ],
}

_FAKE_RELEASE_NO_IMAGES: dict[str, Any] = {
    **_FAKE_RELEASE_DETAIL,
    "images": [],
}

_FAKE_RELEASE_NO_PRIMARY_IMAGE: dict[str, Any] = {
    **_FAKE_RELEASE_DETAIL,
    "images": [
        {"type": "secondary", "uri": "https://img.discogs.com/back.jpg"},
    ],
}

_FAKE_SEARCH_EMPTY: dict[str, Any] = {"results": []}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_response(json_data: dict[str, Any]) -> MagicMock:
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = json_data
    return mock_response


def _make_client() -> DiscogsClient:
    return DiscogsClient(api_token="test-token")


# ---------------------------------------------------------------------------
# Release selection
# ---------------------------------------------------------------------------


def test_fetch_release_selects_earliest_year() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_DETAIL),
    ]

    client.fetch_release(artist="Pink Floyd", title="The Wall")

    second_call_args = client._http_client.get.call_args_list[1]
    assert "/releases/101" in second_call_args[0][0]


# ---------------------------------------------------------------------------
# Edition hint
# ---------------------------------------------------------------------------


def test_fetch_release_includes_edition_in_search_params() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_DETAIL),
    ]

    client.fetch_release(artist="Pink Floyd", title="The Wall", edition="2011 remaster")

    search_call_kwargs = client._http_client.get.call_args_list[0][1]
    assert search_call_kwargs["params"]["q"] == "2011 remaster"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_fetch_release_raises_on_empty_results() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.return_value = _make_mock_response(_FAKE_SEARCH_EMPTY)

    with pytest.raises(DiscogsAPIError) as exc_info:
        client.fetch_release(artist="Unknown Artist", title="Unknown Title")

    assert exc_info.value.status_code == 404


def test_fetch_release_raises_on_http_error() -> None:
    client = _make_client()
    mock_response = MagicMock()
    mock_response.status_code = 503
    http_error = httpx.HTTPStatusError(
        "Service Unavailable",
        request=MagicMock(),
        response=mock_response,
    )
    mock_get_response = MagicMock()
    mock_get_response.raise_for_status.side_effect = http_error
    client._http_client = MagicMock()
    client._http_client.get.return_value = mock_get_response

    with pytest.raises(DiscogsAPIError) as exc_info:
        client.fetch_release(artist="Pink Floyd", title="The Wall")

    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------


def test_fetch_release_maps_all_fields_correctly() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_DETAIL),
    ]

    release = client.fetch_release(artist="Pink Floyd", title="The Wall")

    assert isinstance(release, Release)
    assert release.artist == "Pink Floyd"
    assert release.album_title == "The Wall"
    assert release.year == 1979
    assert release.genres == ["Rock"]
    assert release.artwork_url == "https://img.discogs.com/cover.jpg"
    assert len(release.tracklist) == 3
    assert release.tracklist[0].title == "In The Flesh?"
    assert release.tracklist[0].duration_seconds == 196.0
    assert release.tracklist[1].title == "Comfortably Numb"
    assert release.tracklist[1].duration_seconds == 382.0


def test_map_release_skips_heading_entries() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_DETAIL),
    ]

    release = client.fetch_release(artist="Pink Floyd", title="The Wall")

    heading_entries = [t for t in release.tracklist if t.title == "Side B"]
    assert heading_entries == []


# ---------------------------------------------------------------------------
# Artwork fallback
# ---------------------------------------------------------------------------


def test_fetch_release_artwork_fallback_to_first_image() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_NO_PRIMARY_IMAGE),
    ]

    release = client.fetch_release(artist="Pink Floyd", title="The Wall")

    assert release.artwork_url == "https://img.discogs.com/back.jpg"


def test_fetch_release_artwork_none_when_no_images() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_RESULTS),
        _make_mock_response(_FAKE_RELEASE_NO_IMAGES),
    ]

    release = client.fetch_release(artist="Pink Floyd", title="The Wall")

    assert release.artwork_url is None


# ---------------------------------------------------------------------------
# _parse_duration unit tests
# ---------------------------------------------------------------------------


def test_parse_duration_standard_mm_ss() -> None:
    assert _parse_duration("3:45") == 225.0


def test_parse_duration_hh_mm_ss() -> None:
    assert _parse_duration("1:02:30") == 3750.0


def test_parse_duration_empty_returns_none() -> None:
    assert _parse_duration("") is None


def test_parse_duration_whitespace_returns_none() -> None:
    assert _parse_duration("   ") is None


def test_parse_duration_malformed_returns_none() -> None:
    assert _parse_duration("-") is None
    assert _parse_duration("?") is None


# ---------------------------------------------------------------------------
# DownloadIntent edition field
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Native-script variant search
# ---------------------------------------------------------------------------

_FAKE_NATIVE_RESULTS: dict[str, Any] = {
    "results": [{"id": 555, "year": 2008, "title": "ゆう - てんのみかく"}]
}

# Earliest-year result is a bad match; the later-year result romanizes to the
# romaji request, so fuzzy ranking must beat pure earliest-year selection.
_FAKE_JAPANESE_MIXED_RESULTS: dict[str, Any] = {
    "results": [
        {"id": 1, "year": 1995, "title": "Shoko Asahara - Greatest Hits"},
        {"id": 2, "year": 2008, "title": "ゆう - 天の味覚"},
    ]
}


def test_fetch_release_falls_back_to_native_variant_search() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_SEARCH_EMPTY),  # romaji attempt: no hits
        _make_mock_response(_FAKE_NATIVE_RESULTS),  # native attempt: hit
        _make_mock_response(_FAKE_RELEASE_DETAIL),  # release detail
    ]

    client.fetch_release(
        artist="Yuu",
        title="Ten no Mikaku",
        artist_native_variants=["ゆう"],
        title_native_variants=["てんのみかく"],
    )

    second_search_params = client._http_client.get.call_args_list[1][1]["params"]
    assert second_search_params["release_title"] == "てんのみかく"
    assert second_search_params["artist"] == "ゆう"
    detail_call_args = client._http_client.get.call_args_list[2]
    assert "/releases/555" in detail_call_args[0][0]


def test_fetch_release_ranks_by_romanized_match_over_earliest_year() -> None:
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.side_effect = [
        _make_mock_response(_FAKE_JAPANESE_MIXED_RESULTS),
        _make_mock_response(_FAKE_RELEASE_DETAIL),
    ]

    client.fetch_release(artist="Yuu", title="Ten no Mikaku")

    detail_call_args = client._http_client.get.call_args_list[1]
    assert "/releases/2" in detail_call_args[0][0]


def test_build_search_attempts_romaji_first_and_dedupes() -> None:
    attempts = _build_search_attempts(
        artist="Yuu",
        title="Ten no Mikaku",
        edition=None,
        artist_native_variants=["ゆう"],
        title_native_variants=["てんのみかく"],
    )

    assert attempts[0] == {
        "artist": "Yuu",
        "release_title": "Ten no Mikaku",
        "type": "release",
    }
    assert {
        "artist": "ゆう",
        "release_title": "てんのみかく",
        "type": "release",
    } in attempts
    assert {"q": "てんのみかく", "type": "release"} in attempts
    # No duplicates.
    assert len(attempts) == len({tuple(sorted(a.items())) for a in attempts})


def test_build_search_attempts_caps_total_attempts() -> None:
    attempts = _build_search_attempts(
        artist="A",
        title="B",
        edition=None,
        artist_native_variants=[f"ア{i}" for i in range(20)],
        title_native_variants=[f"タ{i}" for i in range(20)],
    )
    assert len(attempts) <= _MAX_SEARCH_ATTEMPTS


def test_build_search_attempts_keeps_edition_on_first_attempt() -> None:
    attempts = _build_search_attempts(
        artist="Pink Floyd",
        title="The Wall",
        edition="2011 remaster",
        artist_native_variants=[],
        title_native_variants=[],
    )
    assert attempts == [
        {
            "artist": "Pink Floyd",
            "release_title": "The Wall",
            "type": "release",
            "q": "2011 remaster",
        }
    ]


def test_select_best_result_prefers_romanized_match() -> None:
    best = _select_best_result(
        _FAKE_JAPANESE_MIXED_RESULTS["results"], "yuu ten no mikaku"
    )
    assert best["id"] == 2


def test_select_best_result_breaks_ties_by_earliest_year() -> None:
    results = [
        {"id": 10, "year": 1990, "title": "Pink Floyd - The Wall"},
        {"id": 11, "year": 1979, "title": "Pink Floyd - The Wall"},
    ]
    best = _select_best_result(results, "pink floyd the wall")
    assert best["id"] == 11


def test_romanized_match_score_rescues_hiragana() -> None:
    # Pure hiragana romanizes without word boundaries; compact comparison must
    # still recognise it as the same work.
    assert _romanized_match_score("ゆう - てんのみかく", "Yuu Ten no Mikaku") >= 70.0


def test_romanized_match_score_low_for_unrelated_release() -> None:
    assert _romanized_match_score("レムの撚糸", "Yuu Ten no Mikaku") < 70.0


def test_fetch_release_rejects_below_threshold_match() -> None:
    # A search that only returns unrelated rows must 404 (→ manual review),
    # never silently tag the album with a wrong release.
    junk_results = {"results": [{"id": 99, "year": 2026, "title": "レムの撚糸"}]}
    client = _make_client()
    client._http_client = MagicMock()
    client._http_client.get.return_value = _make_mock_response(junk_results)

    with pytest.raises(DiscogsAPIError) as exc_info:
        client.fetch_release(artist="Yuu", title="Ten no Mikaku")

    assert exc_info.value.status_code == 404


def test_download_intent_accepts_edition_field() -> None:
    intent = DownloadIntent(
        target_type="Album",
        artist="Pink Floyd",
        title="The Wall",
        edition="deluxe edition",
    )
    dumped = intent.model_dump()
    assert dumped["edition"] == "deluxe edition"


def test_download_intent_edition_defaults_to_none() -> None:
    intent = DownloadIntent(
        target_type="Track",
        artist="Radiohead",
        title="Creep",
    )
    assert intent.edition is None


def test_download_intent_coerces_bare_string_native_variant() -> None:
    # LLM structured output sometimes returns a string instead of a list.
    intent = DownloadIntent(
        target_type="Album",
        artist="Yuu",
        title="Ten no Mikaku",
        title_native_variants="天の味覚",
        artist_native_variants=None,
    )
    assert intent.title_native_variants == ["天の味覚"]
    assert intent.artist_native_variants == []
