from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from app.config import settings


_TITLES = re.compile(
    r"\b(mr|mrs|ms|miss|dr|sir|madam|ji)\b\.?",
    re.IGNORECASE,
)
_FILLER = re.compile(
    r"\b(this is|i am|i'm|my name is|myself|speaking|hello|hi|yes|yeah|ok|"
    r"okay|it is|it's|the parent|guardian)\b",
    re.IGNORECASE,
)


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = _TITLES.sub(" ", text)
    text = _FILLER.sub(" ", text)
    text = "".join(
        char if unicodedata.category(char)[0] in {"L", "M"} or char.isspace() else " "
        for char in text
    )
    return " ".join(text.lower().split())


def extract_spoken_name(utterance: str) -> str:
    cleaned = normalize_name(utterance)
    return cleaned


def names_match(spoken: str, registered: str, threshold: float | None = None) -> bool:
    cutoff = settings.name_match_threshold if threshold is None else threshold
    a = normalize_name(spoken)
    b = normalize_name(registered)
    if not a or not b:
        return False
    if a == b:
        return True
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if len(b_tokens) > 1 and len(a_tokens) < 2:
        return False
    if b_tokens.issubset(a_tokens):
        return True
    # Allow small transcription errors, but never accept a surname alone.
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= cutoff and len(a_tokens & b_tokens) >= min(2, len(b_tokens))
