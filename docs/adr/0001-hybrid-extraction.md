# 1. Hybrid Extraction, Not a Single Vision-Model Verdict

Date: 2026-07-14

## Status

Accepted

## Context

The obvious build is one call: hand a frontier vision model the label and the application data and ask "is this compliant?" It is less code and reads as the modern approach.

Two things rule it out. First, latency: output tokens dominate a vision call, and a model asked to reason about compliance emits far more of them than one asked to transcribe. The 5-second target is a stated kill criterion — the prior vendor's 30–40 seconds is why reviewers went back to checking by eye. Second, and more seriously, a model's failure mode here is confident and invisible: vision models score near-perfectly on canonical images and poorly on altered ones, which is exactly the population a compliance tool exists to catch.

## Decision

Split reading from deciding. A vision model transcribes the label into a fixed schema. A local OCR pass reads the same image independently. Compliance is then computed in plain Python against the rulebook. No model is consulted for a verdict.

## Consequences

One terse model call plus microseconds of deterministic checking fits the latency budget. Verdicts are reproducible and explainable — a finding points at a rule and a citation, not a model's opinion.

The two independent readings also buy the confidence signal: where they disagree, the finding goes to a human. Two model calls would not buy this, because two models share the same prior and would agree on the same wrong answer.

The cost is more moving parts than a single call, and OCR is an extra dependency in the image.
