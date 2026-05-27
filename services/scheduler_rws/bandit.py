"""Tiny epsilon-greedy threshold adapter for RWS.

The bandit adjusts only theta. It never changes the feature weights, which
keeps the scheduler explanation stable and easy to roll back.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(slots=True)
class RWSBandit:
    theta: float = 0.5
    epsilon: float = 0.1
    learning_rate: float = 0.05
    min_theta: float = 0.35
    max_theta: float = 0.65
    frozen: bool = False
    observations: int = 0
    last_reward: float = 0.0
    history: list[dict[str, float]] = field(default_factory=list)

    def current_theta(self) -> float:
        if self.frozen:
            return _clamp(self.theta, self.min_theta, self.max_theta)
        theta = _clamp(self.theta, self.min_theta, self.max_theta)
        if random.random() < max(0.0, min(1.0, self.epsilon)):
            return _clamp(theta + random.choice((-0.05, 0.05)), self.min_theta, self.max_theta)
        return theta

    def observe(self, *, decision: bool, reward: float) -> float:
        reward = _clamp(reward, -1.0, 1.0)
        self.observations += 1
        self.last_reward = reward
        if not self.frozen:
            direction = -1.0 if decision and reward < 0 else 1.0 if (not decision and reward < 0) else 0.0
            self.theta = _clamp(self.theta + direction * self.learning_rate, self.min_theta, self.max_theta)
        self.history.append({"theta": self.theta, "reward": reward, "decision": 1.0 if decision else 0.0})
        if len(self.history) > 100:
            self.history = self.history[-100:]
        return self.theta


def _clamp(value: object, low: float, high: float) -> float:
    try:
        raw = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raw = low
    return max(low, min(high, raw))
