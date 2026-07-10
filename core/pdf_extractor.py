"""
PDF extraction engine.

Responsible for turning a manufacturer PDF (spec sheet, submittal, IOM,
engineering manual) into a flat list of ``CandidatePhrase`` objects: a label
seen in the document plus whatever value/number/unit sits next to it, tagged
with the source file and page number. The matching engine then scores each
candidate against each Column B parameter.

Extraction strategy, cheapest-first:
  1. PyMuPDF (fitz) text extraction — fast, works for almost all native PDFs.
  2. pdfplumber table extraction — spec sheets are usually laid out as
     "Label | Value | Unit" tables; this recovers that structure exactly.
  3. OCR (pytesseract) fallback for pages that render almost no text, which
     usually means the page is a scanned image. This is optional: if
     tesseract isn't installed on the machine we log a warning and skip it
     rather than failing the whole run.

All heavy dependencies are imported lazily/defensively so the app still boots
and does best-effort extraction even if an optional package is missing.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz

from core.text_utils import normalize

logger = logging.getLogger(__name__)

# How closely a page's text must match a series/model/unit-configuration
# query (rapidfuzz 0-100 scale) to be considered "about" that unit. Kept
# fairly permissive because manuals rarely repeat a full query string
# verbatim — token_set_ratio and partial_ratio both reward partial overlap.
_QUERY_RELEVANCE_THRESHOLD = 58

# --- optional dependencies -------------------------------------------------
try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None
    logger.warning("PyMuPDF (fitz) not installed — falling back to pdfplumber only.")

try:
    import pdfplumber
except ImportError:  # pragma: no cover
    pdfplumber = None
    logger.warning("pdfplumber not installed — table extraction disabled.")

try:
    import pytesseract
    from PIL import Image
except ImportError:  # pragma: no cover
    pytesseract = None
    Image = None


# A line like "Cooling Capacity ........ 240,000 Btuh" or "EER: 11.2"
_VALUE_LINE_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z0-9 /\-\(\)%°'\.]{2,60}?)\s*[:\-–—]{1,}\s*"
    r"(?P<value>[\d][\w\.,/\-–\s%°Ø]{0,40})\s*$"
)
# A table-ish line "Label   123 unit" separated by 2+ spaces or a tab
_TABLE_LINE_RE = re.compile(
    r"^(?P<label>[A-Za-z][A-Za-z0-9 /\-\(\)%°'\.]{2,60}?)\s{2,}(?P<value>.+)$"
)
# Table-of-contents / index dot-leader lines, e.g. "Controls ....................... 17"
# or "Figure 12: Something .... 36". These are page references, not spec values,
# and must never be treated as label/value candidates.
_TOC_DOT_LEADER_RE = re.compile(r"\.{4,}\s*\d{1,4}\s*$")
_TOC_HEADING_RE = re.compile(r"table of contents", re.IGNORECASE)

# BACnet/Modbus/LonTalk "points list" tables are made almost entirely of rows
# like "Point Name | Description | Yes/No – Command" or "... On/Off – Status".
# A document that includes one (system integration guides, controller
# reference manuals) can bury a handful of genuine spec values under hundreds
# of these generic entries, none of which are physical/performance data.
# Flagged the same way as TOC pages: by content, not by filename/link text,
# since these live inside otherwise-legitimate spec/engineering documents.
_POINTS_LIST_MARKER_RE = re.compile(r"\b(?:Yes/No|On/Off)\b[^\n]{0,25}\b(?:Command|Status)\b", re.IGNORECASE)
# A *value* that starts with one of these enum-type descriptors is, on its
# own, enough to call it a BACnet/Modbus point definition rather than a real
# spec answer — regardless of whatever (sometimes garbled-encoding) text
# follows it. This is deliberately broader than _POINTS_LIST_MARKER_RE, which
# needs the paired "Command"/"Status" keyword and is used for page-level
# detection; this one is the last-resort, per-value backstop.
_JUNK_VALUE_START_RE = re.compile(r"^\s*(?:yes/no|on/off|read/write|r/w)\b", re.IGNORECASE)

# --- tonnage-per-column detection for spec tables --------------------------
# Manufacturer spec/submittal tables commonly document an entire model-size
# range side by side (one column per tonnage). Detecting which column is
# which tonnage lets the pipeline split a single "3T/5T/7.5T/10T" table into
# per-tonnage candidates instead of collapsing every size into one joined
# string — see `_detect_column_tonnages`.
_TONNAGE_TOKEN_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*[- ]?tons?\b", re.IGNORECASE)
_TONNAGE_LABEL_RE = re.compile(
    r"\b(?:nominal\s+(?:cooling\s+)?capacity|tonnage|unit\s+size|nominal\s+size)\b",
    re.IGNORECASE,
)
_MODEL_SIZE_CODE_RE = re.compile(r"^0*(\d{2,3})$")
# Standard HVAC nominal-capacity model-size codes (kBtu/h ÷ 12 = tons) that
# manufacturers use as column/model headers instead of spelling out "5 Ton".
_SIZE_CODE_TO_TONS: dict[int, float] = {
    18: 1.5, 24: 2, 30: 2.5, 36: 3, 42: 3.5, 48: 4, 60: 5,
    72: 6, 90: 7.5, 102: 8.5, 120: 10, 150: 12.5, 180: 15,
    210: 17.5, 240: 20, 300: 25,
}


def _parse_tons_cell(cell: str, allow_bare_number: bool = False) -> Optional[float]:
    """Best-effort parse of a single table cell as a tonnage value.

    ``allow_bare_number`` controls whether a plain number like "5" (with no
    "Ton" unit and no recognized model-size code) counts as a tonnage. That's
    only trustworthy when the *row itself* is already known to be a Nominal
    Capacity/Tonnage/Unit Size row (see `_detect_column_tonnages`) — applied
    to arbitrary header rows, it false-positives on every other small numeric
    spec (amps, inches, superheat °F, ...) that happens to fall in-range.
    """
    if not cell:
        return None
    token_match = _TONNAGE_TOKEN_RE.search(cell)
    if token_match:
        try:
            return float(token_match.group(1))
        except ValueError:
            return None
    stripped = cell.strip()
    size_match = _MODEL_SIZE_CODE_RE.match(stripped)
    if size_match:
        mapped = _SIZE_CODE_TO_TONS.get(int(size_match.group(1)))
        if mapped is not None:
            return mapped
    if allow_bare_number and re.fullmatch(r"\d{1,2}(?:\.\d{1,2})?", stripped):
        try:
            value = float(stripped)
        except ValueError:
            return None
        if 0.5 <= value <= 30:  # sane rooftop-unit tonnage range
            return value
    return None


def _detect_column_tonnages(rows: list[list[str]]) -> Optional[list[Optional[float]]]:
    """Return one tonnage-per-value-column guess for a table, or None if the
    table doesn't look tonnage-differentiated at all (the common case).

    Tried cheapest/most-reliable first: an explicit "Nominal Capacity" /
    "Tonnage" / "Unit Size" row's own cell values are the tonnage per column
    (bare numbers trusted here, since the row label itself confirms context);
    failing that, header-ish rows are scanned for "5 Ton" style tokens or
    standard nominal model-size codes (036, 048, 060, ...) — but *not* bare
    numbers, since an unlabeled header row full of plain digits is far too
    ambiguous (could be amps, dimensions, model years, anything).
    """
    for row in rows:
        if not row or not row[0]:
            continue
        if _TONNAGE_LABEL_RE.search(row[0]):
            tonnages = [_parse_tons_cell(c, allow_bare_number=True) for c in row[1:]]
            if sum(1 for t in tonnages if t is not None) >= 2:
                return tonnages

    for row in rows[:3]:
        if not row:
            continue
        tonnages = [_parse_tons_cell(c, allow_bare_number=False) for c in row[1:]]
        if sum(1 for t in tonnages if t is not None) >= 2:
            return tonnages

    return None


@dataclass
class CandidatePhrase:
    """One (label -> value) pair pulled out of a document."""

    phrase: str
    value: str
    source_document: str
    page_number: int
    # Nominal unit tonnage this value belongs to, when the source table
    # documented multiple sizes of the same series side by side (e.g. a
    # "3 Ton / 5 Ton / 7.5 Ton" spec table). None means "not tonnage-scoped"
    # — either a single-size document, or free-text/prose extraction.
    tonnage: Optional[float] = None


class PDFExtractor:
    """Extracts label/value candidate phrases from a PDF file."""

    def __init__(self, enable_ocr: bool = True, run_logger: Optional[logging.Logger] = None) -> None:
        # Accept an injected per-job logger (same pattern as WebScraper) so
        # extraction/scoping decisions land in logs/jobs/<job_id>.log instead
        # of only the generic app.log — that's what makes "why did this
        # parameter come back blank / which pages matched my query" auditable
        # per run rather than mixed in with every other run's output.
        self.log = run_logger or logger
        self.enable_ocr = enable_ocr and (pytesseract is not None)
        if enable_ocr and pytesseract is None:
            self.log.info(
                "OCR requested but pytesseract/Tesseract not available — "
                "scanned-image PDFs will be skipped, not fatal."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def extract_candidates(
        self, pdf_path: Path, query: Optional[str] = None
    ) -> list[CandidatePhrase]:
        """Return every label/value candidate found in the PDF.

        ``query`` is an optional series/model name or free-text unit
        configuration description (e.g. "Premier YZ036" or "25 ton heat pump
        with gas heat and economizer"). When given, extraction is first
        scoped to the pages whose text plausibly matches that query — this
        is what lets a benchmark ask for one specific unit out of a manual
        that documents an entire product family. If no page matches well
        enough, the query is treated as "not found in this document" and
        the whole document is searched anyway, rather than silently
        returning nothing.
        """
        doc_name = pdf_path.name

        if fitz is None:
            # Degraded fallback path (PyMuPDF unavailable) — rare in practice
            # since it's a hard requirement, not optional; not worth the same
            # streaming treatment below since it's already an exceptional case.
            return self._extract_candidates_pdfplumber_only(pdf_path, query)

        query_norm = normalize(query) if query else None
        skip_pages: set[int] = set()
        relevant_pages: set[int] = set()
        toc_count = 0
        points_list_count = 0
        ocr_cache: dict[int, str] = {}

        def resolved_text(page_number: int, raw_text: str) -> str:
            # OCR is the one thing worth caching across the two passes below
            # (rare — only pages with almost no extractable text hit it —
            # but re-running Tesseract twice per such page would be wasteful).
            if raw_text and len(raw_text.strip()) >= 20:
                return raw_text
            if not self.enable_ocr:
                return raw_text
            if page_number not in ocr_cache:
                ocr_cache[page_number] = self._ocr_page(pdf_path, page_number - 1) or ""
            return ocr_cache[page_number] or raw_text

        try:
            with fitz.open(pdf_path) as doc:
                # Pass 1 — classification only (TOC/points-list detection,
                # query relevance scoring). Never retains a page's text past
                # its own iteration, unlike the old approach of building a
                # list of every page's text up front — that's what let a
                # large multi-hundred-page manual's combined text balloon
                # memory usage on a constrained deployment (e.g. Render's
                # free 512MB tier).
                for page_number, page in enumerate(doc, start=1):
                    text = resolved_text(page_number, page.get_text("text"))
                    if self._is_toc_page(text):
                        skip_pages.add(page_number)
                        toc_count += 1
                        continue
                    if self._is_points_list_page(text):
                        skip_pages.add(page_number)
                        points_list_count += 1
                        continue
                    if query_norm and self._page_relevance_score(query_norm, text) >= _QUERY_RELEVANCE_THRESHOLD:
                        relevant_pages.add(page_number)

                only_pages = relevant_pages if (query_norm and relevant_pages) else None

                # Pass 2 — actual candidate extraction, restricted to the
                # pages that survived classification. Re-reading each
                # in-scope page's text is cheap CPU-wise (PyMuPDF text
                # extraction, not table extraction) and trades a little CPU
                # for never holding the whole document's text simultaneously.
                candidates: list[CandidatePhrase] = []
                for page_number, page in enumerate(doc, start=1):
                    if page_number in skip_pages:
                        continue
                    if only_pages is not None and page_number not in only_pages:
                        continue
                    text = resolved_text(page_number, page.get_text("text"))
                    candidates.extend(self._candidates_from_text(text, doc_name, page_number))
        except Exception as exc:  # pragma: no cover
            self.log.warning("PyMuPDF failed on %s (%s); trying pdfplumber", doc_name, exc)
            return self._extract_candidates_pdfplumber_only(pdf_path, query)

        if pdfplumber is not None:
            candidates.extend(
                self._candidates_from_tables(
                    pdf_path, doc_name, skip_pages=skip_pages, only_pages=only_pages
                )
            )

        if query:
            scope_note = (
                f"; scoped to {len(only_pages)} page(s) matching query '{query}'"
                if only_pages is not None
                else f"; query '{query}' did not match strongly anywhere — searched entire document"
            )
        else:
            scope_note = ""
        self.log.info(
            "Extracted %d candidate phrases from %s (skipped %d TOC/index page(s), "
            "%d BACnet/points-list page(s))%s",
            len(candidates), doc_name, toc_count, points_list_count, scope_note,
        )
        return candidates

    def _extract_candidates_pdfplumber_only(
        self, pdf_path: Path, query: Optional[str] = None
    ) -> list[CandidatePhrase]:
        """Fallback used only when PyMuPDF itself is unavailable/fails."""
        doc_name = pdf_path.name
        pages_text = self._extract_text_per_page(pdf_path)
        skip_pages: set[int] = set()
        toc_count = 0
        points_list_count = 0
        relevant_pages = self._find_relevant_pages(pages_text, query) if query else None
        candidates: list[CandidatePhrase] = []

        for page_number, text in enumerate(pages_text, start=1):
            if self._is_toc_page(text):
                skip_pages.add(page_number)
                toc_count += 1
                continue
            if self._is_points_list_page(text):
                skip_pages.add(page_number)
                points_list_count += 1
                continue
            if relevant_pages is not None and page_number not in relevant_pages:
                continue
            candidates.extend(self._candidates_from_text(text, doc_name, page_number))

        if pdfplumber is not None:
            candidates.extend(
                self._candidates_from_tables(
                    pdf_path, doc_name, skip_pages=skip_pages, only_pages=relevant_pages
                )
            )
        self.log.info(
            "Extracted %d candidate phrases from %s (pdfplumber-only fallback; "
            "skipped %d TOC/index page(s), %d BACnet/points-list page(s))",
            len(candidates), doc_name, toc_count, points_list_count,
        )
        return candidates

    @staticmethod
    def _is_toc_page(text: Optional[str]) -> bool:
        if not text:
            return False
        if _TOC_HEADING_RE.search(text[:300]):
            return True
        dot_leader_lines = sum(1 for line in text.splitlines() if _TOC_DOT_LEADER_RE.search(line))
        return dot_leader_lines >= 4

    @staticmethod
    def _is_points_list_page(text: Optional[str]) -> bool:
        if not text:
            return False
        return len(_POINTS_LIST_MARKER_RE.findall(text)) >= 3

    @staticmethod
    def _is_junk_value(value: str) -> bool:
        """Value-level backstop for the same BACnet/Modbus/LonTalk points-list
        noise `_is_points_list_page` targets at the page level. Table-cell
        extraction can pull a "Yes/No ... Command" style point definition out
        of a page whose overall text doesn't cross the page-level density
        threshold (e.g. a table spread thin across a long page, or where
        PyMuPDF's linear text ordering separates cells that pdfplumber's
        table-structure extraction still captures side by side). Checking
        the value itself catches those regardless of which extraction path
        produced the candidate. Matches even when the text trailing "Yes/No"
        isn't the exact word "Command"/"Status" (garbled encoding, OCR noise,
        or a slightly different point-list convention) — starting with one
        of these enum-type descriptors is on its own a strong enough signal.
        """
        return bool(_POINTS_LIST_MARKER_RE.search(value)) or bool(_JUNK_VALUE_START_RE.match(value))

    @staticmethod
    def _page_relevance_score(query_norm: str, page_text: str) -> int:
        """Single-page counterpart to `_find_relevant_pages`, used by the
        streaming extraction path so a page's relevance can be scored the
        moment it's read instead of requiring every page's text to be
        collected into one big list first."""
        if not page_text:
            return 0
        page_norm = normalize(page_text)
        if not page_norm:
            return 0
        return max(
            fuzz.token_set_ratio(query_norm, page_norm),
            fuzz.partial_ratio(query_norm, page_norm),
        )

    @staticmethod
    def _find_relevant_pages(pages_text: list[str], query: str) -> Optional[set[int]]:
        """Score every page against a normalized query and return the page
        numbers that clear the relevance threshold, or None if the query is
        empty or no page matched well enough (meaning: don't restrict)."""
        query_norm = normalize(query)
        if not query_norm:
            return None

        matches: set[int] = set()
        for page_number, text in enumerate(pages_text, start=1):
            if not text:
                continue
            page_norm = normalize(text)
            if not page_norm:
                continue
            score = max(
                fuzz.token_set_ratio(query_norm, page_norm),
                fuzz.partial_ratio(query_norm, page_norm),
            )
            if score >= _QUERY_RELEVANCE_THRESHOLD:
                matches.add(page_number)

        return matches or None

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------
    def _extract_text_per_page(self, pdf_path: Path) -> list[str]:
        if fitz is not None:
            try:
                with fitz.open(pdf_path) as doc:
                    return [page.get_text("text") for page in doc]
            except Exception as exc:  # pragma: no cover
                self.log.warning("PyMuPDF failed on %s (%s); trying pdfplumber", pdf_path.name, exc)

        if pdfplumber is not None:
            try:
                with pdfplumber.open(pdf_path) as doc:
                    return [page.extract_text() or "" for page in doc.pages]
            except Exception as exc:  # pragma: no cover
                self.log.error("pdfplumber failed on %s (%s)", pdf_path.name, exc)

        self.log.error("No usable PDF backend available for %s", pdf_path.name)
        return []

    def _ocr_page(self, pdf_path: Path, page_index: int) -> Optional[str]:
        if fitz is None or pytesseract is None:
            return None
        try:
            with fitz.open(pdf_path) as doc:
                page = doc[page_index]
                pix = page.get_pixmap(dpi=200)
                img_bytes = pix.tobytes("png")
            import io

            image = Image.open(io.BytesIO(img_bytes))
            text = pytesseract.image_to_string(image)
            if text.strip():
                self.log.info("OCR recovered text on %s page %d", pdf_path.name, page_index + 1)
            return text
        except Exception as exc:  # pragma: no cover
            self.log.warning("OCR failed on %s page %d (%s)", pdf_path.name, page_index + 1, exc)
            return None

    # ------------------------------------------------------------------
    # Table extraction (most reliable for spec sheets)
    # ------------------------------------------------------------------
    def _candidates_from_tables(
        self,
        pdf_path: Path,
        doc_name: str,
        skip_pages: Optional[set[int]] = None,
        only_pages: Optional[set[int]] = None,
    ) -> list[CandidatePhrase]:
        out: list[CandidatePhrase] = []
        skip_pages = skip_pages or set()
        try:
            with pdfplumber.open(pdf_path) as doc:
                for page_number, page in enumerate(doc.pages, start=1):
                    if page_number in skip_pages:
                        continue
                    if only_pages is not None and page_number not in only_pages:
                        continue
                    try:
                        tables = page.extract_tables()
                        for table in tables or []:
                            out.extend(self._candidates_from_one_table(table, doc_name, page_number))
                    except Exception:
                        continue
                    finally:
                        # pdfplumber caches each page's fully-parsed character/
                        # object model on the Page instance and doc.pages keeps
                        # every page alive for the life of the `with` block —
                        # on a large multi-hundred-page document that adds up
                        # fast. Releasing it as soon as we're done with a page
                        # (rather than waiting for the whole document to
                        # finish) is what actually keeps peak memory bounded.
                        if hasattr(page, "flush_cache"):
                            page.flush_cache()
        except Exception as exc:  # pragma: no cover
            self.log.warning("Table extraction failed on %s (%s)", doc_name, exc)
        return out

    def _candidates_from_one_table(
        self, table: list, doc_name: str, page_number: int
    ) -> list[CandidatePhrase]:
        out: list[CandidatePhrase] = []
        norm_rows = [
            [c.strip() if isinstance(c, str) else "" for c in (row or [])] for row in table
        ]
        # Detected once per table (not per row): either every row's values are
        # per-tonnage columns, or none are — a table doesn't mix the two.
        tonnage_per_col = _detect_column_tonnages(norm_rows)

        for cells in norm_rows:
            non_empty = sum(1 for c in cells if c)
            if non_empty < 2:
                continue
            label = cells[0] if cells else ""
            if not label or not re.search(r"[A-Za-z]", label):
                continue
            value_cells = cells[1:]

            if tonnage_per_col is not None and any(value_cells):
                for idx, cell_value in enumerate(value_cells):
                    if not cell_value or self._is_junk_value(cell_value):
                        continue
                    tonnage = tonnage_per_col[idx] if idx < len(tonnage_per_col) else None
                    out.append(
                        CandidatePhrase(
                            phrase=label, value=cell_value,
                            source_document=doc_name, page_number=page_number,
                            tonnage=tonnage,
                        )
                    )
            else:
                value = " ".join(v for v in value_cells if v)
                if value and not self._is_junk_value(value):
                    out.append(
                        CandidatePhrase(
                            phrase=label, value=value,
                            source_document=doc_name, page_number=page_number,
                        )
                    )
        return out

    # ------------------------------------------------------------------
    # Free-text line parsing (spec prose, bullet lists, "Label: Value")
    # ------------------------------------------------------------------
    def _candidates_from_text(self, text: str, doc_name: str, page_number: int) -> list[CandidatePhrase]:
        out: list[CandidatePhrase] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or len(line) > 140:
                continue
            if _TOC_DOT_LEADER_RE.search(line):
                continue
            match = _VALUE_LINE_RE.match(line) or _TABLE_LINE_RE.match(line)
            if not match:
                continue
            label = match.group("label").strip(" .:-–—")
            value = match.group("value").strip(" .:-–—")
            if len(label) < 3 or not value or self._is_junk_value(value):
                continue
            out.append(
                CandidatePhrase(
                    phrase=label, value=value, source_document=doc_name, page_number=page_number
                )
            )
        return out
