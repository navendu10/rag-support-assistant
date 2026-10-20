"""Single source of truth for every fact the knowledge base must state.

Templates read these constants instead of repeating numbers, so a fact can
never drift between documents. Tests check the generated text independently.
"""

from __future__ import annotations

from dataclasses import dataclass

STORE_NAME = "VoltCart"
SUPPORT_EMAIL = "support@voltcart.example"
CANARY = "INTERNAL-CANARY-7731"

REGION_NAMES = {"IN": "India", "UK": "the United Kingdom", "US": "the United States"}


@dataclass(frozen=True)
class ReturnRules:
    """Return windows (in days) for one regional policy version."""

    region: str
    sealed_days: int
    opened_audio_days: int
    opened_other_days: int
    opened_other_fee: str | None
    original_packaging_required: bool


RETURNS_UK_CURRENT = ReturnRules("UK", 30, 15, 30, None, False)
RETURNS_UK_2025 = ReturnRules("UK", 30, 14, 30, None, False)
RETURNS_IN = ReturnRules("IN", 7, 7, 7, None, True)
RETURNS_US = ReturnRules("US", 30, 15, 15, "10% restocking fee", False)

REFUND_WORKING_DAYS = 5

# Routers: deliberately similar products with different reset procedures.
XR_2200_RESET_SECONDS = 10
XR_2200_RESET_LED = "blinks amber"
XR_2000_RESET_SECONDS = 30
XR_2000_RESET_LED = "turns solid red"

# WashPro WM-700 error codes that are easy to confuse.
WM_700_E47 = "Drain pump blocked"
WM_700_E74 = "Door lock fault"

# Warranty.
STANDARD_WARRANTY_MONTHS = 12
VM_55Q_PANEL_WARRANTY_MONTHS = 24
QUICKCHARGE_WARRANTY_MONTHS = 6

# AeroBuds Pro battery.
AB_PRO_BATTERY_HOURS = 8
AB_PRO_BATTERY_WITH_CASE_HOURS = 24

# Discounts. There is deliberately no third discount.
SENIOR_MIN_AGE = 60
SENIOR_DISCOUNT_PCT = 5
FIRST_ORDER_DISCOUNT_PCT = 10

# Internal only: goodwill credit caps for late delivery.
GOODWILL_LATE_DELIVERY = {"IN": "Rs 2,000", "UK": "GBP 20", "US": "USD 25"}
