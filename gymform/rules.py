"""The society's gym rules, transcribed from the source PDF as data.

Keeping the rulebook here (rather than inside a template) means the form,
the acknowledgement checkboxes, the confirmation email and the printable
poster all render from one source of truth. Edit the text here and every
surface updates together.

Source document: "Society Gym Rules for Personal Trainers" — Silicon Bay,
Wadgaon Sheri, Pune 411014.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SOCIETY_NAME = "Silicon Bay Society"
SOCIETY_ADDRESS = "Wadgaon Sheri, Pune - 411014"
DOCUMENT_TITLE = "Society Gym Rules for Personal Trainers"


# ---------------------------------------------------------------------------
# Gym operating hours (rule 2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OperatingWindow:
    label: str
    start: str  # "HH:MM", 24-hour
    end: str


OPERATING_WINDOWS: tuple[OperatingWindow, ...] = (
    OperatingWindow("Morning", "06:00", "11:00"),
    OperatingWindow("Evening", "16:00", "21:00"),
)

# Rule 4 / rule 14: one trainer may train at most this many clients at a time.
MAX_CLIENTS_PER_SESSION = 4


# ---------------------------------------------------------------------------
# Amenity usage fee slabs (rule 1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeeSlab:
    min_clients: int
    max_clients: int | None  # None = no upper bound
    monthly_fee_inr: int

    @property
    def label(self) -> str:
        if self.max_clients is None:
            return f"{self.min_clients} and more clients"
        if self.min_clients == 1:
            return f"Up to {self.max_clients} clients"
        return f"{self.min_clients} to {self.max_clients} clients"


FEE_SLABS: tuple[FeeSlab, ...] = (
    FeeSlab(1, 4, 1_000),
    FeeSlab(5, 9, 2_000),
    FeeSlab(10, None, 3_000),
)


def monthly_fee_for(client_count: int) -> int:
    """Monthly amenity usage fee owed for a given number of clients."""
    if client_count <= 0:
        return 0
    for slab in FEE_SLABS:
        if client_count >= slab.min_clients and (
            slab.max_clients is None or client_count <= slab.max_clients
        ):
            return slab.monthly_fee_inr
    return FEE_SLABS[-1].monthly_fee_inr


# ---------------------------------------------------------------------------
# Accepted government ID types (rule 1)
# ---------------------------------------------------------------------------

ID_PROOF_TYPES: tuple[str, ...] = ("Aadhar Card", "PAN Card", "Driving License")


# ---------------------------------------------------------------------------
# The rulebook itself
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rule:
    """One numbered section of the rules document.

    ``key`` is the stable form-field name for that section's acknowledgement
    checkbox — renaming a title never invalidates already-stored submissions.
    """
    number: int
    key: str
    title: str
    paragraphs: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()
    acknowledgement: str = "I have read and agree to this rule."


RULES: tuple[Rule, ...] = (
    Rule(
        number=1,
        key="registration_approval",
        title="Registration & Approval",
        paragraphs=(
            "All personal trainers must register with the Silicon Bay Society office "
            "before providing training in the society gym.",
            "Trainer entry will be permitted only when they are coming to train a "
            "specific resident. Trainers must sign the security register with in-time "
            "and out-time for every visit. Trainers must carry their ID proof whenever "
            "inside society premises. The trainers must only use the gym and none of "
            "the other amenities or facilities of the society.",
            "The trainer should provide a complete list of clients they are training "
            "in Silicon Bay, with client name, flat number and training slot.",
        ),
        bullets=(
            "Government ID proof (Aadhar / PAN / Driving License)",
            "Mobile number",
            "Residential address details",
        ),
        acknowledgement=(
            "I confirm the registration details and client list I have submitted above "
            "are true and complete, and I will follow the entry and ID rules."
        ),
    ),
    Rule(
        number=2,
        key="gym_timings",
        title="Gym Timings",
        paragraphs=(
            "Trainers must inform their training timings to the society office before "
            "beginning the training sessions. The society gym operating hours are from "
            "6 am to 11 am and 4 pm to 9 pm. In case a client prefers a training slot "
            "outside of these hours the trainer needs to inform the society office and "
            "the security team.",
            "Training sessions cannot start before opening time or continue after "
            "closing time. Trainers are not allowed to use the gym outside official "
            "timings.",
        ),
        acknowledgement="I will train only within the notified gym timings.",
    ),
    Rule(
        number=3,
        key="resident_priority",
        title="Resident Priority & Shared Usage",
        paragraphs=(
            "The gym is a common facility meant primarily for residents. Personal "
            "training cannot block or reserve equipment.",
            "Trainers must ensure other residents can freely enter and use the gym at "
            "any time. If equipment is required by other residents, equipment must be "
            "shared politely. Trainers cannot occupy multiple machines simultaneously "
            "during peak hours.",
        ),
        acknowledgement="I will give residents priority and share equipment politely.",
    ),
    Rule(
        number=4,
        key="trainee_limit",
        title="Limit on Trainees",
        paragraphs=(
            "A trainer can train only four clients at a time unless approved otherwise "
            "by the society committee. Group training sessions inside the society gym "
            "are not permitted without written approval. Trainees should be residents "
            "of Silicon Bay, outside people cannot attend the personal trainer sessions "
            "in the society gym.",
        ),
        acknowledgement=(
            "I will train at most four clients at a time, and only Silicon Bay residents."
        ),
    ),
    Rule(
        number=5,
        key="dress_hygiene",
        title="Dress Code & Hygiene",
        paragraphs=(
            "Trainers must wear proper gym attire (sports t-shirt, track pants/shorts).",
            "The trainer needs to ensure their clients wear clean indoor gym shoes with "
            "clean soles. Outdoor dirty footwear is not allowed inside the gym.",
            "Trainers must maintain professional behaviour, cleanliness, and personal "
            "hygiene.",
        ),
        acknowledgement="I will follow the dress code and hygiene requirements.",
    ),
    Rule(
        number=6,
        key="equipment_care",
        title="Equipment Usage & Care",
        paragraphs=(
            "Equipment must be used properly and safely. Dropping weights, slamming "
            "machines, or mishandling equipment is prohibited.",
        ),
        bullets=(
            "All weights, dumbbells, plates, and accessories are returned to their "
            "designated place after use.",
            "Benches, mats, and machines must be wiped clean after use.",
        ),
        acknowledgement="I will use equipment safely and leave it clean and racked.",
    ),
    Rule(
        number=7,
        key="damage_liability",
        title="Damage Liability",
        paragraphs=(
            "If any gym equipment is damaged due to negligence, misuse, or improper "
            "training, the trainer and/or trainee will be responsible. The responsible "
            "party must:",
            "The society may suspend trainer access until the damage is resolved.",
        ),
        bullets=(
            "Pay full repair cost, or replace the equipment with same or equivalent "
            "model, and",
            "Arrange installation if required.",
        ),
        acknowledgement=(
            "I accept liability for damage caused by negligence or misuse during my "
            "sessions."
        ),
    ),
    Rule(
        number=8,
        key="safety_external_equipment",
        title="Safety & External Equipment",
        paragraphs=(
            "Trainers must ensure that the exercises performed by trainees are safe and "
            "appropriate. The society management is not responsible for injuries "
            "occurring during personal training sessions.",
            "Trainers may bring small accessories such as resistance bands or skipping "
            "ropes, but heavy equipment requires society permission.",
            "Any external equipment brought into the gym must be taken back after the "
            "training session. No trainer or resident can store personal equipment, "
            "shoes, bags, mats, or other belongings in the society gym.",
        ),
        acknowledgement=(
            "I take responsibility for the safety of my sessions and will not store "
            "anything in the gym."
        ),
    ),
    Rule(
        number=9,
        key="noise_disturbance",
        title="Noise & Disturbance",
        paragraphs=(
            "Trainers must avoid excessive shouting, loud instructions, swearing or "
            "disturbing behaviour. Music or audio devices must be used with low volume.",
        ),
        acknowledgement="I will keep noise and music low.",
    ),
    Rule(
        number=10,
        key="commercial_activity",
        title="Commercial Activity",
        paragraphs=(
            "Personal training is a private arrangement between the resident and the "
            "trainer. The society is not responsible for any payment disputes. Trainers "
            "cannot solicit other residents for training services inside society "
            "premises without approval.",
        ),
        acknowledgement=(
            "I understand the society is not party to my fees, and I will not solicit "
            "residents on the premises."
        ),
    ),
    Rule(
        number=11,
        key="security_compliance",
        title="Security Compliance",
        paragraphs=(
            "Trainers must follow all society security protocols. Entry can be denied "
            "if the trainer:",
        ),
        bullets=(
            "Fails to register",
            "Violates society rules",
            "Receives complaints from residents.",
        ),
        acknowledgement="I will follow all security protocols.",
    ),
    Rule(
        number=12,
        key="violations_penalties",
        title="Violations & Penalties",
        paragraphs=(
            "If any rule is violated, the Society Managing Committee may:",
        ),
        bullets=(
            "Issue a warning",
            "Temporarily suspend trainer entry",
            "Permanently ban the trainer from the society gym or premises.",
        ),
        acknowledgement="I accept these penalties for any rule violation.",
    ),
    Rule(
        number=13,
        key="maximum_occupancy",
        title="Maximum Gym Occupancy",
        paragraphs=(
            "If the gym becomes crowded, residents will always get priority over "
            "personal training sessions.",
        ),
        acknowledgement="I will yield to residents when the gym is crowded.",
    ),
    Rule(
        number=14,
        key="trainer_presence",
        title="Maximum Trainer Presence Rule",
        paragraphs=(
            "Only one personal trainer for a maximum of four residents per session is "
            "allowed in the gym. We do not want the trainers to turn the gym into a "
            "group class studio.",
        ),
        acknowledgement="I will not run group classes in the society gym.",
    ),
    Rule(
        number=15,
        key="no_equipment_blocking",
        title="No Equipment Blocking Rule",
        paragraphs=(
            "Trainers cannot reserve or block multiple machines or equipment for their "
            "training session. All equipment must remain available to other residents.",
        ),
        acknowledgement="I will not reserve or block equipment.",
    ),
)

RULE_KEYS: tuple[str, ...] = tuple(rule.key for rule in RULES)
RULES_BY_KEY: dict[str, Rule] = {rule.key: rule for rule in RULES}


DECLARATION = (
    "I have read, understood and agree to abide by all the rules of the "
    f"{SOCIETY_NAME} gym for personal trainers listed above. I understand that "
    "any violation may lead to a warning, temporary suspension, or a permanent "
    "ban from the society gym and premises."
)
