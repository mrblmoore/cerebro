"""
Redaction — strip secrets and sensitive personal data out of captured text.

Activity capture (screenshots, typed text, transcripts) inevitably sweeps up
things Cerebro must never keep: a password typed into a login box, a card number
read aloud on a call, an API key pasted into a terminal. This is the gate every
piece of captured content passes through before it is written to disk.

Two rules shape it:

* **Fail safe.** When something *looks* like a secret, redact it. A false
  positive costs a masked token; a false negative stores a credential.
* **Redact, don't drop.** The surrounding context is what makes the capture
  useful, so a matched secret becomes ``[redacted:password]`` in place rather
  than removing the whole line.
"""

import re
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class Rule:
    name: str
    pattern: "re.Pattern"
    #: When set, only this capture group is masked, keeping a readable label
    #: (e.g. "password: [redacted]" rather than "[redacted]").
    group: int = 0


def _compile(pattern: str) -> "re.Pattern":
    return re.compile(pattern, re.IGNORECASE)


# The order matters: more specific rules run first so a card number is labelled
# as a card, not caught by a generic long-digit rule.
RULES: List[Rule] = [
    # --- credentials keyed by a nearby label ---------------------------------
    Rule("password", _compile(
        r"\b(?:pass(?:word|wd|phrase)?|pwd|pin|passcode)\b"
        r"\s*(?:is|was|=|:)?\s*(\S{3,})"), group=1),
    Rule("secret", _compile(
        r"\b(?:secret|api[_-]?key|access[_-]?key|client[_-]?secret|token|auth)\b"
        r"\s*[:=]?\s*(\S{6,})"), group=1),
    Rule("connection_string", _compile(
        r"\b(?:pwd|password)=([^;\s]+)", ), group=1),

    # --- token shapes recognisable on their own ------------------------------
    Rule("bearer_token", _compile(r"\bBearer\s+([A-Za-z0-9._\-]{16,})"), group=1),
    Rule("jwt", _compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}")),
    Rule("aws_key", _compile(r"\bAKIA[0-9A-Z]{16}\b")),
    Rule("openai_key", _compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    Rule("private_key", _compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----")),

    # --- financial / identity ------------------------------------------------
    # 13-19 digit runs that pass Luhn — checked in code, not by the regex.
    Rule("credit_card", _compile(r"\b\d(?:[ -]?\d){12,18}\b")),
    Rule("ssn", _compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    Rule("iban", _compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),
]

# Applied after masking; these are informational rather than secret, so they are
# only masked when the caller asks for aggressive PII handling.
PII_RULES: List[Rule] = [
    Rule("email", _compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    Rule("phone", _compile(r"\b(?:\+?\d{1,3}[ .\-]?)?(?:\(?\d{3}\)?[ .\-]?)\d{3}[ .\-]?\d{4}\b")),
]


def _luhn(digits: str) -> bool:
    """Luhn check, so ordinary long numbers are not mistaken for card numbers."""
    numbers = [int(character) for character in digits if character.isdigit()]
    if not 13 <= len(numbers) <= 19:
        return False
    checksum = 0
    parity = len(numbers) % 2
    for index, digit in enumerate(numbers):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def redact(text: str, redact_pii: bool = False) -> Tuple[str, List[str]]:
    """
    Redact secrets in ``text``. Returns the cleaned text and the list of rule
    names that fired, so callers can log *that* something was removed without
    logging *what*.
    """
    if not text:
        return text, []

    fired: List[str] = []

    def mask(rule: Rule, match: "re.Match") -> str:
        if rule.name == "credit_card" and not _luhn(match.group(0)):
            return match.group(0)  # a long number that is not a card
        fired.append(rule.name)
        placeholder = f"[redacted:{rule.name}]"
        if rule.group and match.groups():
            start, end = match.span(rule.group)
            return match.group(0)[: start - match.start()] + placeholder \
                + match.group(0)[end - match.start():]
        return placeholder

    rules = RULES + (PII_RULES if redact_pii else [])
    for rule in rules:
        text = rule.pattern.sub(lambda m, r=rule: mask(r, m), text)

    # De-duplicate while preserving order, so the log reads cleanly.
    seen = set()
    unique = [name for name in fired if not (name in seen or seen.add(name))]
    return text, unique


def looks_sensitive(window_title: str, application: str = "") -> bool:
    """
    True when the *whole* capture should be skipped, not merely redacted.

    A password manager, a bank login, a Windows credential prompt — capturing
    these at all is a mistake no amount of field-level redaction fixes, so the
    activity recorder drops the frame entirely.
    """
    haystack = f"{window_title or ''} {application or ''}".lower()
    markers = (
        "password", "sign in", "log in", "login", "credential", "authenticator",
        "1password", "lastpass", "bitwarden", "keepass", "keeper",
        "bank", "banking", "wallet", "paypal", "checkout", "payment",
        "private browsing", "incognito",
    )
    return any(marker in haystack for marker in markers)
