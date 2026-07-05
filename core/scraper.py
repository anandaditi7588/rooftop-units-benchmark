"""
Web scraping engine.

Design notes (read this before wiring up a new competitor):

* Manufacturer product/spec pages are official documentation, so the scraper
  enforces a domain allow-list per competitor (config/competitors.json ->
  "allowed_domains") and never wanders off onto forums, resellers, or
  aggregator sites. This mirrors the spec's "never scrape unofficial
  websites if official documentation exists".
* Real manufacturer sites frequently gate spec sheets behind JS-rendered
  catalogs, "select your region" walls, or a lead-capture form, and their
  markup changes over time. A scraper that hard-codes CSS selectors for
  seven different vendor sites would be brittle and would silently break.
  Instead this module does two honest things:
    1. Best-effort discovery: fetch each configured seed URL, follow same-
       domain links one level deep, and collect any anchor that looks like
       a PDF (by extension or by link text containing "spec", "submittal",
       "manual", "IOM", "catalog", etc., biased by the competitor's
       `keywords`), then download it.
    2. Always honor manually placed files: engineers can drop a PDF into
       source_documents/<Competitor>/ and the pipeline will use it whether
       or not the network is reachable. This is the reliable path and is
       always checked first.
* Playwright is used only if it's installed (heavy optional dependency) —
  needed for sites that render their document list client-side. If it is
  not installed, or its browser binaries were never provisioned, the
  scraper logs that and continues with the plain requests+BeautifulSoup
  crawl, which still succeeds on the majority of static catalog pages.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core.config import Competitor, settings

logger = logging.getLogger(__name__)

# On many Windows machines, Python's bundled `certifi` CA list doesn't know
# about a corporate/antivirus TLS-inspection root certificate that Windows
# itself already trusts (added to the OS certificate store). The symptom is
# every single HTTPS request failing with CERTIFICATE_VERIFY_FAILED /
# "unable to get local issuer certificate" — including to sites like
# google.com, not just manufacturer sites. `truststore` (optional) patches
# Python's ssl module to verify against the OS trust store instead, which
# fixes this without ever disabling certificate verification.
try:
    import truststore

    truststore.inject_into_ssl()
    logger.info("truststore active: HTTPS requests verify against the OS certificate store.")
except ImportError:  # pragma: no cover
    logger.warning(
        "truststore not installed. If every scraping request fails with "
        "CERTIFICATE_VERIFY_FAILED, run: pip install truststore"
    )

_PDF_HINT_WORDS = (
    "spec", "submittal", "manual", "iom", "catalog", "datasheet",
    "data sheet", "brochure", "engineering", "install", "technical",
)
# Document types that are real, official PDFs but essentially never contain
# physical/performance specs — they're controls/network integration guides.
# A product page often links a couple of these alongside the actual spec
# sheet; downloading them anyway dilutes a small per-competitor PDF budget
# with hundreds of generic "point name -> Yes/No" style entries that then
# out-compete the handful of genuine spec values in the matching pool. They
# are excluded from discovery entirely (see _discover_pdf_links) unless
# excluding them would leave zero candidates for that competitor.
_NEGATIVE_PDF_HINT_WORDS = (
    "bacnet", "lontalk", "modbus", "protocol", "integration",
    "communication module", "network module", "gateway",
)


class WebScraper:
    def __init__(self, run_logger: logging.Logger | None = None) -> None:
        self.log = run_logger or logger
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent})
        self._playwright_available = self._check_playwright()

    @staticmethod
    def _check_playwright() -> bool:
        try:
            import playwright  # noqa: F401

            return True
        except ImportError:
            return False

    # ------------------------------------------------------------------
    def gather_documents(
        self, competitor: Competitor, enable_scraping: bool = True, query: Optional[str] = None
    ) -> list[Path]:
        """Return every PDF available for a competitor: manually dropped
        files first, then (optionally) freshly scraped ones.

        ``query`` is the optional series/model name or unit-configuration
        description the user typed on the setup page. When scraping is
        enabled, it's folded into link-discovery scoring so that pages/PDFs
        whose link text mentions that series are preferred over generic
        catalog pages.
        """
        docs: list[Path] = []

        existing = sorted(competitor.source_dir.glob("*.pdf"))
        if existing:
            self.log.info("%s: found %d manually-provided PDF(s) in %s",
                           competitor.name, len(existing), competitor.source_dir)
            docs.extend(existing)

        if enable_scraping:
            try:
                downloaded = self._crawl_and_download(competitor, query)
                docs.extend(p for p in downloaded if p not in docs)
            except Exception as exc:
                self.log.warning("%s: live scraping failed (%s); continuing with "
                                  "whatever local documents are available.", competitor.name, exc)
        else:
            self.log.info("%s: web scraping disabled for this run; using local documents only.",
                           competitor.name)

        if not docs:
            self.log.warning(
                "%s: no documents found (no local PDFs, scraping unavailable/empty). "
                "All parameters for this competitor will be left blank.",
                competitor.name,
            )
        return docs

    # ------------------------------------------------------------------
    def _crawl_and_download(self, competitor: Competitor, query: Optional[str] = None) -> list[Path]:
        downloaded: list[Path] = []
        seen_urls: set[str] = set()
        query_terms = self._query_terms(query)

        for seed_url in competitor.search_urls:
            self.log.info("%s: searching official page %s%s",
                           competitor.name, seed_url,
                           f" (scoped to '{query}')" if query_terms else "")
            html = self._get(seed_url)
            if html is None:
                continue

            pdf_links = self._discover_pdf_links(seed_url, html, competitor, query_terms)
            for url in pdf_links:
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                if len(downloaded) >= settings.max_pdfs_per_competitor:
                    break
                path = self._download_pdf(url, competitor)
                if path:
                    downloaded.append(path)

        return downloaded

    def _get(self, url: str) -> str | None:
        try:
            resp = self.session.get(url, timeout=settings.request_timeout_seconds)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            self.log.info("Could not fetch %s (%s)", url, self._explain(exc))
            return None

    @staticmethod
    def _explain(exc: Exception) -> str:
        """Add an actionable hint for the one failure mode that isn't
        self-explanatory: a machine-wide TLS trust issue (usually an
        antivirus/corporate proxy doing certificate inspection) that makes
        every HTTPS request fail, not just this one site."""
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return (
                f"{exc} — this usually means Python doesn't trust a certificate "
                f"your OS already trusts (antivirus/corporate TLS inspection is the "
                f"common cause on Windows). Fix: pip install truststore, then restart "
                f"the server."
            )
        return str(exc)

    def _discover_pdf_links(
        self, base_url: str, html: str, competitor: Competitor, query_terms: Optional[list[str]] = None
    ) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        preferred: list[tuple[str, int]] = []   # (url, relevance_score) — real spec/submittal candidates
        fallback: list[tuple[str, int]] = []    # controls/network integration guides — last resort only
        query_terms = query_terms or []

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or href.startswith("#") or href.startswith("mailto:"):
                continue
            absolute = urljoin(base_url, href)

            if not self._is_allowed_domain(absolute, competitor):
                continue

            text = (anchor.get_text() or "").strip().lower()
            looks_like_pdf = href.lower().endswith(".pdf")
            hints_hit = sum(1 for w in _PDF_HINT_WORDS if w in href.lower() or w in text)
            keyword_hit = sum(1 for kw in competitor.keywords if kw.lower() in text)
            # A link whose visible text mentions a token from the requested
            # series/model/configuration is worth far more than a generic
            # "spec sheet" hit — that's what lets a query like "Premier
            # YZ036" surface the right document instead of a generic catalog.
            query_hit = sum(1 for term in query_terms if term in text or term in href.lower())
            negative_hit = sum(1 for w in _NEGATIVE_PDF_HINT_WORDS if w in href.lower() or w in text)

            if looks_like_pdf or hints_hit or query_hit:
                score = (5 if looks_like_pdf else 0) + hints_hit + 2 * keyword_hit + 4 * query_hit
                (fallback if negative_hit else preferred).append((absolute, score))

        preferred.sort(key=lambda t: t[1], reverse=True)
        fallback.sort(key=lambda t: t[1], reverse=True)
        # Controls/network integration guides only get pulled in if there are
        # no real spec/submittal candidates at all for this page — otherwise
        # they'd eat into the small per-competitor download budget and drown
        # out genuine physical/performance data with generic BACnet points.
        return [url for url, _ in preferred] or [url for url, _ in fallback]

    @staticmethod
    def _query_terms(query: Optional[str]) -> list[str]:
        """Break a free-text series/model/configuration query into lowercase
        tokens worth matching against link text, dropping very short/common
        words that would match almost anything."""
        if not query:
            return []
        stopwords = {"the", "with", "and", "for", "unit", "units", "a", "an", "of", "to"}
        tokens = re.findall(r"[a-z0-9]+", query.lower())
        return [t for t in tokens if len(t) >= 3 and t not in stopwords]

    @staticmethod
    def _is_allowed_domain(url: str, competitor: Competitor) -> bool:
        netloc = urlparse(url).netloc.lower()
        return any(netloc == d or netloc.endswith("." + d) for d in competitor.allowed_domains)

    def _download_pdf(self, url: str, competitor: Competitor) -> Path | None:
        try:
            resp = self.session.get(url, timeout=settings.request_timeout_seconds, stream=True)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower() and not url.lower().endswith(".pdf"):
                return None

            digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
            filename = self._safe_filename(url, digest)
            dest = competitor.source_dir / filename
            if dest.exists():
                self.log.info("%s: already downloaded %s", competitor.name, filename)
                return dest

            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=8192):
                    fh.write(chunk)
            self.log.info("%s: downloaded %s -> %s", competitor.name, url, dest.name)
            return dest
        except requests.RequestException as exc:
            self.log.info("%s: failed to download %s (%s)", competitor.name, url, self._explain(exc))
            return None

    @staticmethod
    def _safe_filename(url: str, digest: str) -> str:
        name = urlparse(url).path.rsplit("/", 1)[-1] or "document.pdf"
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        return f"{digest}_{name}"
