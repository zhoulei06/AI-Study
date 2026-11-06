"""Validated plans and durable execution state; no provider-specific types."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,39}$")
    task: str = Field(min_length=1, max_length=800)
    depends_on: list[str] = Field(default_factory=list, max_length=6)
    tools: list[str] = Field(default_factory=list, max_length=20)
    success_criteria: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        min_length=1, max_length=5
    )


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["direct", "planned"]
    goal: str = Field(min_length=1, max_length=1600)
    steps: list[Step] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_dependencies(self):
        if (self.mode == "direct") != (not self.steps):
            raise ValueError("direct requires no steps; planned requires steps")
        seen = set()
        for step in self.steps:
            if step.id in seen or not set(step.depends_on) <= seen:
                raise ValueError("Steps must have unique ids in dependency order")
            seen.add(step.id)
        return self


class Verification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    reason: str = Field(max_length=1200)


class StepState(Step):
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    result: str = ""
    calls: int = 0
    start_message_count: int = 0


class TaskState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    user_message_id: str
    goal: str
    phase: Literal["direct", "executing", "finalizing", "completed", "failed"]
    version: int = 1
    replans: int = 0
    steps: list[StepState] = Field(default_factory=list)
    current_step_id: str | None = None
    issue: str = ""

    def current_step(self) -> StepState | None:
        return next((s for s in self.steps if s.id == self.current_step_id), None)

    def public(self) -> dict:
        # Results, prompts and tool arguments never enter activity snapshots.
        return {
            "id": self.id,
            "goal": self.goal,
            "phase": self.phase,
            "version": self.version,
            "replans": self.replans,
            "steps": [
                {"id": s.id, "task": s.task, "status": s.status} for s in self.steps
            ],
        }
