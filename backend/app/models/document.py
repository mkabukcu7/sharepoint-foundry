from pydantic import BaseModel


class DocumentMetadata(BaseModel):
    documentName: str
    fileType: str
    contentOwner: str
    summary: str
    topics: list[str]
    businessArea: str
    audience: str
    documentRisk: str
    lastReviewedDate: str
    freshnessStatus: str
    containsPricing: bool
    containsConfidentialInfo: bool
    suggestedTags: list[str]
    reviewStatus: str
    humanReviewRequired: bool
    sourcePath: str
    extractedCharacters: int

