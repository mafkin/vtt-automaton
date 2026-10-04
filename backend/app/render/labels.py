LABELS = {
    "Finnish": {
        "raw": "Säännöt (RAW)",
        "ruling": "Tulkinta",
        "full_rule": "Koko sääntö Archives of Nethysissä",
        "raw_only": "vain RAW",
        "confidence": {"high": "varma", "medium": "tulkinnanvarainen", "low": "epävarma"},
    },
    "English": {
        "raw": "Rules as Written",
        "ruling": "Ruling",
        "full_rule": "Full rule on Archives of Nethys",
        "raw_only": "RAW only",
        "confidence": {"high": "high", "medium": "medium", "low": "low"},
    },
}


def labels_for(language: str) -> dict:
    return LABELS.get(language, LABELS["English"])
