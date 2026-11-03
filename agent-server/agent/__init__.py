from .limits import AgentLimits

__all__ = ["Agent", "AgentLimits", "AgentResult"]


def __getattr__(name):
    if name in {"Agent", "AgentResult"}:
        from .loop import Agent, AgentResult
        globals().update(Agent=Agent, AgentResult=AgentResult)
        return globals()[name]
    raise AttributeError(name)
