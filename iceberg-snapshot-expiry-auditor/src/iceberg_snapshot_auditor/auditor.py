"""Snapshot auditing and dry-run expiration planning for PyIceberg tables.

The default behavior is read-only. Snapshot expiration is only committed when
``--execute`` is explicitly supplied.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from pyiceberg.catalog import load_catalog

LOG = logging.getLogger("iceberg_snapshot_auditor")


@dataclass(frozen=True)
class SnapshotRecord:
    snapshot_id: int
    timestamp_ms: int
    committed_at: str
    parent_snapshot_id: int | None
    is_current: bool
    is_tagged_or_branched: bool
    age_days: float
    would_expire: bool
    reason: str


def _utc_iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat()


def _snapshot_id(snapshot: Any) -> int:
    return int(snapshot.snapshot_id)


def _refs_snapshot_ids(table: Any) -> set[int]:
    """Return snapshot IDs referenced by Iceberg branches/tags when available."""
    protected: set[int] = set()
    refs = getattr(table.metadata, "refs", None) or {}
    for ref in refs.values():
        snapshot_id = getattr(ref, "snapshot_id", None)
        if snapshot_id is not None:
            protected.add(int(snapshot_id))
    return protected


def audit_snapshots(
    table: Any,
    older_than_days: int = 7,
    retain_last: int = 5,
    now_ms: int | None = None,
) -> list[SnapshotRecord]:
    """Create a conservative expiration plan without mutating the table.

    Candidates must be older than the age threshold and outside the newest
    ``retain_last`` snapshots. The current snapshot and snapshots referenced
    by branches/tags are always protected in the report.
    """
    if older_than_days < 0:
        raise ValueError("older_than_days must be >= 0")
    if retain_last < 1:
        raise ValueError("retain_last must be >= 1")

    now_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    snapshots = sorted(list(table.snapshots()), key=lambda s: int(s.timestamp_ms))
    newest_ids = {_snapshot_id(s) for s in snapshots[-retain_last:]}
    protected_ids = _refs_snapshot_ids(table)
    current = table.current_snapshot()
    current_id = int(current.snapshot_id) if current is not None else None
    cutoff_ms = now_ms - older_than_days * 24 * 60 * 60 * 1000

    records: list[SnapshotRecord] = []
    for snapshot in snapshots:
        sid = _snapshot_id(snapshot)
        ts_ms = int(snapshot.timestamp_ms)
        age_days = max(0.0, (now_ms - ts_ms) / (24 * 60 * 60 * 1000))
        is_current = sid == current_id
        is_referenced = sid in protected_ids
        if ts_ms >= cutoff_ms:
            would_expire, reason = False, "younger than threshold"
        elif sid in newest_ids:
            would_expire, reason = False, "within retain_last window"
        elif is_current:
            would_expire, reason = False, "current snapshot"
        elif is_referenced:
            would_expire, reason = False, "referenced by branch/tag"
        else:
            would_expire, reason = True, "older than threshold and outside retain_last window"

        records.append(
            SnapshotRecord(
                snapshot_id=sid,
                timestamp_ms=ts_ms,
                committed_at=_utc_iso(ts_ms),
                parent_snapshot_id=getattr(snapshot, "parent_snapshot_id", None),
                is_current=is_current,
                is_tagged_or_branched=is_referenced,
                age_days=round(age_days, 2),
                would_expire=would_expire,
                reason=reason,
            )
        )
    return records


def _load_table(catalog_name: str, table_identifier: str, properties: dict[str, str]) -> Any:
    catalog = load_catalog(catalog_name, **properties)
    return catalog.load_table(table_identifier)


def run_audit(
    catalog_name: str,
    table_identifier: str,
    properties: dict[str, str],
    older_than_days: int,
    retain_last: int,
    execute: bool = False,
) -> dict[str, Any]:
    table = _load_table(catalog_name, table_identifier, properties)
    records = audit_snapshots(table, older_than_days=older_than_days, retain_last=retain_last)
    candidates = [record for record in records if record.would_expire]

    result: dict[str, Any] = {
        "catalog": catalog_name,
        "table": table_identifier,
        "mode": "execute" if execute else "dry-run",
        "older_than_days": older_than_days,
        "retain_last": retain_last,
        "snapshot_count": len(records),
        "candidate_count": len(candidates),
        "snapshots": [asdict(record) for record in records],
    }

    if execute and candidates:
        # PyIceberg performs the authoritative expiration transaction. The
        # audit plan is advisory; the API also applies Iceberg's own safety
        # rules for current snapshots and references.
        cutoff_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - older_than_days * 24 * 60 * 60 * 1000
        table.expire_snapshots().older_than(cutoff_ms).retain_last(retain_last).commit()
        result["expiration_committed"] = True
        result["note"] = (
            "PyIceberg expiration was committed. Re-load the table and re-run "
            "the audit to verify the post-expiration snapshot inventory."
        )
    elif execute:
        result["expiration_committed"] = False
        result["note"] = "No eligible snapshots found; no expiration transaction was submitted."
    else:
        result["expiration_committed"] = False
        result["note"] = "Dry-run only. No table metadata or files were changed."

    return result


def _parse_properties(items: Iterable[str]) -> dict[str, str]:
    properties: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Catalog property must be KEY=VALUE, got: {item!r}")
        key, value = item.split("=", 1)
        if not key.strip():
            raise ValueError(f"Catalog property key cannot be empty: {item!r}")
        properties[key.strip()] = value
    return properties


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit Iceberg snapshots and preview or execute snapshot expiration."
    )
    parser.add_argument("--catalog", default=os.getenv("ICEBERG_CATALOG", "default"),
                        help="PyIceberg catalog name (default: ICEBERG_CATALOG or 'default').")
    parser.add_argument("--table", required=True, help="Fully qualified table identifier, e.g. analytics.events.")
    parser.add_argument("--older-than-days", type=int, default=7, help="Age threshold in days (default: 7).")
    parser.add_argument("--retain-last", type=int, default=5, help="Keep at least this many newest snapshots (default: 5).")
    parser.add_argument("--property", action="append", default=[], metavar="KEY=VALUE",
                        help="Catalog property; may be repeated. Prefer environment/config for secrets.")
    parser.add_argument("--execute", action="store_true",
                        help="Actually commit expiration. Omit this flag for dry-run (the default).")
    parser.add_argument("--json-out", help="Optional path to write the complete JSON report.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        properties = _parse_properties(args.property)
        report = run_audit(
            catalog_name=args.catalog,
            table_identifier=args.table,
            properties=properties,
            older_than_days=args.older_than_days,
            retain_last=args.retain_last,
            execute=args.execute,
        )
        output = json.dumps(report, indent=2, default=str)
        print(output)
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as handle:
                handle.write(output + "\n")
        return 0
    except Exception as exc:
        LOG.exception("Snapshot audit failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
