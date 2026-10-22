"""同一主题更新，几乎相同的文本跳过，其余才新增。"""

from __future__ import annotations

from .long_term import MemoryRepository
from .types import Candidate

_DUPLICATE = 0.9


class MemoryResolver:
    def apply(self, repository: MemoryRepository, candidate: Candidate, score: float) -> str:
        existing = repository.find_subject(candidate.subject)
        if existing is not None:
            if _same(existing.text, candidate.text):
                return "skip"
            repository.save(candidate, score, key=existing.key)
            return "update"

        for item in repository.search(candidate.text, limit=3):
            if item.similarity >= _DUPLICATE and _same(item.text, candidate.text):
                return "skip"
        repository.save(candidate, score)
        return "insert"


def _same(left: str, right: str) -> bool:
    return " ".join(left.split()) == " ".join(right.split())
