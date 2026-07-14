"""Extraction layer — the ExtractorPort and its adapters.

Lands in the Day-3 build stage. One port, justified by a stated requirement:
a restricted network blocked an earlier tool's cloud ML endpoint, so the
extractor must be swappable (hosted vision model for the demo, local OCR or an
in-tenant endpoint for their environment, a fixture replay for tests).
"""
