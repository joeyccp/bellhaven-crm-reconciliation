from __future__ import annotations

import re
import unicodedata


STREET_WORDS = {
    "avenue": "ave", "street": "st", "road": "rd", "boulevard": "blvd",
    "drive": "dr", "lane": "ln", "court": "ct", "parkway": "pkwy",
    "highway": "hwy", "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
    "pike": "pike", "pk": "pike",
}


def clean(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text)).strip()


def normalize_street(value: object) -> str:
    words = clean(value).split()
    return " ".join(STREET_WORDS.get(word, word) for word in words)


def normalize_name(value: object) -> str:
    words = clean(value).replace("health care", "healthcare").split()
    aliases = {"centre": "center", "rehab": "rehabilitation", "and": ""}
    return " ".join(aliases.get(word, word) for word in words if aliases.get(word, word))


def normalize_phone(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else digits


def normalize_zip(value: object) -> str:
    match = re.search(r"\d{5}", str(value or ""))
    return match.group(0) if match else ""
