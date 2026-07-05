"""
Central configuration for the RTU Heat Pump Benchmarking application.

Everything that is "data" rather than "code" lives under /config as JSON so
that new competitors, synonyms, or scoring rules can be added by editing a
file instead of touching Python. This module is the single place that knows
where things are on disk and exposes small, typed accessors around that data.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------

BASE_DIR: Path = Path(__file__).resolve().parent.parent

CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "output"
SOURCE_DOCS_DIR = BASE_DIR / "source_documents"
LOGS_DIR = BASE_DIR / "logs"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

DEFAULT_PARAMETER_FILE = BASE_DIR / "Physical_Data.xlsx"
COMPARISON_EXCEL_PATH = OUTPUT_DIR / "comparison.xlsx"
BENCHMARK_JSON_PATH = OUTPUT_DIR / "benchmark.json"

for _dir in (CONFIG_DIR, DATA_DIR, UPLOADS_DIR, OUTPUT_DIR, SOURCE_DOCS_DIR, LOGS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Small typed views over the JSON registries
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Competitor:
    id: str
    name: str
    color: str
    logo: str
    homepage: str
    allowed_domains: list[str] = field(default_factory=list)
    search_urls: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    @property
    def source_dir(self) -> Path:
        """Folder engineers can drop manufacturer PDFs into by hand.

        This is always checked by the pipeline *before* attempting any live
        scraping, so the tool keeps working even with no internet access or
        when a manufacturer gates its spec sheets behind a login/JS form.
        """
        # Normalize id -> folder name used at scaffold time (CamelCase, no underscores)
        folder_map = {
            "carrier": "Carrier",
            "trane": "Trane",
            "lennox": "Lennox",
            "johnson_controls": "JohnsonControls",
            "daikin": "Daikin",
            "rheem": "Rheem",
            "aaon": "AAON",
        }
        folder_name = folder_map.get(self.id, self.id.title().replace("_", ""))
        path = SOURCE_DOCS_DIR / folder_name
        path.mkdir(parents=True, exist_ok=True)
        return path


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class Registry:
    """Loads and caches the JSON configuration registries.

    Re-reading is cheap and intentional: editing config/*.json takes effect
    on the next benchmarking run without restarting the server.
    """

    @staticmethod
    def competitors() -> list[Competitor]:
        raw = _load_json(CONFIG_DIR / "competitors.json")
        # Ignore any keys that aren't actual Competitor fields (e.g. a "_note"
        # left by whoever last edited the JSON) so config files can carry
        # human-readable documentation without breaking the loader.
        known_fields = {f.name for f in fields(Competitor)}
        return [
            Competitor(**{k: v for k, v in c.items() if k in known_fields})
            for c in raw["competitors"]
        ]

    @staticmethod
    def competitor_map() -> dict[str, Competitor]:
        return {c.id: c for c in Registry.competitors()}

    @staticmethod
    def synonym_groups() -> list[dict[str, Any]]:
        raw = _load_json(CONFIG_DIR / "parameter_synonyms.json")
        return raw["groups"]

    @staticmethod
    def directionality_rules() -> dict[str, list[str]]:
        return _load_json(CONFIG_DIR / "parameter_rules.json")


# ---------------------------------------------------------------------------
# Runtime settings (env-overridable, all optional)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    # Networking
    request_timeout_seconds: int = int(os.getenv("RTU_REQUEST_TIMEOUT", "15"))
    max_pdfs_per_competitor: int = int(os.getenv("RTU_MAX_PDFS_PER_COMPETITOR", "5"))
    user_agent: str = os.getenv(
        "RTU_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RTUBenchmarkBot/1.0 "
        "(+internal engineering benchmarking tool)",
    )

    # Matching engine
    use_sentence_transformers: bool = os.getenv("RTU_USE_EMBEDDINGS", "1") != "0"
    embedding_model_name: str = os.getenv(
        "RTU_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    match_confidence_floor: float = float(os.getenv("RTU_MATCH_FLOOR", "0.42"))

    # OCR
    enable_ocr: bool = os.getenv("RTU_ENABLE_OCR", "1") != "0"

    # Server
    host: str = os.getenv("RTU_HOST", "0.0.0.0")
    port: int = int(os.getenv("RTU_PORT", "8000"))


settings = Settings()
