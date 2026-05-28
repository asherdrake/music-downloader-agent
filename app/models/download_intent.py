from typing import Literal

from pydantic import BaseModel, Field


class DownloadIntent(BaseModel):
    target_type: Literal["Track", "Album"]
    artist: str
    title: str
    resource_hint: str | None = None
    playlist_actions: list[str] = Field(default_factory=list)
