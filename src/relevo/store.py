"""One SQLite file. An item is a job on its way out; `data` holds the panel."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
create table if not exists items(
  id integer primary key, job_id integer unique, route text not null, status text not null,
  data text not null, reason text default '', created_at text not null, updated_at text not null);
create table if not exists subs(endpoint text primary key, body text not null);
create table if not exists meta(k text primary key, v text);
"""
# ready: waiting for the person. sending: claimed by one send. sent / failed / unclear: final, never retried by code.
EDITABLE = ("ready", "failed")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.db.executescript(SCHEMA)

    def add(self, job_id: int, route: str, status: str, data: dict, reason: str = "") -> int | None:
        with self.lock:
            cur = self.db.execute(
                "insert or ignore into items(job_id,route,status,data,reason,created_at,updated_at) values(?,?,?,?,?,?,?)",
                (job_id, route, status, json.dumps(data, ensure_ascii=False), reason, now(), now()))
            return cur.lastrowid if cur.rowcount else None

    def known(self, job_id: int) -> bool:
        return self.db.execute("select 1 from items where job_id=?", (job_id,)).fetchone() is not None

    def get(self, item_id: int) -> dict | None:
        row = self.db.execute("select * from items where id=?", (item_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, statuses: tuple = ()) -> list[dict]:
        q, args = "select * from items", ()
        if statuses:
            q += f" where status in ({','.join('?' * len(statuses))})"
            args = statuses
        return [self._row(r) for r in self.db.execute(q + " order by id desc limit 200", args)]

    def save_data(self, item_id: int, data: dict) -> bool:
        with self.lock:
            cur = self.db.execute(
                f"update items set data=?, updated_at=? where id=? and status in ({','.join('?' * len(EDITABLE))})",
                (json.dumps(data, ensure_ascii=False), now(), item_id, *EDITABLE))
            return cur.rowcount == 1

    def claim(self, item_id: int, statuses: tuple = ("ready",)) -> bool:
        """Only one send can win: this is what makes two taps send one application."""
        marks = ",".join("?" * len(statuses))
        with self.lock:
            cur = self.db.execute(f"update items set status='sending', updated_at=? where id=? and status in ({marks})",
                                  (now(), item_id, *statuses))
            return cur.rowcount == 1

    def finish(self, item_id: int, status: str, reason: str = "") -> None:
        with self.lock:
            self.db.execute("update items set status=?, reason=?, updated_at=? where id=?",
                            (status, reason, now(), item_id))

    def add_sub(self, sub: dict) -> None:
        with self.lock:
            self.db.execute("insert or replace into subs values(?,?)", (sub["endpoint"], json.dumps(sub)))

    def subs(self) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute("select body from subs")]

    def drop_sub(self, endpoint: str) -> None:
        with self.lock:
            self.db.execute("delete from subs where endpoint=?", (endpoint,))

    @staticmethod
    def _row(row) -> dict:
        item = dict(row)
        item["data"] = json.loads(item["data"])
        return item
