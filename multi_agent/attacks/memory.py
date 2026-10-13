"""Transactional, attacker-owned SQLite memory (never a target domain bank)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import AttemptRecord


class AttackerMemory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS campaigns (
                campaign_id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL,
                config_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS candidates (
                candidate_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
                scenario_id TEXT NOT NULL, request_text TEXT NOT NULL,
                strategy TEXT NOT NULL, candidate_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL, round_index INTEGER NOT NULL,
                candidate_id TEXT NOT NULL, strategy TEXT NOT NULL,
                harness_version TEXT NOT NULL, target_run_id TEXT NOT NULL,
                trace_path TEXT, result_path TEXT, reward REAL,
                task_completion REAL, delivery_status TEXT NOT NULL,
                reflection_summary TEXT NOT NULL DEFAULT '',
                feedback_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(campaign_id, round_index)
            );
            CREATE TABLE IF NOT EXISTS evaluations (
                attempt_id INTEGER PRIMARY KEY REFERENCES attempts(attempt_id),
                violation_counts TEXT NOT NULL, unique_violation_count INTEGER
            );
            CREATE TABLE IF NOT EXISTS strategies (
                scenario_id TEXT NOT NULL, strategy TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, reward_sum REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(scenario_id, strategy)
            );
            CREATE TABLE IF NOT EXISTS harness_versions (
                version_id TEXT PRIMARY KEY, config_json TEXT NOT NULL,
                config_hash TEXT NOT NULL, decision TEXT NOT NULL,
                evidence_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS reflections (
                reflection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL, round_index INTEGER NOT NULL,
                summary TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS run_artifacts (
                run_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
                trace_path TEXT, result_path TEXT, artifact_json TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def create_campaign(
        self, campaign_id: str, scenario_id: str, config: dict[str, Any]
    ) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO campaigns(campaign_id, scenario_id, config_json) "
                "VALUES (?, ?, ?)",
                (campaign_id, scenario_id, _json(config)),
            )

    def campaign(self, campaign_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_campaign_state(
        self, campaign_id: str, state: dict[str, Any]
    ) -> None:
        row = self.connection.execute(
            "SELECT config_json FROM campaigns WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown campaign: {campaign_id}")
        config = json.loads(row["config_json"])
        config.update(state)
        with self.connection:
            self.connection.execute(
                "UPDATE campaigns SET config_json=? WHERE campaign_id=?",
                (_json(config), campaign_id),
            )

    def campaigns(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT campaign_id, scenario_id, status, created_at FROM campaigns "
                "ORDER BY created_at"
            )
        ]

    def next_round(self, campaign_id: str) -> int:
        row = self.connection.execute(
            "SELECT MAX(round_index) AS last FROM attempts WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()
        return int(row["last"] or 0) + 1

    def candidate_exists(self, scenario_id: str, request_text: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM candidates WHERE scenario_id=? AND request_text=? LIMIT 1",
            (scenario_id, request_text),
        ).fetchone()
        return row is not None

    def store_attempt(self, record: AttemptRecord, reflection: str = "") -> bool:
        candidate = record.candidate
        feedback = record.feedback
        trace = feedback.trajectory
        with self.connection:
            inserted = self.connection.execute(
                """INSERT OR IGNORE INTO attempts
                (campaign_id, round_index, candidate_id, strategy, harness_version,
                 target_run_id, trace_path, result_path, reward, task_completion,
                 delivery_status, reflection_summary, feedback_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.campaign_id, record.round_index, candidate.candidate_id,
                    candidate.strategy_name, candidate.harness_version,
                    feedback.run_id, trace.trace_path if trace else None,
                    trace.result_path if trace else None, feedback.reward,
                    feedback.task_completion, feedback.execution_status, reflection,
                    feedback.model_dump_json(),
                ),
            )
            if not inserted.rowcount:
                return False
            self.connection.execute(
                "INSERT OR IGNORE INTO candidates "
                "(candidate_id, campaign_id, scenario_id, request_text, strategy, candidate_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    candidate.candidate_id, candidate.campaign_id, candidate.scenario_id,
                    candidate.request_text, candidate.strategy_name, candidate.model_dump_json(),
                ),
            )
            counts = feedback.violations_by_type
            unique_count = len(trace.violations) if trace else None
            cursor = self.connection.execute(
                "SELECT attempt_id FROM attempts WHERE campaign_id=? AND round_index=?",
                (record.campaign_id, record.round_index),
            )
            attempt_id = cursor.fetchone()["attempt_id"]
            self.connection.execute(
                "INSERT INTO evaluations(attempt_id, violation_counts, unique_violation_count) "
                "VALUES (?, ?, ?)",
                (attempt_id, _json(counts), unique_count),
            )
            if feedback.reward is not None:
                self.connection.execute(
                    """INSERT INTO strategies(scenario_id, strategy, attempts, reward_sum)
                    VALUES (?, ?, 1, ?) ON CONFLICT(scenario_id, strategy) DO UPDATE SET
                    attempts=attempts+1, reward_sum=reward_sum+excluded.reward_sum""",
                    (candidate.scenario_id, candidate.strategy_name, feedback.reward),
                )
            if trace:
                self.connection.execute(
                    "INSERT OR REPLACE INTO run_artifacts "
                    "(run_id, campaign_id, trace_path, result_path, artifact_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        trace.run_id, record.campaign_id, trace.trace_path,
                        trace.result_path, trace.model_dump_json(),
                    ),
                )
            if reflection:
                self.connection.execute(
                    "INSERT INTO reflections(campaign_id, round_index, summary) VALUES (?, ?, ?)",
                    (record.campaign_id, record.round_index, reflection),
                )
        return True

    def attempts(self, campaign_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM attempts"
        values: tuple[Any, ...] = ()
        if campaign_id:
            query += " WHERE campaign_id=?"
            values = (campaign_id,)
        query += " ORDER BY attempt_id"
        return [dict(row) for row in self.connection.execute(query, values)]

    def similar_experience(
        self,
        scenario_id: str,
        strategy: str | None = None,
        limit: int = 5,
        cross_task: bool = False,
    ) -> list[dict[str, Any]]:
        query = (
            "SELECT a.attempt_id, a.round_index, a.strategy, a.reward, a.task_completion, "
            "a.feedback_json, c.request_text, c.scenario_id FROM attempts a "
            "JOIN candidates c USING(candidate_id) WHERE 1=1"
        )
        values: list[Any] = []
        if not cross_task:
            query += " AND c.scenario_id=?"
            values.append(scenario_id)
        if strategy:
            query += " AND a.strategy=?"
            values.append(strategy)
        if cross_task:
            query += " ORDER BY CASE WHEN c.scenario_id=? THEN 0 ELSE 1 END, a.attempt_id DESC"
            values.append(scenario_id)
        else:
            query += " ORDER BY a.attempt_id DESC"
        query += " LIMIT ?"
        values.append(limit)
        results: list[dict[str, Any]] = []
        for row in self.connection.execute(query, values):
            item = dict(row)
            feedback = json.loads(row["feedback_json"])
            if cross_task and item["scenario_id"] != scenario_id:
                item["request_text"] = None
                feedback = {
                    "execution_status": feedback.get("execution_status"),
                    "violations_by_type": feedback.get("violations_by_type", {}),
                    "reward": feedback.get("reward"),
                }
            item["feedback"] = feedback
            results.append(item)
        results.sort(
            key=lambda item: (
                0 if item["scenario_id"] == scenario_id else 1,
                item["attempt_id"],
            )
        )
        return results

    def strategy_statistics(
        self, scenario_id: str, cross_task: bool = False
    ) -> dict[str, tuple[int, float]]:
        query = "SELECT strategy, attempts, reward_sum FROM strategies"
        params: tuple[Any, ...] = ()
        if not cross_task:
            query += " WHERE scenario_id=?"
            params = (scenario_id,)
        return {
            row["strategy"]: (
                int(row["attempts"]),
                float(row["reward_sum"]) / max(int(row["attempts"]), 1),
            )
            for row in self.connection.execute(query, params)
        }

    def save_harness(
        self, version_id: str, config: dict[str, Any], decision: str,
        evidence: dict[str, Any],
    ) -> None:
        raw = _json(config)
        import hashlib

        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO harness_versions "
                "(version_id, config_json, config_hash, decision, evidence_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (version_id, raw, digest, decision, _json(evidence)),
            )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
