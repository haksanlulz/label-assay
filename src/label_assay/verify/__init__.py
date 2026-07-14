"""Verification layer — the pure compliance engine.

Dispatches match strategies from the rulebook (never branches on individual
rules) and emits Findings. Pure: no I/O, no AI, exhaustively unit-testable.
"""
