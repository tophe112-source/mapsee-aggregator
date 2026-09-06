#!/usr/bin/env node
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { parse, run } from './agent-context.mjs';

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-context-'));
process.on('exit', () => { try { fs.rmSync(root, { recursive: true, force: true }); } catch {} });
const git = (...args) => spawnSync('git', args, { cwd: root, encoding: 'utf8' });
fs.mkdirSync(path.join(root, 'src'), { recursive: true });
fs.mkdirSync(path.join(root, 'supabase', 'migrations'), { recursive: true });
fs.mkdirSync(path.join(root, 'docs', 'agents'), { recursive: true });
fs.writeFileSync(path.join(root, 'src', 'helpers.py'), 'async def parse_fish(value):\n    return value\n');
fs.writeFileSync(path.join(root, 'src', 'wide.js'), Array.from({ length: 120 }, () => 'z'.repeat(390)).join('\n'));
fs.writeFileSync(path.join(root, 'src', 'binary.js'), Buffer.from([0, 1, 2]));
fs.writeFileSync(path.join(root, 'src', 'big.js'), ['// --- Aquarium ---', 'function feedFish() {}', ...Array.from({ length: 300 }, (_, i) => `const row${i} = () => ${i};`), 'x'.repeat(10000), ''].join('\n'));
fs.writeFileSync(path.join(root, 'src', 'paged.js'), Array.from({ length: 24 }, (_, i) => `const page${i} = () => ${i};`).join('\n') + '\n');
fs.writeFileSync(path.join(root, 'supabase', 'migrations', '001.sql'), 'create table fish (id int);\ncreate unique index fish_id_idx on fish(id);\ncreate materialized view fish_rollup as select id from fish;\n');
fs.writeFileSync(path.join(root, 'supabase', 'migrations', '002.sql'), 'create table newer_fish (id int);\n');
fs.writeFileSync(path.join(root, 'supabase', 'migrations', '003.sql'), 'create table latest_fish (id int);\n');
fs.writeFileSync(path.join(root, 'docs', 'agents', 'retrieval-fixture.md'), [
  '- **Wrapped retrieval',
  '  headline** — measured fixture note',
  'The important NeedlePhrase appears only in this body.',
  'A second NeedlePhrase keeps note pagination observable.',
  '',
  '- **Neighboring note**',
  'This note is irrelevant and must not be returned.',
  '',
  '- **Another matching note**',
  'NeedlePhrase appears in this separate note body.',
  '',
  '- **Third matching note**',
  'NeedlePhrase appears here too.',
  '',
  '- **Fourth matching note**',
  'NeedlePhrase appears here too.',
  '',
].join('\n'));
fs.writeFileSync(path.join(root, 'docs', 'agents', 'INDEX.md'), 'NeedlePhrase duplicate index entry\n');
const longNote = [
  '# Long bounded note',
  `${'long prefix '.repeat(30)}LongNeedle${' long suffix'.repeat(30)}`,
  ...Array.from({ length: 128 }, (_, i) => `Measured filler line ${i + 1}.`),
  'LateNeedle appears near the end of this long note section.',
  'Closing line.',
].join('\n') + '\n';
fs.writeFileSync(path.join(root, 'docs', 'agents', 'long-note.md'), longNote);
fs.writeFileSync(path.join(root, 'supabase', 'migrations', '004_history.sql'), [
  '-- NeedlePhrase repeated comment one',
  '-- NeedlePhrase repeated comment two',
  'create or replace',
  'function public.NeedlePhrase_func(p_id uuid)',
  'returns integer',
  'language sql',
  'as $$ select 1; $$;',
].join('\n') + '\n');
fs.writeFileSync(path.join(root, 'supabase', 'migrations', '005_history.sql'), [
  '-- NeedlePhrase repeated comment three',
  'create table needle_table (id int);',
].join('\n') + '\n');
fs.writeFileSync(path.join(root, 'supabase', 'migrations', '006_history.sql'), '-- NeedlePhrase repeated comment four\n');
fs.writeFileSync(path.join(root, '.env'), 'TOKEN=secret\n');
fs.writeFileSync(path.join(root, 'secret.pem'), 'private\n');
try { fs.symlinkSync(path.join(root, '.env'), path.join(root, 'src', 'internal-secret.js')); } catch (error) { if (error.code !== 'EPERM' && error.code !== 'EACCES') throw error; }
assert.equal(git('init', '-q').status, 0);
assert.equal(git('config', 'user.email', 'agent@example.invalid').status, 0);
assert.equal(git('config', 'user.name', 'agent-context').status, 0);
assert.equal(git('add', '.').status, 0);
assert.equal(git('commit', '-qm', 'fixture').status, 0);

