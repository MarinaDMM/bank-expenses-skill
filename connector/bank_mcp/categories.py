"""Sort bank transactions into expense categories. Rules are plain regexes; first match wins.

This file holds only generic rules (common Dutch providers and shops). Anything personal lives in a private
file that is never committed: ~/.config/bank-mcp/categories.json (see categories.example.json). Your rules are
checked first, so they can override the generic ones.

Matching runs on 'counterparty + description', lower-cased.
"""

import json
import re

from bank_mcp import config

HOUSEHOLD, BUSINESS = "household", "business"
SCOPES = {HOUSEHOLD, BUSINESS}

# (category, scope, regex). Order matters: specific rules first.
RULES: list[tuple[str, str, str]] = [
    # housing
    ("Mortgage", HOUSEHOLD, r"obvion|hypotheek|mortgage"),
    ("Energy", HOUSEHOLD, r"vandebron|eneco|vattenfall|greenchoice|essent|pure energie|budget energie|engie|oxxio|frank energie"),
    ("Water", HOUSEHOLD, r"\bpwn\b|waternet|vitens|dunea|evides|oasen|waterleidingbedrijf|waterschap"),
    ("Local taxes", HOUSEHOLD, r"belastingsamenwerking|lokale heffingen|gemeentelijke|gemeente"),
    ("Service charges (VvE)", HOUSEHOLD, r"vereniging van eigenaars|\bvve\b"),
    # insurance
    ("Health insurance", HOUSEHOLD, r"zilveren kruis|friesland zorg|zorgverzeker|menzis|\bvgz\b|\bcz\b|\bovb\b|a\.s\.r"),
    ("Other insurance", HOUSEHOLD, r"inshared|voogd|achmea|nn schade|allianz|schadev|ohra|centraal beheer"),
    # taxes
    ("Tax office", HOUSEHOLD, r"belastingdienst"),
    # money to card (statement payments); the card's own spending is not itemised by the bank
    ("Credit card payments", HOUSEHOLD, r"incasso ing creditcard|credit card extra|platinumcard|transfer to credit card"),
    # telecom
    ("Internet & mobile", HOUSEHOLD, r"\bkpn\b|ziggo|odido|vodafone|t-mobile"),
    # daily life
    ("Groceries", HOUSEHOLD, r"lidl|vomar|dekamarkt|sligro|albert heijn|\bah\b|jumbo|aldi|hoogvliet|plus supermarkt|supermark"),
    ("Health & drugstore", HOUSEHOLD, r"kruidvat|apotheek|infomedics|etos|tandarts|huisarts|mondzorg"),
    ("Sports & clubs", HOUSEHOLD, r"sportschool|sportcentrum|fitness|tennis|padel|zwembad"),
    ("Eating out & leisure", HOUSEHOLD, r"restaurant|sushi|pathe|coffee|noodle|takeaway|thuisbezorgd|cafe|bioscoop"),
    ("Home & garden", HOUSEHOLD, r"beterwitgoed|ikea|praxis|gamma|karwei|hornbach|intratuin|bauhaus|karcher"),
    ("Electronics & Apple", HOUSEHOLD, r"apple store|apple\.com|coolblue|mediamarkt"),
    ("Travel & holidays", HOUSEHOLD, r"booking\.com|hotel|airbnb|ryanair|wizz|klm|transavia|kiwi\.com|trip\.com"),
    ("Loans & credit", HOUSEHOLD, r"afterpay|klarna"),
    ("Personal care", HOUSEHOLD, r"kapper|salon|douglas|rituals"),
    ("Professional services", HOUSEHOLD, r"notaris|advocaat|adviseur"),
    ("Online shopping", HOUSEHOLD, r"bol\.com|bolcom|amazon|temu|zalando|tk maxx"),
    ("Transport", HOUSEHOLD, r"\bns\b|ns groep|ov-chip|easypark|shell|\bbp\b|parkeer|ev charge|charge session"),
    # payments to people (Tikkie etc.): real money out, but not a merchant
    ("Payments to people", HOUSEHOLD, r"via tikkie|tikkie"),
]

IBAN_TAIL = re.compile(r"\[IBAN \.\.\.(\w{4})\]")
# Positive amounts that are really refunds from a shop.
REFUND_CATEGORIES = {"Payments to people", "Online shopping", "Home & garden", "Electronics & Apple", "Groceries", "Eating out & leisure", "Health & drugstore"}

