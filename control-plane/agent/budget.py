"""F-17 Budget meter: explicit pricing config, provider usage accounting, and limits."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

from control_plane.agent.llm import LlmUsage


class BudgetAccountingError(Exception):
    """Raised when USD spend cannot be computed reliably for a configured limit."""


@dataclass(frozen=True)
class ModelPricing:
    """Explicit per-million-token USD pricing for a single model."""

    input_per_1m_usd: float
    output_per_1m_usd: float

    def compute_cost(self, usage: LlmUsage) -> float:
        if usage.input_tokens < 0 or usage.output_tokens < 0:
            raise BudgetAccountingError("Token usage counts must be non-negative.")
        return round(
            (usage.input_tokens / 1_000_000.0) * self.input_per_1m_usd
            + (usage.output_tokens / 1_000_000.0) * self.output_per_1m_usd,
            8,
        )


def load_pricing_from_env(raw_json: str | None = None) -> dict[str, ModelPricing]:
    """Parse explicit model pricing from ``LLM_PRICING_JSON``."""
    source = os.environ.get("LLM_PRICING_JSON") if raw_json is None else raw_json
    if source is None or not source.strip():
        return {}
    try:
        parsed = json.loads(source)
    except json.JSONDecodeError as exc:
        raise BudgetAccountingError(
            f"Invalid LLM_PRICING_JSON configuration: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise BudgetAccountingError("LLM_PRICING_JSON must be a JSON object.")

    table: dict[str, ModelPricing] = {}
    for model_name, entry in parsed.items():
        if not isinstance(model_name, str) or not model_name.strip():
            raise BudgetAccountingError(
                "Model name in LLM_PRICING_JSON must be non-empty."
            )
        if not isinstance(entry, dict):
            raise BudgetAccountingError(
                f"Pricing entry for '{model_name}' must be a JSON object."
            )
        in_price = entry.get("input_per_1m_usd")
        out_price = entry.get("output_per_1m_usd")
        if (
            isinstance(in_price, bool)
            or isinstance(out_price, bool)
            or not isinstance(in_price, (int, float))
            or not isinstance(out_price, (int, float))
            or in_price < 0
            or out_price < 0
        ):
            raise BudgetAccountingError(
                f"Pricing for '{model_name}' requires non-negative numeric "
                "'input_per_1m_usd' and 'output_per_1m_usd'."
            )
        table[model_name.strip()] = ModelPricing(
            input_per_1m_usd=float(in_price),
            output_per_1m_usd=float(out_price),
        )
    return table


class BudgetMeter:
    """Tracks tool-call and USD spend for one session without fake 0.0 accounting."""

    def __init__(
        self,
        *,
        calls_max: int,
        usd_max: float,
        pricing: Mapping[str, ModelPricing] | None = None,
        require_usd_accounting: bool = False,
        initial_calls_used: int = 0,
        initial_usd_used: float | None = None,
    ) -> None:
        self.calls_max = calls_max
        self.usd_max = usd_max
        self.pricing = dict(pricing) if pricing is not None else {}
        self.require_usd_accounting = require_usd_accounting
        self.calls_used = initial_calls_used
        self.usd_used: float | None = initial_usd_used

    def record_llm_usage(self, usage: LlmUsage | None) -> float | None:
        """Record provider token usage and return updated ``usd_used`` (or ``None``)."""
        if usage is None or not usage.model:
            if self.require_usd_accounting and self.usd_max > 0:
                raise BudgetAccountingError(
                    "Provider usage or model name is missing; cannot verify USD budget."
                )
            return self.usd_used

        model_pricing = self.pricing.get(usage.model)
        if model_pricing is None:
            if self.require_usd_accounting and self.usd_max > 0:
                raise BudgetAccountingError(
                    f"No explicit pricing configured for model {usage.model!r}; "
                    "cannot verify USD budget."
                )
            return self.usd_used

        turn_cost = model_pricing.compute_cost(usage)
        current = 0.0 if self.usd_used is None else self.usd_used
        self.usd_used = round(current + turn_cost, 8)
        return self.usd_used

    def is_usd_exceeded(self) -> bool:
        return self.usd_used is not None and self.usd_used > self.usd_max
