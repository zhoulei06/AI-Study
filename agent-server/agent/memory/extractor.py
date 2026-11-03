"""用模型从一轮对话里抽出候选记忆。失败时当没有候选，不打断回答。"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from .types import Candidate
from ..runtime.context import invoke_auxiliary

_PROMPT = """从这一轮对话提取值得跨会话记住的内容。
只保留稳定事实、用户偏好、用户明确要求记住的事。
不要保留天气、当前时间、一次性计算、工具报错、寒暄。
输出 JSON 数组，不要 Markdown。每项字段：
- text：一句话，简体中文
- kind：fact、preference、episode 之一
- subject：稳定主题，例如 user.name、preference.reply_style。同一主题以后会被更新
- confidence：0 到 1
没有可记的内容就输出 []。
"""


class MemoryExtractor:
    def __init__(self, model: BaseChatModel) -> None:
        self.model = model

    def extract(self, user_text: str, assistant_text: str) -> list[Candidate]:
        user_text = user_text.strip()
        assistant_text = assistant_text.strip()
        if not user_text or not assistant_text:
            return []
        plain = self.model.model_copy(update={"extra_body": None})
        response = invoke_auxiliary(plain,
            [
                SystemMessage(content=_PROMPT),
                HumanMessage(
                    content=f"用户：{user_text}\n助手：{assistant_text}",
                ),
            ], purpose="长期记忆提取",
        )
        return _parse(str(response.content))


def _parse(raw: str) -> list[Candidate]:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if match is None:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    found: list[Candidate] = []
    for item in payload:
        candidate = _candidate(item)
        if candidate is not None:
            found.append(candidate)
    return found


def _candidate(item: Any) -> Candidate | None:
    if not isinstance(item, dict):
        return None
    text = str(item.get("text") or "").strip()
    kind = str(item.get("kind") or "").strip()
    subject = str(item.get("subject") or "").strip()
    try:
        confidence = float(item.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    if not text or not kind:
        return None
    return Candidate(text=text, kind=kind, subject=subject, confidence=confidence)
