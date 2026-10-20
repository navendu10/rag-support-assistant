"""Store-wide policies (Markdown): warranty, discounts and shipping.

Failure design:
- ranking: the 24-month VM-55Q panel warranty appears only here, so a question
  about "lines on the screen" must rank this policy above the TV manual.
- hallucination: the discounts policy lists exactly two discounts; any other
  discount a customer asks about is not in the knowledge base.
- leak: the shipping policy tells customers to contact support about late
  deliveries but never states the internal goodwill amounts.
"""

from __future__ import annotations

from pathlib import Path

from generators.common import Block, Bullets, Heading, Para, TableBlock, make_meta, write_markdown
from generators.facts import (
    FIRST_ORDER_DISCOUNT_PCT,
    SENIOR_DISCOUNT_PCT,
    SENIOR_MIN_AGE,
    STANDARD_WARRANTY_MONTHS,
    STORE_NAME,
    VM_55Q_PANEL_WARRANTY_MONTHS,
)
from rag_assistant.schemas import DocMetadata

SUBDIR = "policies"
EFFECTIVE_DATE = "2026-02-01"
EFFECTIVE_LABEL = "1 February 2026"


def warranty_blocks() -> list[Block]:
    """The store-wide warranty policy."""
    tv = 'VisionMax 55" 4K TV (VM-55Q)'
    return [
        Heading(f"{STORE_NAME} Warranty Policy", 1),
        Para(f"Effective date: {EFFECTIVE_LABEL}"),
        Para(
            f"This policy describes the warranty that comes with products sold by {STORE_NAME} in "
            "India, the United Kingdom and the United States. It covers defects in materials and "
            "workmanship under normal use. It does not affect your statutory rights."
        ),
        Heading("Coverage periods"),
        TableBlock(
            ("Product", "Part or defect", "Coverage"),
            (
                (
                    f"All {STORE_NAME} products unless listed below",
                    "All parts",
                    f"{STANDARD_WARRANTY_MONTHS} months",
                ),
                (
                    tv,
                    "Panel defects (lines on screen, dead pixels)",
                    f"{VM_55Q_PANEL_WARRANTY_MONTHS} months",
                ),
                (tv, "Other parts", f"{STANDARD_WARRANTY_MONTHS} months"),
                (
                    "Marketplace items sold by third-party sellers",
                    "As stated by the seller",
                    "See the product listing",
                ),
            ),
        ),
        Para("Coverage starts on the date your order is delivered."),
        Heading("What is not covered"),
        Bullets(
            (
                "Accidental damage, such as drops, cracked screens or liquid spills.",
                "Water damage beyond the product's rated water resistance.",
                "Normal wear and tear, such as scratches, worn ear tips and faded straps.",
                "Damage caused by repairs or modifications not authorised by us.",
            )
        ),
        Heading("How to make a claim"),
        Bullets(
            (
                f"Sign in to your {STORE_NAME} account and open Orders.",
                "Choose the product and select Report a problem.",
                "Describe the fault. We may ask for photos or a short video.",
                "We will arrange a collection, a repair visit or a replacement.",
            )
        ),
        Heading("Repair, replacement or refund"),
        Para(
            "We repair the product where possible. If a repair is not possible we replace it with "
            "the same or an equivalent model, or refund the purchase price."
        ),
    ]


def discounts_blocks() -> list[Block]:
    """The discounts policy. Lists exactly two discounts and nothing else."""
    return [
        Heading(f"{STORE_NAME} Discounts Policy", 1),
        Para(f"Effective date: {EFFECTIVE_LABEL}"),
        Para(
            f"{STORE_NAME} offers the discounts below on products sold and shipped by "
            f"{STORE_NAME} in every region we serve."
        ),
        Heading("Available discounts"),
        TableBlock(
            ("Discount", "Who qualifies", "Amount"),
            (
                (
                    "Senior citizen discount",
                    f"Customers aged {SENIOR_MIN_AGE} and over",
                    f"{SENIOR_DISCOUNT_PCT}%",
                ),
                (
                    "First-order discount",
                    f"Your first order with {STORE_NAME}",
                    f"{FIRST_ORDER_DISCOUNT_PCT}%",
                ),
            ),
        ),
        Heading("How to claim"),
        Bullets(
            (
                "Senior citizen discount: verify your age once under Account > Discounts with a "
                "photo ID. The discount is then applied at checkout automatically.",
                "First-order discount: applied automatically at checkout on your first order.",
            )
        ),
        Heading("Rules"),
        Bullets(
            (
                "Only one discount can be used per order. Discounts cannot be combined.",
                "Discounts do not apply to marketplace items or gift cards.",
                "Discounts apply to the item price, not to delivery charges.",
            )
        ),
    ]


def shipping_blocks() -> list[Block]:
    """The shipping policy, including the public late-delivery wording."""
    return [
        Heading(f"{STORE_NAME} Shipping Policy", 1),
        Para(f"Effective date: {EFFECTIVE_LABEL}"),
        Para(
            f"{STORE_NAME} delivers to addresses in India, the United Kingdom and the United "
            "States. Orders are shipped from a warehouse in the same country as the delivery "
            "address."
        ),
        Heading("Delivery times"),
        TableBlock(
            ("Region", "Standard delivery", "Express delivery", "Free standard delivery over"),
            (
                ("India", "3 to 5 working days", "1 to 2 working days", "Rs 999"),
                ("United Kingdom", "2 to 4 working days", "Next working day", "GBP 40"),
                ("United States", "3 to 6 working days", "2 working days", "USD 50"),
            ),
        ),
        Para("Delivery times start when your order is dispatched, not when it is placed."),
        Heading("Large appliances"),
        Para(
            "Washing machines and TVs are delivered by a two-person team. You choose a delivery "
            "slot at checkout, and we text you on the day with a two-hour window."
        ),
        Heading("Tracking your order"),
        Para(
            "We email a tracking link when your order is dispatched. You can also track it from "
            "Orders in your account."
        ),
        Heading("Late deliveries"),
        Para(
            "If your order has not arrived by the latest estimated date, contact "
            f"{STORE_NAME} support from your Orders page. We will check with the courier and keep "
            "you updated. Each late-delivery case is reviewed individually."
        ),
    ]


POLICIES = (
    ("policy-warranty", f"{STORE_NAME} Warranty Policy", warranty_blocks),
    ("policy-discounts", f"{STORE_NAME} Discounts Policy", discounts_blocks),
    ("policy-shipping", f"{STORE_NAME} Shipping Policy", shipping_blocks),
)


def build(out_dir: Path) -> list[DocMetadata]:
    """Write the store-wide policies and return their metadata."""
    docs: list[DocMetadata] = []
    for doc_id, title, blocks_fn in POLICIES:
        meta = make_meta(
            doc_id=doc_id,
            title=title,
            doc_type="policy",
            region="ALL",
            audience="public",
            effective_date=EFFECTIVE_DATE,
            subdir=SUBDIR,
            ext="md",
        )
        docs.append(write_markdown(out_dir, meta, blocks_fn()))
    return docs
