from app.pipeline.graph import create_graph
from app.core.config import get_settings
from app.core.llm import get_llm

from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.sqlite import SqliteSaver

_INITIAL_STATE = {
    "request": "Download the album 3 by tricot, create a playlist for it, set the image to the album art, and add the correct tracks in the album order.",
    "download_intent": None,
    "candidates": [],
    "scored_candidates": [],
    "search_iteration": 0,
    "selected_candidate_url": None,
}

with SqliteSaver.from_conn_string(":memory:") as checkpointer:
    graph: CompiledStateGraph = create_graph(get_llm(), get_settings(), checkpointer)
    result = graph.invoke(_INITIAL_STATE)
    print(result)
