"""Regional return policies: IN, UK and US (Markdown + PDF) and an outdated UK PDF.

Failure design:
- chunking: each return-window table is ~1,800 characters and starts after a
  ~600-character intro, so a 1,000-character splitter separates the header and
  sealed rows from the opened rows.
- query_region: the three regions use near-identical wording with different numbers.
- stale: the 2025 UK policy (opened audio 14 days) sits beside the 2026 one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from generators.common import (
    Block,
    Bullets,
    Heading,
    Para,
    TableBlock,
    make_meta,
    write_markdown,
    write_pdf,
)
from generators.facts import (
    REFUND_WORKING_DAYS,
    REGION_NAMES,
    RETURNS_IN,
    RETURNS_UK_2025,
    RETURNS_UK_CURRENT,
    RETURNS_US,
    STORE_NAME,
    SUPPORT_EMAIL,
    ReturnRules,
)
from rag_assistant.schemas import DocMetadata

SUBDIR = "policies"


@dataclass(frozen=True)
class Category:
    """A product category row in the return-window table."""

    name: str
    examples: str
    is_audio: bool = False


CATEGORIES = (
    Category("Audio", "Headphones, earbuds and speakers", is_audio=True),
    Category("TVs", "Televisions and displays"),
    Category("Laptops", "Laptops and computers"),
    Category("Networking", "Routers and mesh systems"),
    Category("Wearables", "Smartwatches and fitness bands"),
    Category("Large appliances", "Washing machines"),
    Category("Power and charging", "Power banks, chargers and cables"),
    Category("Accessories", "Cases, stands and other accessories"),
)


def _sealed_note(rules: ReturnRules) -> str:
    """Conditions column for a sealed item."""
    if rules.original_packaging_required:
        return "Unopened with seal intact; original packaging required"
    return "Unopened with seal intact"


def _opened_note(rules: ReturnRules, category: Category) -> str:
    """Conditions column for an opened item."""
    if rules.original_packaging_required:
        return "Original packaging and all accessories required"
    if category.is_audio:
        return "All ear tips, cables and accessories included"
    if rules.opened_other_fee:
        return rules.opened_other_fee
    return "All accessories and manuals included"


def return_table(rules: ReturnRules) -> TableBlock:
    """Build the return-window table: every sealed row, then every opened row."""
    sealed = tuple(
        (c.name, c.examples, "Sealed", f"{rules.sealed_days} days", _sealed_note(rules))
        for c in CATEGORIES
    )
    opened = tuple(
        (
            c.name,
            c.examples,
            "Opened",
            f"{rules.opened_audio_days if c.is_audio else rules.opened_other_days} days",
            _opened_note(rules, c),
        )
        for c in CATEGORIES
    )
    header = ("Product category", "Examples", "Condition", "Return window", "Conditions and fees")
    return TableBlock(header, sealed + opened)


def _intro(region_name: str) -> Para:
    """Opening paragraph (~600 characters) placed before the table."""
    return Para(
        f"This policy explains how to return items bought from {STORE_NAME} in {region_name}. "
        f"It applies to orders delivered to an address in {region_name} and placed on or after "
        "the effective date shown above. Return windows start on the day your order is "
        "delivered, and the deadline is the last calendar day of the window. To be accepted, a "
        f"return must be started from your {STORE_NAME} account before the window ends. Faulty "
        f"items are handled separately under the {STORE_NAME} Warranty Policy and are not "
        "limited by the windows below. Items bought from marketplace sellers follow the "
        "seller's own returns terms, which are shown on the product listing."
    )


def _condition_text(rules: ReturnRules) -> str:
    """Paragraph on the state returned items must be in."""
    text = (
        "Items must be returned with all accessories, cables, manuals and free gifts that came "
        "with them. We inspect every return when it reaches our returns centre."
    )
    if rules.original_packaging_required:
        text += " In this region every return must be in its original packaging."
    return text


def _refund_text(rules: ReturnRules) -> str:
    """Paragraph on how and when refunds are paid."""
    text = (
        "Once your return has passed inspection, we refund the original payment method within "
        f"{REFUND_WORKING_DAYS} working days. Your bank or card provider may take longer to show "
        "the credit."
    )
    if rules.opened_other_fee:
        text += " Where a restocking fee applies, it is deducted from the refund amount."
    return text


def policy_blocks(rules: ReturnRules, effective_label: str) -> list[Block]:
    """All blocks for one regional returns policy."""
    region_name = REGION_NAMES[rules.region]
    return [
        Heading(f"{STORE_NAME} Returns Policy - {region_name.removeprefix('the ')}", 1),
        Para(f"Effective date: {effective_label}"),
        _intro(region_name),
        Heading("Return windows"),
        Para("The table below lists the return window for each product category and condition."),
        return_table(rules),
        Heading("How to start a return"),
        Bullets(
            (
                f"Sign in to your {STORE_NAME} account and open Orders.",
                "Choose the item and select Start a return.",
                "Pick a reason and tell us whether the item has been opened.",
                "Print the prepaid label, or book a collection for large appliances.",
                "Pack the item securely and hand it to the courier.",
            )
        ),
        Heading("Condition of returned items"),
        Para(_condition_text(rules)),
        Heading("Refunds"),
        Para(_refund_text(rules)),
        Heading("Items that cannot be returned"),
        Bullets(
            (
                "Gift cards.",
                "Software and digital downloads once they have been accessed.",
                "Items damaged through misuse or accidents after delivery.",
            )
        ),
        Heading("Faulty or damaged items"),
        Para(
            "If your item arrived damaged or develops a fault, report it from the Orders page. "
            f"Faults are covered by the {STORE_NAME} Warranty Policy."
        ),
        Heading("Contact us"),
        Para(f"Questions about this policy? Email {SUPPORT_EMAIL} or use Help in your account."),
    ]


@dataclass(frozen=True)
class PolicyVersion:
    """One returns policy to generate."""

    doc_id: str
    rules: ReturnRules
    effective_date: str
    effective_label: str
    formats: tuple[str, ...]


VERSIONS = (
    PolicyVersion("returns-in-2026", RETURNS_IN, "2026-04-01", "1 April 2026", ("md", "pdf")),
    PolicyVersion(
        "returns-uk-2026", RETURNS_UK_CURRENT, "2026-04-01", "1 April 2026", ("md", "pdf")
    ),
    PolicyVersion("returns-us-2026", RETURNS_US, "2026-04-01", "1 April 2026", ("md", "pdf")),
    PolicyVersion("returns-uk-2025", RETURNS_UK_2025, "2025-01-01", "1 January 2025", ("pdf",)),
)


def _title(rules: ReturnRules) -> str:
    """Document title shared by every version for a region."""
    return f"{STORE_NAME} Returns Policy ({rules.region})"


def build(out_dir: Path) -> list[DocMetadata]:
    """Write every returns policy and return their metadata."""
    docs: list[DocMetadata] = []
    for version in VERSIONS:
        blocks = policy_blocks(version.rules, version.effective_label)
        common = {
            "title": _title(version.rules),
            "doc_type": "policy",
            "region": version.rules.region,
            "audience": "public",
            "effective_date": version.effective_date,
            "subdir": SUBDIR,
        }
        if "md" in version.formats:
            meta = make_meta(doc_id=version.doc_id, ext="md", **common)
            docs.append(write_markdown(out_dir, meta, blocks))
        if "pdf" in version.formats:
            meta = make_meta(doc_id=f"{version.doc_id}-print", ext="pdf", **common)
            docs.append(write_pdf(out_dir, meta, blocks))
    return docs
