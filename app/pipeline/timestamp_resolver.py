from pathlib import Path
from typing import Any

import yt_dlp
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.models.chapter_map import ChapterEntry, TimestampResolution
from app.models.release import Release

# Top N comments handed to the LLM during Level 2 extraction.
_MAX_COMMENTS: int = 50

_INFO_OPTS: dict[str, Any] = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "getcomments": True,
    "noplaylist": True,
}

_LLM_SYSTEM_PROMPT = """\
You extract album track boundaries from the page text of a single long video \
that contains a full album back-to-back (a "compilation").

Given the video description and top comments, find the tracklist with \
timestamps. Each entry has a start time written as MM:SS or HH:MM:SS and a \
track name. Return them in playing order.

Rules:
- start_time: copy the timestamp verbatim in MM:SS or HH:MM:SS form.
- track_name: the track title only — strip leading track numbers, the timestamp \
itself, and surrounding punctuation/dashes.
- If no tracklist with timestamps is present, return an empty list. Do not \
invent timestamps.\
"""


class _LLMRawChapter(BaseModel):
    start_time: str
    track_name: str


class _LLMChapterMap(BaseModel):
    chapters: list[_LLMRawChapter] = Field(default_factory=list)


def _parse_timestamp(text: str) -> float | None:
    """Convert an ``MM:SS`` or ``HH:MM:SS`` timestamp to seconds.

    Returns ``None`` for anything that does not parse cleanly so callers can
    drop junk entries rather than fabricating a boundary.
    """
    stripped = text.strip()
    if not stripped:
        return None
    parts = stripped.split(":")
    try:
        if len(parts) == 2:
            return float(int(parts[0]) * 60 + int(parts[1]))
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]))
    except ValueError:
        return None
    return None


def _format_timestamp(total_seconds: float) -> str:
    """Render seconds as a zero-padded ``HH:MM:SS`` string."""
    whole_seconds = int(total_seconds)
    hours = whole_seconds // 3600
    minutes = (whole_seconds % 3600) // 60
    seconds = whole_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _fetch_info_json(url: str) -> dict[str, Any]:
    """Single yt-dlp metadata dump feeding Levels 1 and 2 (no download)."""
    with yt_dlp.YoutubeDL(_INFO_OPTS) as ydl:
        return ydl.extract_info(url, download=False) or {}


def _native_chapters(info_json: dict[str, Any]) -> list[ChapterEntry]:
    """Level 1: read the deterministic native ``chapters`` array, if present."""
    native = info_json.get("chapters") or []
    chapters: list[ChapterEntry] = []
    for chapter in native:
        start_time = chapter.get("start_time")
        title = chapter.get("title")
        if start_time is None or not title:
            continue
        chapters.append(
            ChapterEntry(start_seconds=float(start_time), track_name=str(title))
        )
    return chapters


def _llm_chapters(
    description: str,
    comments: list[str],
    llm: BaseChatModel,
) -> list[ChapterEntry]:
    """Level 2: ask the LLM to extract timestamps from page text.

    The LLM returns raw timestamp strings; conversion to seconds is done here
    deterministically so the model never has to perform arithmetic.
    """
    page_text = description
    if comments:
        joined_comments = "\n".join(comments)
        page_text = f"{description}\n\n--- COMMENTS ---\n{joined_comments}"

    structured_model = llm.with_structured_output(_LLMChapterMap)
    raw_map: _LLMChapterMap = structured_model.invoke(
        [
            SystemMessage(content=_LLM_SYSTEM_PROMPT),
            HumanMessage(content=page_text),
        ]
    )

    chapters: list[ChapterEntry] = []
    for raw_chapter in raw_map.chapters:
        start_seconds = _parse_timestamp(raw_chapter.start_time)
        if start_seconds is None or not raw_chapter.track_name.strip():
            continue
        chapters.append(
            ChapterEntry(
                start_seconds=start_seconds,
                track_name=raw_chapter.track_name.strip(),
            )
        )
    return chapters


def _discogs_chapters(release: Release) -> list[ChapterEntry]:
    """Level 3: derive boundaries from cumulative Discogs track durations.

    Requires a duration on every track — a single missing value makes the
    cumulative arithmetic unsound, so the whole level is treated as a miss.
    """
    tracklist = release.tracklist
    if not tracklist:
        return []
    if any(track.duration_seconds is None for track in tracklist):
        return []

    chapters: list[ChapterEntry] = []
    cumulative_seconds = 0.0
    for track in tracklist:
        chapters.append(
            ChapterEntry(start_seconds=cumulative_seconds, track_name=track.title)
        )
        cumulative_seconds += float(track.duration_seconds)  # type: ignore[arg-type]
    return chapters


def _write_chapter_map(
    chapters: list[ChapterEntry],
    output_dir: Path,
    map_filename: str,
) -> Path:
    """Write the Chapter Map as ``HH:MM:SS Track Name`` lines, one per track."""
    output_dir.mkdir(parents=True, exist_ok=True)
    chapter_map_path = output_dir / map_filename
    lines = [
        f"{_format_timestamp(chapter.start_seconds)} {chapter.track_name}"
        for chapter in chapters
    ]
    chapter_map_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return chapter_map_path


def resolve_timestamps(
    url: str,
    release: Release | None,
    llm: BaseChatModel,
    output_dir: Path,
    map_filename: str = "chapters.txt",
) -> TimestampResolution:
    """Resolve Compilation Source track boundaries via the 3-level cascade.

    Level 1 (native chapters) short-circuits without writing a file — yt-dlp
    splits embedded chapters itself. Levels 2 (LLM) and 3 (Discogs) write a
    plaintext Chapter Map and return its path. If all levels find nothing the
    result carries ``method="manual_required"`` (the ``chapter_map_prompt``
    interrupt signal) and nothing is downloaded.
    """
    info_json = _fetch_info_json(url)

    # try chapters from yt first
    native = _native_chapters(info_json)
    if native:
        return TimestampResolution(method="native", chapters=native)

    # if no chapters, check desc or comments
    description = info_json.get("description") or ""
    comments = [
        comment["text"]
        for comment in (info_json.get("comments") or [])
        if comment.get("text")
    ][:_MAX_COMMENTS]

    llm_chapters = _llm_chapters(description, comments, llm)
    if llm_chapters:
        chapter_map_path = _write_chapter_map(llm_chapters, output_dir, map_filename)
        return TimestampResolution(
            method="llm", chapters=llm_chapters, chapter_map_path=chapter_map_path
        )

    # if llm_chapters doesn't work, try Discogs
    if release is not None:
        discogs_chapters = _discogs_chapters(release)
        if discogs_chapters:
            chapter_map_path = _write_chapter_map(
                discogs_chapters, output_dir, map_filename
            )
            return TimestampResolution(
                method="discogs",
                chapters=discogs_chapters,
                chapter_map_path=chapter_map_path,
            )

    return TimestampResolution(method="manual_required")
