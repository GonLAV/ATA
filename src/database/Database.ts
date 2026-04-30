import path from 'path';
import fs from 'fs';
import { createRequire } from 'module';
import { getConfig } from '../config/Config';

const loadModule = createRequire(__filename);

interface SqlStatement {
  run(...params: unknown[]): unknown;
  get(...params: unknown[]): unknown;
  all(...params: unknown[]): unknown[];
}

interface SqlDatabase {
  exec(sql: string): void;
  prepare(sql: string): SqlStatement;
  close(): void;
  pragma?(sql: string): unknown;
}

let db: SqlDatabase | null = null;

/**
 * Returns the singleton SQLite database connection, initialising the schema
 * on first call.
 */
export function getDatabase(): SqlDatabase {
  if (db) return db;

  const dbPath = getConfig().databasePath;
  const dir = path.dirname(path.resolve(dbPath));
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  db = createDatabase(dbPath);
  applyPragma(db, 'journal_mode = WAL');
  applyPragma(db, 'foreign_keys = ON');
  initSchema(db);
  return db;
}

function initSchema(database: SqlDatabase): void {
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

    CREATE TABLE IF NOT EXISTS product_risk_signals (
      id              TEXT PRIMARY KEY,
      run_id          TEXT NOT NULL REFERENCES test_runs(id),
      scenario_id     TEXT REFERENCES test_scenarios(id),
      type            TEXT NOT NULL,
      title           TEXT NOT NULL,
      severity        TEXT NOT NULL DEFAULT 'medium',
      evidence        TEXT NOT NULL,
      recommendation  TEXT NOT NULL,
      url             TEXT NOT NULL,
      detected_at     TEXT NOT NULL
    );
  `);
}

function createDatabase(dbPath: string): SqlDatabase {
  const nodeSqlite = tryLoadNodeSqlite();
  if (nodeSqlite) {
    return new nodeSqlite.DatabaseSync(dbPath);
  }

  const betterSqlite = tryLoadBetterSqlite();
  if (betterSqlite) {
    return new betterSqlite(dbPath);
  }

  throw new Error(
    'No SQLite runtime is available. Use Node.js 22+ for node:sqlite or install better-sqlite3 with native build tools.',
  );
}

function tryLoadNodeSqlite(): { DatabaseSync: new (path: string) => SqlDatabase } | undefined {
  try {
    return loadModule('node:sqlite') as { DatabaseSync: new (path: string) => SqlDatabase };
  } catch {
    return undefined;
  }
}

function tryLoadBetterSqlite(): (new (path: string) => SqlDatabase) | undefined {
  try {
    const module = loadModule('better-sqlite3') as { default?: new (path: string) => SqlDatabase } | (new (path: string) => SqlDatabase);
    return typeof module === 'function' ? module : module.default;
  } catch {
    return undefined;
  }
}

function applyPragma(database: SqlDatabase, statement: string): void {
  if (database.pragma) {
    database.pragma(statement);
    return;
  }

  database.exec(`PRAGMA ${statement}`);
}

/** Close the database (useful in tests). */
export function closeDatabase(): void {
  if (db) {
    db.close();
    db = null;
  }
}
