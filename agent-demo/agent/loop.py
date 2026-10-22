"""用 LangGraph 跑一轮对话。

图本身在 graph.py。这里负责：

- 把用户消息送进图，并把 token、工具、确认转成原来的事件
- 命令行遇到 add_note 时当场询问，网页则停下来等下一次请求
- 同一条 thread 的历史放在 PostgreSQL 的 checkpoint 里
"""

from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from .graph import (
    BudgetMiddleware,
    LimitStop,
    approval_message,
    build_model,
    compile_agent,
    rejected_message,
)
from .limits import AgentLimits, LimitTracker
from .mcp_client import McpConnection
from .memory.context_builder import ContextBuilder, MemoryContextMiddleware
from .memory.extractor import MemoryExtractor
from .memory.long_term import MemoryRepository, PostgresMemory
from .memory.policy import PolicyEngine
from .memory.resolver import MemoryResolver
from .memory.short_term import PostgresSessions, ShortTermMemory, ShortTermMiddleware
from .tools import ToolRegistry, default_tools

SYSTEM_PROMPT = """你是一个会用工具的助手。
计算、查时间、查天气用本地工具。笔记相关的事用 MCP 工具。
不要自己编造数字、天气、笔记内容或记忆。
系统提示里的会话摘要和长期记忆只在和当前问题有关时使用，不要把无关记忆复述出来。
记笔记直接调用 add_note，不要先询问要不要保存；系统会请用户确认。
删除笔记直接调用 delete_note。确认由 MCP server 在这次调用里向 client 发起，不要自己先问。
工具返回结果后，用简体中文给出最终回答。
回答用 Markdown：分点用列表，代码放进代码块，需要强调时用加粗。
"""

_CONFIRM_TOOLS = frozenset({"add_note"})
EventHandler = Callable[[str, dict[str, str]], None]


@dataclass
class PendingAction:
    tool_call_id: str
    name: str
    arguments: str


@dataclass
class PendingApproval:
    interrupt_id: str
    tool_call_id: str
    name: str
    arguments: str
    message: str
    actions: list[PendingAction] = field(default_factory=list)


@dataclass
class AgentResult:
    text: str
    stop_reason: str
    usage: dict[str, str]
    approval: PendingApproval | None = None