const map = run({ file: 'src/big.js', lines: 40, maxWords: 1500 }, root);
assert.match(map, /Sections\/declarations/);
assert.match(map, /Aquarium/);
assert.match(map, /Total matches:/);
assert.ok(map.split('\n').length < 400, 'default map must stay bounded');
const slice = run({ file: 'src/big.js', around: 125, lines: 120, maxWords: 1500 }, root);
assert.match(slice, /Slice: lines/);
assert.match(slice, /\| const row/);
assert.ok(slice.split('\n').length <= 150, 'slice must obey hard line bound');
const giantSlice = run({ file: 'src/big.js', around: 303, lines: 1, maxWords: 50 }, root);
assert.match(giantSlice, /…/);
assert.match(giantSlice, /Output truncated: yes/);
assert.ok(giantSlice.length <= 16000, 'hard character cap must hold');
const wide = run({ file: 'src/wide.js', around: 60, lines: 120, maxWords: 4000 }, root);
assert.ok(wide.length <= 16000 && wide.length > 15000, 'exercise the actual character budget');
assert.match(wide, /Output truncated: yes/);
const lowBudget = run({ file: 'src/big.js', maxWords: 50 }, root);
assert.ok(lowBudget.trim().split(/\s+/).length <= 50, 'footer stays inside word budget');
assert.match(lowBudget, /Output truncated: yes/);
assert.throws(() => run({ file: 'src/binary.js', maxWords: 1500 }, root), /binary/);
const sql = run({ file: 'supabase/migrations/001.sql', maxWords: 1500 }, root);
assert.match(sql, /CREATE TABLE fish/);
assert.match(sql, /CREATE UNIQUE INDEX fish_id_idx/);
assert.match(sql, /CREATE MATERIALIZED VIEW fish_rollup/);
assert.match(run({ file: 'src/helpers.py', maxWords: 1500 }, root), /parse_fish/);
const found = run({ find: 'row12', maxWords: 1500 }, root);
assert.match(found, /Literal search/);
assert.match(found, /src\/big\.js:/);
assert.match(run({ find: 'row12|row13', maxWords: 1500 }, root), /Total matches: 0/);
const scoped = run({ find: 'table', path: 'supabase/migrations', latest: true, maxWords: 1500 }, root);
assert.ok(scoped.indexOf('003.sql') < scoped.indexOf('001.sql'), 'latest should reverse lexical migration order');
assert.throws(() => run({ file: '../src/big.js', maxWords: 1500 }, root), /outside|secret|path/);
assert.throws(() => run({ file: '.env', maxWords: 1500 }, root), /tracked|secret|path/);
assert.throws(() => run({ file: 'src/helpers.py', around: 99, lines: 1, maxWords: 1500 }, root), /line/);
fs.mkdirSync(path.join(root, 'tools'), { recursive: true });
const tool = fileURLToPath(new URL('./agent-context.mjs', import.meta.url));
const copiedTool = path.join(root, 'tools', 'agent-context.mjs');
fs.copyFileSync(tool, copiedTool);
const fromElsewhere = spawnSync(process.execPath, [copiedTool, '--file', 'src/big.js', '--lines', '1', '--around', '125'], { cwd: os.tmpdir(), encoding: 'utf8' });
assert.equal(fromElsewhere.status, 0, fromElsewhere.stderr);
assert.match(fromElsewhere.stdout, new RegExp(`Repository: ${root.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`));
assert.ok(fromElsewhere.stdout.length < 16000, 'character cap must hold');
if (fs.existsSync(path.join(root, 'src', 'internal-secret.js'))) assert.match(run({ find: 'TOKEN', maxWords: 1500 }, root), /skipped: [1-9]/);
if (fs.existsSync(path.join(root, 'src', 'internal-secret.js'))) {
  assert.throws(() => run({ file: 'src/internal-secret.js', maxWords: 1500 }, root), /secret/);
  assert.doesNotMatch(run({ find: 'TOKEN', maxWords: 1500 }, root), /TOKEN=secret/);
} else console.log('Symlink checks skipped: operating system did not allow fixture creation.');
assert.match(run({ find: 'parse_fish', path: '.', maxWords: 1500 }, root), /helpers.py/);
const notes = run({ notes: 'needlephrase', maxWords: 1500 }, root);
assert.match(notes, /docs\/agents\/retrieval-fixture\.md:\d+-\d+ \| Wrapped retrieval headline/);
assert.match(notes, /match \d+: The important NeedlePhrase/);
assert.doesNotMatch(notes, /Neighboring note/);
assert.doesNotMatch(notes, /INDEX\.md/);
const noteSlice = run({ file: 'docs/agents/retrieval-fixture.md', around: 4, lines: 2, maxWords: 1500 }, root);
assert.match(noteSlice, /3 \| The important NeedlePhrase/);
assert.match(noteSlice, /4 \| A second NeedlePhrase/);
const longExcerpt = run({ notes: 'longneedle', maxWords: 1500 }, root);
assert.match(longExcerpt, /LongNeedle/);
const lateExcerpt = run({ notes: 'lateneedle', maxWords: 1500 }, root);
const lateRange = lateExcerpt.match(/docs\/agents\/long-note\.md:(\d+)-(\d+) \|/);
assert.ok(lateRange, 'late note match reports a source range');
assert.ok(Number(lateRange[2]) - Number(lateRange[1]) + 1 <= 40, 'long note range stays within 40 lines');
assert.ok(Number(lateRange[1]) <= 131 && Number(lateRange[2]) >= 131, 'late note range contains the match');
assert.match(lateExcerpt, /LateNeedle/);
const sqlSummary = run({ find: 'NeedlePhrase', path: 'supabase/migrations', summary: true, maxWords: 1500 }, root);
assert.equal((sqlSummary.match(/^supabase\/migrations\/\d{3}_history\.sql:/gm) || []).length, 3, 'summary emits one row per matching file');
assert.match(sqlSummary, /004_history\.sql:.*lines 1,2,4/);
assert.match(sqlSummary, /Total matches: 5/);
assert.throws(() => parse(['--summary']), /requires --find/);
assert.throws(() => parse(['--summary', '--file', 'src/helpers.py']), /requires --find/);

