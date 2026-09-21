import os

from dotenv import load_dotenv

from backend.app.services.ai_providers import get_provider


def main() -> None:
    load_dotenv()
    provider_name = os.getenv("AI_PROVIDER", "mock").strip().lower()
    if provider_name != "foundry_wtw":
        raise RuntimeError(f"Live demo requires AI_PROVIDER=foundry_wtw, not {provider_name}")

    provider = get_provider()
    result = provider.analyze(
        "demo-readiness-check.txt",
        (
            "Benefits Overview. Business area: Benefits. Metadata category: FAQ. "
            "Author: Demo Readiness. Language: English. Sentiment: Neutral. "
            "Approval status: Pending. Last reviewed age days: 10. "
            "Employee guidance about benefit eligibility and plan resources."
        ),
    )
    classification = result.get("wtwClassification")
    if not isinstance(classification, dict):
        raise RuntimeError("Foundry response did not include a controlled WTW classification")
    if not isinstance(classification.get("reviewRequired"), bool):
        raise RuntimeError("Foundry classification did not include a valid reviewRequired value")
    print(
        "Foundry live check passed: "
        f"material type={result.get('metadataCategory', 'Unknown')}, "
        f"topics={len(result.get('themes', []))}, "
        f"review required={classification['reviewRequired']}"
    )


if __name__ == "__main__":
    main()
