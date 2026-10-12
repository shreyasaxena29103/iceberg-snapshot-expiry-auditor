from types import SimpleNamespace

from iceberg_snapshot_auditor.auditor import audit_snapshots


class FakeTable:
    def __init__(self):
        self._snapshots = [
            SimpleNamespace(snapshot_id=1, timestamp_ms=1_000, parent_snapshot_id=None),
            SimpleNamespace(snapshot_id=2, timestamp_ms=2_000, parent_snapshot_id=1),
            SimpleNamespace(snapshot_id=3, timestamp_ms=3_000, parent_snapshot_id=2),
            SimpleNamespace(snapshot_id=4, timestamp_ms=4_000, parent_snapshot_id=3),
        ]
        self.metadata = SimpleNamespace(refs={})
    def snapshots(self):
        return iter(self._snapshots)
    def current_snapshot(self):
        return self._snapshots[-1]


def test_audit_protects_current_and_newest_snapshots():
    table = FakeTable()
    records = audit_snapshots(table, older_than_days=0, retain_last=2, now_ms=10_000_000)
    by_id = {record.snapshot_id: record for record in records}
    assert by_id[4].is_current
    assert not by_id[4].would_expire
    assert not by_id[3].would_expire
    assert by_id[1].would_expire
    assert by_id[2].would_expire


def test_rejects_invalid_retention():
    table = FakeTable()
    try:
        audit_snapshots(table, retain_last=0)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected ValueError")
