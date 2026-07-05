"""Pydantic data models shared across the API, pipeline, and job manager."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class StartBenchmarkRequest(BaseModel):
    competitors: list[str] = Field(..., description="Competitor ids, e.g. ['carrier','trane']")
    use_default_parameters: bool = Field(
        True, description="If true, use Physical_Data.xlsx; otherwise use uploaded_file_token"
    )
    uploaded_file_token: Optional[str] = Field(
        None, description="Token returned by /api/upload-parameters when a custom sheet was uploaded"
    )
    enable_web_scraping: bool = Field(
        True,
        description="If true, attempt live scraping of official manufacturer sites in addition to any "
        "PDFs manually placed in source_documents/<Competitor>/",
    )
    series_name: Optional[str] = Field(
        None,
        description="Exact product series / model name to scope the benchmark to, e.g. "
        "'WeatherExpert' or 'Premier YZ036'. Takes priority over unit_config_description.",
    )
    unit_config_description: Optional[str] = Field(
        None,
        description="Free-text unit configuration to search for when the exact series/model isn't "
        "known, e.g. '25 ton heat pump rooftop unit with gas heat and economizer'. Only used if "
        "series_name is blank.",
    )


class ParameterCell(BaseModel):
    value: Optional[str] = None
    source_document: Optional[str] = None
    page_number: Optional[int] = None
    confidence: float = 0.0
    matched_phrase: Optional[str] = None


class ParameterRow(BaseModel):
    category: Optional[str] = None
    parameter: str
    unit: Optional[str] = None
    values: dict[str, ParameterCell] = Field(default_factory=dict)  # competitor_id -> cell
    is_best_highlight: dict[str, bool] = Field(default_factory=dict)
    has_discrepancy: bool = False


class JobStatus(BaseModel):
    job_id: str
    status: str  # queued | running | completed | failed | cancelled
    progress: int = 0  # 0-100
    message: str = ""
    stage: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    stats: dict = Field(default_factory=dict)


class BenchmarkSummary(BaseModel):
    competitors: list[str]
    parameters_total: int
    parameters_matched: int
    parameters_missing: int
    documents_processed: int
    documents_downloaded: int
    extraction_accuracy: float
    generated_at: str
