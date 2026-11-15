"""Business rules and the date arithmetic the agent's tools rely on (Session 10).

The rules live in ``configs/business_rules.yaml`` and are checked against the generated
policy text by a test. Everything here is a pure function of dates and rules, so the
model never does date arithmetic itself: the tools hand it computed results.

Conventions:

- Warranty and return windows start on the **delivery** date.
- A return window of N days ends on delivery + N days, and that last day is included.
- Warranty of N months ends on the same day of the month N months later; when that day
  doesn't exist (29 February in a non-leap year, 31 April), the month's last day.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from rag_assistant.config import CONFIGS_DIR

RULES_PATH = CONFIGS_DIR / "business_rules.yaml"
DEFAULT = "default"


class ProductWarranty(BaseModel):
    """A product with its own warranty terms (the VisionMax TV)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    panel_defects_months: int = Field(gt=0)
    other_parts_months: int = Field(gt=0)


class WarrantyRules(BaseModel):
    """Warranty periods."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    starts: str
    standard_months: int = Field(gt=0)
    products: dict[str, ProductWarranty] = {}
    marketplace_codes: list[str] = []


class RegionReturns(BaseModel):
    """One region's return windows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sealed_days: int = Field(gt=0)
    opened_days: dict[str, int]
    opened_fee_percent: dict[str, int]
    original_packaging_required: bool

    def opened_window(self, category: str) -> tuple[int, int]:
        """``(days, fee percent)`` for an opened item of ``category``."""
        days = self.opened_days.get(category, self.opened_days[DEFAULT])
        fee = self.opened_fee_percent.get(category, self.opened_fee_percent[DEFAULT])
        return days, fee


class ReturnRules(BaseModel):
    """Return windows by region."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    starts: str
    refund_working_days: int = Field(gt=0)
    regions: dict[str, RegionReturns]


class BusinessRules(BaseModel):
    """Everything in ``configs/business_rules.yaml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_date: date
    warranty: WarrantyRules
    returns: ReturnRules


def load_business_rules(path: Path = RULES_PATH) -> BusinessRules:
    """Load and validate the business rules."""
    return BusinessRules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def add_months(start: date, months: int) -> date:
    """``start`` plus ``months``, clamped to the month's last day (29 Feb + 12 -> 28 Feb)."""
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class WarrantyPeriod:
    """One warranty period for an order line."""

    covers: str  # "all parts", "panel defects", "other parts"
    months: int
    ends: date
    covered: bool  # the reference date is on or before ``ends``


def warranty_periods(
    product_code: str, delivered: date, rules: BusinessRules, today: date
) -> list[WarrantyPeriod]:
    """The VoltCart warranty periods for a delivered product (empty for marketplace items)."""
    w = rules.warranty
    if product_code in w.marketplace_codes:
        return []
    special = w.products.get(product_code)
    terms = (
        [
            ("panel defects", special.panel_defects_months),
            ("other parts", special.other_parts_months),
        ]
        if special
        else [("all parts", w.standard_months)]
    )
    return [
        WarrantyPeriod(covers, months, end := add_months(delivered, months), today <= end)
        for covers, months in terms
    ]


@dataclass(frozen=True)
class ReturnWindow:
    """Whether a delivered order line can still be returned, and on what terms."""

    condition: str  # "opened" or "sealed"
    window_days: int
    last_day: date
    days_since_delivery: int
    eligible: bool
    fee_percent: int
    original_packaging_required: bool


def return_window(
    region: str,
    category: str,
    opened: bool,
    delivered: date,
    rules: BusinessRules,
    today: date,
) -> ReturnWindow:
    """The return window for a delivered line; the last day (delivery + N days) counts."""
    terms = rules.returns.regions[region]
    if opened:
        days, fee = terms.opened_window(category)
    else:
        days, fee = terms.sealed_days, 0
    last_day = delivered + timedelta(days=days)
    return ReturnWindow(
        condition="opened" if opened else "sealed",
        window_days=days,
        last_day=last_day,
        days_since_delivery=(today - delivered).days,
        eligible=delivered <= today <= last_day,
        fee_percent=fee,
        original_packaging_required=terms.original_packaging_required,
    )
