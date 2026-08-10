from datetime import date, datetime


def freshness_status(last_reviewed: str, today: date | None = None) -> str:
    today = today or date.today()
    reviewed = datetime.fromisoformat(last_reviewed).date()
    age = (today - reviewed).days
    if age <= 180:
        return "Current"
    if age <= 365:
        return "Needs Review"
    return "Stale"


def review_status(document_risk: str, freshness: str, owner: str) -> str:
    if document_risk == "High Risk":
        return "Human Review Required"
    if freshness != "Current":
        return "Review Required"
    if not owner or owner == "Unassigned":
        return "Owner Required"
    return "Approved for AI"


def build_governance_summary(documents: list[dict]) -> dict:
    total = len(documents)
    counts = {
        "totalDocuments": total,
        "currentDocuments": sum(1 for d in documents if d["freshnessStatus"] == "Current"),
        "needsReview": sum(1 for d in documents if d["freshnessStatus"] == "Needs Review"),
        "staleDocuments": sum(1 for d in documents if d["freshnessStatus"] == "Stale"),
        "highRiskDocuments": sum(1 for d in documents if d["documentRisk"] == "High Risk"),
        "humanReviewRequired": sum(1 for d in documents if d["humanReviewRequired"]),
    }
    freshness = _pct(counts["currentDocuments"], total)
    review_coverage = _pct(sum(1 for d in documents if d["reviewStatus"] in ["Approved for AI", "Human Review Required"]), total)
    ownership = _pct(sum(1 for d in documents if d.get("contentOwner") and d["contentOwner"] != "Unassigned"), total)
    counts["transparencyScore"] = round((freshness + review_coverage + ownership) / 3)
    counts["byBusinessArea"] = _group(documents, "businessArea")
    counts["byTopic"] = _topic_group(documents)
    counts["byOwner"] = _group(documents, "contentOwner")
    counts["staleContent"] = [
        {"document": d["documentName"], "lastReview": d["lastReviewedDate"], "status": d["freshnessStatus"]}
        for d in documents if d["freshnessStatus"] == "Stale"
    ]
    return counts


def _pct(value: int, total: int) -> int:
    return 0 if total == 0 else round((value / total) * 100)


def _group(documents: list[dict], key: str) -> list[dict]:
    result = {}
    for doc in documents:
        name = doc.get(key) or "Unassigned"
        bucket = result.setdefault(name, {"name": name, "count": 0, "current": 0, "needsReview": 0, "stale": 0})
        bucket["count"] += 1
        if doc["freshnessStatus"] == "Current":
            bucket["current"] += 1
        elif doc["freshnessStatus"] == "Needs Review":
            bucket["needsReview"] += 1
        else:
            bucket["stale"] += 1
    return list(result.values())


def _topic_group(documents: list[dict]) -> list[dict]:
    expanded = []
    for doc in documents:
        for topic in doc.get("topics", []):
            item = dict(doc)
            item["topic"] = topic
            expanded.append(item)
    return _group(expanded, "topic")

