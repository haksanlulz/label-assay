"""Match strategies — the comparisons the rulebook dispatches to.

Each strategy is a pure function that compares an extracted field against a
reference (or the filed application value) and returns a structured result. The
engine selects the strategy per rule from the rulebook; nothing here
branches on an individual rule.
"""
