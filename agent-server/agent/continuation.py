"""Continue a persisted answer using a fresh model call, without replaying tools."""
from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage


@dataclass(frozen=True)
class PartialAnswer:
    message_id: str
    text: str

    def prompt_messages(self) -> list:
        # This instruction belongs only to the model request, never to the
        # user's persisted conversation or its user-message count.
        return [
            AIMessage(content=self.text),
            HumanMessage(content=(
                "上一条助手回答因用户取消而中断，现在用户点击了继续生成。"
                "请紧接该回答已有文本的最后一个字符继续，只输出尚未生成的后续内容。"
                "不要重复已有内容，不要从头重写，不要添加‘继续’等开场白。"
                "保留原有语言、Markdown 和未闭合的代码块、列表、句子的结构。"
                "已经获得的工具结果在上下文中；本次只续写回答，不执行新的工具。"
            )),
        ]
