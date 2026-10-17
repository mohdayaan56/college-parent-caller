import re


def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def normalize_phone(value: str) -> str:
    """Normalize to a comparable E.164-ish form for India-friendly matching."""
    digits = digits_only(value)
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10:
        digits = "91" + digits
    if digits.startswith("0") and len(digits) == 11:
        digits = "91" + digits[1:]
    return "+" + digits


def phones_match(a: str, b: str) -> bool:
    da, db = digits_only(normalize_phone(a)), digits_only(normalize_phone(b))
    if not da or not db:
        return False
    return da == db
