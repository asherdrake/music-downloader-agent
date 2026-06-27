from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.clients.discogs import DiscogsClient, get_discogs_client
from app.core.config import Settings
from app.core.native_script import gather_native_variants
from app.models.pipeline_state import PipelineState
from app.models.release import Release
from app.pipeline.candidate_evaluator import evaluate_candidates
from app.pipeline.candidate_search import search_candidates
from app.pipeline.downloader import (
    _album_dir,
    download_compilation,
    download_playlist,
    download_track,
)
from app.pipeline.metadata_injector import fetch_artwork, inject_metadata
from app.pipeline.request_parser import parse_request
from app.pipeline.timestamp_resolver import read_chapter_map, resolve_timestamps

_MAX_SEARCH_ITERATIONS: int = 3


def _route_after_evaluate(state: PipelineState) -> str:
    if state.get("scored_candidates"):
        return "candidate_review"
    if state.get("search_iteration", 0) >= _MAX_SEARCH_ITERATIONS:
        return END
    return "search_candidates"


def _selected_is_playlist(state: PipelineState) -> bool:
    """Whether the candidate the user picked is a YouTube playlist.

    A Playlist Source Album resolves to many entries up front; a Compilation
    Source is a single back-to-back video that must be split by timestamps.
    """
    selected_url = state.get("selected_candidate_url")
    for candidate in state.get("scored_candidates", []):
        if candidate.url == selected_url:
            return candidate.is_playlist
    return False


def _route_after_review(state: PipelineState) -> str:
    """Track and Playlist-Source Albums download directly; Compilation Sources
    (single-video Albums) need timestamp resolution first."""
    intent = state["download_intent"]
    if intent.target_type == "Track" or _selected_is_playlist(state):
        return "download"
    return "resolve_timestamps"


def _route_after_resolve(state: PipelineState) -> str:
    if state.get("timestamp_method") == "manual_required":
        return "chapter_map_prompt"
    return "download"


def create_graph(
    llm: BaseChatModel,
    settings: Settings,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    def _fetch_release(state: PipelineState) -> Release:
        intent = state["download_intent"]
        candidates = state.get("candidates") or state.get("scored_candidates") or []
        candidate_titles = [candidate.title for candidate in candidates]
        return discogs_client.fetch_release(
            artist=intent.artist,
            title=intent.title,
            edition=intent.edition,
            artist_native_variants=gather_native_variants(
                intent.artist, candidate_titles, intent.artist_native_variants
            ),
            title_native_variants=gather_native_variants(
                intent.title, candidate_titles, intent.title_native_variants
            ),
        )

    def parse_request_node(state: PipelineState) -> dict:
        download_intent = parse_request(state["request"], llm)
        return {"download_intent": download_intent}

    def search_candidates_node(state: PipelineState) -> dict:
        candidates = search_candidates(state["download_intent"])
        return {
            "candidates": candidates,
            "search_iteration": state.get("search_iteration", 0) + 1,
        }

    def evaluate_candidates_node(state: PipelineState) -> dict:
        scored = evaluate_candidates(
            state.get("candidates", []),
            state["download_intent"],
        )
        return {"scored_candidates": scored}

    def candidate_review_node(state: PipelineState) -> dict:
        selected_url: str = interrupt(
            {"candidates": state.get("scored_candidates", [])}
        )
        return {"selected_candidate_url": selected_url}

    def resolve_timestamps_node(state: PipelineState) -> dict:
        intent = state["download_intent"]
        release = _fetch_release(state)
        output_dir = _album_dir(
            settings.local_files_directory, intent.artist, intent.title
        )
        resolution = resolve_timestamps(
            url=state["selected_candidate_url"],
            release=release,
            llm=llm,
            output_dir=output_dir,
        )
        return {
            "release": release,
            "chapters": resolution.chapters,
            "timestamp_method": resolution.method,
            "chapter_map_path": resolution.chapter_map_path,
        }

    def chapter_map_prompt_node(state: PipelineState) -> dict:
        release = state.get("release")
        tracklist = [track.title for track in release.tracklist] if release else []
        chapter_map_file_path: str = interrupt(
            {
                "download_intent": state["download_intent"],
                "tracklist": tracklist,
            }
        )
        chapters = read_chapter_map(Path(chapter_map_file_path))
        return {"chapters": chapters, "timestamp_method": "manual"}

    def download_node(state: PipelineState) -> dict:
        intent = state["download_intent"]
        url = state["selected_candidate_url"]
        use_m4a = state.get("use_m4a", False)
        if intent.target_type == "Track":
            result = download_track(
                url, intent, settings.local_files_directory, use_m4a
            )
        elif _selected_is_playlist(state):
            result = download_playlist(
                url, intent, settings.local_files_directory, use_m4a
            )
        else:
            result = download_compilation(
                url,
                intent,
                state.get("chapters", []),
                settings.local_files_directory,
                use_m4a,
            )
        return {"download_result": result}

    def inject_metadata_node(state: PipelineState) -> dict:
        release = state.get("release") or _fetch_release(state)
        artwork_bytes = fetch_artwork(release.artwork_url)
        result = inject_metadata(state["download_result"], release, artwork_bytes)
        return {"download_result": result, "release": release}

    discogs_client: DiscogsClient = get_discogs_client()

    builder: StateGraph = StateGraph(PipelineState)
    builder.add_node("parse_request", parse_request_node)
    builder.add_node("search_candidates", search_candidates_node)
    builder.add_node("evaluate_candidates", evaluate_candidates_node)
    builder.add_node("candidate_review", candidate_review_node)
    builder.add_node("resolve_timestamps", resolve_timestamps_node)
    builder.add_node("chapter_map_prompt", chapter_map_prompt_node)
    builder.add_node("download", download_node)
    builder.add_node("inject_metadata", inject_metadata_node)

    builder.set_entry_point("parse_request")
    builder.add_edge("parse_request", "search_candidates")
    builder.add_edge("search_candidates", "evaluate_candidates")
    builder.add_conditional_edges(
        "evaluate_candidates",
        _route_after_evaluate,
        {
            "candidate_review": "candidate_review",
            "search_candidates": "search_candidates",
            END: END,
        },
    )
    builder.add_conditional_edges(
        "candidate_review",
        _route_after_review,
        {
            "download": "download",
            "resolve_timestamps": "resolve_timestamps",
        },
    )
    builder.add_conditional_edges(
        "resolve_timestamps",
        _route_after_resolve,
        {
            "chapter_map_prompt": "chapter_map_prompt",
            "download": "download",
        },
    )
    builder.add_edge("chapter_map_prompt", "download")
    builder.add_edge("download", "inject_metadata")
    builder.add_edge("inject_metadata", END)

    return builder.compile(checkpointer=checkpointer)
