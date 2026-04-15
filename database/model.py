from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
from enum import Enum


class FileFormat(str, Enum):
    PDF  = "pdf"
    DOCX = "docx"
    TXT  = "txt"
    UNKNOWN = "unknown"


class IngestionStatus(str, Enum):
    PENDING    = "pending"
    PROCESSING = "processing"
    DONE       = "done"
    FAILED     = "failed"


# ---- CV Parsing Output ----

class ExperienceEntry(BaseModel):
    company:    Optional[str] = None
    role:       Optional[str] = None
    start_date: Optional[str] = None
    end_date:   Optional[str] = None
    duration_months: Optional[int] = None
    description: Optional[str] = None
    technologies: List[str] = Field(default_factory=list)


class EducationEntry(BaseModel):
    institution: Optional[str] = None
    degree:      Optional[str] = None
    field:       Optional[str] = None
    graduation_year: Optional[int] = None
    grade:       Optional[str] = None


class ParsedCV(BaseModel):
    """Structured output from the Ingestion & Parsing Agent."""
    full_name:        Optional[str] = None
    email:            Optional[str] = None
    phone:            Optional[str] = None
    linkedin_url:     Optional[str] = None
    location:         Optional[str] = None

    # Skills (never infer gender/age/nationality — privacy guardrail)
    technical_skills: List[str] = Field(default_factory=list)
    soft_skills:      List[str] = Field(default_factory=list)
    domain_expertise: List[str] = Field(default_factory=list)  # e.g. "cloud", "fintech"
    certifications:   List[str] = Field(default_factory=list)
    languages:        List[str] = Field(default_factory=list)

    # Timeline
    experience:       List[ExperienceEntry] = Field(default_factory=list)
    education:        List[EducationEntry]  = Field(default_factory=list)
    total_years_exp:  Optional[float] = None

    # Summary
    cv_summary:       Optional[str] = None  # One-paragraph LLM summary

    # Quality metadata
    parse_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    parse_warnings:   List[str] = Field(default_factory=list)


# ---- Screening Output ----

class ScreeningResult(BaseModel):
    """Output from the Semantic Matching Agent."""
    candidate_id:       UUID
    cv_version_id:      UUID
    job_id:             UUID

    # Scores
    semantic_similarity: float = Field(ge=0.0, le=1.0)
    relevance_score:     float = Field(ge=0.0, le=1.0)
    potential_score:     float = Field(ge=0.0, le=1.0)
    composite_score:     float = Field(ge=0.0, le=1.0)

    # Explainability
    strengths:           List[str]
    gaps:                List[str]
    transferable_skills: List[str]
    value_add_insights:  List[str]
    llm_rationale:       str   # Full plain-English explanation for HR

    # Validation flags
    anomalies:           List[Dict[str, Any]] = Field(default_factory=list)

    screened_at:         datetime = Field(default_factory=datetime.utcnow)


# ---- API Request/Response Models ----

class JobCreateRequest(BaseModel):
    title:       str
    department:  Optional[str] = None
    location:    Optional[str] = None
    description: str
    created_by:  str


class JobResponse(BaseModel):
    id:          UUID
    title:       str
    department:  Optional[str]
    description_raw: str
    is_active:   bool
    created_at:  datetime

    class Config:
        from_attributes = True


class CandidateResponse(BaseModel):
    id:             UUID
    full_name:      Optional[str]
    email:          Optional[str]
    current_status: str
    is_returning:   bool
    source:         Optional[str]

    class Config:
        from_attributes = True


class BulkUploadResponse(BaseModel):
    total_received: int
    queued:         int
    failed:         int
    correlation_ids: List[str]
    errors:         List[Dict[str, str]]  # filename → error message


class HealthResponse(BaseModel):
    status:      str
    database:    bool
    ollama:      bool
    version:     str = "1.0.0"