# "Jan de Vries", "J. Jansen": 2 to 4 words, no company markers, no digits.
PERSON_NAME = re.compile(r"^[A-Za-z][\w.'-]*(?: [A-Za-z][\w.'-]*){1,3}$")
COMPANY_WORDS = re.compile(r"\b(b\.?v\.?|n\.?v\.?|bank|via|stichting|ltd|limited|inc|gmbh|shop|store|club|centrum|nederland)\b", re.I)


class Rules:
    """Generic rules plus the user's private ones (checked first)."""

    def __init__(self, user_rules: list[tuple[str, str, str]] | None = None, internal_names: list[str] | None = None):
        self.rules = [(c, s, re.compile(p)) for c, s, p in (user_rules or []) + RULES]
        names = [n for n in (internal_names or []) if n]
        self.internal_name = re.compile("|".join(re.escape(n) for n in names), re.IGNORECASE) if names else None


def load_user_file() -> tuple[list[tuple[str, str, str]], list[str]]:
    """(rules, internal_names) from categories.json, or empty if the file does not exist."""
    path = config.home() / "categories.json"
    if not path.is_file():
        return [], []
    data = json.loads(path.read_text())
    rules = []
    for index, rule in enumerate(data.get("rules", []), 1):
        if not (isinstance(rule, list) and len(rule) == 3 and all(isinstance(x, str) for x in rule)):
            raise ValueError(f"{path}: rule {index} must be [category, scope, regex]")
        if rule[1] not in SCOPES:
            raise ValueError(f"{path}: rule {index} scope must be 'household' or 'business', got {rule[1]!r}")
        try:
            re.compile(rule[2])
        except re.error as error:
            raise ValueError(f"{path}: rule {index} has an invalid regex: {error}") from None
        rules.append((rule[0], rule[1], rule[2]))
    return rules, [str(n) for n in data.get("internal_names", [])]


_active: Rules | None = None


def active() -> Rules:
    global _active
    if _active is None:
        _active = Rules(*load_user_file())
    return _active


def reload() -> None:
    global _active
    _active = None


def is_internal(tx: dict, own_last4: set[str]) -> bool:
    """Money moving between your own accounts and family is not spending."""
    text = f"{tx['counterparty']} {tx['description']}"
    if "tikkie" in text.lower():
        return False
    if set(IBAN_TAIL.findall(text)) & own_last4:
        return True
    pattern = active().internal_name
    return bool(pattern and pattern.search(tx["counterparty"]))


def looks_like_person(tx: dict) -> bool:
    name = tx["counterparty"].strip()
    description = tx["description"].lower()
    if not name or any(ch.isdigit() for ch in name) or COMPANY_WORDS.search(name):
        return False
    if "incasso" in description or "machtiging" in description or "apple pay" in description:
        return False
    return bool(PERSON_NAME.match(name))


def categorize(tx: dict) -> tuple[str, str]:
    text = f"{tx['counterparty']} {tx['description']}".lower()
    for category, scope, pattern in active().rules:
        if pattern.search(text):
            return category, scope
    if tx["amount"] < 0 and looks_like_person(tx):
        return "Payments to people", HOUSEHOLD
    return "Uncategorised", HOUSEHOLD


def expense_of(tx: dict, own_last4: set[str]) -> tuple[str, str, float] | None:
    """(category, scope, amount) where amount > 0 is spending and < 0 a refund, or None if it is not an expense."""
    if tx["status"] and tx["status"] != "BOOK":
        return None
    if is_internal(tx, own_last4):
        return None
    category, scope = categorize(tx)
    if tx["amount"] < 0:
        return category, scope, -tx["amount"]
    if category in REFUND_CATEGORIES:
        return category, scope, -tx["amount"]
    return None


KINDS = {"earned", "transfer"}


def load_income_rules() -> list[tuple[str, str, str]]:
    """Private income rules from categories.json: [label, kind, regex] with kind 'earned' or 'transfer'."""
    path = config.home() / "categories.json"
    if not path.is_file():
        return []
    rules = []
    for index, rule in enumerate(json.loads(path.read_text()).get("income_rules", []), 1):
        if not (isinstance(rule, list) and len(rule) == 3 and all(isinstance(x, str) for x in rule)):
            raise ValueError(f"{path}: income rule {index} must be [label, kind, regex]")
        if rule[1] not in KINDS:
            raise ValueError(f"{path}: income rule {index} kind must be 'earned' or 'transfer', got {rule[1]!r}")
        try:
            re.compile(rule[2])
        except re.error as error:
            raise ValueError(f"{path}: income rule {index} has an invalid regex: {error}") from None
        rules.append((rule[0], rule[1], rule[2]))
    return rules
