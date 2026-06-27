from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ChapterEntry(BaseModel):
    """A single resolved track boundary within a Compilation Source."""

    start_seconds: float
    track_name: str


class TimestampResolution(BaseModel):
    """Outcome of the multi-tiered timestamp resolution cascade.

    ``method`` records which level produced the result. ``"manual_required"``
    (with empty ``chapters`` and no ``chapter_map_path``) is the signal that all
    three levels failed and a ``chapter_map_prompt`` interrupt is needed — a
    future graph node translates it into a real LangGraph ``interrupt()``.

    ``chapter_map_path`` is set only for the ``"llm"`` and ``"discogs"`` levels,
    which write a plaintext Chapter Map for yt-dlp injection. Level 1
    (``"native"``) needs no file because yt-dlp splits embedded chapters itself.
    """

    method: Literal["native", "llm", "discogs", "manual_required"]
    chapters: list[ChapterEntry] = Field(default_factory=list)
    chapter_map_path: Path | None = None
