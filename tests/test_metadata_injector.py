import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

from app.models.download_result import DownloadResult, TrackResult
from app.models.release import Release, TrackListing
from app.pipeline.metadata_injector import fetch_artwork, inject_metadata

_ARTWORK = b"\xff\xd8\xff\xe0\x00\x10JFIFfakejpegpayload"

_FFMPEG = shutil.which("ffmpeg")
_requires_ffmpeg = pytest.mark.skipif(_FFMPEG is None, reason="ffmpeg not available")


# --- helpers -----------------------------------------------------------------


def _fake_release() -> Release:
    return Release(
        discogs_id=1,
        artist="Pink Floyd",
        album_title="The Wall",
        year=1979,
        genres=["Rock"],
        artwork_url="https://img.discogs.com/cover.jpg",
        tracklist=[
            TrackListing(position="A1", title="In the Flesh?"),
            TrackListing(position="A2", title="The Thin Ice"),
            TrackListing(position="A3", title="Another Brick in the Wall"),
        ],
    )


def _make_empty_mp3(path: Path) -> Path:
    path.write_bytes(b"")
    return path


def _make_silent_m4a(path: Path) -> Path:
    subprocess.run(
        [
            _FFMPEG,
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "0.1",
            "-c:a",
            "aac",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _track(path: Path, number: int = 1, title: str = "In the Flesh?") -> TrackResult:
    return TrackResult(track_number=number, title=title, path=path, skipped=False)


def _result(tracks: list[TrackResult]) -> DownloadResult:
    return DownloadResult(
        url="https://youtube.com/watch?v=x",
        use_m4a=tracks[0].path.suffix.lower() == ".m4a",
        tracks=tracks,
        downloaded_count=len(tracks),
        skipped_count=0,
    )


# --- fetch_artwork -----------------------------------------------------------


def test_fetch_artwork_none_returns_none() -> None:
    assert fetch_artwork(None) is None


@patch("app.pipeline.metadata_injector.httpx.get")
def test_fetch_artwork_returns_bytes(mock_get: MagicMock) -> None:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.content = _ARTWORK
    mock_get.return_value = response

    assert fetch_artwork("https://img.discogs.com/cover.jpg") == _ARTWORK


@patch("app.pipeline.metadata_injector.httpx.get")
def test_fetch_artwork_http_error_returns_none(mock_get: MagicMock) -> None:
    mock_get.side_effect = httpx.HTTPError("boom")
    assert fetch_artwork("https://img.discogs.com/cover.jpg") is None


# --- mp3 field-by-field ------------------------------------------------------


def test_mp3_writes_title(tmp_path: Path) -> None:
    mp3 = _make_empty_mp3(tmp_path / "01 - track.mp3")
    inject_metadata(_result([_track(mp3)]), _fake_release(), _ARTWORK)
    tags = ID3(mp3)
    assert str(tags["TIT2"].text[0]) == "In the Flesh?"


def test_mp3_writes_artist(tmp_path: Path) -> None:
    mp3 = _make_empty_mp3(tmp_path / "01 - track.mp3")
    inject_metadata(_result([_track(mp3)]), _fake_release(), _ARTWORK)
    assert str(ID3(mp3)["TPE1"].text[0]) == "Pink Floyd"


def test_mp3_writes_album(tmp_path: Path) -> None:
    mp3 = _make_empty_mp3(tmp_path / "01 - track.mp3")
    inject_metadata(_result([_track(mp3)]), _fake_release(), _ARTWORK)
    assert str(ID3(mp3)["TALB"].text[0]) == "The Wall"


def test_mp3_writes_genre(tmp_path: Path) -> None:
    mp3 = _make_empty_mp3(tmp_path / "01 - track.mp3")
    inject_metadata(_result([_track(mp3)]), _fake_release(), _ARTWORK)
    assert str(ID3(mp3)["TCON"].text[0]) == "Rock"


def test_mp3_writes_year(tmp_path: Path) -> None:
    mp3 = _make_empty_mp3(tmp_path / "01 - track.mp3")
    inject_metadata(_result([_track(mp3)]), _fake_release(), _ARTWORK)
    # translate=False keeps the raw v2.3 TYER frame (mutagen otherwise
    # auto-upgrades it to the v2.4 TDRC form on read).
    tags = ID3(mp3, translate=False)
    assert str(tags["TYER"].text[0]) == "1979"


def test_mp3_writes_track_number_with_total(tmp_path: Path) -> None:
    mp3 = _make_empty_mp3(tmp_path / "01 - track.mp3")
    inject_metadata(_result([_track(mp3)]), _fake_release(), _ARTWORK)
    assert str(ID3(mp3)["TRCK"].text[0]) == "1/3"


def test_mp3_writes_disc_number(tmp_path: Path) -> None:
    mp3 = _make_empty_mp3(tmp_path / "01 - track.mp3")
    inject_metadata(_result([_track(mp3)]), _fake_release(), _ARTWORK)
    assert str(ID3(mp3)["TPOS"].text[0]) == "1/1"


def test_mp3_tag_version_is_v23(tmp_path: Path) -> None:
    mp3 = _make_empty_mp3(tmp_path / "01 - track.mp3")
    inject_metadata(_result([_track(mp3)]), _fake_release(), _ARTWORK)
    assert ID3(mp3).version == (2, 3, 0)


def test_mp3_embeds_artwork(tmp_path: Path) -> None:
    mp3 = _make_empty_mp3(tmp_path / "01 - track.mp3")
    inject_metadata(_result([_track(mp3)]), _fake_release(), _ARTWORK)
    apic = ID3(mp3).getall("APIC")
    assert len(apic) == 1
    assert apic[0].data == _ARTWORK
    assert apic[0].type == 3


def test_mp3_without_artwork_writes_no_apic(tmp_path: Path) -> None:
    mp3 = _make_empty_mp3(tmp_path / "01 - track.mp3")
    inject_metadata(_result([_track(mp3)]), _fake_release(), None)
    tags = ID3(mp3)
    assert tags.getall("APIC") == []
    assert str(tags["TIT2"].text[0]) == "In the Flesh?"


def test_mp3_track_numbers_follow_discogs_order(tmp_path: Path) -> None:
    tracks = [
        _track(_make_empty_mp3(tmp_path / f"{n:02d} - t.mp3"), number=n)
        for n in (1, 2, 3)
    ]
    inject_metadata(_result(tracks), _fake_release(), _ARTWORK)

    release = _fake_release()
    for index, track in enumerate(tracks):
        tags = ID3(track.path)
        assert str(tags["TRCK"].text[0]) == f"{index + 1}/3"
        assert str(tags["TIT2"].text[0]) == release.tracklist[index].title


# --- m4a (mp4 atoms) ---------------------------------------------------------


@_requires_ffmpeg
def test_m4a_writes_all_fields(tmp_path: Path) -> None:
    m4a = _make_silent_m4a(tmp_path / "01 - track.m4a")
    inject_metadata(_result([_track(m4a)]), _fake_release(), _ARTWORK)

    audio = MP4(m4a)
    assert audio["\xa9nam"] == ["In the Flesh?"]
    assert audio["\xa9ART"] == ["Pink Floyd"]
    assert audio["\xa9alb"] == ["The Wall"]
    assert audio["\xa9gen"] == ["Rock"]
    assert audio["\xa9day"] == ["1979"]
    assert audio["trkn"] == [(1, 3)]
    assert audio["disk"] == [(1, 1)]


@_requires_ffmpeg
def test_m4a_embeds_artwork(tmp_path: Path) -> None:
    m4a = _make_silent_m4a(tmp_path / "01 - track.m4a")
    inject_metadata(_result([_track(m4a)]), _fake_release(), _ARTWORK)
    covers = MP4(m4a)["covr"]
    assert len(covers) == 1
    assert bytes(covers[0]) == _ARTWORK


@_requires_ffmpeg
def test_m4a_without_artwork_writes_no_cover(tmp_path: Path) -> None:
    m4a = _make_silent_m4a(tmp_path / "01 - track.m4a")
    inject_metadata(_result([_track(m4a)]), _fake_release(), None)
    assert "covr" not in MP4(m4a)
