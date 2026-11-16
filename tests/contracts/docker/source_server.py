"""Seed an ephemeral source-owned index, then serve the production application."""

# Mounted standalone into an isolated container, with no published host ports.
# ruff: noqa: INP001, S104

import duckdb
import uvicorn
from open_pulse_sources.index.infoscience.paths import duckdb_path

path = duckdb_path()
path.parent.mkdir(parents=True, exist_ok=True)
with duckdb.connect(str(path)) as connection:
    connection.execute(
        "CREATE TABLE organizations("
        "org_uuid VARCHAR, name VARCHAR, acronym VARCHAR, parent_org_uuid VARCHAR)",
    )
    connection.execute(
        "INSERT INTO organizations VALUES (?, ?, ?, NULL)",
        [
            "12345678-1234-1234-1234-123456789abc",
            "Boundary smoke organization",
            "BOUNDARY",
        ],
    )
uvicorn.run("open_pulse_sources.service.app:app", host="0.0.0.0", port=8080)
