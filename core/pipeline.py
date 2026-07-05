"""
BenchmarkPipeline: end-to-end orchestration.

    gather documents  ->  extract candidates  ->  semantic match
    ->  build comparison matrix  ->  write comparison.xlsx + benchmark.json

Every stage reports progress through the shared ``job_manager`` so the
frontend can show a live progress bar, and every stage writes to a
per-job log file (downloaded files, search URLs, matched/missing
parameters, errors, timings) as required by the spec.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.config import COMPARISON_EXCEL_PATH, BENCHMARK_JSON_PATH, Competitor, Registry
from core.excel_io import write_comparison_excel
from core.job_manager import JobCancelledError, job_manager
from core.logging_setup import get_run_logger
from core.matching import MatchingEngine
from core.pdf_extractor import CandidatePhrase, PDFExtractor
from core.scraper import WebScraper
from core.schemas import ParameterCell, ParameterRow

_NUMERIC_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _leading_number(value: str) -> Optional[float]:
    match = _NUMERIC_RE.search(value)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


class BenchmarkPipeline:
    def __init__(
        self,
        job_id: str,
        competitor_ids: list[str],
        parameter_defs: list[dict],
        enable_web_scraping: bool = True,
        unit_query: Optional[str] = None,
    ) -> None:
        self.job_id = job_id
        self.log = get_run_logger(job_id)
        comp_map = Registry.competitor_map()
        self.competitors: list[Competitor] = [
            comp_map[cid] for cid in competitor_ids if cid in comp_map
        ]
        unknown = set(competitor_ids) - set(comp_map)
        if unknown:
            self.log.warning("Ignoring unknown competitor ids: %s", sorted(unknown))

        self.parameter_defs = parameter_defs
        self.enable_web_scraping = enable_web_scraping
        # Optional series/model name (or, failing that, a free-text unit
        # configuration description) the user typed on the setup page to
        # scope this run to one specific unit instead of an entire product
        # family. None means "benchmark whatever the documents contain".
        self.unit_query = (unit_query or "").strip() or None

        self.scraper = WebScraper(self.log)
        self.extractor = PDFExtractor(run_logger=self.log)
        self.matcher = MatchingEngine()
        self.directionality = Registry.directionality_rules()

    # ------------------------------------------------------------------
    def run(self) -> None:
        start = time.time()
        self.log.info(
            "Benchmark job %s started: competitors=%s, parameters=%d, scraping=%s, query=%r",
            self.job_id, [c.id for c in self.competitors], len(self.parameter_defs),
            self.enable_web_scraping, self.unit_query,
        )

        n_stages = len(self.competitors) + 2
        progress_step = 90 // max(n_stages, 1)
        progress = 2
        job_manager.update(self.job_id, progress=progress, stage="init",
                            message="Preparing benchmarking run…")

        candidates_by_competitor: dict[str, list[CandidatePhrase]] = {}
        docs_processed = 0
        docs_downloaded = 0

        for competitor in self.competitors:
            if job_manager.is_cancelled(self.job_id):
                self.log.info("Job cancelled before starting %s — stopping.", competitor.name)
                raise JobCancelledError()

            job_manager.update(
                self.job_id, stage="collecting", progress=progress,
                message=f"Searching official sources for {competitor.name}…",
            )
            docs = self.scraper.gather_documents(
                competitor, self.enable_web_scraping, query=self.unit_query
            )
            docs_downloaded += len(docs)

            all_candidates: list[CandidatePhrase] = []
            # Progress used to only advance once per *competitor*, so a
            # single large document (or the only competitor selected) left
            # the bar sitting still for many minutes even while genuinely
            # working — indistinguishable from actually being stuck. Now it
            # ticks up after each document within the competitor's own
            # progress allocation, so multi-minute extractions show visible
            # movement instead of one long pause.
            per_doc_step = progress_step / max(len(docs), 1)
            for doc_index, doc_path in enumerate(docs, start=1):
                if job_manager.is_cancelled(self.job_id):
                    self.log.info(
                        "Job cancelled mid-way through %s (%d/%d documents done) — stopping.",
                        competitor.name, doc_index - 1, len(docs),
                    )
                    raise JobCancelledError()

                doc_message = f"Extracting {doc_path.name} ({doc_index}/{len(docs)}) for {competitor.name}…"
                if self.unit_query:
                    doc_message = (
                        f"Extracting '{self.unit_query}' from {doc_path.name} "
                        f"({doc_index}/{len(docs)}) for {competitor.name}…"
                    )
                job_manager.update(
                    self.job_id, stage="extracting",
                    progress=int(progress + (doc_index - 1) * per_doc_step),
                    message=doc_message,
                )
                try:
                    all_candidates.extend(self.extractor.extract_candidates(doc_path, query=self.unit_query))
                    docs_processed += 1
                except Exception as exc:  # noqa: BLE001
                    self.log.error("Failed extracting %s: %s", doc_path, exc)

            candidates_by_competitor[competitor.id] = all_candidates
            self.log.info("%s: %d candidate phrases from %d document(s)",
                           competitor.name, len(all_candidates), len(docs))
            progress += progress_step

        job_manager.update(self.job_id, stage="matching", progress=progress,
                            message="Running AI semantic/fuzzy parameter matching…")
        parameter_rows = self._build_parameter_rows(candidates_by_competitor)
        progress += progress_step

        matched = sum(
            1 for row in parameter_rows if any(c.value for c in row.values.values())
        )
        missing = len(parameter_rows) - matched
        accuracy = round(matched / len(parameter_rows), 4) if parameter_rows else 0.0

        job_manager.update(self.job_id, stage="reporting", progress=96,
                            message="Generating comparison Excel and dashboard data…")
        write_comparison_excel(
            COMPARISON_EXCEL_PATH, parameter_rows, self.competitors, query=self.unit_query
        )
        summary = self._write_benchmark_json(parameter_rows, docs_processed, docs_downloaded, accuracy)

        elapsed = round(time.time() - start, 1)
        self.log.info(
            "Benchmark job %s completed in %.1fs — matched=%d missing=%d accuracy=%.1f%%",
            self.job_id, elapsed, matched, missing, accuracy * 100,
        )
        job_manager.update(
            self.job_id, status="completed", progress=100,
            stage="done", message=f"Completed in {elapsed}s",
            stats={
                "parameters_total": len(parameter_rows),
                "parameters_matched": matched,
                "parameters_missing": missing,
                "extraction_accuracy": accuracy,
                "documents_processed": docs_processed,
                "documents_downloaded": docs_downloaded,
                "elapsed_seconds": elapsed,
                "comparison_excel": str(COMPARISON_EXCEL_PATH),
                "benchmark_json": str(BENCHMARK_JSON_PATH),
                "unit_query": self.unit_query,
            },
        )

    # ------------------------------------------------------------------
    def _build_parameter_rows(
        self, candidates_by_competitor: dict[str, list[CandidatePhrase]]
    ) -> list[ParameterRow]:
        rows: list[ParameterRow] = []

        # Per (competitor_id, category), the set of candidate identities
        # already claimed by an earlier sibling row in that same category.
        # This is what stops "Quantity/Size", "Type", "Capacity Steps" (all
        # under one compressor-data category) from silently repeating the
        # same single candidate four times over — see find_best_match's
        # exclude_identities for the full reasoning.
        used_per_competitor_category: dict[tuple[str, Optional[str]], set[tuple]] = {}

        for pdef in self.parameter_defs:
            parameter = pdef["parameter"]
            category = pdef.get("category")
            row = ParameterRow(category=category, parameter=parameter, unit=pdef.get("unit"))

            # Some parameter templates reuse generic labels ("Type", "Capacity
            # Steps") across many categories (compressor data vs. supply fan
            # vs. return fan, etc.) — matching on the bare label alone can't
            # tell those apart and would collapse them all onto whichever
            # candidate scores highest overall. Folding the category into the
            # match query disambiguates them without changing what's shown to
            # the user (the row still displays `parameter` on its own).
            effective_query = f"{category} {parameter}" if category else parameter

            for competitor in self.competitors:
                candidates = candidates_by_competitor.get(competitor.id, [])
                used_key = (competitor.id, category)
                already_used = used_per_competitor_category.get(used_key, set())

                match = self.matcher.find_best_match(effective_query, candidates, exclude_identities=already_used)
                if match is None:
                    row.values[competitor.id] = ParameterCell(confidence=0.0)
                    self.log.info("MISSING  | %-30s | %s", parameter, competitor.name)
                    continue

                used_per_competitor_category.setdefault(used_key, set()).add(
                    self.matcher.candidate_identity(match.candidate)
                )

                row.values[competitor.id] = ParameterCell(
                    value=match.candidate.value,
                    source_document=match.candidate.source_document,
                    page_number=match.candidate.page_number,
                    confidence=round(match.score, 3),
                    matched_phrase=match.candidate.phrase,
                )
                self.log.info(
                    "MATCHED  | %-30s | %-20s | score=%.2f (%s) | '%s' -> '%s'",
                    parameter, competitor.name, match.score, match.matched_via,
                    match.candidate.phrase, match.candidate.value,
                )

            self._annotate_discrepancy_and_best(row, parameter)
            rows.append(row)

        return rows

    def _annotate_discrepancy_and_best(self, row: ParameterRow, parameter: str) -> None:
        non_empty = {cid: cell.value for cid, cell in row.values.items() if cell.value}
        if len(non_empty) >= 2:
            normalized = {re.sub(r"\s+", " ", v.strip().lower()) for v in non_empty.values()}
            row.has_discrepancy = len(normalized) > 1

        canonical = self.matcher.canonical_name(parameter)
        if canonical is None or len(non_empty) < 2:
            return

        numeric_values = {cid: _leading_number(v) for cid, v in non_empty.items()}
        numeric_values = {cid: v for cid, v in numeric_values.items() if v is not None}
        if len(numeric_values) < 2:
            return

        if canonical in self.directionality.get("higher_is_better", []):
            best_id = max(numeric_values, key=numeric_values.get)
        elif canonical in self.directionality.get("lower_is_better", []):
            best_id = min(numeric_values, key=numeric_values.get)
        else:
            return

        row.is_best_highlight[best_id] = True

    # ------------------------------------------------------------------
    def _write_benchmark_json(
        self,
        parameter_rows: list[ParameterRow],
        docs_processed: int,
        docs_downloaded: int,
        accuracy: float,
    ) -> dict:
        matched = sum(1 for row in parameter_rows if any(c.value for c in row.values.values()))
        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "job_id": self.job_id,
            "unit_query": self.unit_query,
            "competitors": [
                {"id": c.id, "name": c.name, "color": c.color} for c in self.competitors
            ],
            "summary": {
                "competitors": [c.id for c in self.competitors],
                "parameters_total": len(parameter_rows),
                "parameters_matched": matched,
                "parameters_missing": len(parameter_rows) - matched,
                "documents_processed": docs_processed,
                "documents_downloaded": docs_downloaded,
                "extraction_accuracy": accuracy,
            },
            "parameters": [
                {
                    "category": row.category,
                    "parameter": row.parameter,
                    "unit": row.unit,
                    "has_discrepancy": row.has_discrepancy,
                    "values": {
                        cid: {
                            "value": cell.value,
                            "source_document": cell.source_document,
                            "page_number": cell.page_number,
                            "confidence": cell.confidence,
                            "matched_phrase": cell.matched_phrase,
                            "is_best": row.is_best_highlight.get(cid, False),
                        }
                        for cid, cell in row.values.items()
                    },
                }
                for row in parameter_rows
            ],
        }

        BENCHMARK_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(BENCHMARK_JSON_PATH, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)
        return summary
