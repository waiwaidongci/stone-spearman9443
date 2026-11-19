Schema v1 is defined by the SQLAlchemy models and created at startup via Base.metadata.create_all. For this local small application no destructive migration is run. Back up named db-data before modifying schemas.

## v2 — instrument calibration (additive)

New tables only; existing research tables are untouched, so an existing v1 volume keeps working and the new tables are created on the next backend start:

- `calibration_projects` — name, source file, chosen baseline instrument, and a monotonically increasing `revision`.
- `calibration_instruments` — unique `(project_id, name)` roster.
- `calibration_pairs` — `lower <= x_a - x_b <= upper`, plus `basis` text and timestamps.
- `calibration_runs` — frozen snapshot JSON, SHA256 checksum, `snapshot_revision`, unique `(project_id, idempotency_key)`, result, publication fields.

`revision` is bumped by every roster/baseline/interval edit. Run creation takes `SELECT … FOR UPDATE` on the project row before reading the roster and intervals, so a frozen run always reflects exactly one revision.
