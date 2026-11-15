# Prompts for Claude Code on the PI cluster

Three prompts cover the whole life of a project. Start Claude Code inside the repo on PI and
paste the one you need. Only Prompt A has blanks to fill in.

---

## Prompt A: port a new repo (once per project)

Fill the blanks first:
- GPU or not: if you don't know, leave "decide".
- Driver CUDA versions: needed only for GPU projects. Find them once with
  `srun --account=defake --partition=sporc-gpu --gres=gpu:a100:1 --time=00:05:00 nvidia-smi`
  (and the TIGRIS equivalent), then reuse them for every project.

```
Make this repository run unchanged on RIT Research Computing, where I can only paste
commands by hand. Do all work on this machine. Never ssh to RIT.

TARGETS (same shared directory, separate venv per cluster; Slurm account is always defake)
- SPORC:  Linux x86_64,  A100 (sm_80), driver supports CUDA ____ ; CPU-only nodes also exist
- TIGRIS: Linux aarch64, GH200 (sm_90), driver supports CUDA ____
- No root, apt or Docker. Jobs run under Slurm, launched only through the rc/ scripts.
- GPU needed: ____ (yes / no / decide). If "decide": CPU-only (GPUS=0) unless the code does
  local model inference; if you find local inference, tell me before adding torch.

PART 0 - prepare
- Ignore any conda/.venv active in my shell; use uv (install it if missing:
  curl -LsSf https://astral.sh/uv/install.sh | sh).
- Check that `git push --dry-run` works and the tree is clean. If not, stop and tell me.
- git pull. Record the working setup first: pip freeze of the current env to
  /tmp/<repo>_working_freeze.txt, plus versions of any non-Python tools it uses (node, CLIs,
  ffmpeg, ...). If a .venv exists, move it to .venv.pre-uv as a fallback; .venv* must be
  in .gitignore.
- cp -r ~/rc-kit/rc ./rc && chmod +x rc/*.sh rc/job.slurm
  Never edit rc/*.sh or rc/job.slurm; only rc/project.env. If an rc script has a bug, tell me.

PART 1 - dependencies -> pyproject.toml + uv.lock
- Migrate requirements.txt / setup.py / environment.yml into pyproject.toml if needed.
  `uv init --bare`, then `uv add -r requirements.txt`, is a good start.
- Pin top-level packages to the exact versions that work here (use the freeze as reference).
  Never copy a whole freeze: nvidia-*, triton etc. are platform-specific.
- rc/setup.sh runs plain `uv sync --locked`, with no extras. Everything needed at runtime
  must be in the base dependencies (or in default dependency groups). Vendored or local
  packages become editable path sources in [tool.uv.sources].
- Add:
    [tool.uv]
    environments = ["sys_platform == 'linux'"]
    required-environments = [
      "sys_platform == 'linux' and platform_machine == 'x86_64'",
      "sys_platform == 'linux' and platform_machine == 'aarch64'",
    ]
  If torch is used: an explicit index https://download.pytorch.org/whl/cuXXX, where XXX is
  the highest PyTorch CUDA build <= BOTH driver versions above, with torch/torchvision/...
  pinned to it in [tool.uv.sources]. PyPI's default torch may target a newer CUDA than RIT.
- .python-version = the Python version that works here, matching requires-python.
- Avoid C/CUDA dependencies without linux-aarch64 wheels (flash-attn, some bitsandbytes,
  xformers, decord, faiss-gpu versions): pin a version that has one, replace it, or make it
  optional with a graceful fallback. Report every such decision.
- Non-Python tools (Node.js, npm CLIs, ...) go in extra_setup() in rc/project.env:
  rc_install_node <version> and `npm install -g <pkg>@<exact version>`. They are installed
  per cluster and put on PATH. Never rely on system or ~/.local tools.
- uv lock, then uv sync --locked; the project must still run here.

PART 2 - portable code
- No hard-coded absolute paths. Every input/output location is a CLI arg or env var with
  a repo-relative default.
- Scripts call `python` from PATH: no .venv/bin/python, no `conda run`.
- HF downloads use the default caches (rc sets HF_HUB_CACHE etc.); no hard-coded cache_dir.
- Device from torch.cuda.is_available(); no cuda:N; no pip/apt/sudo at runtime.
- Secrets only from environment variables (rc loads ~/.config/rit-secrets/<repo>.env when
  NEEDS_SECRETS=1). The code must work with no .env file in the repo, which would sit on
  group-shared storage. CLIs that support it use API-key auth, not interactive logins.
- Several jobs may share a node: anything written to /tmp must be per-job, e.g.
  ${TMPDIR:-/tmp}/<repo>_${SLURM_JOB_ID:-local}. Same for shared state dirs in $HOME
  (e.g. ~/.toolname); $HOME is shared by both clusters.
- Files the code needs at runtime must be tracked by git. preflight lists gitignored code;
  commit what is needed (via .gitignore exceptions, after removing secrets/paths).

PART 3 - long runs survive being killed (time limit, maintenance)
- Write results incrementally (append a JSONL line per item and flush, or checkpoint every
  N steps).
- Resume: rerunning the identical command skips finished work. Results that ended in an
  error (quota, API failure, crash) must NOT count as finished.
- A --limit N (or equivalent) for smoke tests; save run config + git commit with results.
- Commands in rc/project.env use ${VAR:-default} for anything I may override at submit time
  (e.g. MODEL=x rc/submit.sh full); never overwrite such variables. A DRY_RUN=1 that prints
  the resolved commands without running them is welcome.

PART 4 - fill rc/project.env
- PREP_CMD (downloads, no GPU), SMOKE_CMD (1-10 items, minutes; first verify any CLIs with
  --version), FULL_CMD (resumable), alternative FULL commands as comments.
- CHECK_IMPORTS, RESULT_GLOB, NEEDS_SECRETS, cluster_env() / extra_setup() if needed.
- GPUS, CPUS, MEM, TIME_*: measured or estimated, with reasoning.
  (A100 has 40 GB, GH200 96 GB; never hold a GPU for an API-only workload.)

PART 5 - verify and ship
- bash rc/preflight.sh --smoke until PREFLIGHT PASSED. If the smoke fails only because of
  something outside the code (API credit, quota), say so clearly and run
  bash rc/preflight.sh without --smoke.
- Commit with clear messages and push. Never print or commit real secret values.
- Report: (a) what changed (b) dependency decisions (c) exact PREP/SMOKE/FULL commands and
  output locations (d) resources and why (e) a ready-to-paste
  ~/.config/rit-secrets/<repo>.env template with placeholder values only, if secrets are
  needed (f) anything uncertain.
```

---

## Prompt B: something failed on RIT (paste the full output of rc/status.sh after it)

```
This failed on RIT (rc/status.sh output below). Find the root cause and fix it here, in the
code, pyproject.toml/uv.lock or rc/project.env, not with manual commands on RIT. Re-run
bash rc/preflight.sh --smoke (or without --smoke if the cause is API credit), commit and
push. Then tell me only which RIT block to run next: normally
`rc/update.sh && rc/submit.sh check && rc/wait.sh` if the env changed, otherwise
`rc/update.sh && rc/submit.sh smoke && rc/wait.sh`.

[paste]
```

---

## Prompt C: upgrade the rc kit inside an existing repo (after you install a newer kit)

```
~/rc-kit has a newer version. Copy every file from ~/rc-kit/rc/ into ./rc/ EXCEPT
rc/project.env (keep ours). chmod +x rc/*.sh rc/job.slurm. Compare rc/project.env against
~/rc-kit/rc/project.env and add only new settings that apply to us. Run bash rc/preflight.sh,
commit "Upgrade rc kit" and push.
```
