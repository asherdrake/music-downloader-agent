"""Module A: the Pipeline event-stream layer.

The reusable backbone that wraps a LangGraph start/resume run and yields an
ordered sequence of typed events. It is transport-agnostic: it knows nothing
about HTTP or SSE. The SSE endpoints in ``main.py`` are a thin shell over it.

A run produces a stream of node-level ``status`` events and terminates with
exactly one of:
  - an ``interrupt`` event (the run suspended at a human-in-the-loop node), or
  - a ``done`` event (the run reached END).

Interrupts are delivered as a *generic typed envelope* — ``{type, payload}`` —
so future interrupt types (e.g. ``chapter_map_prompt``) need no transport
changes. The ``interrupt_type`` is derived from the suspended node's name, which
keeps this layer decoupled from the graph internals.

``progress`` and ``error`` events arrive in later slices.
"""

from collections.abc import Iterator
from typing import Any, Literal, Union

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel

# The sentinel key LangGraph uses to flag an interrupt chunk when streaming with
# ``stream_mode="updates"``; it is not a real node update.
_INTERRUPT_CHUNK_KEY: str = "__interrupt__"


class StatusEvent(BaseModel):
    """A pipeline node completed a super-step."""

    type: Literal["status"] = "status"
    node: str


class InterruptEvent(BaseModel):
    """The run suspended at a human-in-the-loop interrupt (generic envelope)."""

    type: Literal["interrupt"] = "interrupt"
    interrupt_type: str
    payload: dict[str, Any]


class DoneEvent(BaseModel):
    """The run reached END; payload carries a final-state summary."""

    type: Literal["done"] = "done"
    payload: dict[str, Any]


PipelineEvent = Union[StatusEvent, InterruptEvent, DoneEvent]


def _terminal_event(graph: CompiledStateGraph, config: dict[str, Any]) -> PipelineEvent:
    """Inspect the post-run state and build the single terminating event."""
    snapshot = graph.get_state(config)
    if snapshot.next:
        # The suspended node's name *is* the interrupt type (e.g.
        # "candidate_review"). Same access path main.py uses for the value.
        interrupt_type = snapshot.next[0]
        interrupt_value = snapshot.tasks[0].interrupts[0].value
        payload = (
            dict(interrupt_value)
            if isinstance(interrupt_value, dict)
            else {"value": interrupt_value}
        )
        return InterruptEvent(interrupt_type=interrupt_type, payload=payload)

    final_state = snapshot.values
    return DoneEvent(
        payload={
            "selected_candidate_url": final_state.get("selected_candidate_url"),
            "download_result": final_state.get("download_result"),
            "release": final_state.get("release"),
        }
    )


def stream_pipeline_events(
    graph: CompiledStateGraph,
    command_or_input: Union[dict[str, Any], Command],
    config: dict[str, Any],
) -> Iterator[PipelineEvent]:
    """Run (or resume) the graph and yield an ordered sequence of typed events.

    ``command_or_input`` is the initial state dict for a start run, or a
    ``Command(resume=...)`` for a resume run — so one generator serves both
    directions of the interaction protocol.
    """
    for chunk in graph.stream(command_or_input, config, stream_mode="updates"):
        for node_name in chunk:
            if node_name == _INTERRUPT_CHUNK_KEY:
                continue
            yield StatusEvent(node=node_name)

    yield _terminal_event(graph, config)
