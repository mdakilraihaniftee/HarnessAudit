"""PID identities and session ownership; no signal based on numeric PID alone."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
from pathlib import Path


def identity(pid):
    try:
        proc = Path("/proc") / str(pid)
        stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        cmd = (proc / "cmdline").read_bytes()
        return dict(
            pid=int(pid),
            start_time=stat[19],
            state=stat[0],
            ppid=int(stat[1]),
            pgid=int(stat[2]),
            session=int(stat[3]),
            command=cmd.decode(errors="replace").replace("\0", " "),
            command_sha256=hashlib.sha256(cmd).hexdigest(),
            executable=os.readlink(proc / "exe") if stat[0] != "Z" else None,
        )
    except (OSError, ValueError, IndexError):
        return None


def same_process(saved, current):
    return bool(
        saved
        and current
        and all(saved.get(k) == current.get(k) for k in ["pid", "start_time", "command_sha256"])
    )


class OwnedProcess:
    def __init__(self, proc, *, owned=True, gpu_uuids=(), server_id=None):
        self.proc, self.owned = proc, owned
        self.record = identity(proc.pid)
        self.members = {}
        self.gpu_uuids, self.server_id = list(gpu_uuids), server_id
        if owned and (not self.record or self.record["session"] != proc.pid):
            raise ValueError("Owned process must start a new session")
        self.refresh()

    @classmethod
    def launch(cls, command, **kwargs):
        return cls(subprocess.Popen(command, start_new_session=True, **kwargs))

    def refresh(self):
        if not self.record:
            return
        current = {}
        for path in Path("/proc").iterdir():
            if path.name.isdigit():
                item = identity(int(path.name))
                if item:
                    current[item["pid"]] = item
        trusted = {
            pid
            for pid, saved in self.members.items()
            if pid in current and saved["start_time"] == current[pid]["start_time"]
        }
        root = current.get(self.record["pid"])
        if root and root["start_time"] == self.record["start_time"]:
            trusted.add(root["pid"])
        changed = True
        while changed:
            changed = False
            for pid, item in current.items():
                previous = self.record if pid == self.record["pid"] else self.members.get(pid)
                if previous and previous["start_time"] != item["start_time"]:
                    continue
                own_session = item["session"] == self.record["session"] and any(
                    current[owner]["session"] == self.record["session"] for owner in trusted
                )
                if pid in trusted or item["ppid"] in trusted or own_session:
                    self.members[pid] = item
                    if pid not in trusted:
                        trusted.add(pid)
                        changed = True

    def stop(self, timeout=3):
        if not self.owned:
            return dict(ok=True, reused_server_preserved=True, signalled=[])
        self.refresh()
        sent, mismatches = [], []
        for sig in [signal.SIGTERM, signal.SIGKILL]:
            for pid, saved in list(self.members.items()):
                current = identity(pid)
                if not current or current["state"] == "Z":
                    continue
                if not same_process(saved, current):
                    mismatches.append(pid)
                    continue
                os.kill(pid, sig)
                sent.append(dict(pid=pid, signal=sig.name))
            until = time.monotonic() + timeout
            while time.monotonic() < until:
                self.proc.poll()
                if not any(
                    (cur := identity(pid)) and cur["state"] != "Z" and same_process(saved, cur)
                    for pid, saved in self.members.items()
                ):
                    break
                time.sleep(0.05)
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            pass
        remaining = [
            identity(pid)
            for pid, saved in self.members.items()
            if (cur := identity(pid)) and cur["state"] != "Z" and same_process(saved, cur)
        ]
        return dict(
            ok=not remaining and not mismatches,
            owned_record=self.record,
            members=list(self.members.values()),
            remaining=remaining,
            identity_mismatches=mismatches,
            signalled=sent,
            gpu_uuids=self.gpu_uuids,
            model_server_id=self.server_id,
            zombie_definition=(
                "Zombies consume no GPU/worker execution resources; parent wait reaps owned child"
            ),
        )


async def wait_tcp(port, proc, timeout=30):
    import asyncio
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("MCP service exited before readiness")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            await asyncio.sleep(0.05)
    raise TimeoutError("MCP readiness timeout")


def cleanup_record(saved, *, timeout=3, members=None):
    """Cleanup a recorded session without constructing a new identity for a stale PID."""
    current = identity(saved["pid"])
    if current and not same_process(saved, current):
        return dict(ok=False, reason="Identity mismatch; no signal sent", signalled=[])
    known = dict(members or {})
    if current:
        known[current["pid"]] = saved
        for path in Path("/proc").iterdir():
            if path.name.isdigit():
                item = identity(int(path.name))
                if item and item["session"] == saved["session"]:
                    known[item["pid"]] = item
    sent = []
    for sig in [signal.SIGTERM, signal.SIGKILL]:
        for pid, record in known.items():
            item = identity(pid)
            if item and item["state"] != "Z" and same_process(record, item):
                os.kill(pid, sig)
                sent.append(dict(pid=pid, signal=sig.name))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not any(
                (item := identity(pid)) and item["state"] != "Z" and same_process(record, item)
                for pid, record in known.items()
            ):
                break
            time.sleep(0.05)
    remaining = [
        item
        for pid, record in known.items()
        if (item := identity(pid)) and item["state"] != "Z" and same_process(record, item)
    ]
    return dict(
        ok=not remaining,
        remaining=remaining,
        signalled=sent,
        owned_record=saved,
        members=list(known.values()),
    )


def tracked_model_server(root, cfg, ids):
    """Record Popen identity immediately, including interrupted/partial startup."""
    import threading
    import uuid
    from unittest.mock import patch

    from multi_agent.local_inference import server
    from multi_agent.local_inference.gpu_discovery import discover
    from multi_agent.research_phase1.gpu import validate_ids, verify_server
    from multi_agent.research_phase1.pilot import start_server

    from .audit import write

    if server.endpoint_ready(cfg.inference.endpoint, cfg.inference.served_name):
        start_server(root, cfg, ids)  # Strict existing-server compatibility validation only.
        return None, None
    snapshot = discover()
    validate_ids(ids, snapshot)
    for i in ids:
        dev = snapshot.by_index(i)
        if not dev.processes_visible or dev.processes or dev.used_gb > 1:
            raise ValueError(f"GPU {i} occupied; automatic sharing is disabled")
    name = "bach-phase1r-" + uuid.uuid4().hex[:12]
    captured = []
    original_popen = subprocess.Popen
    stopped = threading.Event()

    def monitor():
        while not stopped.wait(0.2):
            for owned in captured:
                owned.refresh()

    def create(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        if kwargs.get("start_new_session"):
            owned = OwnedProcess(proc)
            owned.gpu_uuids = [snapshot.by_index(i).uuid for i in ids]
            owned.server_id = name
            captured.append(owned)
            write(
                root / "server_process_identity.json",
                dict(owned.record, gpu_uuids=owned.gpu_uuids, model_server_identity=name),
            )
        return proc

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        with patch.object(server.subprocess, "Popen", create):
            result = server.launch(
                cfg, name=name, reuse=False, max_retries=0, log_dir=root / "logs"
            )
        write(root / "server_launch.json", result.to_dict())
        if not result.ok or len(captured) != 1:
            raise RuntimeError(result.error or "Cannot establish unique server ownership")
        write(root / "server_verification.json", verify_server(cfg, ids))
        return captured[0], name
    except BaseException:
        for owned in captured:
            write(root / "partial_startup_cleanup.json", owned.stop())
        release_model_reservations(name, captured[0].record if captured else None)
        raise
    finally:
        stopped.set()
        watcher.join(timeout=2)


def release_model_reservations(name, record):
    from multi_agent.local_inference import server
    from multi_agent.local_inference.gpu_allocator import GpuReservations

    from .audit import read, write

    path = server._state_path(name)
    state = read(path, {})
    if not record or state.get("pid") != record.get("pid"):
        return
    current = identity(record["pid"])
    if current and current["state"] != "Z":
        return
    GpuReservations().release(list(state.get("reservation_keys") or []))
    history = server.state_dir() / "history"
    history.mkdir(exist_ok=True)
    write(history / (path.stem + "_phase1r_stopped.json"), dict(state, stopped_at=time.time()))
    path.unlink(missing_ok=True)
