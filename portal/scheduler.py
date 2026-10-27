"""Scheduled updates from a dataset's source URL (OPZ 1.29, 1.33).

The administrator gives a dataset a source URL and an interval in minutes. When a run is due, the source is
fetched; changed content becomes a new version, unchanged content leaves the version alone, and every run is
written to the import history. The fetcher is a parameter, so tests and air-gapped installs can supply their own.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from . import importers, service
from .db import Store

Fetcher = Callable[[str], bytes]


def http_fetch(url: str) -> bytes:
    if not url.startswith(("https://", "http://")):
        raise ValueError("only http and https sources")
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - scheme checked above
        return r.read(50_000_000)


def due(store: Store, at: datetime | None = None) -> list[dict]:
    now = at or datetime.now(timezone.utc)
    out = []
    for d in service.list_datasets(store):
        if not d["source_url"] or not d["schedule_minutes"]:
            continue
        runs = [h for h in service.import_history(store, d["id"]) if h["mode"] == "scheduled"]
        last = datetime.fromisoformat(runs[0]["at"]) if runs else None
        if last is None or now - last >= timedelta(minutes=d["schedule_minutes"]):
            out.append(d)
    return out


def run_due(store: Store, fetch: Fetcher = http_fetch, at: datetime | None = None) -> list[dict]:
    results = []
    for d in due(store, at):
        fmt = d["source_url"].rsplit(".", 1)[-1].lower().split("?")[0]
        try:
            rows = importers.parse(fetch(d["source_url"]), fmt)
            result = service.import_rows(store, d["id"], rows, mode="scheduled", source_format=fmt)
        except (importers.ImportFailed, OSError, ValueError) as e:
            errors = getattr(e, "errors", [{"row": None, "field": None, "message": f"source unavailable: {e}"}])
            with store.tx() as con:
                con.execute("INSERT INTO imports(dataset_id, at, mode, ok, errors_json) VALUES (?,?,?,?,?)",
                            (d["id"], datetime.now(timezone.utc).isoformat(timespec="seconds"), "scheduled", 0,
                             json.dumps(errors, ensure_ascii=False)))
            result = {"ok": False, "errors": errors}
        results.append({"dataset": d["id"]} | result)
    return results


def start(store: Store, every_s: int = 60, fetch: Fetcher = http_fetch) -> threading.Thread:
    def loop():
        stop = threading.Event()
        while not stop.wait(every_s):
            run_due(store, fetch)
    t = threading.Thread(target=loop, name="portal-scheduler", daemon=True)
    t.start()
    return t
