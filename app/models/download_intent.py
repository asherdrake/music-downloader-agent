from typing import Literal

from pydantic import BaseModel, Field, field_validator


class PlaylistAction(BaseModel):
    playlist_name: str
    create_if_missing: bool = True
    set_cover_image: Literal["album_art"] | None = None
    track_order: Literal["album_order", "reverse"] = "album_order"


class DownloadIntent(BaseModel):
    target_type: Literal["Track", "Album"]
    artist: str
    title: str
    resource_hint: str | None = None
    edition: str | None = None
    artist_native_variants: list[str] = Field(default_factory=list)
    title_native_variants: list[str] = Field(default_factory=list)
    playlist_actions: list[PlaylistAction] = Field(default_factory=list)

    @field_validator("artist_native_variants", "title_native_variants", mode="before")
    @classmethod
    def _coerce_variants_to_list(cls, value: object) -> list[str]:
        # LLM structured output sometimes returns a bare string (or null) for
        # these fields instead of a list; normalise so parsing never fails.
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        return list(value) if isinstance(value, (list, tuple)) else value
