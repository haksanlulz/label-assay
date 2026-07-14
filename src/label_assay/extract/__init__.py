"""Extraction layer — one port, several adapters.

An extractor turns a label image into a structured `Extraction`, and nothing
else. It never sees the application data and never sees the OCR output: those are
independent channels, and their independence is what makes cross-checking them a
real confidence signal later (a model handed the expected answer would just echo
it back).

The port is a Protocol so an adapter never imports the core to subclass it. This
is the one seam the requirements justify — the client's firewall blocked the
previous vendor's cloud ML endpoint, so the backend must be swappable: a hosted
vision model for the demo, a local OCR or in-tenant Azure endpoint for their
environment, a fixture replay for tests.
"""
