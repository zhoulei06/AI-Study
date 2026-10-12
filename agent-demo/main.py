"""命令行入口：单次提问，或进入多轮聊天。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

from agent import Agent, AgentLimits

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="手写 OpenAI Agent Loop 演示")
    parser.add_argument("prompt", nargs="*", help="直接传入问题；省略则进入多轮聊天")
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--max-tool-calls", type=int, default=12)
    parser.add_argument("--timeout", type=float, default=60.0, help="秒")
    parser.add_argument("--token-budget", type=int, default=20_000)
    parser.add_argument("--cost-budget", type=float, default=0.05, help="美元")
    parser.add_argument("--quiet", action="store_true", help="不打印循环过程")
    return parser.parse_args()


def print_result(result) -> None:
    print("\n----- 回答 -----")
    print(result.text)
    print("\n----- 用量 -----")
    for key, value in result.usage.items():
        print(f"{key}: {value}")
    print(f"stop_reason: {result.stop_reason}")


def main() -> int:
    args = parse_args()
    agent = Agent(
        limits=AgentLimits(
            max_iterations=args.max_iterations,
            max_turns=args.max_turns,
            max_tool_calls=args.max_tool_calls,
            timeout=args.timeout,
            token_budget=args.token_budget,
            cost_budget=args.cost_budget,
        ),
        verbose=not args.quiet,
    )

    try:
        prompt = " ".join(args.prompt).strip()
        if prompt:
            print_result(agent.chat(prompt))
            return 0

        print("多轮聊天。输入 quit 退出。")
        print("可以问：记一条笔记，标题 Loop，正文是 Agent 会反复调用工具。然后读 notes://catalog")
        while True:
            try:
                user_input = input("\n你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not user_input:
                continue
            if user_input.lower() in {"quit", "exit", "q"}:
                return 0
            print_result(agent.chat(user_input))
    finally:
        agent.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
