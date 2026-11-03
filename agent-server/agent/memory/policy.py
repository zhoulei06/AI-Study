"""记忆能不能进库：打分，并挡住密钥和空话。"""

from __future__ import annotations

import re

from .types import Candidate, Review

_KINDS = frozenset({"fact", "preference", "episode"})
_SECRET = re.compile(
    r"(sk-[a-z0-9]{8,}|api[_-]?key|password|secret|token\s*[:=]|密码\s*[:：]|密钥)",
    re.IGNORECASE,
)


class PolicyEngine:
    def __init__(self, *, min_score: float = 0.6) -> None:
        self.min_score = min_score

    def review(self, candidate: Candidate) -> Review:
        text = candidate.text.strip()
        if not text:
            return Review(False, 0.0, "空内容")
        if candidate.kind not in _KINDS:
            return Review(False, 0.0, "类型不允许")
        if _SECRET.search(text) or _SECRET.search(candidate.subject):
            return Review(False, 0.0, "含有密钥或口令")
        if len(text) > 200:
            return Review(False, 0.0, "太长，不像一条记忆")
        score = max(0.0, min(1.0, candidate.confidence))
        if candidate.kind == "episode":
            score *= 0.9
        if score < self.min_score:
            return Review(False, score, "置信度不够")
        return Review(True, score, "通过")
