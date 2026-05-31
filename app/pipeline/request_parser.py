from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.models.download_intent import DownloadIntent

_SYSTEM_PROMPT = """\
You are a music download intent parser. Given a free-form user request, extract structured information.

Rules:
- target_type: "Track" for a single song; "Album" for a full album, EP, or LP
- artist: the performing artist name
- title: the track or album title
- resource_hint: any URL in the request (YouTube, SoundCloud, Bandcamp, etc.), or null if none present
- edition: a specific release edition mentioned by the user (e.g. "2011 remaster", "deluxe edition"); null if none mentioned
- playlist_actions: list of Spotify playlist actions the user wants applied (empty list if none mentioned).
  Each action has:
  - playlist_name: name of the target playlist
  - create_if_missing: true if the user wants the playlist created if it does not exist (default true)
  - set_cover_image: "album_art" if the user wants the playlist cover set to the album artwork; null otherwise
  - track_order: "album_order" if the user wants tracks added in album order (default); "reverse" if reverse order\
"""


def parse_request(music_request: str, model: BaseChatModel) -> DownloadIntent:
    structured_model = model.with_structured_output(DownloadIntent)
    return structured_model.invoke(
        [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=music_request),
        ]
    )
