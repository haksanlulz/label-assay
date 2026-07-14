"""Deterministic text processing — canonicalization and numeric parsing.

Pure functions, no I/O. This is the half of the system the AI cannot be trusted
to do: the compliance verdicts are computed here, from normalized text, not by a
model.
"""
