"""Supply-chain and deploy-surface hardening, pinned as static assertions.

These guard configuration no request exercises: the CI actions must stay pinned
to immutable commit SHAs (a movable major tag can be repointed after an upstream
compromise, and the deploy job carries the HF_TOKEN), and the production server
must not advertise its banner. Neither is reachable through the app, so a static
check on the committed config is the only place they can regress-test.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_workflow_actions_are_pinned_to_full_commit_shas() -> None:
    workflow = (REPO / ".github" / "workflows" / "hf-deploy.yml").read_text(encoding="utf-8")
    refs = re.findall(r"uses:\s*(\S+)", workflow)
    assert refs, "no `uses:` actions found in the deploy workflow"
    for ref in refs:
        _action, _, version = ref.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", version), (
            f"{ref} is not pinned to a full 40-char commit SHA — a movable tag "
            "can be repointed after an upstream compromise"
        )


def test_dependabot_maintains_the_action_pins() -> None:
    # SHA pins freeze the version; Dependabot is what keeps them current.
    config = (REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert "github-actions" in config


def test_prod_server_does_not_advertise_its_banner() -> None:
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    assert "--no-server-header" in dockerfile
