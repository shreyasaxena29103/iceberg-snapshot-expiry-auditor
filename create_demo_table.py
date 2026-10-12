
from pyarrow import Table as ArrowTable
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField, StringType

#catalog = load_catalog("default")


from pathlib import Path
from pyiceberg.catalog import load_catalog

project_dir = Path(__file__).resolve().parent
warehouse_dir = project_dir / "warehouse"
warehouse_dir.mkdir(parents=True, exist_ok=True)

catalog = load_catalog(
    "default",
    **{
        "type": "sql",
        "uri": f"sqlite:///{(warehouse_dir / 'catalog.db').as_posix()}",
        "warehouse": str(warehouse_dir.resolve()),
    },
)

print("Catalog initialized successfully")
print("Warehouse:", warehouse_dir.resolve().as_uri())


# Create the namespace if it does not exist.
try:
    catalog.create_namespace("audit_demo")
except NamespaceAlreadyExistsError:
    pass

schema = Schema(
    NestedField(
        field_id=1,
        name="event_id",
        field_type=LongType(),
        required=True,
    ),
    NestedField(
        field_id=2,
        name="event_type",
        field_type=StringType(),
        required=False,
    ),
)

identifier = "audit_demo.events"

# Create the table only if it doesn't already exist.
if catalog.table_exists(identifier):
    table = catalog.load_table(identifier)
else:
    table = catalog.create_table(
        identifier=identifier,
        schema=schema,
    )

# Each append creates a new snapshot.

import pyarrow as pa

for event_id, event_type in [
    (1, "login"),
    (2, "purchase"),
    (3, "logout"),
]:
    data = pa.Table.from_arrays(
        [
            pa.array([event_id], type=pa.int64()),
            pa.array([event_type], type=pa.string()),
        ],
        schema=pa.schema([
            pa.field("event_id", pa.int64(), nullable=False),
            pa.field("event_type", pa.string(), nullable=True),
        ]),
    )

    table.append(data)

print(f"Table: {identifier}")
print(f"Snapshots created: {len(list(table.snapshots()))}")
print("Demo table is ready.")

