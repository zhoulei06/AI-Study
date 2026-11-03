"""短期记忆、长期记忆，以及提取、审核、组装。"""

from importlib import import_module

__all__ = [
    "ContextBuilder",
    "MemoryExtractor",
    "MemoryRepository",
    "PolicyEngine",
    "MemoryResolver",
    "SessionState",
    "ShortTermMemory",
    "ShortTermMiddleware",
    "PostgresSessions",
    "PostgresMemory",
]

_MODULES = {
    "ContextBuilder": "context_builder", "MemoryExtractor": "extractor",
    "MemoryRepository": "long_term", "PostgresMemory": "long_term",
    "PolicyEngine": "policy", "MemoryResolver": "resolver",
    "SessionState": "short_term", "ShortTermMemory": "short_term",
    "ShortTermMiddleware": "short_term", "PostgresSessions": "short_term",
}


def __getattr__(name):
    if name in _MODULES:
        value = getattr(import_module(f".{_MODULES[name]}", __name__), name)
        globals()[name] = value
        return value
    raise AttributeError(name)
