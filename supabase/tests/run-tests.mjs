import { readFile } from 'node:fs/promises';
import { PGlite } from '@electric-sql/pglite';

const db = new PGlite();
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8');
try {
  await db.exec(await read('./bootstrap.sql'));
  await db.exec(await read('../migrations/20261127000100_initial_schema.sql'));
  await db.exec(await read('../seed.sql'));
  await db.exec(await read('../seed.sql')); // Demo seed is repeatable.
  await db.exec(await read('./schema_rls.sql'));
  const { rows } = await db.query('select count(*)::int as count from test_results');
  console.log(`PASS: ${rows[0].count} PostgreSQL schema/RLS assertions; migration and repeat seed applied.`);
} finally {
  await db.close();
}
