"""Extraction layer — one port, several adapters.

An extractor turns a label image into a structured `Extraction`, and nothing
else. It never sees the application data and never sees the OCR output: those are
independent channels, and their independence is what makes cross-checking them a
real confidence signal later (a model handed the expected answer would just echo
it back).

The port is a Protocol so an adapter never imports the core to subclass it. This
is the one seam the requirements justify: a regulated environment may not be able
to reach a public ML endpoint at all, so the backend must be swappable. A hosted
vision model for the deployed instance, a local OCR or in-tenant endpoint for a
restricted network, a fixture replay for tests.
"""
