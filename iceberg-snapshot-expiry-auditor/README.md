# Iceberg Snapshot Expiry Auditor

A small Python CLI built with PyIceberg that inventories table snapshots, explains which snapshots appear eligible for expiration, and writes a JSON audit report. **Dry-run is the default**; expiration requires the explicit `--execute` flag.

## Repository layout

```text
iceberg-snapshot-expiry-auditor/
├── README.md
├── requirements.txt
├── src/
│   └── iceberg_snapshot_auditor/
│       ├── __init__.py
│       └── auditor.py
└── tests/
```

## 1. Install

Python 3.10+ is recommended.

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

Configure a PyIceberg catalog in `~/.pyiceberg.yaml` or pass catalog properties on the command line. Example for a local SQL catalog:

```yaml
catalog:
  default:
    type: sql
    uri: sqlite:////absolute/path/to/iceberg_catalog.db
    warehouse: file:///absolute/path/to/warehouse
```

Use the configuration appropriate to your actual catalog (REST, Glue, Hive, SQL, etc.). For cloud catalogs, configure credentials using the provider's recommended environment/credential chain; avoid putting secrets in shell history.

## 2. Run a dry-run audit

From the repository root:

```bash
PYTHONPATH=src python -m iceberg_snapshot_auditor.auditor \
  --catalog default \
  --table analytics.events \
  --older-than-days 7 \
  --retain-last 5 \
  --json-out snapshot-audit.json
```

The command is read-only unless `--execute` is supplied. The JSON report includes each snapshot's ID, timestamp, age, current/reference flags, proposed action, and reason.

## 3. Execute expiration (destructive)

Review the dry-run report first, then explicitly run:

```bash
PYTHONPATH=src python -m iceberg_snapshot_auditor.auditor \
  --catalog default \
  --table analytics.events \
  --older-than-days 30 \
  --retain-last 10 \
  --execute
```

This calls PyIceberg's `table.expire_snapshots().older_than(cutoff_ms).retain_last(retain_last).commit()`. It changes table metadata and may make data files eligible for deletion according to the catalog/FileIO implementation. Test in a non-production table first and ensure retention requirements are met.

## Catalog properties

Pass repeatable `--property KEY=VALUE` options when needed, for example:

```bash
PYTHONPATH=src python -m iceberg_snapshot_auditor.auditor \
  --catalog my_rest_catalog \
  --table analytics.events \
  --property type=rest \
  --property uri=https://catalog.example.com \
  --property warehouse=my-warehouse
```

Prefer a catalog config file or environment/credential chain for sensitive values.

## Important behavior and limitations

- Dry-run is an **estimate**, not a simulation of the catalog's commit. The audit protects the current snapshot, the newest `retain_last` snapshots, and snapshots referenced by known branches/tags.
- The actual PyIceberg expiration API is authoritative and may retain additional snapshots based on Iceberg semantics and implementation/version.
- Snapshot expiration is not the same as orphan-file cleanup. Do not add orphan-file deletion unless you have a separately reviewed retention policy.
- This tool does not scan data files or validate table data; it reads table metadata.
- Confirm API compatibility with your installed PyIceberg version and catalog backend before production use.

## Exit codes

- `0`: audit/report completed.
- `1`: catalog loading, audit, or expiration failed.
