"""Supply-chain and deploy-surface hardening, pinned as static assertions.

These guard configuration no request exercises: the CI actions must stay pinned
to immutable commit SHAs (a movable major tag can be repointed after an upstream
compromise, and the deploy job carries the HF_TOKEN), and the production server
must not advertise its banner. Neither is reachable through the app, so a static
check on the committed config is the only place they can regress-test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_workflow_actions_are_pinned_to_full_commit_shas() -> None:
    # Every workflow file, not a named one: a workflow added later with a
    # movable tag would otherwise ship unguarded (some workflows legitimately
    # have no `uses:` at all — the glob still covers them when they gain one).
    workflows_dir = REPO / ".github" / "workflows"
    workflows = sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml"))
    assert workflows, "no workflow files found under .github/workflows"
    refs = [
        (path.name, ref)
        for path in workflows
        for ref in re.findall(r"uses:\s*(\S+)", path.read_text(encoding="utf-8"))
    ]
    assert refs, "no `uses:` actions found in any workflow"
    for name, ref in refs:
        _action, _, version = ref.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", version), (
            f"{name}: {ref} is not pinned to a full 40-char commit SHA — a movable "
            "tag can be repointed after an upstream compromise"
        )


def test_dependabot_maintains_the_action_pins() -> None:
    # SHA pins freeze the version; Dependabot is what keeps them current. The
    # config is parsed, not substring-matched: the file's own comments mention
    # the ecosystem by name, which a substring check would accept as coverage.
    config = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    actions = [
        entry
        for entry in config["updates"]
        if entry.get("package-ecosystem") == "github-actions"
    ]
    assert actions, "dependabot.yml has no github-actions update entry"
    entry = actions[0]
    assert entry.get("directory") == "/"
    assert entry.get("schedule", {}).get("interval")


def test_base_image_is_pinned_by_digest_and_maintained() -> None:
    # Same class as the action pins: a tag is movable, a digest is not. The tag
    # stays in the ref so Dependabot's docker updater knows which stream to
    # track — and that updater entry must exist, or the pin silently rots.
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    frm = re.search(r"^FROM\s+(\S+)\s*$", dockerfile, flags=re.MULTILINE)
    assert frm is not None, "no FROM instruction in the Dockerfile"
    assert re.search(r"@sha256:[0-9a-f]{64}$", frm.group(1)), (
        f"base image {frm.group(1)} is not pinned to an immutable digest"
    )
    config = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    docker = [e for e in config["updates"] if e.get("package-ecosystem") == "docker"]
    assert docker, "dependabot.yml has no docker update entry to maintain the digest pin"
    assert docker[0].get("schedule", {}).get("interval")


def test_prod_server_does_not_advertise_its_banner() -> None:
    # The flag must sit in the CMD argv itself: the Dockerfile's comments also
    # name it, so a whole-file substring survives the flag being dropped from
    # the command that actually starts the server.
    dockerfile = (REPO / "Dockerfile").read_text(encoding="utf-8")
    cmd = re.search(r"^CMD\s+(\[.*\])\s*$", dockerfile, flags=re.MULTILINE)
    assert cmd is not None, "no exec-form CMD instruction in the Dockerfile"
    argv = json.loads(cmd.group(1))
    assert "--no-server-header" in argv
