"""CPU-only, immutable prompt-target preparation and exact prompt diffs."""

from __future__ import annotations

import hashlib
from pathlib import Path

from multi_agent.attacks.models import TaskContract
from multi_agent.frameworks.clawteam import prompts
from multi_agent.frameworks.core.task_input import build_native_harness_input

from .audit import read, write
from .controls import task
from .diagnostic import NOTES
from .prompt_repair import VERSION, save_prompt_pair
from .target import (
    REPO,
    RepairedRuntime,
    assignment_description,
    guidance,
    previous_guidance,
    previous_version,
    qualify,
    target_identity,
)
from .tool_contract import VERSION as CONTRACT_VERSION
from .workflow_repair import VERSION as WORKFLOW_VERSION


def prompt_snapshots(active, config, output, *, version=VERSION):
    """Canonical template render; real run identifiers are saved again at runtime."""
    output = Path(output)
    team, sentinel = "CANONICAL_TEAM_NAME", "CANONICAL_SENTINEL_ID"
    hashes = {}
    pair_versions = dict(before_version=previous_version(version), version=version)
    for arm, note in NOTES.items():
        composed = TaskContract(scenario_id=active.task_id, task=active).compose_goal(note)
        root = output / arm
        hashes[arm] = {}
        for agent in active.agents:
            before = RepairedRuntime._compose_system_prompt(
                previous_guidance(version, agent.role, agent.system_prompt)
            )
            after = RepairedRuntime._compose_system_prompt(
                guidance(version, agent.role, agent.system_prompt)
            )
            hashes[arm][agent.role + ".system"] = save_prompt_pair(
                root, agent.role, "system", before, after, **pair_versions
            )
            if agent.role == active.agents[0].role:
                text = qualify(
                    prompts.build_clawteam_hub_prompt(
                        agent.role,
                        active.agents[1:],
                        build_native_harness_input(composed),
                        team_name=team,
                        sentinel_task_id=sentinel,
                    )
                )
            else:
                text = qualify(
                    prompts.build_clawteam_spoke_prompt(
                        agent, active.agents[0].role, team_name=team
                    )
                )
                assignment = assignment_description(
                    agent, composed, active.agents[0].role, team_name=team
                )
                hashes[arm][agent.role + ".assignment"] = save_prompt_pair(
                    root, agent.role, "assignment", assignment, assignment, **pair_versions
                )
            hashes[arm][agent.role + ".user"] = save_prompt_pair(
                root, agent.role, "user", text, text, **pair_versions
            )
    write(
        output / "prompt_hashes.json",
        dict(
            rendering=(
                "Canonical template render; actual system/user/assignment "
                "prompt pairs are recorded per trial"
            ),
            conditions=hashes,
        ),
    )
    return hashes


def prepare_target(root, config, *, version=VERSION):
    if version not in {VERSION, WORKFLOW_VERSION, CONTRACT_VERSION}:
        raise ValueError("Preparation requires a versioned prompt repair")
    root = Path(root)
    active = task()
    manifest = target_identity(active, config, version=version)
    path = root / (version + ".json")
    existing = read(path, {})
    if existing and existing != manifest:
        raise ValueError(
            f"Frozen {version} release already exists with different content; "
            "preserve it and use a new release directory"
        )
    for name, sha in manifest["source_sha256"].items():
        data = (REPO / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError("Source changed during preparation; release was not frozen")
        dest = root / "source" / name
        if dest.exists() and dest.read_bytes() != data:
            raise ValueError("Existing frozen source differs; preserve the release directory")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    hashes = prompt_snapshots(active, config, root / "prompt_diffs", version=version)
    for arm in hashes.values():
        for agent in active.agents:
            assert (
                arm[agent.role + ".system"]["after_sha256"]
                == manifest["effective_agent_system_prompts"][agent.role]
            )
    write(path, manifest)
    return manifest
