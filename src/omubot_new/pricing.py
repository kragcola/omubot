"""Operator-entered model prices and immutable per-intent cost evidence."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import Field

from .types import ModelUsage, StrictModel

ModelTask = Literal["reply", "thinker", "vision", "schedule", "dream", "memory", "journal", "unknown"]


class ModelPrice(StrictModel):
    # Provider is the exact policy destination/profile fingerprint, not a
    # guessed vendor name or a model-name fallback.
    provider: str = Field(min_length=1, max_length=256)
    model: str = Field(min_length=1, max_length=200)
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    input_per_million: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    output_per_million: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    cached_input_per_million: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)
    cache_write_per_million: float | None = Field(default=None, ge=0, le=1_000_000, allow_inf_nan=False)


@dataclass(frozen=True, slots=True)
class FrozenModelPrice:
    provider: str
    model: str
    task: ModelTask
    price: ModelPrice | None

    def evidence(self, usage: ModelUsage | None = None) -> dict[str, object]:
        evidence: dict[str, object] = {
            "provider": self.provider, "model": self.model, "bound_task": self.task,
            "price": self.price.model_dump(mode="json") if self.price is not None else None,
            "state": "unavailable", "estimated_cost": None,
        }
        if self.price is None:
            evidence["reason"] = "unknown_price"
            return evidence
        rates = (
            self.price.input_per_million, self.price.output_per_million,
            self.price.cached_input_per_million, self.price.cache_write_per_million,
        )
        if any(rate is None for rate in rates):
            evidence["reason"] = "unknown_rate"
            return evidence
        if usage is None:
            evidence["reason"] = "unknown_usage"
            return evidence
        uncached = usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens
        if uncached < 0:
            evidence["reason"] = "inconsistent_usage"
            return evidence
        tokens = (uncached, usage.output_tokens, usage.cached_input_tokens, usage.cache_write_tokens)
        total = Decimal(0)
        for rate, count in zip(rates, tokens, strict=True):
            assert rate is not None
            total += Decimal(str(rate)) * count
        # Decimal strings preserve small costs without a float/rounding estimate.
        evidence.update(state="estimated", estimated_cost=format(total / Decimal(1_000_000), "f"))
        return evidence


def freeze_model_price(
    prices: Collection[ModelPrice], *, provider: str, model: str, task: ModelTask,
) -> FrozenModelPrice:
    selected = next((price for price in prices if (price.provider, price.model) == (provider, model)), None)
    return FrozenModelPrice(provider=provider, model=model, task=task, price=selected)
