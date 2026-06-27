from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Iterator, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel
from starlette.concurrency import iterate_in_threadpool

from app.core.config import Settings, get_settings
from app.core.llm import get_llm
from app.models.download_result import DownloadResult
from app.models.release import Release
from app.models.scored_candidate import ScoredCandidate
from app.pipeline.event_stream import PipelineEvent, stream_pipeline_events
from app.pipeline.graph import create_graph

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.checkpoint_db_path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(settings.checkpoint_db_path)) as checkpointer:
        app.state.checkpointer = checkpointer
        yield


app = FastAPI(
    title="Music Downloader Agent",
    version="0.1.0",
    lifespan=lifespan,
)


class DownloadRequest(BaseModel):
    request: str
    use_m4a: bool = False


class CandidateReviewResponse(BaseModel):
    status: Literal["candidate_review"]
    thread_id: str
    candidates: list[ScoredCandidate]


class SearchExhaustedResponse(BaseModel):
    status: Literal["search_exhausted"]
    thread_id: str


class ResumeRequest(BaseModel):
    thread_id: str
    selected_candidate_url: str


class DownloadCompleteResponse(BaseModel):
    status: Literal["completed"]
    thread_id: str
    selected_candidate_url: str
    download_result: DownloadResult
    release: Release | None = None


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/download")
def download(
    body: DownloadRequest,
    request: Request,
    model: Annotated[BaseChatModel, Depends(get_llm)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CandidateReviewResponse | SearchExhaustedResponse:
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    graph = create_graph(model, settings, request.app.state.checkpointer)
    graph.invoke(
        {
            "request": body.request,
            "download_intent": None,
            "candidates": [],
            "scored_candidates": [],
            "search_iteration": 0,
            "selected_candidate_url": None,
            "use_m4a": body.use_m4a,
            "download_result": None,
        },
        config,
    )
    snapshot = graph.get_state(config)
    if snapshot.next:
        interrupt_value = snapshot.tasks[0].interrupts[0].value
        return CandidateReviewResponse(
            status="candidate_review",
            thread_id=thread_id,
            candidates=interrupt_value["candidates"],
        )
    return SearchExhaustedResponse(status="search_exhausted", thread_id=thread_id)


@app.post("/resume")
def resume(
    body: ResumeRequest,
    request: Request,
    model: Annotated[BaseChatModel, Depends(get_llm)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DownloadCompleteResponse | CandidateReviewResponse | SearchExhaustedResponse:
    config = {"configurable": {"thread_id": body.thread_id}}
    graph = create_graph(model, settings, request.app.state.checkpointer)
    snapshot = graph.get_state(config)
    if not snapshot.next:
        raise HTTPException(
            status_code=404, detail="Thread not found or already completed"
        )
    graph.invoke(Command(resume=body.selected_candidate_url), config)
    snapshot = graph.get_state(config)
    if snapshot.next:
        interrupt_value = snapshot.tasks[0].interrupts[0].value
        return CandidateReviewResponse(
            status="candidate_review",
            thread_id=body.thread_id,
            candidates=interrupt_value["candidates"],
        )
    final_state = snapshot.values
    return DownloadCompleteResponse(
        status="completed",
        thread_id=body.thread_id,
        selected_candidate_url=final_state.get("selected_candidate_url", ""),
        download_result=final_state["download_result"],
        release=final_state.get("release"),
    )


# --- SSE streaming endpoints (thin shell over the Module A event-stream layer) ---

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _sse_frame(event: PipelineEvent, thread_id: str) -> str:
    """Serialise one PipelineEvent into a Server-Sent Events frame.

    The terminal events (interrupt/done) carry the thread_id so the browser can
    POST the candidate selection and resume the run.
    """
    if event.type in ("interrupt", "done"):
        event = event.model_copy(
            update={"payload": {**event.payload, "thread_id": thread_id}}
        )
    return f"data: {event.model_dump_json()}\n\n"


def _sse_response(events: Iterator[PipelineEvent], thread_id: str) -> StreamingResponse:
    """Bridge the blocking event generator onto the event loop as an SSE body."""

    def frames() -> Iterator[str]:
        for event in events:
            yield _sse_frame(event, thread_id)

    return StreamingResponse(
        iterate_in_threadpool(frames()),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@app.post("/download/stream")
def download_stream(
    body: DownloadRequest,
    request: Request,
    model: Annotated[BaseChatModel, Depends(get_llm)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    thread_id = str(uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    graph = create_graph(model, settings, request.app.state.checkpointer)
    initial_state = {
        "request": body.request,
        "download_intent": None,
        "candidates": [],
        "scored_candidates": [],
        "search_iteration": 0,
        "selected_candidate_url": None,
        "use_m4a": body.use_m4a,
        "download_result": None,
    }
    events = stream_pipeline_events(graph, initial_state, config)
    return _sse_response(events, thread_id)


@app.post("/resume/stream")
def resume_stream(
    body: ResumeRequest,
    request: Request,
    model: Annotated[BaseChatModel, Depends(get_llm)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    config = {"configurable": {"thread_id": body.thread_id}}
    graph = create_graph(model, settings, request.app.state.checkpointer)
    snapshot = graph.get_state(config)
    if not snapshot.next:
        raise HTTPException(
            status_code=404, detail="Thread not found or already completed"
        )
    events = stream_pipeline_events(
        graph, Command(resume=body.selected_candidate_url), config
    )
    return _sse_response(events, body.thread_id)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