class Agent:
    def __init__(
        self,
        *,
        model: str | None = None,
        limits: AgentLimits | None = None,
        tools: ToolRegistry | None = None,
        verbose: bool = True,
        use_mcp: bool = True,
        thinking: bool = False,
    ):
        self.limits = limits or AgentLimits()
        self.verbose = verbose
        self.tools = tools or default_tools()
        self.mcp: McpConnection | None = None
        self._memory = PostgresMemory()
        self.store = self._memory.store
        try:
            self._sessions = PostgresSessions()
        except Exception:
            self.close()
            raise
        self.checkpointer = self._sessions.saver
        system_prompt = SYSTEM_PROMPT
        try:
            self._build(model=model, system_prompt=system_prompt, use_mcp=use_mcp, thinking=thinking)
        except Exception:
            self.close()
            raise

    def _build(
        self,
        *,
        model: str | None,
        system_prompt: str,
        use_mcp: bool,
        thinking: bool,
    ) -> None:
        if use_mcp:
            self.mcp = McpConnection()
            self.tools.extend(self.mcp.agent_tools())
            system_prompt = f"{SYSTEM_PROMPT}\n{self.mcp.model_instructions()}"
            self._log(self.mcp.startup_summary())
        self.system_prompt = system_prompt
        self._budget = BudgetMiddleware()
        self.chat_model = build_model(thinking=thinking, timeout=self.limits.timeout)
        if model:
            self.chat_model.model_name = model
        self.model = self.chat_model.model_name
        user_id = self.mcp.username if self.mcp is not None and self.mcp.username else "local"
        self.repository = MemoryRepository(self.store, user_id)
        self.extractor = MemoryExtractor(self.chat_model)
        self.policy = PolicyEngine()
        self.resolver = MemoryResolver()
        self.context_builder = ContextBuilder(self.repository)
        self.short_term = ShortTermMemory()
        self.graph = compile_agent(
            model=self.chat_model,
            tools=self.tools.langchain_tools(),
            system_prompt=system_prompt,
            budget=self._budget,
            checkpointer=self.checkpointer,
            store=self.store,
            memory_middleware=[
                ShortTermMiddleware(self.chat_model, self.short_term, self._log),
                MemoryContextMiddleware(self.context_builder, self._log),
            ],
        )
        self._log("短期记忆: PostgreSQL checkpoints")
        self._log(
            f"长期记忆向量: {self._memory.embedding_model} ({self._memory.dims})"
        )
        self._log(f"长期记忆命名空间: study/{user_id}")
        self.tracker = LimitTracker(self.limits, self.model)
        self.thread_id = str(uuid.uuid4())

    def close(self) -> None:
        if self.mcp is not None:
            self.mcp.close()
            self.mcp = None
        try:
            sessions = getattr(self, "_sessions", None)
            if sessions is not None:
                sessions.close()
                self._sessions = None
        finally:
            memory = getattr(self, "_memory", None)
            if memory is not None:
                memory.close()
                self._memory = None

    def chat(self, user_input: str) -> AgentResult:
        """处理一轮用户输入。多轮聊天反复调用这个方法即可。"""
        stop = self.tracker.begin_turn()
        if stop:
            return self._stopped(self.tracker, stop, "已达到 max_turns，不再接受新的用户消息。")
        self._log(f"\n========== turn {self.tracker.turns} ==========")
        return self._run(
            {"messages": [HumanMessage(content=user_input)]},
            thread_id=self.thread_id,
            tracker=self.tracker,
            on_event=None,
            approvals=None,
            announced=set(),
        )

    def run_messages(
        self,
        messages: list[BaseMessage],
        *,
        tracker: LimitTracker | None = None,
        on_event: EventHandler | None = None,
        approvals: dict[str, bool] | None = None,
        thread_id: str,
    ) -> AgentResult:
        """跑一轮网页请求。历史在 thread_id 对应的 checkpoint 里。"""
        active = tracker or LimitTracker(self.limits, self.model)
        stop = active.begin_turn()
        if stop:
            return self._stopped(active, stop, "已达到 max_turns，不再接受新的用户消息。", on_event)
        self._log(f"\n========== turn {active.turns} ==========")
        config = self._config(thread_id)
        decisions = dict(approvals or {})
        snapshot = self.graph.get_state(config)
        if snapshot.interrupts:
            if not decisions:
                approval = _approval_from_state(snapshot)
                self._announce(on_event, approval)
                return AgentResult(
                    text="",
                    stop_reason="interrupt",
                    usage=active.snapshot(),
                    approval=approval,
                )
            graph_input: Any = Command(resume={"decisions": _decisions(snapshot, decisions)})
            announced = set(decisions)
        else:
            stored = list(snapshot.values.get("messages") or []) if snapshot.values else []
            if stored:
                fresh = _new_user_messages(stored, messages)
                if not fresh:
                    text = _final_text(snapshot.values.get("messages") or [])
                    return AgentResult(text=text, stop_reason="completed", usage=active.snapshot())
                graph_input = {"messages": fresh}
                announced = set()
            else:
                settled, pending = self._settle(messages, decisions, active, on_event)
                if pending is not None:
                    return AgentResult(
                        text="",
                        stop_reason="interrupt",
                        usage=active.snapshot(),
                        approval=pending,
                    )
                graph_input = {"messages": settled}
                announced = set(decisions)
        return self._run(
            graph_input,
            thread_id=thread_id,
            tracker=active,
            on_event=on_event,
            approvals=decisions,
            announced=announced,
        )

    def _run(
        self,
        graph_input: Any,
        *,
        thread_id: str,
        tracker: LimitTracker,
        on_event: EventHandler | None,
        approvals: dict[str, bool] | None,
        announced: set[str],
    ) -> AgentResult:
        self._budget.tracker = tracker
        self._budget.log = self._log
        self._streamed_text = False
        config = self._config(thread_id)
        decisions = dict(approvals or {})
        try:
            while True:
                approval = self._consume(graph_input, config, on_event, announced)
                if approval is None:
                    text = _final_text(self.graph.get_state(config).values.get("messages") or [])
                    self._log("  最终回答")
                    if on_event is not None and not self._streamed_text:
                        self._emit(on_event, "assistant_text", text=text)
                    self._remember(config, on_event)
                    return AgentResult(text=text, stop_reason="completed", usage=tracker.snapshot())

                known = [decisions[item.tool_call_id] for item in approval.actions if item.tool_call_id in decisions]
                if on_event is not None and not known:
                    self._announce(on_event, approval)
                    return AgentResult(
                        text="",
                        stop_reason="interrupt",
                        usage=tracker.snapshot(),
                        approval=approval,
                    )
                if on_event is None and not known:
                    flags = [_cli_approved(item.name, item.arguments) for item in approval.actions]
                else:
                    approved = known[0] if known else False
                    flags = [approved for _item in approval.actions]
                for item in approval.actions:
                    announced.add(item.tool_call_id)
                graph_input = Command(
                    resume={
                        "decisions": [
                            _decision(item.name, flag)
                            for item, flag in zip(approval.actions, flags, strict=True)
                        ]
                    }
                )
        except LimitStop as exc:
            return self._stopped(tracker, exc.reason, exc.hint, on_event)
        finally:
            self._budget.tracker = None

    def _consume(
        self,
        graph_input: Any,
        config: dict[str, Any],
        on_event: EventHandler | None,
        announced: set[str],
    ) -> PendingApproval | None:
        text_open = False
        message_id = ""
        reasoning_id: str | None = None
        answer_step = False
        model_step = False
        seen_results: set[str] = set()
        tool_arguments: dict[str, str] = {}

        def close_reasoning() -> None:
            nonlocal reasoning_id
            if reasoning_id is None:
                return
            self._emit(on_event, "reasoning_end", id=reasoning_id)
            reasoning_id = None

        def close_model() -> None:
            nonlocal model_step, answer_step, text_open
            close_reasoning()
            if text_open:
                self._emit(on_event, "text_end", id=message_id)
                text_open = False
            if answer_step:
                self._emit(on_event, "step_end", name="生成回答")
                answer_step = False
            if model_step:
                self._emit(on_event, "step_end", name="调用模型")
                model_step = False

        try:
            for chunk in self.graph.stream(
                graph_input,
                config,
                stream_mode=["messages", "updates"],
                version="v2",
            ):
                kind = chunk.get("type") if isinstance(chunk, dict) else None
                if kind == "messages":
                    token, _metadata = chunk["data"]
                    if not isinstance(token, AIMessageChunk):
                        continue
                    if not model_step:
                        self._emit(on_event, "step_start", name="调用模型")
                        model_step = True
                    thought = _chunk_reasoning(token)
                    if thought:
                        if reasoning_id is None:
                            reasoning_id = str(uuid.uuid4())
                            self._emit(on_event, "reasoning_start", id=reasoning_id)
                        self._emit(on_event, "reasoning_delta", id=reasoning_id, delta=thought)
                    text = _chunk_text(token)
                    if text:
                        close_reasoning()
                        if not text_open:
                            self._emit(on_event, "step_start", name="生成回答")
                            answer_step = True
                            message_id = str(token.id or uuid.uuid4())
                            self._emit(on_event, "text_start", id=message_id)
                            text_open = True
                        self._streamed_text = True
                        self._emit(on_event, "text_delta", id=message_id, delta=text)
                elif kind == "updates":
                    data = chunk.get("data") or {}
                    if "__interrupt__" in data:
                        close_model()
                        snapshot = self.graph.get_state(config)
                        return _approval_from_state(snapshot)
                    for update in data.values():
                        messages = update.get("messages") if isinstance(update, dict) else None
                        if not messages:
                            continue
                        if any(isinstance(item, AIMessage) and not isinstance(item, AIMessageChunk) for item in messages):
                            for item in messages:
                                if isinstance(item, AIMessage) and item.tool_calls:
                                    for call in item.tool_calls:
                                        call_id = str(call.get("id") or "")
                                        if call_id:
                                            tool_arguments[call_id] = json.dumps(
                                                call.get("args") or {},
                                                ensure_ascii=False,
                                            )
                                    if item.id:
                                        message_id = str(item.id)
                            close_model()
                        for item in messages:
                            if not isinstance(item, ToolMessage):
                                continue
                            call_id = str(item.tool_call_id or "")
                            if not call_id or call_id in seen_results:
                                continue
                            seen_results.add(call_id)
                            name = item.name or "工具"
                            step_name = f"执行 {name}"
                            self._emit(on_event, "step_start", name=step_name)
                            if call_id not in announced:
                                self._emit(
                                    on_event,
                                    "tool_start",
                                    id=call_id,
                                    name=name,
                                    arguments=tool_arguments.get(call_id, "{}"),
                                    parent=message_id,
                                )
                                announced.add(call_id)
                            self._emit(
                                on_event,
                                "tool_result",
                                id=call_id,
                                name=name,
                                result=_message_text(item),
                            )
                            self._emit(on_event, "step_end", name=step_name)
        finally:
            close_model()

        snapshot = self.graph.get_state(config)
        if snapshot.interrupts:
            return _approval_from_state(snapshot)
        return None

    def _settle(
        self,
        messages: list[BaseMessage],
        approvals: dict[str, bool],
        tracker: LimitTracker,
        on_event: EventHandler | None,
    ) -> tuple[list[BaseMessage], PendingApproval | None]:
        """checkpoint 还没有这轮对话时，先补上上次没执行完的工具。"""
        output = list(messages)
        for call in _unanswered(output):
            if call.name in _CONFIRM_TOOLS and call.id not in approvals:
                approval = PendingApproval(
                    interrupt_id=str(uuid.uuid4()),
                    tool_call_id=call.id,
                    name=call.name,
                    arguments=call.arguments,
                    message=approval_message(call.name, call.arguments),
                    actions=[PendingAction(call.id, call.name, call.arguments)],
                )
                self._announce(on_event, approval)
                return output, approval
            approved = approvals.get(call.id, call.name not in _CONFIRM_TOOLS)
            if not approved:
                result = rejected_message(call.name)
            else:
                stop = tracker.before_tool()
                if stop:
                    raise LimitStop(stop, f"循环在执行工具前停下：{stop}")
                result = self.tools.execute(call.name, call.arguments)
                tracker.record_tool_call()
            self._log(f"  ok {call.name}({call.arguments}) => {result}")
            self._emit(on_event, "tool_result", id=call.id, name=call.name, result=result)
            output.append(ToolMessage(content=result, tool_call_id=call.id, name=call.name))
        return output, None

    def _remember(self, config: dict[str, Any], on_event: EventHandler | None) -> None:
        messages = list(self.graph.get_state(config).values.get("messages") or [])
        user_text, assistant_text = _exchange(messages)
        if not user_text or not assistant_text or assistant_text.startswith("[stopped:"):
            return
        self._emit(on_event, "step_start", name="整理长期记忆")
        try:
            candidates = self.extractor.extract(user_text, assistant_text)
            if not candidates:
                self._log("  长期记忆：没有候选")
                return
            for candidate in candidates:
                review = self.policy.review(candidate)
                if not review.allowed:
                    self._log(f"  长期记忆跳过（{review.reason}）：{candidate.text}")
                    continue
                action = self.resolver.apply(self.repository, candidate, review.score)
                self._log(f"  长期记忆 {action}：{candidate.subject or candidate.text}")
        except Exception as exc:
            self._log(f"  长期记忆提取失败：{exc}")
        finally:
            self._emit(on_event, "step_end", name="整理长期记忆")

    def _announce(self, on_event: EventHandler | None, approval: PendingApproval) -> None:
        self._log(f"  等待确认 {approval.name}")
        self._emit(
            on_event,
            "tool_start",
            id=approval.tool_call_id,
            name=approval.name,
            arguments=approval.arguments,
        )

    def _config(self, thread_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": max(50, self.limits.max_iterations * 8),
        }

    def _stopped(
        self,
        tracker: LimitTracker,
        reason: str,
        hint: str,
        on_event: EventHandler | None = None,
    ) -> AgentResult:
        text = f"[stopped: {reason}] {hint}"
        self._log(f"  {text}")
        self._emit(on_event, "assistant_text", text=text)
        return AgentResult(text=text, stop_reason=reason, usage=tracker.snapshot())

    def _emit(self, on_event: EventHandler | None, kind: str, **payload: str) -> None:
        if on_event is not None:
            on_event(kind, payload)

    def _log(self, line: str) -> None:
        if not self.verbose:
            return
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe = line.encode(encoding, errors="replace").decode(encoding, errors="replace")
            print(safe, flush=True)


