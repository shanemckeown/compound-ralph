#!/usr/bin/env node
"use strict";

/** PostgreSQL-WASM fallback for the scratch-from-empty migration proof.
 *
 * Native PostgreSQL remains the primary backend.  PGlite is PostgreSQL compiled
 * to WASM and is used only where a sandbox forbids the server's semaphore
 * syscalls.  Input and output are JSON files so no database URL or secret is
 * ever printed.
 */

const fs = require("node:fs");
const path = require("node:path");

async function applyChain(PGlite, drizzleDir) {
  const db = new PGlite();
  const journal = JSON.parse(
    fs.readFileSync(path.join(drizzleDir, "meta/_journal.json"), "utf8")
  );
  const applied = [];
  const failures = [];
  for (const entry of journal.entries) {
    const sqlPath = path.join(drizzleDir, `${entry.tag}.sql`);
    if (!fs.existsSync(sqlPath)) {
      failures.push({ tag: entry.tag, error: "SQL FILE MISSING" });
      continue;
    }
    const sql = fs.readFileSync(sqlPath, "utf8");
    try {
      await db.exec("BEGIN");
      await db.exec(sql);
      await db.exec("COMMIT");
      applied.push(entry.tag);
    } catch (error) {
      try {
        await db.exec("ROLLBACK");
      } catch (_) {
        // Keep the original migration error.
      }
      failures.push({ tag: entry.tag, error: String(error.message).slice(-1000) });
    }
  }
  return { db, applied, failures, entry_count: journal.entries.length };
}

async function rows(db, sql) {
  const result = await db.query(sql);
  return result.rows.map((row) => Object.values(row).map(String));
}

async function main() {
  const [requestPath, responsePath] = process.argv.slice(2);
  if (!requestPath || !responsePath || !process.env.PGLITE_ENTRY) {
    throw new Error("usage: PGLITE_ENTRY=... pglite-proof.cjs request.json response.json");
  }
  const { PGlite } = require(process.env.PGLITE_ENTRY);
  const request = JSON.parse(fs.readFileSync(requestPath, "utf8"));
  const base = await applyChain(PGlite, request.base_dir);
  const integration = await applyChain(PGlite, request.integration_dir);
  const actual = {
    tables: await rows(
      integration.db,
      "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2"
    ),
    columns: await rows(
      integration.db,
      "SELECT table_schema, table_name, column_name, is_nullable FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1,2,ordinal_position"
    ),
    constraints: await rows(
      integration.db,
      "SELECT n.nspname, c.relname, con.conname FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2,3"
    ),
    indexes: await rows(
      integration.db,
      "SELECT schemaname, tablename, indexname FROM pg_indexes WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2,3"
    ),
  };
  await base.db.close();
  await integration.db.close();
  fs.writeFileSync(
    responsePath,
    JSON.stringify({
      base: {
        applied: base.applied,
        failures: base.failures,
        entry_count: base.entry_count,
      },
      integration: {
        applied: integration.applied,
        failures: integration.failures,
        entry_count: integration.entry_count,
      },
      actual,
    })
  );
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exit(1);
});
