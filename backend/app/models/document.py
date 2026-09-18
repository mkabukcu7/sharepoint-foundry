from typing import Literal

from pydantic import BaseModel, Field


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
    wtwClassification: dict | None = None


class MetadataReviewUpdate(BaseModel):
    field: Literal["businessArea", "audience", "language", "author", "countryOfOrigin"]
    decision: Literal["accepted", "edited", "rejected"]
    value: str | None = Field(default=None, max_length=200)

