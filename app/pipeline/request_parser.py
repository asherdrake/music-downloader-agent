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
- playlist_actions: Spotify playlist names the user wants the download added to (empty list if none mentioned)\
"""


def parse_request(music_request: str, model: BaseChatModel) -> DownloadIntent:
    structured_model = model.with_structured_output(DownloadIntent)
    return structured_model.invoke(
        [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=music_request),
        ]
    )
