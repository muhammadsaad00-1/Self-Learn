"""Which language to reply in, decided by code from the customer's own newest message.

The triage model reads the whole conversation, so after English turns it labelled a Roman
Urdu question "en" (found in a real run). Script and a list of everyday Roman Urdu words are
enough to tell, without a model.
"""

import re
from typing import Literal

Language = Literal["en", "ur", "roman_ur"]

_URDU_SCRIPT = re.compile(r"[؀-ۿ]")
# Everyday words that don't occur in English sentences ("hai", not "me" or "to").
_ROMAN_URDU = {
    "hai",
    "hain",
    "kya",
    "kyun",
    "nahi",
    "nahin",
    "mera",
    "meri",
    "mere",
    "mujhe",
    "aap",
    "ap",
    "kal",
    "aaj",
    "abhi",
    "ko",
    "se",
    "ki",
    "ka",
    "ke",
    "kar",
    "karna",
    "raha",
    "rahi",
    "gaye",
    "gayi",
    "gaya",
    "tha",
    "thi",
    "bhej",
    "bheje",
    "bhai",
    "paise",
    "rupay",
    "kitna",
    "kitne",
    "kahan",
    "kab",
    "kaise",
    "wapas",
    "chahiye",
    "hoga",
    "hogi",
    "subah",
    "shaam",
    "raat",
    "bas",
    "theek",
    "acha",
    "shukriya",
    "kat",
    "mile",
    "aur",
    "bhi",
    "koi",
}


def detect(text: str) -> Language | None:
    """'ur' or 'roman_ur' when clear, else None (let the caller's default stand)."""
    if _URDU_SCRIPT.search(text):
        return "ur"
    words = re.findall(r"[a-z]+", text.lower())
    hits = sum(1 for w in words if w in _ROMAN_URDU)
    return "roman_ur" if hits >= 2 and hits / max(len(words), 1) >= 0.2 else None
