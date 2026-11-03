"""几个用来演示 Function Calling 的小工具。

真正的 Agent 里工具会更复杂（读文件、搜代码、调 API）。
这里故意做得很薄：重点是看清「模型生成调用 → 程序执行 → 结果回填」。
"""

from __future__ import annotations

import ast
import hashlib
import json
import operator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from .runtime.context import current_run, LimitStop
from .errors import ErrorCode, make_error, normalize_error
from dataclasses import replace
from .runtime.retry import (
    RetryPolicy,
    call_with_retry,
    is_transient,
    reserve_tool_retry,
)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]
    read_only: bool = False
    retry_managed: bool = False

    def schema(self) -> dict[str, Any]:
        parameters = dict(self.parameters)
        parameters.setdefault("type", "object")
        return parameters


_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _eval_math(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_math(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_math(node.left), _eval_math(node.right))
    if isinstance(node, ast.Expression):
        return _eval_math(node.body)
    raise ValueError("只支持数字和 + - * / **")


def calculator(expression: str) -> str:
    tree = ast.parse(expression, mode="eval")
    value = _eval_math(tree)
    return str(value)


def get_current_time(timezone: str = "local") -> str:
    now = datetime.now().astimezone()
    return json.dumps(
        {
            "timezone": timezone or "local",
            "iso": now.isoformat(timespec="seconds"),
            "display": now.strftime("%Y-%m-%d %H:%M:%S"),
        },
        ensure_ascii=False,
    )


_WEATHER = {
    "beijing": {"city": "Beijing", "display": "北京", "temp_c": 18, "condition": "晴"},
    "北京": {"city": "Beijing", "display": "北京", "temp_c": 18, "condition": "晴"},
    "shanghai": {
        "city": "Shanghai",
        "display": "上海",
        "temp_c": 22,
        "condition": "多云",
    },
    "上海": {"city": "Shanghai", "display": "上海", "temp_c": 22, "condition": "多云"},
    "shenzhen": {
        "city": "Shenzhen",
        "display": "深圳",
        "temp_c": 28,
        "condition": "小雨",
    },
    "深圳": {"city": "Shenzhen", "display": "深圳", "temp_c": 28, "condition": "小雨"},
    "new york": {
        "city": "New York",
        "display": "New York",
        "temp_c": 15,
        "condition": "阴",
    },
}


def get_weather(city: str) -> str:
    key = city.strip().lower()
    data = _WEATHER.get(key) or _WEATHER.get(city.strip())
    if data is None:
        return json.dumps(
            {"error": f"没有 {city} 的演示数据，可试：北京 / 上海 / 深圳 / New York"},
            ensure_ascii=False,
        )
    return json.dumps(data, ensure_ascii=False)


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {tool.name: tool for tool in tools}

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def langchain_tools(self) -> list[StructuredTool]:
        return [_langchain_tool(tool) for tool in self._tools.values()]

    def extend(self, tools: list[Tool]) -> None:
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"工具名冲突: {tool.name}")
            self._tools[tool.name] = tool

    def execute(self, name: str, arguments_json: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return make_error(ErrorCode.TOOL_NOT_FOUND, source="tool", operation="tool").tool_result()

        try:
            args = json.loads(arguments_json or "{}")
            if not isinstance(args, dict):
                return make_error(ErrorCode.INVALID_ARGUMENT, source="tool", operation=name).tool_result()
        except json.JSONDecodeError:
            return make_error(ErrorCode.INVALID_ARGUMENT, source="tool", operation=name).tool_result()

        try:
            return _invoke_tool(tool, args)
        except LimitStop:
            raise
        except Exception as exc:
            return normalize_error(exc, source="tool", operation=name, partial=False).tool_result()


def _langchain_tool(tool: Tool) -> StructuredTool:
    def run(**kwargs: Any) -> str:
        cleaned = {key: value for key, value in kwargs.items() if value is not None}
        try:
            return _invoke_tool(tool, cleaned)
        except LimitStop:
            raise
        except Exception as exc:
            return normalize_error(exc, source="tool", operation=tool.name, partial=False).tool_result()

    parameters = dict(tool.parameters)
    parameters.setdefault("type", "object")
    return StructuredTool(
        name=tool.name,
        description=tool.description or tool.name,
        args_schema=parameters,
        func=run,
        metadata={"read_only": tool.read_only},
    )


def _invoke_tool(tool: Tool, arguments: dict[str, Any]) -> str:
    context = current_run()
    key = hashlib.sha256(
        (
            tool.name
            + json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        ).encode()
    ).hexdigest()
    if not tool.read_only and context is not None:
        with context.tool_lock:
            if key in context.uncertain_writes:
                return make_error(ErrorCode.OPERATION_OUTCOME_UNKNOWN, source="tool", operation=tool.name, outcome_unknown=True).tool_result()
    try:
        if tool.retry_managed:
            return str(tool.handler(**arguments))
        policy = RetryPolicy.configured(
            "tool_read", safe=tool.read_only and not tool.retry_managed
        )
        return str(
            call_with_retry(
                lambda: tool.handler(**arguments),
                policy=policy,
                label=f"tool:{tool.name}",
                before_attempt=reserve_tool_retry,
            )
        )
    except Exception as exc:
        if not tool.read_only and is_transient(exc) and context is not None:
            with context.tool_lock:
                context.uncertain_writes.add(key)
            error = normalize_error(exc, source="tool", operation=tool.name, partial=False)
            return replace(error, code=ErrorCode.OPERATION_OUTCOME_UNKNOWN, retryable=False,
                           outcome_unknown=True, cause_code=error.code).tool_result()
        raise


def default_tools() -> ToolRegistry:
    return ToolRegistry(
        [
            Tool(
                name="calculator",
                description="计算一个数学表达式，例如 23*17 或 (3+5)**2",
                parameters={
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "只包含数字和 + - * / ** 的表达式",
                        }
                    },
                    "required": ["expression"],
                },
                handler=calculator,
                read_only=True,
            ),
            Tool(
                name="get_current_time",
                description="获取当前本地时间",
                parameters={
                    "type": "object",
                    "properties": {
                        "timezone": {
                            "type": "string",
                            "description": "时区说明，演示里只回本地时间",
                        }
                    },
                },
                handler=get_current_time,
                read_only=True,
            ),
            Tool(
                name="get_weather",
                description="查询城市天气。这是演示数据，不是真实气象 API",
                parameters={
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "城市名，例如 北京 或 Shanghai",
                        }
                    },
                    "required": ["city"],
                },
                handler=get_weather,
                read_only=True,
            ),
        ]
    )
