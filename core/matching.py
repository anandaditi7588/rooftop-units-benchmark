"""
Semantic / fuzzy parameter matching engine.

This is the piece that solves the core problem stated in the spec: a
Column B parameter like "Sound Level" needs to be recognized against a
manual that says "Sound Power Rating (dBA)" or "Acoustics". No single
technique is robust enough on its own, so three signals are combined:

  1. Synonym groups (config/parameter_synonyms.json) — deterministic domain
     knowledge: "ESP" and "External Static Pressure" are declared equivalent.
  2. Fuzzy string similarity (RapidFuzz) — catches near-identical wording,
     abbreviations, punctuation/casing differences.
  3. Semantic embeddings (sentence-transformers, optional) — catches
     genuinely different wording for the same concept ("Net Cooling" vs
     "Cooling Capacity") that fuzzy matching alone would miss. If the
     sentence-transformers package (and its model weights) is not available
     — e.g. no internet access to download the model — the engine falls
     back to a TF-IDF + cosine-similarity model built on the fly from the
     document's own vocabulary, so semantic matching still works, just with
     slightly lower recall on very different phrasings.

The combined score is used both to pick the best candidate for a parameter
and as the "Confidence Score" column written into the output Excel/JSON.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz

from core.config import Registry, settings
from core.pdf_extractor import CandidatePhrase
from core.text_utils import normalize

logger = logging.getLogger(__name__)


@dataclass
class MatchResult:
    candidate: CandidatePhrase
    score: float
    matched_via: str  # "synonym+fuzzy", "embedding", "fuzzy", "tfidf"


class _EmbeddingBackend:
    """Lazy wrapper around sentence-transformers so importing this module
    never requires the (heavy, optionally-offline-unavailable) package."""

    def __init__(self, model_name: str):
        self._model = None
        self._model_name = model_name
        self._unavailable = False

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._unavailable:
            return False
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._model = SentenceTransformer(self._model_name)
            logger.info("Loaded sentence-transformers model '%s'", self._model_name)
            return True
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.info(
                "sentence-transformers unavailable (%s) — falling back to "
                "TF-IDF/fuzzy matching only.",
                exc,
            )
            self._unavailable = True
            return False

    def similarity(self, a: str, b: str) -> Optional[float]:
        if not self._ensure_loaded():
            return None
        import numpy as np  # local import: only needed if embeddings work

        vecs = self._model.encode([a, b], normalize_embeddings=True)
        return float(np.dot(vecs[0], vecs[1]))

    def similarity_batch(self, query: str, corpus: list[str]) -> Optional[list[float]]:
        if not self._ensure_loaded() or not corpus:
            return None
        import numpy as np

        vecs = self._model.encode([query] + corpus, normalize_embeddings=True)
        q = vecs[0]
        return [float(np.dot(q, v)) for v in vecs[1:]]


class _TfidfBackend:
    """Fallback semantic-ish similarity using scikit-learn TF-IDF + cosine.

    Built fresh per document-candidate corpus (cheap: a few hundred short
    phrases at most), so no persistent vocabulary/model management needed.
    """

    def similarity_batch(self, query: str, corpus: list[str]) -> Optional[list[float]]:
        if not corpus:
            return None
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:  # pragma: no cover
            return None

        try:
            vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
            matrix = vectorizer.fit_transform([query] + corpus)
            sims = cosine_similarity(matrix[0:1], matrix[1:]).flatten()
            return [float(s) for s in sims]
        except ValueError:
            return None


class MatchingEngine:
    def __init__(self) -> None:
        self._synonym_lookup = self._build_synonym_lookup()
        self._embedding_backend = (
            _EmbeddingBackend(settings.embedding_model_name)
            if settings.use_sentence_transformers
            else None
        )
        self._tfidf_backend = _TfidfBackend()

    # ------------------------------------------------------------------
    @staticmethod
    def _build_synonym_lookup() -> dict[str, str]:
        """term (normalized) -> canonical group name"""
        lookup: dict[str, str] = {}
        for group in Registry.synonym_groups():
            canonical = group["canonical"]
            lookup[normalize(canonical)] = canonical
            for term in group.get("terms", []):
                lookup[normalize(term)] = canonical
        return lookup

    def canonical_name(self, text: str) -> Optional[str]:
        """Public accessor: canonical synonym-group name for a piece of
        text, or None if it doesn't match any known engineering concept.
        Used by the pipeline to decide whether "best value" highlighting
        (higher/lower-is-better) applies to a given Column B parameter."""
        return self._canonical_of(text)

    def _canonical_of(self, text: str) -> Optional[str]:
        norm = normalize(text)
        if norm in self._synonym_lookup:
            return self._synonym_lookup[norm]
        # try substring containment against known terms for partial phrase hits
        for term_norm, canonical in self._synonym_lookup.items():
            if term_norm and (term_norm in norm or norm in term_norm):
                return canonical
        return None

    @staticmethod
    def _fuzzy(a: str, b: str) -> float:
        return fuzz.WRatio(a, b) / 100.0

    # ------------------------------------------------------------------
    @staticmethod
    def candidate_identity(candidate: CandidatePhrase) -> tuple:
        """A stable identity for a candidate, used to stop the same exact
        (phrase, value) pair from being reused across sibling rows that
        share a category (see `exclude_identities` on find_best_match)."""
        return (
            candidate.source_document, candidate.page_number,
            candidate.phrase, candidate.value, candidate.tonnage,
        )

    def find_best_match(
        self,
        parameter: str,
        candidates: list[CandidatePhrase],
        exclude_identities: Optional[set[tuple]] = None,
    ) -> Optional[MatchResult]:
        """Return the single best-scoring candidate for a parameter, or
        None if nothing clears the confidence floor.

        ``exclude_identities`` lets the caller rule out candidates already
        claimed by a sibling parameter (e.g. several rows sharing one
        category, like "Quantity/Size", "Type", "Capacity Steps" all under
        "Compressor Data - Standard Capacity, Standard Efficiency"). Without
        this, a long shared category prefix can dominate the match score
        enough that every sibling row independently picks the same single
        candidate — four rows confidently repeating one value instead of
        four distinct ones, or honestly coming up blank.
        """
        if not candidates:
            return None
        if exclude_identities:
            candidates = [c for c in candidates if self.candidate_identity(c) not in exclude_identities]
        if not candidates:
            return None

        param_norm = normalize(parameter)
        param_canonical = self._canonical_of(parameter)
        phrases = [c.phrase for c in candidates]
        phrases_norm = [normalize(p) for p in phrases]

        embed_scores: Optional[list[float]] = None
        if self._embedding_backend is not None:
            embed_scores = self._embedding_backend.similarity_batch(parameter, phrases)
        tfidf_scores: Optional[list[float]] = None
        if embed_scores is None:
            tfidf_scores = self._tfidf_backend.similarity_batch(param_norm, phrases_norm)

        best: Optional[MatchResult] = None
        for idx, candidate in enumerate(candidates):
            fuzzy_score = self._fuzzy(param_norm, phrases_norm[idx])

            synonym_boost = 0.0
            matched_via = "fuzzy"
            cand_canonical = self._canonical_of(candidate.phrase)
            if param_canonical is not None and cand_canonical == param_canonical:
                synonym_boost = 0.30
                matched_via = "synonym+fuzzy"

            if embed_scores is not None:
                semantic = embed_scores[idx]
                combined = 0.5 * semantic + 0.35 * fuzzy_score + synonym_boost
                if synonym_boost:
                    matched_via = "synonym+embedding"
                elif semantic >= 0.6:
                    matched_via = "embedding"
            elif tfidf_scores is not None:
                semantic = tfidf_scores[idx]
                combined = 0.45 * semantic + 0.4 * fuzzy_score + synonym_boost
                if not synonym_boost and semantic >= 0.5:
                    matched_via = "tfidf"
            else:
                combined = 0.7 * fuzzy_score + synonym_boost

            combined = min(combined, 1.0)

            if best is None or combined > best.score:
                best = MatchResult(candidate=candidate, score=combined, matched_via=matched_via)

        if best and best.score >= settings.match_confidence_floor:
            return best
        return None
