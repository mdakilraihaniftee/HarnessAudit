"""Standard-library checks of the extracted R2/R3 reviewer release evidence."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def verify(root):
    root = Path(root).resolve()
    subprocess.run([sys.executable, str(root / "validate_bundle.py"), str(root)], check=True)
    receipts = []
    for version in ["TARGET_V1R2", "TARGET_V1R3"]:
        release = root / "phase1r" / ("target_v1r" + version[-1])
        manifest = json.loads((release / (version + ".json")).read_text())
        identity = manifest.pop("target_identity")
        digest = hashlib.sha256(
            json.dumps(
                manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        assert identity == version + ":" + digest, version
        for name, expected in manifest["source_sha256"].items():
            assert (
                hashlib.sha256((release / "source" / name).read_bytes()).hexdigest() == expected
            ), name
        pairs = 0
        for path in (release / "prompt_diffs").rglob("*.json"):
            hashes = json.loads(path.read_text())
            if "before_sha256" not in hashes:
                continue
            pairs += 1
            stem = path.with_suffix("")
            for ending, key in [
                (".before.md", "before_sha256"),
                (".after.md", "after_sha256"),
                (".diff", "diff_sha256"),
            ]:
                assert (
                    hashlib.sha256(Path(str(stem) + ending).read_bytes()).hexdigest()
                    == hashes[key]
                ), str(path)
            if path.name.endswith(".system.json"):
                role = path.name.removesuffix(".system.json")
                assert hashes["after_sha256"] == manifest["effective_agent_system_prompts"][role]
                if version == "TARGET_V1R3":
                    previous = json.loads(
                        (root / "phase1r/target_v1r2/TARGET_V1R2.json").read_text()
                    )
                    assert (
                        hashes["before_sha256"] == previous["effective_agent_system_prompts"][role]
                    )
            else:
                assert hashes["before_sha256"] == hashes["after_sha256"]
        receipts.append(
            dict(
                target_identity=identity,
                frozen_sources_checked=len(manifest["source_sha256"]),
                prompt_pairs_checked=pairs,
            )
        )
    for version in ["v1", "v2", "v3"]:
        campaign = root / "phase1r/diagnostic_smoke" / version
        assert (campaign / "smoke_acceptance.json").exists()
        assert list(campaign.rglob("*.sqlite"))
    result = dict(
        release_passed=True, releases=receipts, previous_failures_present=["v1", "v2", "v3"]
    )
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    verify(sys.argv[1])
