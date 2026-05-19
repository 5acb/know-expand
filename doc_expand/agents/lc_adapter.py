"""LangChain BaseChatModel adapter around QuotaAwareRouter.

Allows QuotaAwareRouter (litellm-backed, with model fallback) to be used
with langgraph's StateGraph + ToolNode ReAct pattern and any other
LangChain component that expects a BaseChatModel.

Message conversion:
  LangChain messages → OpenAI dicts  (for litellm)
  litellm response dict             → LangChain AIMessage (for langgraph)
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages import ToolCall as LCToolCall
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode


def _to_openai_dict(msg: BaseMessage) -> dict:
    """Convert a LangChain message to an OpenAI-format dict for litellm."""
    if isinstance(msg, SystemMessage):
        return {"role": "system", "content": msg.content or ""}
    if isinstance(msg, HumanMessage):
        return {"role": "user", "content": msg.content or ""}
    if isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": msg.tool_call_id,
            "content": msg.content or "",
        }
    if isinstance(msg, AIMessage):
        d: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["args"]),
                    },
                }
                for tc in msg.tool_calls
            ]
        return d
    return {"role": "user", "content": str(msg.content)}


def _to_lc_aimessage(router_response: dict) -> AIMessage:
    """Convert a router.call_with_tools() response dict to a LangChain AIMessage."""
    content = router_response.get("content") or ""
    raw_tool_calls = router_response.get("tool_calls") or []

    lc_tool_calls: list[LCToolCall] = []
    for tc in raw_tool_calls:
        args_raw = tc["function"].get("arguments", "{}")
        try:
            args = json.loads(args_raw)
        except (json.JSONDecodeError, TypeError):
            args = {}
        lc_tool_calls.append(
            LCToolCall(id=tc["id"], name=tc["function"]["name"], args=args)
        )

    return AIMessage(content=content, tool_calls=lc_tool_calls)


class RouterChatModel(BaseChatModel):
    """LangChain-compatible chat model backed by QuotaAwareRouter.

    Use make_lc_model() to construct — it wires the router post-init so
    Pydantic doesn't need to serialise the router object.
    """

    role: str = "agent"
    _router: Any = None
    _tools: list[dict] = []

    @property
    def _llm_type(self) -> str:
        return f"quota_aware_router_{self.role}"

    def bind_tools(self, tools: list, **kwargs) -> "RouterChatModel":
        from langchain_core.utils.function_calling import convert_to_openai_tool
        openai_tools = [convert_to_openai_tool(t) for t in tools]
        new = RouterChatModel(role=self.role)
        new._router = self._router
        new._tools = openai_tools
        return new

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        # Sync path: only safe to call outside a running event loop.
        # The graph uses call_model (async) → ainvoke, so this is a fallback only.
        import asyncio
        return asyncio.run(
            self._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        )

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        oai_messages = [_to_openai_dict(m) for m in messages]
        response_dict = await self._router.call_with_tools(oai_messages, self._tools)
        ai_msg = _to_lc_aimessage(response_dict)
        return ChatResult(generations=[ChatGeneration(message=ai_msg)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGeneration]:
        result = self._generate(messages, stop=stop, run_manager=run_manager)
        yield result.generations[0]


def make_lc_model(router: Any) -> RouterChatModel:
    """Construct a RouterChatModel wired to the given QuotaAwareRouter."""
    model = RouterChatModel(role=router.role)
    model._router = router
    return model


def build_react_graph(
    model: RouterChatModel,
    tools: list,
    system_prompt: str,
    recursion_limit: int = 50,
):
    """Build a langgraph ReAct StateGraph (the stable langgraph 1.x pattern).

    Returns a compiled graph that accepts {"messages": [...]} input and
    returns {"messages": [...]} output.
    """
    from langchain_core.messages import SystemMessage as _SM

    tool_node = ToolNode(tools)
    bound_model = model.bind_tools(tools)

    async def call_model(state: MessagesState) -> dict:
        messages = state["messages"]
        # Prepend system message on the first call only
        if not any(isinstance(m, _SM) for m in messages):
            messages = [_SM(content=system_prompt)] + list(messages)
        response = await bound_model.ainvoke(messages)
        return {"messages": [response]}

    def should_continue(state: MessagesState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(MessagesState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()
