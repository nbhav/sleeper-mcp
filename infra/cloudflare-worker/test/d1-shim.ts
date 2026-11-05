// Minimal D1Database stand-in over node:sqlite for tests. It applies the real
// migrations/*.sql files and counts D1 queries the way Workers bills
// subrequests: each first/all/run outside a batch is one query and each
// batch() is one query.
import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

type Row = Record<string, unknown>;

export type D1Shim = {
  db: D1Database;
  sqlite: DatabaseSync;
  queries: string[];
  queryCount(): number;
  resetCounts(): void;
};

const MIGRATIONS_DIR = new URL("../migrations/", import.meta.url);

export function createD1(options: { migrate?: boolean } = {}): D1Shim {
  const sqlite = new DatabaseSync(":memory:");
  if (options.migrate !== false) {
    for (const file of readdirSync(MIGRATIONS_DIR).filter((name) => name.endsWith(".sql")).sort()) {
      sqlite.exec(readFileSync(new URL(file, MIGRATIONS_DIR), "utf8"));
    }
  }
  const queries: string[] = [];
  let count = 0;

  const normalize = (values: unknown[]) => values.map((value) => {
    if (value === undefined) return null;
    if (typeof value === "boolean") return value ? 1 : 0;
    return value as string | number | null;
  });

  class Statement {
    sql: string;
    params: unknown[];
    constructor(sql: string, params: unknown[] = []) {
      this.sql = sql;
      this.params = params;
    }
    bind(...values: unknown[]): Statement {
      return new Statement(this.sql, values);
    }
    execAll(): Row[] {
      const statement = sqlite.prepare(this.sql);
      if (statement.columns().length === 0) {
        statement.run(...normalize(this.params));
        return [];
      }
      return statement.all(...normalize(this.params)).map((row) => ({ ...row }));
    }
    execRun() {
      const result = sqlite.prepare(this.sql).run(...normalize(this.params));
      return { success: true, results: [], meta: { changes: Number(result.changes) } };
    }
    async first<T = Row>(column?: string): Promise<T | null> {
      count += 1;
      queries.push(this.sql);
      const row = this.execAll()[0];
      if (!row) return null;
      return (column ? row[column] : row) as T;
    }
    async all<T = Row>() {
      count += 1;
      queries.push(this.sql);
      return { success: true, results: this.execAll() as T[], meta: {} };
    }
    async run() {
      count += 1;
      queries.push(this.sql);
      return this.execRun();
    }
  }

  const db = {
    prepare(sql: string) {
      return new Statement(sql);
    },
    async batch(statements: Statement[]) {
      count += 1;
      queries.push(`BATCH(${statements.length})`);
      sqlite.exec("BEGIN");
      try {
        const results = statements.map((statement) => ({ success: true, results: statement.execAll(), meta: {} }));
        sqlite.exec("COMMIT");
        return results;
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
    async exec(sql: string) {
      count += 1;
      sqlite.exec(sql);
      return { count: 1, duration: 0 };
    }
  };

  return {
    db: db as unknown as D1Database,
    sqlite,
    queries,
    queryCount: () => count,
    resetCounts: () => {
      count = 0;
      queries.length = 0;
    }
  };
}
