from __future__ import annotations

import json
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
  fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL, decision TEXT NOT NULL DEFAULT 'pending',
  result TEXT, created_at TEXT NOT NULL, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS reconciliation_runs (
  run_id TEXT PRIMARY KEY, context TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


def _decision_key(payload: dict) -> str:
    """Stable proposal identity across run metadata and timestamped note text."""
    operations = []
    for operation in payload.get("operations") or []:
        item = {key: value for key, value in operation.items() if key not in {"administrator"}}
        if "fields" in item:
            item["fields"] = {key: value for key, value in item["fields"].items() if key != "note"}
        operations.append(item)
    identity = {
        "run_id": payload.get("run_id", "legacy-bellhaven"),
        "kind": payload.get("kind"),
        "community": (payload.get("community") or {}).get("source_url") or (payload.get("community") or {}).get("name"),
        "account_id": (payload.get("account") or {}).get("account_id"),
        "operations": operations,
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class ReviewStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def sync(self, proposals: list[dict], run_id: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            scoped_run_id = run_id or (proposals[0].get("run_id") if proposals else None)
            fingerprints = [proposal["fingerprint"] for proposal in proposals]
            scoped_rows = []
            if scoped_run_id:
                all_rows = self.conn.execute(
                    "SELECT fingerprint,payload,decision FROM proposals"
                ).fetchall()
                scoped_rows = [row for row in all_rows if json.loads(row["payload"]).get("run_id") == scoped_run_id]
                decided_keys = {
                    _decision_key(json.loads(row["payload"])) for row in scoped_rows
                    if row["decision"] != "pending"
                }
                proposals = [proposal for proposal in proposals if _decision_key(proposal) not in decided_keys]
                fingerprints = [proposal["fingerprint"] for proposal in proposals]
                pending_rows = [row for row in scoped_rows if row["decision"] == "pending"]
                stale = [
                    row["fingerprint"] for row in pending_rows
                    if row["fingerprint"] not in fingerprints
                    or _decision_key(json.loads(row["payload"])) in decided_keys
                ]
                if stale:
                    placeholders = ",".join("?" for _ in stale)
                    self.conn.execute(f"DELETE FROM proposals WHERE fingerprint IN ({placeholders})", stale)
            for proposal in proposals:
                self.conn.execute("INSERT OR IGNORE INTO proposals(fingerprint,payload,created_at) VALUES(?,?,?)",
                                  (proposal["fingerprint"], json.dumps(proposal), now))
                self.conn.execute(
                    "UPDATE proposals SET payload=? WHERE fingerprint=? AND decision='pending'",
                    (json.dumps(proposal), proposal["fingerprint"]),
                )

    def backfill_source_context(self, source_context: dict) -> int:
        """Add immutable run context to legacy proposals without changing decisions."""
        rows = self.conn.execute("SELECT fingerprint,payload,created_at FROM proposals").fetchall()
        changed = 0
        with self.conn:
            for row in rows:
                payload = json.loads(row["payload"])
                if payload.get("source_context"):
                    continue
                historical = dict(source_context)
                historical["website_collected_at"] = row["created_at"]
                historical["crm_collected_at"] = row["created_at"]
                payload["source_context"] = historical
                self.conn.execute(
                    "UPDATE proposals SET payload=? WHERE fingerprint=?",
                    (json.dumps(payload), row["fingerprint"]),
                )
                changed += 1
        return changed

    def create_run(self, context: dict) -> None:
        run_id = context["run_id"]
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO reconciliation_runs(run_id,context,created_at) VALUES(?,?,?)",
                (run_id, json.dumps(context), context.get("created_at") or datetime.now(timezone.utc).isoformat()),
            )

    def list_runs(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT run_id,context,created_at FROM reconciliation_runs ORDER BY created_at DESC"
        ).fetchall()
        return [{**json.loads(row["context"]), "run_id": row["run_id"], "created_at": row["created_at"]} for row in rows]

    def get_run(self, run_id: str) -> dict:
        row = self.conn.execute(
            "SELECT run_id,context,created_at FROM reconciliation_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if not row:
            raise KeyError(run_id)
        return {**json.loads(row["context"]), "run_id": row["run_id"], "created_at": row["created_at"]}

    def ensure_legacy_run(self, context: dict) -> str:
        """Attach pre-run-model proposals to one preserved historical run."""
        run_id = context.get("run_id", "legacy-bellhaven")
        historical_context = {**context, "run_id": run_id}
        rows = self.conn.execute("SELECT fingerprint,payload,created_at FROM proposals").fetchall()
        created_at = min((row["created_at"] for row in rows), default=datetime.now(timezone.utc).isoformat())
        historical_context.setdefault("created_at", created_at)
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO reconciliation_runs(run_id,context,created_at) VALUES(?,?,?)",
                (run_id, json.dumps(historical_context), created_at),
            )
            for row in rows:
                payload = json.loads(row["payload"])
                if payload.get("run_id"):
                    continue
                payload["run_id"] = run_id
                payload.setdefault("source_context", historical_context)
                payload["source_context"]["run_id"] = run_id
                self.conn.execute(
                    "UPDATE proposals SET payload=? WHERE fingerprint=?",
                    (json.dumps(payload), row["fingerprint"]),
                )
        return run_id

    def list(self, decision: str | None = None) -> list[dict]:
        sql, params = "SELECT * FROM proposals", ()
        if decision:
            sql += " WHERE decision=?"; params = (decision,)
        sql += " ORDER BY created_at, fingerprint"
        rows = self.conn.execute(sql, params).fetchall()
        return [{**json.loads(r["payload"]), "decision": r["decision"],
                 "result": json.loads(r["result"]) if r["result"] else None,
                 "created_at": r["created_at"], "decided_at": r["decided_at"]} for r in rows]

    def get(self, fingerprint: str) -> dict:
        row = self.conn.execute("SELECT * FROM proposals WHERE fingerprint=?", (fingerprint,)).fetchone()
        if not row: raise KeyError(fingerprint)
        return {**json.loads(row["payload"]), "decision": row["decision"],
                "result": json.loads(row["result"]) if row["result"] else None,
                "created_at": row["created_at"], "decided_at": row["decided_at"]}

    def decide(self, fingerprint: str, decision: str, result: object = None) -> None:
        with self.conn:
            self.conn.execute("UPDATE proposals SET decision=?, result=?, decided_at=? WHERE fingerprint=? AND decision='pending'",
                              (decision, json.dumps(result), datetime.now(timezone.utc).isoformat(), fingerprint))

    def transition(self, fingerprint: str, expected: str, decision: str, result: object = None) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE proposals SET decision=?, result=?, decided_at=? WHERE fingerprint=? AND decision=?",
                (decision, json.dumps(result), datetime.now(timezone.utc).isoformat(), fingerprint, expected),
            )
        return cursor.rowcount == 1

    def reopen(self, fingerprint: str, expected: str = "withdrawn") -> bool:
        """Return a proposal to Pending while retaining its rollback audit for a safe retry."""
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE proposals SET decision='pending', decided_at=? WHERE fingerprint=? AND decision=?",
                (datetime.now(timezone.utc).isoformat(), fingerprint, expected),
            )
        return cursor.rowcount == 1
