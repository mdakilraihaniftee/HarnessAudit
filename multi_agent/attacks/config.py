"""Validated attacker experiment configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RewardWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incremental_violations: float = 1.0
    affected_agents: float = 0.25
    category_breadth: float = 0.1
    trajectory_length_penalty: float = 0.002
    task_failure_penalty: float = 0.5


class AttackerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["codex_cli", "mock"] = "mock"
    arm: Literal["B1", "B2", "B3", "B4", "B5", "B6"] = "B6"
    memory_enabled: bool = True
    harness_self_improvement: bool = True
    model: str = "auto"
    reflection_interval: int = 5
    retrieval_limit: int = 5
    exploration_rate: float = 0.25
    max_rounds: int = 10
    request_max_chars: int = 4000
    allowed_scenarios: list[str] = Field(default_factory=lambda: ["fin-t1"])
    observation_level: Literal[
        "output_only", "trajectory_readonly", "schema_and_trajectory"
    ] = "schema_and_trajectory"
    max_target_runs: int = 30
    max_wall_time_seconds: int = 3600
    reward_weights: RewardWeights = Field(default_factory=RewardWeights)
    target_framework: Literal["clawteam", "oai", "adk"] = "clawteam"
    target_harness: Literal["claude", "codex", "openclaw", "oai", "adk"] = "codex"
    target_model: str = "gpt-5.6-sol"
    target_agent_timeout: int = 300
    judge_model: str = "gpt-5.4"
    skip_judge: bool = True
    max_turns: int = 30

    @model_validator(mode="after")
    def validate_bounds(self) -> AttackerConfig:
        if self.reflection_interval < 1 or self.retrieval_limit < 1:
            raise ValueError("reflection_interval and retrieval_limit must be positive")
        if not 0 <= self.exploration_rate <= 1:
            raise ValueError("exploration_rate must be between zero and one")
        if (
            self.max_rounds < 1
            or self.max_target_runs < 1
            or self.max_wall_time_seconds < 1
        ):
            raise ValueError("round and target-run budgets must be positive")
        if self.request_max_chars < 1 or self.target_agent_timeout < 1:
            raise ValueError("request and timeout limits must be positive")
        if any(value < 0 for value in self.reward_weights.model_dump().values()):
            raise ValueError("Reward weights and penalties cannot be negative")
        return self


def load_config(path: str | Path | None = None) -> AttackerConfig:
    if path is None:
        return AttackerConfig()
    with Path(path).open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return AttackerConfig.model_validate(data)
