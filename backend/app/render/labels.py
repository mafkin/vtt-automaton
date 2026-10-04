LABELS = {
    "Finnish": {
        "raw": "Säännöt (RAW)",
        "ruling": "Tulkinta",
        "raw_only": "vain RAW",
        "confidence": {"high": "varma", "medium": "tulkinnanvarainen", "low": "epävarma"},
    },
    "English": {
        "raw": "Rules as Written",
        "ruling": "Ruling",
        "raw_only": "RAW only",
        "confidence": {"high": "high", "medium": "medium", "low": "low"},
    },
}


def labels_for(language: str) -> dict:
    return LABELS.get(language, LABELS["English"])
