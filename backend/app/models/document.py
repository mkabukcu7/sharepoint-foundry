from pydantic import BaseModel


class DocumentMetadata(BaseModel):
    documentName: str
    fileType: str
    summary: str
    themes: list[str]
    suggestedTags: list[str]
    language: str
    author: str
    sentiment: str
    businessArea: str
    audience: str
    metadataCategory: str
    countryOfOrigin: str
    customMetadata: dict[str, str]
    reviewStatus: str
    approvalStatus: str
    recencyDays: int | None
    metadataLink: str
    sourcePath: str
    extractedCharacters: int

