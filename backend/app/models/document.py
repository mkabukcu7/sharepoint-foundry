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
    approvedTaxonomy: dict | None = None


class MetadataReviewUpdate(BaseModel):
    field: Literal[
        "businessArea",
        "audience",
        "language",
        "author",
        "countryOfOrigin",
        "materialType",
        "topics",
        "businesses",
        "industries",
        "geographies",
        "collections",
        "languages",
    ]
    decision: Literal["accepted", "edited", "rejected"]
    value: str | list[str] | None = Field(default=None)


class ClassificationFlagResolution(BaseModel):
    flagId: str = Field(min_length=1, max_length=200)
    reviewer: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)


class MetadataApproval(BaseModel):
    reviewer: str = Field(min_length=1, max_length=120)


class SharePointVersionActionUpdate(BaseModel):
    action: Literal["new-document", "replace-existing"]


class SharePointStagingImport(BaseModel):
    documentNames: list[str] = Field(min_length=1, max_length=20)


class SearchQuery(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top: int = Field(default=5, ge=1, le=20)
