"""The TTB rulebook, as data.

Every rule lives in `rules/*.yaml`, not in code. Each rule declares its CFR
citation (a required field — an uncited rule fails to load) and the match
strategy the engine should apply. This directory is the single source of truth
for what TTB requires; `tests/test_ssot.py` enforces that no statutory text or
threshold is hardcoded elsewhere.
"""
