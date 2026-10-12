"""几个用来演示 Function Calling 的小工具。

真正的 Agent 里工具会更复杂（读文件、搜代码、调 API）。
这里故意做得很薄：重点是看清「模型生成调用 → 程序执行 → 结果回填」。
"""

from __future__ import annotations

import ast
import json
import operator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., str]

    def schema(self) -> dict[str, Any]:
        # OpenAI Chat Completions 要求的 function tool 格式
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


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
    "beijing": {"city": "Beijing", "temp_c": 18, "condition": "晴"},
    "北京": {"city": "Beijing", "temp_c": 18, "condition": "晴"},
    "shanghai": {"city": "Shanghai", "temp_c": 22, "condition": "多云"},
    "上海": {"city": "Shanghai", "temp_c": 22, "condition": "多云"},
    "shenzhen": {"city": "Shenzhen", "temp_c": 28, "condition": "小雨"},
    "深圳": {"city": "Shenzhen", "temp_c": 28, "condition": "小雨"},
    "new york": {"city": "New York", "temp_c": 15, "condition": "阴"},
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

    def extend(self, tools: list[Tool]) -> None:
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"工具名冲突: {tool.name}")
            self._tools[tool.name] = tool

    def execute(self, name: str, arguments_json: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'"

        try:
            args = json.loads(arguments_json or "{}")
            if not isinstance(args, dict):
                return "Error: tool arguments must be a JSON object"
        except json.JSONDecodeError as exc:
            return f"Error: invalid JSON arguments ({exc})"

        try:
            return str(tool.handler(**args))
        except TypeError as exc:
            return f"Error: bad arguments for {name}: {exc}"
        except Exception as exc:
            return f"Error while running {name}: {exc}"


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
            ),
        ]
    )
