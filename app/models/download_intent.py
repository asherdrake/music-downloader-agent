from typing import Literal

from pydantic import BaseModel, Field


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
    playlist_actions: list[PlaylistAction] = Field(default_factory=list)
