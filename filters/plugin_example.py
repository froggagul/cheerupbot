import re


def keep_non_manager_and_seoul(row: dict) -> bool:
    text = " ".join(
        str(row.get(key, ""))
        for key in ("title", "company", "location")
    ).lower()
    if not text.strip():
        return False
    if re.search(r"팀장|manager|head|lead|senior", text, flags=re.IGNORECASE):
        return False
    return True
