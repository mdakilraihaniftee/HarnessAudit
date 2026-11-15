# Task for the coding agent (PI cluster): make this repo run unchanged on RIT

Paste the block below to the agent once per new project. Fill in the two driver lines first
(see README, "Find the RIT driver versions"; you only do that once).

---

Make this repository run unchanged on RIT Research Computing. The rc/ folder (copied from my
rc-kit) contains the generic scripts; do not edit rc/*.sh or rc/job.slurm — only rc/project.env.
If you believe an rc script has a bug, tell me instead of editing it.

Targets (same code, same shared directory, separate venvs):
- SPORC: Linux x86_64, NVIDIA A100 (sm_80), driver supports CUDA ____
- TIGRIS: Linux aarch64, NVIDIA GH200 (sm_90), driver supports CUDA ____
No root, no apt, no Docker. Jobs run under Slurm. Python env = uv, from pyproject.toml + uv.lock.

1. Dependencies -> pyproject.toml + uv.lock
   - If the repo uses requirements.txt / setup.py / environment.yml, migrate: `uv init --bare`
     (if no pyproject.toml), then `uv add -r requirements.txt` (or the equivalent).
   - Pin the top-level versions that currently work on PI (exact `==`). Use `uv pip freeze` of the
     working env only as a reference for top-level packages. Never copy a whole freeze: nvidia-*,
     triton, etc. are platform-specific transitive deps and break the other architecture.
   - Add this block (choose the cuXXX index = the highest PyTorch CUDA build that is <= BOTH driver
     versions above; PyPI's default torch may be built for a newer CUDA than RIT's drivers):

     ```toml
     [tool.uv]
     environments = ["sys_platform == 'linux'"]
     required-environments = [
       "sys_platform == 'linux' and platform_machine == 'x86_64'",
       "sys_platform == 'linux' and platform_machine == 'aarch64'",
     ]

     [[tool.uv.index]]
     name = "pytorch-cuda"
     url = "https://download.pytorch.org/whl/cu128"   # <- pick per the rule above
     explicit = true

     [tool.uv.sources]
     torch = [{ index = "pytorch-cuda" }]
     torchvision = [{ index = "pytorch-cuda" }]       # add torchaudio etc. if used
     ```
   - Write `.python-version` (3.12 unless the repo needs another version) and a matching
     `requires-python` (e.g. ">=3.12,<3.13").
   - Avoid dependencies with C/CUDA code that have no linux-aarch64 wheel (common offenders:
     flash-attn, some bitsandbytes/xformers/decord/faiss-gpu versions). Pin a version that has
     one, replace it, or make it an optional extra and make the code fall back gracefully.
     List every such decision in your report.
   - Non-Python tools (Node.js, npm CLIs, ...) cannot come from uv. Install them per cluster in
     `extra_setup()` in rc/project.env using `rc_install_node <version>` and
     `npm install -g <pkg>@<exact version>` (they land in the per-cluster venv dir and are put on
     PATH automatically). Never rely on system node or tools installed in ~/.local.
   - Run `uv lock`, then `uv sync --locked` and confirm it still runs on PI.

2. Code portability
   - No hard-coded absolute paths. Every input/output location is a CLI argument or env var
     with a repo-relative default (data/, results/, checkpoints/).
   - Model/dataset downloads use the Hugging Face defaults (HF_HUB_CACHE / HF_DATASETS_CACHE are
     set by rc); don't pass hard-coded cache_dir values.
   - Device from torch.cuda.is_available(); no hard-coded cuda:N; no pip install / apt / sudo
     at runtime. Credentials only from environment variables.

3. Long runs must survive being killed (time limit, maintenance, preemption)
   - Write results incrementally (e.g. append one JSONL line per example and flush; or
     checkpoint every N steps) so a kill loses at most the current item/interval.
   - A `--resume` (or equivalent) that skips finished items / loads the latest checkpoint;
     safe to start the identical command again.
   - A `--limit N` (or equivalent) for smoke tests, if the repo lacks one.
   - Save the run config (all args + `git rev-parse HEAD`) next to the results.

4. Fill in rc/project.env
   - PREP_CMD: downloads only (no GPU), if the project needs large downloads.
   - SMOKE_CMD: 1-10 examples, finishes in minutes, writes to results/smoke*.
   - FULL_CMD: the real run, with resume enabled.
   - CHECK_IMPORTS: the project's key top-level modules.
   - GPUS=0 if the project only calls remote APIs (no local model inference): don't hold GPUs idle.
   - CPUS / MEM / GPUS / TIME_*: realistic estimates (model size in fp16 vs A100 40 GB and
     GH200 96 GB; RAM about 1.5x the largest thing loaded on CPU). Explain your numbers.
   - NEEDS_SECRETS=1 if API keys are needed (names of the variables in your report).
   - cluster_env(): only if a cluster needs special env vars.

5. Verify and ship
   - `chmod +x rc/*.sh rc/job.slurm` (git records the executable bit on commit)
   - `bash rc/preflight.sh --smoke` until it prints PREFLIGHT PASSED (treat its warnings seriously).
   - Commit and push.
   - Report: what changed, dependency decisions (especially aarch64), the exact PREP/SMOKE/FULL
     commands, expected outputs and their location, resource estimates.

---

## Second prompt: when something fails on RIT

On RIT run `rc/status.sh` (or `rc/status.sh JOBID`), copy all of its output, and give the agent:

> This failed on RIT (output of rc/status.sh below). Find the root cause and fix it here in the
> code, pyproject.toml/uv.lock, or rc/project.env — not with manual commands on RIT. Re-run
> `bash rc/preflight.sh --smoke`, commit and push. Then tell me only which rc command to run
> next on RIT (normally `rc/update.sh && rc/submit.sh check` if the env changed, otherwise
> `rc/update.sh && rc/submit.sh smoke`).
>
> [paste]