const nextOffset = output => {
  const match = output.match(/Next offset: (\d+|none)/);
  assert.ok(match, 'paged output includes a next offset');
  return match[1] === 'none' ? null : Number(match[1]);
};
const exhaust = (options, recordRe, expectedTotal, budget = 100) => {
  const records = []; let offset = 0; const seenOffsets = new Set();
  for (;;) {
    assert.ok(!seenOffsets.has(offset), 'pagination must advance');
    seenOffsets.add(offset);
    const page = run({ ...options, offset, maxWords: budget }, root);
    assert.ok(page.trim().split(/\s+/).length <= budget, 'each page respects its word budget');
    assert.ok(page.length <= 16000, 'each page respects its character budget');
    records.push(...(page.match(recordRe) || []));
    const next = nextOffset(page);
    if (next === null) break;
    assert.ok(next > offset, 'next offset must advance');
    offset = next;
    assert.ok(seenOffsets.size < 100, 'pagination must remain bounded');
  }
  assert.equal(new Set(records).size, records.length, 'pagination has no duplicate records');
  assert.ok(seenOffsets.size > 1, 'fixture must actually require another page');
  assert.equal(records.length, expectedTotal, 'pagination returns every record');
  return records;
};
exhaust({ file: 'src/paged.js' }, /^\s+\d+ page\d+/gm, 24, 60);
exhaust({ find: 'NeedlePhrase', path: 'supabase/migrations' }, /^supabase\/migrations\/.*NeedlePhrase.*$/gm, 5, 60);
exhaust({ find: 'NeedlePhrase', path: 'supabase/migrations', summary: true }, /^supabase\/migrations\/\d{3}_history\.sql:.*$/gm, 3, 80);
exhaust({ notes: 'NeedlePhrase' }, /^docs\/agents\/retrieval-fixture\.md:\d+-\d+ .*$/gm, 4, 80);
assert.match(run({ notes: 'absent-value', maxWords: 100 }, root), /Entries: 0-0\/0\nNext offset: none/);
assert.throws(() => run({ notes: 'absent-value', offset: 1, maxWords: 100 }, root), /offset is outside/);
assert.throws(() => parse(['--notes', 'value', '--offset', '-1']), /non-negative/);
assert.throws(() => parse(['--file', 'src/paged.js', '--offset', '1', '--around', '1']), /cannot be used/);
assert.throws(() => parse(['--find']), /requires a value/);
assert.throws(() => parse(['--find', 'table', '--path']), /requires a value/);
assert.throws(() => parse(['--latest']), /requires --find/);
console.log('agent-context checks passed: bounded source map, SQL headings, literal search, refusal checks');
