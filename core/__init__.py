"""Core package for the RTU Heat Pump Benchmarking application.

This package is intentionally organized so that each concern (config,
extraction, matching, scraping, orchestration, job tracking, Excel I/O)
lives in its own module with a small, well-defined public interface.
That is what makes it possible to add a new competitor, a new document
source, or a new report format without touching unrelated code.
"""
