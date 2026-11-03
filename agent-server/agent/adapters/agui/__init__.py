"""Official AG-UI conversion around server-authoritative graph input.

Pinned to ag-ui-langgraph 0.0.47. Only prepare_stream is customized: message
merging, continuation and approval decisions belong to our execution layer.
Raw graph state and compacted snapshots must not overwrite durable UI history.
"""

from __future__ import annotations

from ag_ui.core import RunAgentInput
from ag_ui_langgraph import LangGraphAgent
from ...cancellation import check_cancelled
from ...errors import ApplicationError, normalize_error


class BoundLangGraphAgent(LangGraphAgent):
    def __init__(self, *, graph, graph_input, config):
        super().__init__(
            name="agent-server",
            graph=graph,
            config=config,
            emit_interrupt_outcome=True,
            enable_legacy_on_interrupt_event=False,
            emit_raw_events=False,
            emit_subagent_events=False,
        )
        self.graph_input = graph_input
        self.failure = None

    async def prepare_stream(self, input, agent_state, config):
        self.active_run["schema_keys"] = self.get_schema_keys(config)

        async def events():
            try:
                async for event in self.graph.astream_events(
                    self.graph_input,
                    config,
                    version="v2",
                    durability="sync",
                ):
                    if "agent:internal" not in event.get("tags", []):
                        yield event
            except Exception as exc:
                self.failure = normalize_error(exc, source="model")
                # Preserve the classified error across the vendor's RUN_ERROR
                # conversion, while keeping raw provider text out of that event.
                raise ApplicationError(self.failure) from None

        return {
            "stream": events(),
            "state": dict(agent_state.values or {}),
            "config": config,
        }


async def stream_graph(graph, graph_input, config, context):
    adapter = BoundLangGraphAgent(graph=graph, graph_input=graph_input, config=config)
    request = RunAgentInput(
        thread_id=context.thread_id,
        run_id=context.run_id,
        messages=[],
        state={},
        tools=[],
        context=[],
        forwarded_props={},
    )
    # The application emits one terminal event only after persistence/memory
    # enqueueing. Snapshot messages describe compacted graph state, not history.
    excluded = {
        "RUN_STARTED",
        "RUN_FINISHED",
        "STATE_SNAPSHOT",
        "STATE_DELTA",
        "MESSAGES_SNAPSHOT",
        "RAW",
        "CUSTOM",
    }
    async for event in adapter.run(request):
        if event.type == "RUN_ERROR":
            check_cancelled(context.cancel)
            raise ApplicationError(
                adapter.failure or normalize_error(RuntimeError(), source="model")
            )
        if event.type not in excluded:
            yield event
