# 3. The TTB rulebook is data, not code

Date: 2026-07-14

## Status

Accepted

## Context

The regulations change. Parts 5 and 7 were restructured and renumbered in 2022; standards of fill were re-promulgated in January 2025. A rulebook smeared across `if` statements means every regulatory change is a code change, and no one can answer "what does this tool actually check, and under what authority?" without reading the source.

Three options: rules in code, rules in a database, rules in files.

## Decision

Rules live in `rulebook/rules/*.yaml`. Each declares a **required** `citation` — an uncited rule fails to load, so every finding carries its citation by construction. Code knows match *strategies* (`verbatim`, `brand_match`, `abv_consistency`, `warning_bold`) selected per rule; the engine never branches on an individual rule.

Not a database: for a regulation, git history *is* the audit trail, a row is not diffable, and a prototype has no admin UI to justify one.

Not a condition DSL inside the YAML either. That is an interpreter to write and defend. Rules use closed-vocabulary predicate fields; the escape hatch (JSONLogic, CEL) was not needed.

## Consequences

A regulatory change of the right shape is a YAML edit and a test row. Adding a rule whose strategy already exists is a data change only.

`tests/test_ssot.py` greps the source and fails if statutory text is hardcoded anywhere outside the rulebook — the discipline is executable, not aspirational. It has already caught the mandated text leaking into comments twice.

The report embeds a content hash of the rulebook, so a verdict is traceable to the exact rules that produced it. The cost is a load-time validation layer and a slightly indirect engine.
