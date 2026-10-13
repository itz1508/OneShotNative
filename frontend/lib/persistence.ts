/**
 * SQLite Persistence Architecture & Contract Specification (Gap 9).
 *
 * Defines the persistence contract, WAL configuration pragmas, migration
 * tracking schema, soft-delete semantics, and retention pruning policies
 * required for production-grade SQLite storage of OneShot runs and traces.
 *
 * This provides the exact blueprints for server-side persistence implementations
 * (e.g. Python aiosqlite / sqlite3 or client-side OPFS SQLite).
 */

export interface SqlitePragmas {
  journalMode: "WAL";
  synchronous: "NORMAL";
  busyTimeoutMs: number;
  foreignKeys: boolean;
  cacheSize: number;
}

export const RECOMMENDED_SQLITE_PRAGMAS: SqlitePragmas = {
  journalMode: "WAL", // Prevents read-write contention
  synchronous: "NORMAL", // Safe in WAL mode with fast writes
  busyTimeoutMs: 5000, // Waits up to 5s before throwing SQLITE_BUSY
  foreignKeys: true, // Enforce referential integrity
  cacheSize: -20000, // 20 MB cache
};

export const SQLITE_INIT_PRAGMAS_SQL = `
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
PRAGMA cache_size = -20000;
`.trim();

export const MIGRATION_SCHEMA_SQL = `
-- Tracks executed schema migrations with cryptographic checksums
CREATE TABLE IF NOT EXISTS _schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    checksum TEXT NOT NULL
);
`.trim();

export const RUN_TABLES_SCHEMA_SQL = `
-- Runs table: stores run request metadata and state with soft delete
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    intent TEXT NOT NULL,
    task_kind TEXT NOT NULL,
    state TEXT NOT NULL,
    options_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL -- Soft delete marker (Gap 9)
);

CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs (created_at) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_runs_deleted_at ON runs (deleted_at);

-- Sources table: scope sources registered for each run
CREATE TABLE IF NOT EXISTS sources (
    id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    operation TEXT NOT NULL,
    label TEXT,
    meta_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL,
    PRIMARY KEY (run_id, id)
);

-- Trace events table: immutable, ordered execution log
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    index_num INTEGER NOT NULL,
    stage TEXT,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    detail TEXT,
    at_ms INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_traces_run ON traces (run_id, index_num);

-- Delivered outputs table: snapshot of final delivered or reviewed document
CREATE TABLE IF NOT EXISTS outputs (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    output_json TEXT NOT NULL,
    quality_outcome TEXT NOT NULL,
    supported_task BOOLEAN NOT NULL,
    finalized BOOLEAN DEFAULT 0,
    finalized_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL
);
`.trim();

/**
 * SQL for soft-deleting a run and its associated records.
 */
export const SOFT_DELETE_RUN_SQL = `
UPDATE runs SET deleted_at = CURRENT_TIMESTAMP WHERE run_id = :run_id AND deleted_at IS NULL;
UPDATE sources SET deleted_at = CURRENT_TIMESTAMP WHERE run_id = :run_id AND deleted_at IS NULL;
UPDATE outputs SET deleted_at = CURRENT_TIMESTAMP WHERE run_id = :run_id AND deleted_at IS NULL;
`.trim();

/**
 * SQL for purging expired runs past the retention window (e.g. 30 days).
 */
export function buildRetentionPruneSql(retentionDays = 30): string {
  return `
-- Hard delete runs marked as deleted older than retentionDays or created older than retentionDays
DELETE FROM runs
WHERE (deleted_at IS NOT NULL AND deleted_at < datetime('now', '-${retentionDays} days'))
   OR (deleted_at IS NULL AND created_at < datetime('now', '-${retentionDays * 2} days'));
`.trim();
}
