"""Product manuals (PDF, 2-4 pages each) for the eight VoltCart products.

Failure design:
- product_code: the XR-2200 and XR-2000 manuals come from one template and differ
  only in Wi-Fi standard, speed, security and the factory-reset procedure. WM-700
  error codes E-47 and E-74 look alike but mean different things.
- ranking: the VM-55Q troubleshooting row for "Lines on screen" points at the HDMI
  cable, a close lexical match that says nothing about the 24-month panel warranty
  (which lives only in the warranty policy).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from generators.common import (
    Block,
    Bullets,
    Heading,
    PageBreakBlock,
    Para,
    TableBlock,
    make_meta,
    write_pdf,
)
from generators.facts import (
    AB_PRO_BATTERY_HOURS,
    AB_PRO_BATTERY_WITH_CASE_HOURS,
    PB_20K_USB_C_OUTPUT_W,
    STORE_NAME,
    WM_700_E47,
    WM_700_E74,
    XR_2000_MAX_SPEED_MBPS,
    XR_2000_RESET_LED,
    XR_2000_RESET_SECONDS,
    XR_2200_MAX_SPEED_MBPS,
    XR_2200_RESET_LED,
    XR_2200_RESET_SECONDS,
)
from rag_assistant.schemas import DocMetadata

SUBDIR = "manuals"

SAFETY = (
    "Read all instructions before using the product and keep this manual for reference.",
    "Use only the power adapter or cable supplied with the product, or an approved replacement.",
    "Do not expose the product to rain, moisture or extreme heat unless it is rated for it.",
    "Do not open the casing. There are no user-serviceable parts inside.",
    "Keep small parts and packaging away from young children.",
    "Unplug mains-powered products during lightning storms or when unused for long periods.",
    "If the product gives off smoke, a burning smell or unusual heat, switch it off, unplug it "
    f"and contact {STORE_NAME} support.",
)

CARE = (
    "Clean the product with a soft, dry cloth. Do not use solvents, abrasive cleaners or sprays "
    "directly on the product.",
    "Store the product in a cool, dry place away from direct sunlight when it is not in use.",
    "Recycling: do not dispose of this product with household waste. Take it to an approved "
    "electronics recycling point. Products with built-in batteries must be recycled separately "
    "from general waste.",
)


@dataclass(frozen=True)
class Manual:
    """Content for one product manual."""

    code: str
    name: str
    effective_date: str
    intro: str
    in_the_box: tuple[str, ...]
    setup: tuple[str, ...]
    specs: tuple[tuple[str, str], ...]
    usage: tuple[Block, ...]
    troubleshooting: tuple[tuple[str, str], ...]
    label_location: str
    error_codes: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)

    @property
    def doc_id(self) -> str:
        """Document id derived from the product code."""
        return f"manual-{self.code.lower()}"


def manual_blocks(m: Manual) -> list[Block]:
    """Lay out a manual using the shared section structure."""
    blocks: list[Block] = [
        Heading(f"{m.name} ({m.code}) User Manual", 1),
        Para(
            f"Thank you for choosing the {m.name} ({m.code}). This manual explains how to set "
            "up, use and look after your product. The latest version is always available from "
            f"your {STORE_NAME} account under Orders > Product documents."
        ),
        Para(m.intro),
        Heading("Safety information"),
        Bullets(SAFETY),
        Heading("In the box"),
        Bullets(m.in_the_box),
        Heading("Getting started"),
        Bullets(m.setup),
        Heading("Specifications"),
        TableBlock(("Specification", "Value"), m.specs),
        *m.usage,
        PageBreakBlock(),
        Heading("Troubleshooting"),
        Para("Try these steps before contacting support. Most problems are solved in minutes."),
        TableBlock(("Problem", "What to try"), m.troubleshooting),
    ]
    if m.error_codes:
        blocks += [
            Heading("Error codes"),
            Para("The display shows an error code when the product detects a problem."),
            TableBlock(("Code", "Meaning", "What to do"), m.error_codes),
        ]
    blocks += [
        Heading("Care and cleaning"),
        *(Para(text) for text in CARE),
        Heading("Warranty and support"),
        Para(
            f"Your {m.name} is covered by the {STORE_NAME} Warranty Policy, which sets out how "
            "long each part is covered and how to make a claim. Keep your order confirmation as "
            f"proof of purchase. For help, visit the {STORE_NAME} Help Centre or contact support "
            f"from the Orders page in your account. Have your product code ({m.code}) and serial "
            f"number ready. The serial number is printed on the label {m.label_location}."
        ),
    ]
    return blocks


# ------------------------------------------------------------------ Routers


def _router(
    code: str, standard: str, speed: str, security: str, seconds: int, led: str, led_row: str
) -> Manual:
    """Shared template for the two routers; only the facts passed in differ."""
    name = f"{code} Wi-Fi Router"
    return Manual(
        code=code,
        name=name,
        effective_date="2026-01-15",
        intro=(
            f"The {code} is a dual-band {standard} router for homes and small offices. It "
            "creates a fast, secure wireless network and has four wired ports for computers, "
            f"consoles and TVs. You can manage it from a web browser or the {STORE_NAME} Home app."
        ),
        in_the_box=(
            f"{code} router",
            "Power adapter (12 V, 1.5 A)",
            "Ethernet cable (1 m)",
            "Quick start guide",
        ),
        setup=(
            "Switch off your modem.",
            "Connect the yellow WAN port on the router to your modem using the Ethernet cable.",
            "Connect the power adapter and switch on the router. The LED blinks white while it "
            "starts up.",
            "Switch your modem back on. When the LED is steady white, the router is online.",
            "Join the Wi-Fi network printed on the label on the base of the router, using the "
            "password printed underneath it.",
            f"Optional: open the {STORE_NAME} Home app and follow the prompts to choose your own "
            "network name and password.",
        ),
        specs=(
            ("Model", code),
            ("Wi-Fi standard", standard),
            ("Bands", "2.4 GHz and 5 GHz"),
            ("Maximum wireless speed", speed),
            ("Ports", "1 x Gigabit WAN, 4 x Gigabit LAN"),
            ("Wireless security", security),
            ("Antennas", "4 external"),
            ("Power", "12 V, 1.5 A"),
            ("Dimensions", "230 x 145 x 40 mm"),
        ),
        usage=(
            Heading("LED status"),
            TableBlock(
                ("LED", "Meaning"),
                (
                    ("Off", "No power"),
                    ("White, blinking", "Starting up"),
                    ("White, steady", "Connected to the internet"),
                    ("Purple, steady", "No internet connection"),
                    led_row,
                ),
            ),
            Heading("Using your router"),
            Para(
                "Guest network: create a separate network for visitors in the app under Settings "
                "> Guest Wi-Fi. Guests can use the internet but cannot see devices on your main "
                "network."
            ),
            Para(
                "Parental controls: set schedules and content filters for each device under "
                "Settings > Family. Changes apply within a minute."
            ),
            Para(
                "Firmware updates: the router checks for updates every night and installs them "
                "automatically between 2 am and 4 am. You can also check manually in the web "
                "settings at http://192.168.0.1 under Administration > Firmware."
            ),
            Heading("Factory reset"),
            Para(
                "A factory reset erases all settings, including your Wi-Fi name and password, and "
                "restores the original settings printed on the label. Use it if you have "
                "forgotten your password or the router is not responding."
            ),
            Para(
                f"To reset the {code}: with the router switched on, press and hold the reset "
                f"button for {seconds} seconds until the LED {led}. Then release the button. The "
                "router restarts, which takes about two minutes."
            ),
        ),
        troubleshooting=(
            (
                "No internet (LED purple)",
                "Restart your modem and router. Check the WAN cable is in the yellow port.",
            ),
            (
                "Slow Wi-Fi",
                "Move the router to a central, raised position away from metal objects. Use the "
                "5 GHz band for devices close to the router.",
            ),
            (
                "Forgot Wi-Fi password",
                "Find it in the app under Settings > Wi-Fi, or perform a factory reset.",
            ),
            (
                "Cannot open the settings page",
                "Connect to the router's network and go to http://192.168.0.1.",
            ),
            (
                "Devices keep disconnecting",
                "Update the router firmware and the device's network drivers.",
            ),
        ),
        label_location="on the base of the router",
    )


XR_2200 = _router(
    "XR-2200",
    "Wi-Fi 6 (802.11ax)",
    f"Up to {XR_2200_MAX_SPEED_MBPS} Mbps (AX3000)",
    "WPA2 and WPA3",
    XR_2200_RESET_SECONDS,
    XR_2200_RESET_LED,
    ("Amber, blinking", "Factory reset in progress"),
)

XR_2000 = _router(
    "XR-2000",
    "Wi-Fi 5 (802.11ac)",
    f"Up to {XR_2000_MAX_SPEED_MBPS} Mbps (AC1200)",
    "WPA2",
    XR_2000_RESET_SECONDS,
    XR_2000_RESET_LED,
    ("Red, steady", "Factory reset in progress"),
)

# ------------------------------------------------------------------ Others

AB_PRO = Manual(
    code="AB-PRO",
    name="AeroBuds Pro Wireless Earbuds",
    effective_date="2026-01-15",
    intro=(
        "AeroBuds Pro are true wireless earbuds with active noise cancellation, a transparency "
        "mode and a pocket-sized charging case."
    ),
    in_the_box=(
        "AeroBuds Pro earbuds (left and right)",
        "Charging case",
        "USB-C charging cable",
        "Ear tips in three sizes (S, M, L)",
    ),
    setup=(
        "Charge the case and earbuds fully before first use. The case LED turns green when full.",
        "Open the case lid near your phone.",
        "Press and hold the button on the case for 3 seconds until the LED flashes white.",
        "On your phone, open Bluetooth settings and select AeroBuds Pro.",
    ),
    specs=(
        ("Model", "AB-PRO"),
        ("Bluetooth", "5.3"),
        ("Noise cancellation", "Active, with transparency mode"),
        ("Battery life (earbuds)", f"{AB_PRO_BATTERY_HOURS} hours"),
        ("Battery life (with case)", f"{AB_PRO_BATTERY_WITH_CASE_HOURS} hours"),
        ("Charging", "USB-C, wireless charging case"),
        ("Water resistance", "IPX4 (sweat and splash resistant)"),
        ("Weight", "5 g per earbud, 45 g case"),
    ),
    usage=(
        Heading("Battery and charging"),
        Para(
            f"When fully charged, AeroBuds Pro play for up to {AB_PRO_BATTERY_HOURS} hours, and up "
            f"to {AB_PRO_BATTERY_WITH_CASE_HOURS} hours with the charging case. Ten minutes in the "
            "case gives about one hour of listening. Battery life is lower with noise "
            "cancellation on at high volume."
        ),
        Heading("Controls"),
        Bullets(
            (
                "Tap once: play or pause.",
                "Tap twice: next track.",
                "Press and hold: switch between noise cancellation and transparency mode.",
            )
        ),
        Heading("Choosing ear tips"),
        Para(
            "A good seal improves sound and noise cancellation. Try each tip size and choose the "
            "one that feels secure without pressure."
        ),
    ),
    troubleshooting=(
        ("One earbud has no sound", "Put both earbuds in the case, close the lid, then reopen."),
        (
            "Earbuds will not pair",
            "Remove AeroBuds Pro from your phone's Bluetooth list, then repeat the pairing steps.",
        ),
        (
            "Reset the earbuds",
            "With both earbuds in the open case, hold the case button for 15 seconds until the "
            "LED flashes orange.",
        ),
        ("Case will not charge", "Try a different USB-C cable and wall adapter."),
    ),
    label_location="inside the charging case lid",
)

VM_55Q = Manual(
    code="VM-55Q",
    name='VisionMax 55" 4K TV',
    effective_date="2026-01-15",
    intro=(
        'The VisionMax 55" is a 4K QLED smart TV with HDR10 support, built-in streaming apps '
        "and voice control through the supplied remote."
    ),
    in_the_box=(
        "VisionMax 55-inch TV",
        "Stand feet (2) and screws",
        "Voice remote with 2 AAA batteries",
        "Power cable",
    ),
    setup=(
        "Lay the TV face down on a soft, flat surface and attach the stand feet with the screws.",
        "Stand the TV upright, or mount it with a VESA 300 x 300 wall bracket (not included).",
        "Connect the power cable and any HDMI devices.",
        "Switch on the TV and follow the on-screen setup: language, Wi-Fi and account sign-in.",
    ),
    specs=(
        ("Model", "VM-55Q"),
        ("Screen", "55-inch QLED, 3840 x 2160 (4K UHD)"),
        ("HDR", "HDR10, HLG"),
        ("Refresh rate", "60 Hz"),
        ("Inputs", "3 x HDMI 2.1, 2 x USB, optical audio out"),
        ("Connectivity", "Wi-Fi 5, Bluetooth 5.0, Ethernet"),
        ("Speakers", "2 x 10 W"),
        ("Power consumption", "110 W typical"),
    ),
    usage=(
        Heading("Picture settings"),
        Para(
            "Choose a picture mode under Settings > Picture. Standard suits most rooms, Cinema "
            "gives the most accurate colours and Game reduces input lag."
        ),
        Heading("Streaming apps"),
        Para(
            "Press the Home button to open apps. Sign in to each streaming service with your own "
            "account. The TV updates apps automatically when connected to the internet."
        ),
    ),
    troubleshooting=(
        (
            "Lines on screen",
            "Check the HDMI cable is firmly connected at both ends. Try a different HDMI port, or "
            "replace the cable with a certified high-speed HDMI cable. Make sure the connected "
            "device outputs a supported resolution.",
        ),
        ("No picture", "Check the input source with the Input button on the remote."),
        ("No sound", "Check the TV is not muted and the audio output is set to TV speakers."),
        ("Remote not working", "Replace the batteries and point the remote at the TV."),
        ("Wi-Fi drops", "Move the router closer or connect an Ethernet cable."),
    ),
    label_location="on the back of the TV",
)

WM_700 = Manual(
    code="WM-700",
    name="WashPro 7 kg Washing Machine",
    effective_date="2026-01-15",
    intro=(
        "The WashPro 7 kg is a front-loading washing machine with 15 programmes, a quick "
        "30-minute wash and an energy-saving eco mode."
    ),
    in_the_box=(
        "WashPro washing machine",
        "Inlet hose",
        "Transit bolt covers (4)",
        "Spanner for transit bolts",
    ),
    setup=(
        "Remove the four transit bolts from the back with the spanner and fit the covers.",
        "Connect the inlet hose to a cold water tap.",
        "Hook the drain hose over a standpipe or connect it to a sink waste.",
        "Level the machine by adjusting the feet so it does not rock.",
        "Plug in, switch on and run an empty Cotton 60 wash to clean the drum.",
    ),
    specs=(
        ("Model", "WM-700"),
        ("Capacity", "7 kg"),
        ("Maximum spin speed", "1200 rpm"),
        ("Programmes", "15"),
        ("Dimensions", "850 x 595 x 550 mm"),
        ("Water connection", "Cold fill"),
        ("Noise (spin)", "76 dB"),
    ),
    usage=(
        Heading("Choosing a programme"),
        Para(
            "Turn the dial to choose a programme, then adjust temperature and spin if needed. "
            "Press Start. The display shows the time remaining."
        ),
        Heading("Cleaning the drain pump filter"),
        Para(
            "Clean the filter every three months. Open the small door at the bottom right, place "
            "a shallow tray underneath, turn the filter anticlockwise and remove any fluff, coins "
            "or hair. Refit it firmly."
        ),
    ),
    troubleshooting=(
        ("Machine will not start", "Check the door is closed and the child lock is off."),
        ("Water left in the drum", "Clean the drain pump filter and check the drain hose."),
        ("Machine is noisy or shakes", "Check the transit bolts are removed and level the feet."),
        ("Clothes are still wet", "Choose a higher spin speed and avoid overloading the drum."),
    ),
    error_codes=(
        ("E-10", "Water supply problem", "Check the tap is open and the hose is not kinked."),
        ("E-21", "Unbalanced load", "Redistribute the laundry and restart the spin."),
        ("E-47", WM_700_E47, "Clean the drain pump filter, then restart the programme."),
        (
            "E-74",
            WM_700_E74,
            "Close the door firmly. If the code returns, contact support for a repair.",
        ),
        ("E-90", "Control board communication error", "Switch off for 10 minutes, then retry."),
    ),
    label_location="inside the door rim",
)

PB_20K = Manual(
    code="PB-20K",
    name="PowerBank 20K",
    effective_date="2026-01-15",
    intro=(
        "The PowerBank 20K is a 20,000 mAh portable battery that can charge a laptop, phone and "
        "earbuds at the same time."
    ),
    in_the_box=("PowerBank 20K", "USB-C to USB-C cable (60 cm)", "Travel pouch"),
    setup=(
        "Charge the power bank fully before first use with a USB-C charger of 30 W or more.",
        "Press the power button once to see the charge level on the four LEDs.",
        "Connect your device. Charging starts automatically.",
    ),
    specs=(
        ("Model", "PB-20K"),
        ("Capacity", "20,000 mAh (74 Wh)"),
        ("USB-C output", f"Up to {PB_20K_USB_C_OUTPUT_W} W (Power Delivery)"),
        ("USB-A output", "2 x 18 W"),
        ("Recharge time", "About 3.5 hours with a 65 W charger"),
        ("Weight", "400 g"),
    ),
    usage=(
        Heading("Air travel"),
        Para(
            "The PowerBank 20K is rated at 74 Wh, below the 100 Wh limit most airlines set for "
            "power banks. Carry it in your hand luggage, never in checked baggage, and check your "
            "airline's rules before you fly."
        ),
        Heading("Battery level"),
        Para("Each of the four LEDs represents about 25% of the remaining charge."),
    ),
    troubleshooting=(
        ("Device not charging", "Press the power button and try another cable."),
        ("Power bank gets warm", "This is normal at high output. Keep it out of direct sun."),
        ("LEDs do not light", "Recharge the power bank for at least one hour."),
    ),
    label_location="on the underside of the power bank",
)

SW_FIT2 = Manual(
    code="SW-FIT2",
    name="SmartWatch Fit 2",
    effective_date="2026-01-15",
    intro=(
        "SmartWatch Fit 2 tracks activity, heart rate, sleep and blood oxygen, and shows "
        "notifications from your phone."
    ),
    in_the_box=("SmartWatch Fit 2", "Magnetic charging cable", "Silicone strap (fitted)"),
    setup=(
        "Charge the watch until the screen shows a full battery.",
        f"Install the {STORE_NAME} Fit app on your phone.",
        "Open the app, choose Add device and follow the pairing prompts.",
    ),
    specs=(
        ("Model", "SW-FIT2"),
        ("Display", "1.4-inch AMOLED"),
        ("Battery life", "Up to 9 days typical use"),
        ("Water resistance", "5 ATM"),
        ("Sensors", "Heart rate, SpO2, accelerometer, GPS"),
        ("Compatibility", "Android 10 or later, iOS 15 or later"),
    ),
    usage=(
        Heading("Water use"),
        Para(
            "With a 5 ATM rating, SmartWatch Fit 2 is suitable for swimming in a pool or the sea. "
            "Do not use it in hot showers, saunas or for diving. Rinse it with fresh water after "
            "swimming in the sea."
        ),
        Heading("Workouts"),
        Para(
            "Swipe up and choose Workout to track running, cycling, swimming and more. GPS starts "
            "automatically for outdoor workouts."
        ),
    ),
    troubleshooting=(
        ("Watch will not pair", "Restart the watch and your phone, then pair again in the app."),
        ("Notifications missing", "Allow notifications for the Fit app in phone settings."),
        ("Battery drains quickly", "Turn off the always-on display and reduce GPS use."),
    ),
    label_location="on the back of the watch",
)

NB_14 = Manual(
    code="NB-14",
    name="NoteBook 14 Laptop",
    effective_date="2026-01-15",
    intro=(
        "NoteBook 14 is a lightweight 14-inch laptop for work, study and streaming, with all-day "
        "battery life and fast charging over USB-C."
    ),
    in_the_box=("NoteBook 14 laptop", "65 W USB-C charger", "Quick start guide"),
    setup=(
        "Connect the charger and open the lid. The laptop switches on automatically.",
        "Follow the Windows setup screens to choose your region, Wi-Fi and account.",
        "Run Windows Update to install the latest drivers.",
    ),
    specs=(
        ("Model", "NB-14"),
        ("Display", "14-inch, 1920 x 1200, anti-glare"),
        ("Processor", "8-core mobile processor"),
        ("Memory", "16 GB RAM"),
        ("Storage", "512 GB SSD"),
        ("Operating system", "Windows 11 Home"),
        ("Battery life", "Up to 12 hours video playback"),
        ("Ports", "2 x USB-C, 1 x USB-A, HDMI, headphone jack"),
        ("Weight", "1.3 kg"),
    ),
    usage=(
        Heading("Charging"),
        Para(
            "Use the supplied 65 W USB-C charger in either USB-C port. A 30-minute charge gives "
            "about half a battery."
        ),
        Heading("Keyboard shortcuts"),
        Bullets(
            (
                "Fn + F5 / F6: screen brightness.",
                "Fn + F9: keyboard backlight.",
                "Fn + F12: airplane mode.",
            )
        ),
    ),
    troubleshooting=(
        (
            "Laptop will not turn on",
            "Connect the charger for 30 minutes, then hold the power button for 15 seconds.",
        ),
        ("Wi-Fi not available", "Press Fn + F12 to turn off airplane mode."),
        ("Battery drains quickly", "Lower screen brightness and close background apps."),
        ("Fan is loud", "Keep the vents clear and use the laptop on a hard, flat surface."),
    ),
    label_location="on the underside of the laptop",
)

MANUALS = (XR_2200, XR_2000, AB_PRO, VM_55Q, WM_700, PB_20K, SW_FIT2, NB_14)


def build(out_dir: Path) -> list[DocMetadata]:
    """Write every manual as a PDF and return their metadata."""
    docs: list[DocMetadata] = []
    for manual in MANUALS:
        meta = make_meta(
            doc_id=manual.doc_id,
            title=f"{manual.name} ({manual.code}) User Manual",
            doc_type="manual",
            product_code=manual.code,
            region="ALL",
            audience="public",
            effective_date=manual.effective_date,
            subdir=SUBDIR,
            ext="pdf",
        )
        docs.append(write_pdf(out_dir, meta, manual_blocks(manual)))
    return docs
