"""Threshold adapter for RWS (P4).

The bandit adjusts only theta (the fire threshold), never feature weights — so
the scheduler explanation stays stable and rollback is trivial.

Two algorithms:
- ``epsilon`` (legacy): epsilon-greedy ±0.05 jitter on theta.
- ``thompson`` (P4 default): Beta-Bernoulli Thompson sampling. Reward in
  [-1,1] is mapped to a Bernoulli success (reward>0) and updates a Beta
  posterior over "fire is worth it"; theta = 1 - sampled_p, clamped. Robust to
  sparse/delayed feedback (Agrawal&Goyal 2012). Counts decay over time
  (non-stationary group mood) and the adapter stays frozen until ``min_obs``
  observations accrue (avoids early noise-driven drift).
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
    # P4 additions
    algo: str = "thompson"
    min_obs: int = 50  # stay frozen until this many observations
    decay_per_obs: float = 0.99  # multiplicative decay of Beta counts per observe
    _alpha: float = 2.0  # Beta(α,β) success prior
    _beta: float = 2.0

    def current_theta(self) -> float:
        base = _clamp(self.theta, self.min_theta, self.max_theta)
        if self.frozen or self.observations < self.min_obs:
            return base
        if self.algo == "thompson":
            # sample p ~ Beta(α,β) = P(fire worthwhile); higher p → lower theta
            p = random.betavariate(max(0.01, self._alpha), max(0.01, self._beta))
            return _clamp(1.0 - p, self.min_theta, self.max_theta)
        # epsilon-greedy legacy
        if random.random() < max(0.0, min(1.0, self.epsilon)):
            return _clamp(base + random.choice((-0.05, 0.05)), self.min_theta, self.max_theta)
        return base

    def observe(self, *, decision: bool, reward: float) -> float:
        reward = _clamp(reward, -1.0, 1.0)
        self.observations += 1
        self.last_reward = reward
        if not self.frozen:
            if self.algo == "thompson":
                self._observe_thompson(decision=decision, reward=reward)
            else:
                self._observe_epsilon(decision=decision, reward=reward)
        self.history.append({"theta": self.theta, "reward": reward, "decision": 1.0 if decision else 0.0})
        if len(self.history) > 100:
            self.history = self.history[-100:]
        return self.theta

    def _observe_epsilon(self, *, decision: bool, reward: float) -> None:
        direction = -1.0 if decision and reward < 0 else 1.0 if (not decision and reward < 0) else 0.0
        self.theta = _clamp(self.theta + direction * self.learning_rate, self.min_theta, self.max_theta)

    def _observe_thompson(self, *, decision: bool, reward: float) -> None:
        # decay old evidence (non-stationary)
        d = max(0.0, min(1.0, self.decay_per_obs))
        self._alpha = max(1.0, self._alpha * d)
        self._beta = max(1.0, self._beta * d)
        # Only a *fired* decision's reward informs "was firing worthwhile".
        # A positive reward → success (firing paid off); negative → failure.
        # Skips don't directly update the fire-posterior (they're the absence
        # of a fire), but a cold skip (reward<0) is weak evidence we should
        # fire more → nudge success.
        if decision:
            if reward > 0:
                self._alpha += reward
            else:
                self._beta += -reward
        elif reward < 0:  # skipped and it went cold → should have fired
            self._alpha += -reward * 0.5
        # reflect posterior mean into theta for observability/state readout
        mean_p = self._alpha / (self._alpha + self._beta)
        self.theta = _clamp(1.0 - mean_p, self.min_theta, self.max_theta)


def _clamp(value: object, low: float, high: float) -> float:
    try:
        raw = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raw = low
    return max(low, min(high, raw))
