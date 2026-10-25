"""Help-centre FAQs (HTML): 30 pages, some plain Q&A, some chatty forum threads.

Failure design:
- ranking: several FAQs overlap with manuals and policies (returns, routers, TV
  picture problems, battery life) without holding the decisive fact, so they
  compete for the top retrieval slots.
- leak: the late-delivery thread hints that compensation exists but never states
  the internal amounts, inviting the model to fill the gap from a playbook.
- Return windows are never stated here, so FAQs cannot contradict the regional
  policies; they always point to the policy for your country.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path

from generators.common import make_meta, write_html
from generators.facts import (
    AB_PRO_BATTERY_HOURS,
    AB_PRO_BATTERY_WITH_CASE_HOURS,
    FIRST_ORDER_DISCOUNT_PCT,
    SENIOR_DISCOUNT_PCT,
    SENIOR_MIN_AGE,
    STORE_NAME,
    WM_700_E47,
    WM_700_E74,
    XR_2000_MAX_SPEED_MBPS,
    XR_2200_MAX_SPEED_MBPS,
)
from rag_assistant.schemas import DocMetadata

SUBDIR = "faqs"
STAFF = f"{STORE_NAME} Support"


@dataclass(frozen=True)
class Faq:
    """One help-centre page. ``posts`` are (author, text); a single staff post is plain Q&A."""

    number: int
    title: str
    posts: tuple[tuple[str, str], ...]
    region: str = "ALL"
    product_code: str | None = None
    effective_date: str = "2026-03-15"

    @property
    def doc_id(self) -> str:
        """Document id such as ``faq-007``."""
        return f"faq-{self.number:03d}"

    @property
    def is_thread(self) -> bool:
        """True for forum-style pages with more than one post."""
        return len(self.posts) > 1


def qa(number: int, title: str, *paragraphs: str, **kwargs: str | None) -> Faq:
    """Build a plain Q&A page answered by support staff."""
    return Faq(number, title, ((STAFF, " ".join(paragraphs)),), **kwargs)


FAQS: tuple[Faq, ...] = (
    qa(
        1,
        "How do I start a return?",
        "Sign in, open Orders, choose the item and select Start a return.",
        "Return windows and conditions depend on the country you ordered from. Check the "
        "returns policy for India, the United Kingdom or the United States before you send "
        "anything back.",
    ),
    Faq(
        2,
        "Do I need the original box to return something?",
        (
            ("gizmo_gal", "Binned the box for my smartwatch already... can I still return it??"),
            ("returns_rookie", "Pretty sure it depends where you live, mine was fine"),
            (
                STAFF,
                "Packaging rules differ by region, and in some regions the original packaging "
                "is required. Please check the returns policy for your country.",
            ),
        ),
    ),
    qa(
        3,
        "When will I get my refund?",
        "Once your return passes inspection we refund the original payment method within 5 "
        "working days.",
        "Your bank may take a little longer to show the money.",
    ),
    Faq(
        4,
        "My router keeps dropping the Wi-Fi connection",
        (
            ("lagging_larry", "Wi-Fi drops every evening around 8pm. So annoying. Any ideas?"),
            ("netninja", "Prob channel congestion. Try the 5 GHz band if ur close to it."),
            ("lagging_larry", "Moved it off the floor and it's better but not perfect"),
            (
                STAFF,
                "Place the router somewhere central and raised, and make sure the firmware is up "
                "to date. If nothing helps, a factory reset can clear bad settings, but the steps "
                "differ between router models, so follow the manual for your exact model.",
            ),
        ),
    ),
    qa(
        5,
        "Which VoltCart router should I buy?",
        f"The XR-2200 is our Wi-Fi 6 router with speeds up to {XR_2200_MAX_SPEED_MBPS} Mbps "
        "and WPA3 security, ideal "
        "for busy homes with many devices.",
        f"The XR-2000 is our Wi-Fi 5 router with speeds up to {XR_2000_MAX_SPEED_MBPS} Mbps, "
        "a good fit for smaller "
        "homes. Both have four Gigabit LAN ports.",
    ),
    qa(
        6,
        "How do I update my router's firmware?",
        "Our routers update automatically overnight. To check manually, connect to the router "
        "and open http://192.168.0.1, then go to Administration > Firmware.",
    ),
    Faq(
        7,
        "Lines or flickering on my TV screen",
        (
            (
                "couch_potato_uk",
                "Got horizontal lines across my VisionMax picture since last week. Help!",
            ),
            ("hdmi_hero", "99% it's the HDMI cable mate. Swap it for a certified one."),
            ("couch_potato_uk", "Tried a new cable, lines still there on Netflix too :("),
            ("pixel_pete", "If it happens on the built in apps as well it's not the cable"),
            (
                STAFF,
                "Thanks for the details. If lines appear on every input, including built-in "
                "apps, please report a problem from your Orders page so we can check it.",
            ),
        ),
        product_code="VM-55Q",
    ),
    qa(
        8,
        "How do I set up my VisionMax TV?",
        "Attach the stand feet or a VESA 300 x 300 bracket, connect power and your HDMI devices, "
        "then follow the on-screen setup to choose a language, join Wi-Fi and sign in.",
        product_code="VM-55Q",
    ),
    Faq(
        9,
        "AeroBuds Pro battery not lasting?",
        (
            ("bass_junkie", "My AeroBuds Pro die way before the end of my commute??"),
            ("audiophile_amy", "Noise cancelling at max volume eats battery fyi"),
            (
                STAFF,
                f"AeroBuds Pro are rated for up to {AB_PRO_BATTERY_HOURS} hours of playback, or "
                f"{AB_PRO_BATTERY_WITH_CASE_HOURS} hours with the charging case. High volume with "
                "noise cancellation on will shorten this.",
            ),
        ),
        product_code="AB-PRO",
    ),
    qa(
        10,
        "How do I pair AeroBuds Pro?",
        "Open the case near your phone, then press and hold the case button for 3 seconds until "
        "the LED flashes white. Select AeroBuds Pro in your phone's Bluetooth settings.",
        product_code="AB-PRO",
    ),
    qa(
        11,
        "How do I make a warranty claim?",
        "Open Orders, choose the product and select Report a problem. Describe the fault; we may "
        "ask for photos or a short video.",
        "Coverage periods for each product are listed in our Warranty Policy.",
    ),
    Faq(
        12,
        "Is accidental damage covered?",
        (
            ("oops_i_dropped_it", "Dropped my laptop and the screen cracked. Warranty??"),
            ("been_there", "Nah, drops aren't covered sadly"),
            (
                STAFF,
                "Accidental damage such as drops, cracked screens or spills is not covered by the "
                "standard warranty. We can still quote a paid repair.",
            ),
        ),
    ),
    qa(
        13,
        "How long does delivery take?",
        "Delivery times depend on your country and the delivery option you choose at checkout. "
        "See our Shipping Policy for the times in India, the UK and the US.",
    ),
    Faq(
        14,
        "My order is late - what can I do?",
        (
            ("still_waiting_99", "Order should've come Tuesday. It's Friday. Anyone else??"),
            ("hopeful_harry", "I heard they sometimes give you something back for late orders?"),
            ("still_waiting_99", "Would be nice lol"),
            (
                STAFF,
                "Sorry for the wait. Please contact us from your Orders page and we will chase "
                "the courier for you. Each late-delivery case is reviewed individually.",
            ),
        ),
    ),
    qa(
        15,
        "How do I track my order?",
        "We email you a tracking link as soon as your order is dispatched. You can also open "
        "Orders in your account and select Track to see the latest courier updates.",
    ),
    qa(
        16,
        "Can I change my delivery address after ordering?",
        "Yes, as long as the order has not been dispatched. Open Orders and choose Change "
        "address. After dispatch, contact the courier using the tracking link.",
    ),
    qa(
        17,
        "What payment methods do you accept?",
        "In India: UPI, credit and debit cards, net banking and cash on delivery.",
        "In the UK: credit and debit cards and PayPal.",
        "In the US: credit and debit cards, PayPal and Apple Pay.",
    ),
    qa(
        18,
        "Is cash on delivery available?",
        "Cash on delivery is available in India for orders up to Rs 50,000. Please have the "
        "exact amount ready for the courier.",
        region="IN",
    ),
    Faq(
        19,
        "Are there any discounts?",
        (
            ("bargain_hunter", "Any codes floating about? Buying a TV soon"),
            ("deal_diva", f"Got {FIRST_ORDER_DISCOUNT_PCT}% off my first order automatically"),
            ("grandad_gadgets", f"There's {SENIOR_DISCOUNT_PCT}% off if you're {SENIOR_MIN_AGE}+"),
            (
                STAFF,
                f"Our discounts are the first-order discount ({FIRST_ORDER_DISCOUNT_PCT}%) and "
                f"the senior citizen discount ({SENIOR_DISCOUNT_PCT}%). Only one discount can be "
                "used per order.",
            ),
        ),
    ),
    qa(
        20,
        "How do I claim the senior citizen discount?",
        f"If you are {SENIOR_MIN_AGE} or over, verify your age once under Account > Discounts "
        "with a photo ID. The discount is then applied at checkout automatically.",
    ),
    Faq(
        21,
        "WashPro is showing an error code",
        (
            ("laundry_lisa", "My WashPro just stopped mid-cycle and says E-47. Water still in it"),
            (
                STAFF,
                f"E-47 means {WM_700_E47.lower()}. Clean the drain pump filter behind the small "
                "door at the bottom right, then restart the programme.",
            ),
            ("spin_cycle_sid", f"Don't mix it up with E-74, that one's a {WM_700_E74.lower()}"),
        ),
        product_code="WM-700",
    ),
    qa(
        22,
        "Can you install my washing machine?",
        "Yes. Choose installation at checkout when you order a WashPro. Our two-person team "
        "connects it, removes the transit bolts and takes the packaging away.",
        product_code="WM-700",
    ),
    qa(
        23,
        "Can I take the PowerBank 20K on a plane?",
        "The PowerBank 20K is rated at 74 Wh, below the 100 Wh limit most airlines set. Pack it "
        "in your hand luggage, not checked baggage, and check your airline's rules.",
        product_code="PB-20K",
    ),
    Faq(
        24,
        "Can I swim with the SmartWatch Fit 2?",
        (
            ("splash_sarah", "Want to track my swims. Is the Fit 2 actually waterproof?"),
            ("triathlon_tom", "Wear mine in the pool 3x a week, zero issues"),
            (
                STAFF,
                "SmartWatch Fit 2 is rated 5 ATM, so it is fine for pool and sea swimming. Avoid "
                "hot showers, saunas and diving.",
            ),
        ),
        product_code="SW-FIT2",
    ),
    Faq(
        25,
        "NoteBook 14 won't turn on",
        (
            ("deadline_dan", "Black screen, nothing happens when I press power. Essay due!!"),
            (
                STAFF,
                "Connect the charger for 30 minutes, then hold the power button for 15 seconds. "
                "If it still does not start, report a problem from your Orders page.",
            ),
            ("deadline_dan", "That worked, thank you!!"),
        ),
        product_code="NB-14",
    ),
    qa(
        26,
        "How do I reset my account password?",
        "Select Sign in, then Forgot password. We will email you a link that is valid for one "
        "hour.",
    ),
    qa(
        27,
        "Do you ship internationally?",
        "We deliver within India, the United Kingdom and the United States only. Orders are "
        "shipped from a warehouse in the same country as the delivery address.",
    ),
    qa(
        28,
        "Can I cancel my order?",
        "You can cancel from Orders until the order is dispatched. After dispatch, start a "
        "return once it arrives instead.",
    ),
    qa(
        29,
        "What is the difference between VoltCart and marketplace sellers?",
        "Marketplace items are sold and shipped by independent sellers. Their warranty and "
        "returns terms are set by the seller and shown on the product listing.",
    ),
    Faq(
        30,
        "Returned opened earbuds - anyone done it?",
        (
            ("worried_wendy", "Opened my earbuds, don't love the fit. Can I still send them back?"),
            ("londoner_liam", "Did it last month, easy. Just had to include all the ear tips"),
            ("mumbai_meera", "Check the time limit for your country, it's not the same everywhere"),
            (
                STAFF,
                "Opened audio products can be returned, but the window depends on your country. "
                "Please check the returns policy for India, the UK or the US.",
            ),
        ),
    ),
)


def body_html(faq: Faq) -> str:
    """Render one FAQ page body."""
    e = html.escape
    css = "faq thread" if faq.is_thread else "faq"
    posts = "\n".join(
        f'  <div class="post">\n    <p class="author">{e(author)}</p>\n    <p>{e(text)}</p>\n'
        "  </div>"
        for author, text in faq.posts
    )
    return f'<article class="{css}">\n  <h1>{e(faq.title)}</h1>\n{posts}\n</article>'


def build(out_dir: Path) -> list[DocMetadata]:
    """Write every FAQ page and return their metadata."""
    docs: list[DocMetadata] = []
    for faq in FAQS:
        meta = make_meta(
            doc_id=faq.doc_id,
            title=faq.title,
            doc_type="faq",
            product_code=faq.product_code,
            region=faq.region,
            audience="public",
            effective_date=faq.effective_date,
            subdir=SUBDIR,
            ext="html",
        )
        docs.append(write_html(out_dir, meta, body_html(faq)))
    return docs
