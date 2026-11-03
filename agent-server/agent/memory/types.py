"""记忆管道里传递的几条记录。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    """提取器给出的一条候选，还没进库。"""

    text: str
    kind: str
    subject: str
    confidence: float


@dataclass(frozen=True)
class Review:
    """策略引擎的结论。allowed 为假时不会写入。"""

    allowed: bool
    score: float
    reason: str


@dataclass(frozen=True)
class MemoryRecord:
    """已经落在 Store 里的一条长期记忆。"""

    key: str
    text: str
    kind: str
    subject: str
    score: float
    similarity: float = 0.0
