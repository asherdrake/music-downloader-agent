from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.clients.discogs import get_discogs_client
from app.core.config import Settings
from app.core.native_script import gather_native_variants
from app.models.pipeline_state import PipelineState
from app.pipeline.candidate_evaluator import evaluate_candidates
from app.pipeline.candidate_search import search_candidates
from app.pipeline.downloader import run_download
from app.pipeline.metadata_injector import fetch_artwork, inject_metadata
from app.pipeline.request_parser import parse_request

_MAX_SEARCH_ITERATIONS: int = 3


def _route_after_evaluate(state: PipelineState) -> str:
    if state.get("scored_candidates"):
        return "candidate_review"
    if state.get("search_iteration", 0) >= _MAX_SEARCH_ITERATIONS:
        return END
    return "search_candidates"


def create_graph(
    llm: BaseChatModel,
    settings: Settings,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
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
        print(scored)
        return {"scored_candidates": scored}

    def candidate_review_node(state: PipelineState) -> dict:
        selected_url: str = interrupt(
            {"candidates": state.get("scored_candidates", [])}
        )
        return {"selected_candidate_url": selected_url}

    def download_node(state: PipelineState) -> dict:
        result = run_download(
            url=state["selected_candidate_url"],
            intent=state["download_intent"],
            local_files_directory=settings.local_files_directory,
            use_m4a=state.get("use_m4a", False),
        )
        return {"download_result": result}

    def inject_metadata_node(state: PipelineState) -> dict:
        intent = state["download_intent"]
        candidates = state.get("candidates") or state.get("scored_candidates") or []
        candidate_titles = [candidate.title for candidate in candidates]
        release = discogs_client.fetch_release(
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
        artwork_bytes = fetch_artwork(release.artwork_url)
        result = inject_metadata(state["download_result"], release, artwork_bytes)
        return {"download_result": result, "release": release}

    discogs_client = get_discogs_client()

    builder: StateGraph = StateGraph(PipelineState)
    builder.add_node("parse_request", parse_request_node)
    builder.add_node("search_candidates", search_candidates_node)
    builder.add_node("evaluate_candidates", evaluate_candidates_node)
    builder.add_node("candidate_review", candidate_review_node)
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
    builder.add_edge("candidate_review", "download")
    builder.add_edge("download", "inject_metadata")
    builder.add_edge("inject_metadata", END)

    return builder.compile(checkpointer=checkpointer)
