import Database from 'better-sqlite3';
import path from 'path';
import fs from 'fs';

let db: Database.Database | null = null;

/**
 * Returns the singleton SQLite database connection, initialising the schema
 * on first call.
 */
export function getDatabase(): Database.Database {
  if (db) return db;

  const dbPath = process.env.DATABASE_PATH ?? './qa_copilot.db';
  const dir = path.dirname(path.resolve(dbPath));
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  db = new Database(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');
  initSchema(db);
  return db;
}

function initSchema(database: Database.Database): void {
  database.exec(`
    CREATE TABLE IF NOT EXISTS test_runs (
      id              TEXT PRIMARY KEY,
      url             TEXT NOT NULL,
      status          TEXT NOT NULL DEFAULT 'pending',
      started_at      TEXT NOT NULL,
      completed_at    TEXT,
      total_scenarios INTEGER NOT NULL DEFAULT 0,
      passed_scenarios INTEGER NOT NULL DEFAULT 0,
      failed_scenarios INTEGER NOT NULL DEFAULT 0,
      bugs_found      INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS test_scenarios (
      id           TEXT PRIMARY KEY,
      run_id       TEXT NOT NULL REFERENCES test_runs(id),
      title        TEXT NOT NULL,
      description  TEXT NOT NULL,
      priority     TEXT NOT NULL DEFAULT 'medium',
      steps_json   TEXT NOT NULL,
      status       TEXT NOT NULL DEFAULT 'pending',
      duration_ms  INTEGER,
      created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS bug_reports (
      id                  TEXT PRIMARY KEY,
      run_id              TEXT NOT NULL REFERENCES test_runs(id),
      scenario_id         TEXT REFERENCES test_scenarios(id),
      title               TEXT NOT NULL,
      severity            TEXT NOT NULL DEFAULT 'medium',
      description         TEXT NOT NULL,
      reproduction_steps  TEXT NOT NULL,
      expected_behavior   TEXT NOT NULL,
      actual_behavior     TEXT NOT NULL,
      screenshot_path     TEXT,
      url                 TEXT NOT NULL,
      detected_at         TEXT NOT NULL,
      console_errors      TEXT,
      network_errors      TEXT,
      error_stack         TEXT
    );
  `);
}

/** Close the database (useful in tests). */
export function closeDatabase(): void {
  if (db) {
    db.close();
    db = null;
  }
}
