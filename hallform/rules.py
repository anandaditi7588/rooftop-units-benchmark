"""Society amenity booking rules, transcribed from the source document.

Source: "Rule and Regulation — For personal use of Society's amenities by
Resident", Silicon Bay, dated 15/10/2024.

Same approach as the gym rulebook: the venues, charges, timings and rules live
here as data so the form, the confirmation page, the rules page, the office
email and the printed notice all render from one source.
"""
from __future__ import annotations

from dataclasses import dataclass

# Bookings run 8:30 AM to 10 PM at the latest.
BOOKING_DAY_START = "08:30"
BOOKING_DAY_END = "22:00"

SECURITY_DEPOSIT_INR = 5_000


@dataclass(frozen=True)
class Slot:
    """One bookable duration at a venue."""
    key: str
    label: str
    hours: int
    charge_inr: int


@dataclass(frozen=True)
class Venue:
    key: str
    name: str
    location: str
    max_persons: int
    slots: tuple[Slot, ...]
    notes: tuple[str, ...] = ()

    def slot(self, key: str) -> Slot | None:
        return next((s for s in self.slots if s.key == key), None)


VENUES: tuple[Venue, ...] = (
    Venue(
        key="conference_hall",
        name="Conference Hall",
        location="Below D Building",
        max_persons=75,
        slots=(
            Slot("4h", "4 hours", 4, 2_500),
            Slot("8h", "8 hours", 8, 3_500),
        ),
        notes=("No food may be prepared inside the Conference Hall.",),
    ),
    Venue(
        key="party_lawn",
        name="Party Lawn",
        location="At the swimming pool",
        max_persons=75,
        slots=(
            Slot("4h", "4 hours", 4, 3_000),
            Slot("8h", "8 hours", 8, 4_000),
        ),
    ),
    Venue(
        key="community_hall",
        name="Community Hall",
        location="A & B Buildings",
        max_persons=30,
        # The document prices this one per day rather than by the hour.
        slots=(Slot("day", "Full day", 13, 2_000),),
    ),
)

VENUES_BY_KEY: dict[str, Venue] = {v.key: v for v in VENUES}

# What residents may book an amenity for, per the document.
OCCASIONS: tuple[str, ...] = (
    "Birthday party",
    "Get-together party",
    "Pooja",
    "Lunch / dinner",
    "Anniversary",
    "Naming ceremony",
    "Other family function",
)


@dataclass(frozen=True)
class Rule:
    number: int
    key: str
    title: str
    paragraphs: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()
    acknowledgement: str = "I have read and agree to this rule."


RULES: tuple[Rule, ...] = (
    Rule(
        number=1,
        key="personal_use",
        title="Who may book, and for what",
        paragraphs=(
            "The Conference Hall (below D Building), the Party Lawn (at the swimming "
            "pool) and the Community Hall (A & B Buildings) are given to society "
            "residents for their personal events — birthdays, get-togethers, pooja, "
            "lunch or dinner and similar occasions.",
            "Only electricity, light and fan are provided.",
        ),
        acknowledgement=(
            "I am a resident of Silicon Bay and am booking this amenity for my own "
            "personal event."
        ),
    ),
    Rule(
        number=2,
        key="capacity",
        title="Maximum persons",
        paragraphs=(
            "The Conference Hall and the Party Lawn may not exceed 75 persons. The "
            "Community Hall may not exceed 30 persons.",
        ),
        acknowledgement="I will not exceed the maximum number of persons for the venue.",
    ),
    Rule(
        number=3,
        key="charges",
        title="Charges and payment",
        paragraphs=(
            "Cash will be collected one day before the function. The collected amount "
            "is transferred to the society's cultural fund.",
        ),
        bullets=(
            "Conference Hall (D Building) — 8 hours ₹3,500 / 4 hours ₹2,500",
            "Party Lawn (at swimming pool) — 8 hours ₹4,000 / 4 hours ₹3,000",
            "Community Hall (A & B Buildings) — ₹2,000 for a day",
        ),
        acknowledgement="I will pay the booking charges one day before the function.",
    ),
    Rule(
        number=4,
        key="security_deposit",
        title="Security deposit",
        paragraphs=(
            "A refundable security deposit of ₹5,000 is payable by cheque.",
        ),
        acknowledgement="I will give a refundable security deposit of ₹5,000 by cheque.",
    ),
    Rule(
        number=5,
        key="cleaning",
        title="Cleaning",
        paragraphs=(
            "Cleaning of used utensils, disposable glasses and plates, tiles and "
            "toilets is the resident's responsibility.",
            "If the amenity is not properly cleaned, the cleaning amount will be "
            "deducted from the ₹5,000 security deposit.",
        ),
        acknowledgement=(
            "I will clean the amenity after use, and accept a deduction from my "
            "deposit if it is not cleaned properly."
        ),
    ),
    Rule(
        number=6,
        key="damage",
        title="Breakage and damage",
        paragraphs=(
            "If anything is broken or damaged, repair charges will be paid at actual, "
            "after checking and on receipt of a quotation from the vendor.",
        ),
        acknowledgement="I will pay the actual repair cost for any breakage or damage.",
    ),
    Rule(
        number=7,
        key="timing",
        title="Booking timings",
        paragraphs=(
            "Bookings run from 8:30 AM to 10:00 PM at the latest, except for the "
            "society's own common functions and activities.",
        ),
        acknowledgement="I will keep my function within 8:30 AM to 10:00 PM.",
    ),
    Rule(
        number=8,
        key="parking",
        title="Guest parking",
        paragraphs=(
            "Parking for guests' and visitors' vehicles is the resident's "
            "responsibility.",
        ),
        acknowledgement="I will manage parking for my guests' vehicles.",
    ),
    Rule(
        number=9,
        key="furniture",
        title="Chairs and tables",
        paragraphs=(
            "Chairs and tables are the resident's responsibility — they are not "
            "provided with the amenity.",
        ),
        acknowledgement="I will arrange my own chairs and tables.",
    ),
    Rule(
        number=10,
        key="no_cooking",
        title="No food preparation in the Conference Hall",
        paragraphs=(
            "No food may be prepared inside the Conference Hall.",
        ),
        acknowledgement="I will not prepare food inside the Conference Hall.",
    ),
    Rule(
        number=11,
        key="switch_off",
        title="After the function",
        paragraphs=(
            "Lights and fans must be switched off by the resident after the function "
            "is over.",
        ),
        acknowledgement="I will switch off the lights and fans after my function.",
    ),
)

RULE_KEYS: tuple[str, ...] = tuple(rule.key for rule in RULES)

DECLARATION = (
    "I have read, understood and agree to abide by the society's rules and "
    "regulations for personal use of amenities listed above. I accept "
    "responsibility for cleaning, for any breakage or damage, and for keeping my "
    "function within the permitted timings and capacity."
)