def _decisions(snapshot: Any, approvals: dict[str, bool]) -> list[dict[str, str]]:
    approval = _approval_from_state(snapshot)
    known = [approvals[item.tool_call_id] for item in approval.actions if item.tool_call_id in approvals]
    approved = known[0] if known else False
    return [_decision(item.name, approved) for item in approval.actions]


def _decision(name: str, approved: bool) -> dict[str, str]:
    if approved:
        return {"type": "approve"}
    return {"type": "reject", "message": rejected_message(name)}


def _approval_from_state(snapshot: Any) -> PendingApproval:
    interrupt = snapshot.interrupts[0]
    value = interrupt.value if isinstance(interrupt.value, dict) else {}
    requests = value.get("action_requests") or []
    calls = _confirm_calls(snapshot.values.get("messages") or [])
    actions: list[PendingAction] = []
    for index, request in enumerate(requests):
        call = calls[index] if index < len(calls) else None
        args = request.get("args") if isinstance(request, dict) else None
        if not isinstance(args, dict) and call is not None:
            args = call["args"]
        arguments = json.dumps(args or {}, ensure_ascii=False)
        actions.append(
            PendingAction(
                tool_call_id=str((call or {}).get("id") or ""),
                name=str(request.get("name") or (call or {}).get("name") or ""),
                arguments=arguments,
            )
        )
    if not actions and calls:
        arguments = json.dumps(calls[0]["args"], ensure_ascii=False)
        actions.append(PendingAction(str(calls[0]["id"]), str(calls[0]["name"]), arguments))
    first = actions[0]
    message = value.get("action_requests", [{}])[0].get("description") if requests else ""
    if not isinstance(message, str) or not message.strip():
        message = approval_message(first.name, first.arguments)
    if len(actions) > 1:
        message = "\n\n".join(approval_message(item.name, item.arguments) for item in actions)
    return PendingApproval(
        interrupt_id=str(getattr(interrupt, "id", "") or uuid.uuid4()),
        tool_call_id=first.tool_call_id,
        name=first.name,
        arguments=first.arguments,
        message=message,
        actions=actions,
    )


