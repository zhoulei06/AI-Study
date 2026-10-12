"""Agent 循环的安全限制。

一次对话里模型可能反复「思考 → 调工具 → 再思考」。
如果没有上限，它可能死循环，或者把 token / 钱烧光。

本文件只负责计数和判断该不该停，不负责调模型。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


# 仅用于估算 cost_budget，不是官方实时报价。单位：USD / 1M tokens
MODEL_PRICES_PER_MILLION = {
    "deepseek-chat": {"input": 0.28, "output": 0.42},
    "deepseek-reasoner": {"input": 0.28, "output": 0.42},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
}


@dataclass
class AgentLimits:
    # 一次 run 里最多调用几次 LLM。一次 turn 里可能有多次 iteration
    # （因为工具结果还要再喂回模型）
    max_iterations: int = 8

    # 用户说一句话、Agent 给出最终回答 = 1 turn。多轮聊天时用这个卡住总轮数
    max_turns: int = 8

    # 整次 run 里最多真正执行几次工具
    max_tool_calls: int = 12

    # 从本次 Agent 启动开始的墙钟超时（秒）
    timeout: float = 60.0

    # prompt + completion token 总和上限
    token_budget: int = 20_000

    # 估算花费上限（美元）。价格表是近似值，只为演示
    cost_budget: float = 0.05


@dataclass
class LimitTracker:
    limits: AgentLimits
    model: str
    started_at: float = field(default_factory=time.monotonic)
    iterations: int = 0
    turns: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def remaining_seconds(self) -> float:
        return max(0.1, self.limits.timeout - self.elapsed)

    def begin_turn(self) -> str | None:
        """开始新的一轮用户提问。超限则返回 stop_reason。"""
        reason = self._hit("max_turns", self.turns >= self.limits.max_turns)
        if reason:
            return reason
        self.turns += 1
        return None

    def before_llm(self) -> str | None:
        return self._common_checks() or self._hit(
            "max_iterations", self.iterations >= self.limits.max_iterations
        )

    def before_tool(self) -> str | None:
        return self._common_checks() or self._hit(
            "max_tool_calls", self.tool_calls >= self.limits.max_tool_calls
        )

    def record_iteration(self) -> None:
        self.iterations += 1

    def record_tool_call(self) -> None:
        self.tool_calls += 1

    def record_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        prices = MODEL_PRICES_PER_MILLION.get(self.model)
        if prices is None:
            prices = MODEL_PRICES_PER_MILLION["deepseek-chat"]
        self.cost_usd += (
            prompt_tokens / 1_000_000 * prices["input"]
            + completion_tokens / 1_000_000 * prices["output"]
        )

    def after_usage(self) -> str | None:
        """这次 LLM 调用已经花出去了。如果超预算，下一轮循环会停。"""
        return self._common_checks()

    def snapshot(self) -> dict[str, str]:
        return {
            "turns": f"{self.turns}/{self.limits.max_turns}",
            "iterations": f"{self.iterations}/{self.limits.max_iterations}",
            "tool_calls": f"{self.tool_calls}/{self.limits.max_tool_calls}",
            "tokens": f"{self.total_tokens}/{self.limits.token_budget}",
            "cost_usd": f"${self.cost_usd:.6f}/${self.limits.cost_budget:.4f}",
            "elapsed": f"{self.elapsed:.1f}s/{self.limits.timeout:.0f}s",
        }

    def _common_checks(self) -> str | None:
        return (
            self._hit("timeout", self.elapsed >= self.limits.timeout)
            or self._hit("token_budget", self.total_tokens >= self.limits.token_budget)
            or self._hit("cost_budget", self.cost_usd >= self.limits.cost_budget)
        )

    @staticmethod
    def _hit(reason: str, exceeded: bool) -> str | None:
        return reason if exceeded else None
