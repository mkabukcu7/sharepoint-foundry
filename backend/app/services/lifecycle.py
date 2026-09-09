import re


def lifecycle_metadata(text: str) -> dict:
    age = _integer_field(text, "Last reviewed age days")
    approval = _text_field(text, "Approval status") or "Not Recorded"
    if age is None:
        review_status = "Not Recorded"
    elif age <= 180:
        review_status = "Current"
    elif age <= 365:
        review_status = "Needs Review"
    else:
        review_status = "Stale"
    return {
        "reviewStatus": review_status,
        "approvalStatus": approval,
        "recencyDays": age,
    }


def _integer_field(text: str, label: str) -> int | None:
    match = re.search(rf"{re.escape(label)}:\s*(\d+)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _text_field(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}:\s*([^.]+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else None