def _confirm_calls(messages: list[Any]) -> list[dict[str, Any]]:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.tool_calls:
            return [call for call in message.tool_calls if call.get("name") in _CONFIRM_TOOLS]
    return []


def _new_user_messages(stored: list[Any], incoming: list[BaseMessage]) -> list[HumanMessage]:
    """按最后一条已保存的用户消息对齐。摘要删掉前面的消息后，仍然只追加新的一句。"""
    stored_users = [message for message in stored if isinstance(message, HumanMessage)]
    incoming_users = [message for message in incoming if isinstance(message, HumanMessage)]
    if not stored_users:
        return incoming_users
    last = _message_text(stored_users[-1]).strip()
    match = -1
    for index, message in enumerate(incoming_users):
        if _message_text(message).strip() == last:
            match = index
    if match < 0:
        if incoming_users and _message_text(incoming_users[-1]).strip() != last:
            return [incoming_users[-1]]
        return []
    return incoming_users[match + 1 :]


def _exchange(messages: list[Any]) -> tuple[str, str]:
    assistant = ""
    for message in reversed(messages):
        if not assistant:
            if isinstance(message, AIMessage) and not message.tool_calls:
                assistant = _message_text(message).strip()
            continue
        if isinstance(message, HumanMessage):
            return _message_text(message).strip(), assistant
    return "", assistant


def _final_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            text = _message_text(message).strip()
            return text or "(模型没有返回文本)"
    return "(模型没有返回文本)"


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def _chunk_text(message: AIMessageChunk) -> str:
    return _message_text(message)


def _chunk_reasoning(message: AIMessageChunk) -> str:
    value = (message.additional_kwargs or {}).get("reasoning_content")
    return value if isinstance(value, str) else ""


def _unanswered(messages: list[BaseMessage]) -> list["_Call"]:
    answered = {
        message.tool_call_id
        for message in messages
        if isinstance(message, ToolMessage) and message.tool_call_id
    }
    pending: list[_Call] = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            call_id = str(call.get("id") or "")
            if not call_id or call_id in answered:
                continue
            pending.append(
                _Call(
                    id=call_id,
                    name=str(call.get("name") or ""),
                    arguments=json.dumps(call.get("args") or {}, ensure_ascii=False),
                )
            )
            answered.add(call_id)
    return pending


@dataclass
class _Call:
    id: str
    name: str
    arguments: str


def _cli_approved(name: str, arguments: str) -> bool:
    print(f"\n{approval_message(name, arguments)}", flush=True)
    try:
        answer = input("接受这次调用？[y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes", "接受"}
