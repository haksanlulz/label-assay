# 4. Cloud-independence: one swappable extractor port

Date: 2026-07-14

## Status

Accepted

## Context

The client's network blocks outbound traffic to many domains. That is not a hypothetical: it is why the previous vendor's pilot failed — half its features didn't work because the firewall blocked its ML endpoints, and the tool was abandoned. Any design that hard-wires a specific cloud vision endpoint repeats that failure.

The environment is Azure, and FedRAMP scarring is real. A production deployment would plausibly need an in-tenant endpoint or an on-premises model rather than a public API.

## Decision

The extractor is a `Protocol` — a label image in, a structured extraction out, nothing else — with adapters behind it: the hosted vision model for this demo, a fixture replay for deterministic tests, and room for a local or in-tenant endpoint without touching the compliance engine.

A Protocol rather than a base class, so an adapter never imports the core to subclass it.

This is the *one* abstraction a stated requirement justifies. There is deliberately no repository port, storage port, or notifier port: no stated second implementation, no port. The same rule that builds this seam refuses the others.

## Consequences

The model backend is a swap, not a rewrite. Tests run offline and deterministically against the fixture adapter, so the suite needs no API key.

The OCR pass is local and needs no network at all, so the deterministic half of the system keeps working when the endpoint does not — which is the client's stated failure mode. When the reader is unreachable, the app says so plainly rather than returning a stack trace.

The cost is one layer of indirection at the boundary, and a schema that both adapters must satisfy.
