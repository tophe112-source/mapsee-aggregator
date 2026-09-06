#!/usr/bin/env node
/**
 * A deliberately bounded context index for coding agents.
 * Best effort only: headings and declarations are recognised with small,
 * language-agnostic expressions, rather than a full parser.
 */
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const DEFAULT_WORDS = 1500;
const HARD_WORDS = 4000;
const DEFAULT_AROUND = 40;
const HARD_LINES = 120;
const HARD_CHARS = 16000;
const MAX_LINE_CHARS = 400;
const TOOL = path.basename(fileURLToPath(import.meta.url));
const generated = /(?:^|[\/])(?:dist|vendor|assets|node_modules|coverage|\.claude)(?:[\/]|$)|(?:^|[\/])public[\/]src[\/](?:local-play|manifest-data)\.js$/i;
const secret = /(?:^|[\/])(?:\.env(?:\..*)?|.*\.(?:pem|key|p12|pfx|crt|cer)|secrets?(?:\..*)?)(?:$|[\/])/i;
const lock = /(?:^|[\/])(?:package-lock\.json|npm-shrinkwrap\.json|yarn\.lock|pnpm-lock\.yaml|deno\.lock|bun\.lock)$/i;
const binaryExt = /\.(?:png|jpe?g|gif|webp|ico|bmp|avif|mp3|wav|ogg|mp4|mov|webm|woff2?|ttf|otf|zip|gz|pdf|sqlite|db|bin)$/i;
const sourceExt = /\.(?:[cm]?[jt]sx?|mjs|cjs|py|rb|go|rs|java|kt|swift|php|cs|css|scss|html?|vue|svelte|astro|sql|graphql|gql|sh|ps1)$/i;
const agentNote = /^docs\/agents\/(?!INDEX\.md$)[^/]+\.md$/i;

function usage() {
  return `Usage: node tools/${TOOL} --file <path> [--around <line>] [--lines <count>] [--max-words <n>]
       node tools/${TOOL} --find <literal> [--path <directory>] [--latest] [--summary]
       node tools/${TOOL} --notes <literal> [--max-words <n>]

Build a bounded, best-effort map from tracked source files. --file is required
for the default code map; --find searches literal text in tracked code and SQL
migrations. Known secret/generated paths are refused; this is not a secret scanner.
Output is intentionally capped. Read AGENTS.md, then its topic note, then use
this tool for a small source slice and run the relevant test named there.
--notes searches measured notes case-insensitively and returns source ranges,
headlines and matching excerpts. --summary returns one locator per matching file.
These are source excerpts, not semantic summaries or live database definitions.
Options: --around LINE --lines COUNT (default ${DEFAULT_AROUND}, hard max ${HARD_LINES})
         --max-words N (default ${DEFAULT_WORDS}, hard max ${HARD_WORDS})
         --path DIR limits --find to a repository directory; --latest reverses path order
         --offset N continues capped search/map results (not --around slices)
         Offsets require the same options and unchanged source files.
         Filename order does not prove which migrations are applied in the database.
         --help`;
}

function parse(argv) {
  const out = { lines: DEFAULT_AROUND, maxWords: DEFAULT_WORDS };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--help' || a === '-h') return { help: true };
    const next = () => { if (i + 1 >= argv.length || argv[i + 1].startsWith('--')) throw new Error(`${a} requires a value`); return argv[++i]; };
    if (a === '--file') out.file = next();
    else if (a === '--find') out.find = next();
    else if (a === '--notes') out.notes = next();
    else if (a === '--summary') out.summary = true;
    else if (a === '--offset') out.offset = Number(next());
    else if (a === '--around') out.around = Number(next());
    else if (a === '--lines') out.lines = Number(next());
    else if (a === '--max-words') out.maxWords = Number(next());
    else if (a === '--path') out.path = next();
    else if (a === '--latest') out.latest = true;
    else throw new Error(`unknown option: ${a}`);
  }
  if (out.summary && out.find === undefined) throw new Error('--summary requires --find');
  if (out.path !== undefined && out.find === undefined) throw new Error('--path requires --find');
  if (out.latest && out.find === undefined) throw new Error('--latest requires --find');
  if ([out.file, out.find, out.notes].filter(x => x !== undefined).length !== 1) throw new Error('choose exactly one of --file, --find or --notes');
  if (out.find !== undefined && out.find.length === 0) throw new Error('--find requires a non-empty literal');
  if (out.notes !== undefined && !out.notes.trim()) throw new Error('--notes requires a non-empty literal');
  if (out.offset !== undefined && (!Number.isSafeInteger(out.offset) || out.offset < 0)) throw new Error('--offset must be a non-negative safe integer');
  if (out.offset !== undefined && out.around !== undefined) throw new Error('--offset cannot be used with --around');
  if (out.around !== undefined && !out.file) throw new Error('--around requires --file');
  if (out.around !== undefined && (!Number.isInteger(out.around) || out.around < 1)) throw new Error('--around must be a positive line number');
  if (!Number.isInteger(out.lines) || out.lines < 1 || out.lines > HARD_LINES) throw new Error(`--lines must be 1-${HARD_LINES}`);
  if (!Number.isInteger(out.maxWords) || out.maxWords < 50 || out.maxWords > HARD_WORDS) throw new Error(`--max-words must be 50-${HARD_WORDS}`);
  return out;
}

function safeRel(root, requested) {
  if (!requested || typeof requested !== 'string' || path.isAbsolute(requested)) throw new Error('path must be a relative repository path');
  const rel = path.normalize(requested).replaceAll('\\', '/');
  if (rel === '..' || rel.startsWith('../') || rel.includes('/../') || secret.test(rel)) throw new Error('refusing outside or secret path');
  return rel === '.' ? '' : rel;
}

function tracked(root) {
  const r = spawnSync('git', ['-C', root, 'ls-files', '-z'], { encoding: 'utf8' });
  if (r.status !== 0) throw new Error(`git ls-files failed: ${r.stderr?.trim() || 'not a git repository'}`);
  return [...new Set(r.stdout.split('\0').filter(Boolean))].filter(p => !generated.test(p) && !lock.test(p) && !secret.test(p) && !binaryExt.test(p) && (sourceExt.test(p) || agentNote.test(p)));
}

function identity(root) {
  const ask = args => spawnSync('git', ['-C', root, 'rev-parse', ...args], { encoding: 'utf8' });
  const r = ask(['--show-toplevel']);
  const branch = ask(['--abbrev-ref', 'HEAD']);
  const head = ask(['--short', 'HEAD']);
  if ([r, branch, head].some(x => x.status !== 0)) throw new Error(`git identity failed: ${r.stderr?.trim() || 'not a git repository'}`);
  return { repo: path.resolve(r.stdout.trim() || root), branch: branch.stdout.trim() || '(detached)', head: head.stdout.trim() || '(unknown)' };
}

function readable(root, rel) {
  const abs = path.resolve(root, rel);
  const rootAbs = path.resolve(root) + path.sep;
  if (!abs.startsWith(rootAbs)) throw new Error('refusing outside path');
  const st = fs.lstatSync(abs);
  if (!st.isFile() && !st.isSymbolicLink()) throw new Error('requested path is not a file');
  const real = fs.realpathSync(abs);
  if (real !== root && !real.startsWith(rootAbs)) throw new Error('refusing symlink escaping repository');
  const realRel = path.relative(root, real).replaceAll('\\', '/');
  if (secret.test(rel) || secret.test(realRel) || generated.test(rel) || generated.test(realRel) || lock.test(rel) || lock.test(realRel) || binaryExt.test(rel) || binaryExt.test(realRel)) throw new Error('refusing generated, binary, lockfile, or secret path');
  const data = fs.readFileSync(real);
  if (data.subarray(0, Math.min(data.length, 8192)).includes(0)) throw new Error('refusing binary content');
  return data.toString('utf8');
}

function entries(text, rel) {
  if (agentNote.test(rel)) return noteBlocks(text).map(b => ({ line: b.start, name: b.title }));
  const sql = rel.toLowerCase().endsWith('.sql');
  const out = [];
  const patterns = sql
    ? [/^\s*(?:--\s*)?migration\s+(.+)$/i, /^\s*(CREATE|ALTER)\s+(?:OR\s+REPLACE\s+)?((?:UNIQUE\s+)?INDEX|(?:MATERIALIZED\s+)?VIEW|FUNCTION|TABLE|TRIGGER|TYPE)\b(?:\s+IF\s+NOT\s+EXISTS)?\s+([\w."-]+)/i]
    : [/^\s*(?:\/\/|#|<!--|\*)\s*-{3,}\s*(.+?)\s*-{3,}\s*(?:-->)*\s*$/,
      /^\s*(?:export\s+)?(?:async\s+)?function\s+([\w$]+)/,
      /^\s*(?:export\s+)?class\s+([\w$]+)/,
      /^\s*(?:export\s+)?(?:const|let|var)\s+([\w$]+)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>/,
      /^\s*(?:async\s+)?def\s+([\w_]+)/];
  text.split(/\r?\n/).forEach((line, index) => {
    for (const re of patterns) { const m = line.match(re); if (m) { out.push({ line: index + 1, name: sql && m[3] ? `${m[1].toUpperCase()} ${m[2].toUpperCase()} ${m[3]}` : (m[1] || m[2]).trim() }); break; } }
  });
  return out;
}

// Preserve original line ranges; do not interpret SQL or rewrite prose. Note
// headlines can wrap across lines, as many of the measured notes already do.
function noteBlocks(text) {
  const lines = text.split(/\r?\n/);
  const starts = lines.flatMap((line, i) => /^(?:#{1,6}\s|\- \*\*)/.test(line) ? [i] : []);
  if (!starts.length) starts.push(0);
  return starts.map((start, i) => {
    const end = starts[i + 1] ?? lines.length;
    const body = lines.slice(start, end);
    const title = body[0].startsWith('- **')
      ? (body.join(' ').match(/^- \*\*([\s\S]*?)\*\*/)?.[1] || body[0])
      : body[0].replace(/^#+\s*/, '');
    return { start: start + 1, end, title: title.replace(/\s+/g, ' ').slice(0, 180), body };
  });
}

function matchExcerpt(line, needle, insensitive = false) {
  const value = line.trim();
  const at = (insensitive ? value.toLowerCase() : value).indexOf(needle);
  const start = Math.max(0, at - 40), end = start + 160;
  return `${start ? '…' : ''}${value.slice(start, end)}${end < value.length ? '…' : ''}`;
}

function cap(lines, maxWords, maxChars = HARD_CHARS) {
  let words = 0; const kept = [];
  let chars = 0; let clipped = false;
  for (const original of lines) {
    const line = original.length > MAX_LINE_CHARS ? `${original.slice(0, MAX_LINE_CHARS)} …` : original;
    if (line !== original) clipped = true;
    const n = line.trim() ? line.trim().split(/\s+/).length : 0;
    if (words + n > maxWords || chars + line.length + (kept.length ? 1 : 0) > maxChars) break;
    kept.push(line); words += n; chars += line.length + (kept.length > 1 ? 1 : 0);
  }
  const truncated = kept.length < lines.length;
  return { text: kept.join('\n'), truncated, clipped, words, keptLines: kept.length };
}

function pageReport(head, rows, total, options) {
  const offset = options.offset || 0;
  if (offset > rows.length) throw new Error('offset is outside results; source or options may have changed');
  // Reserve the four short footer lines, including their numeric fields, before
  // capping. Count emitted records, not requested records, so no page is skipped.
  const c = cap([...head, ...rows.slice(offset)], options.maxWords - 14, HARD_CHARS - 200);
  const shown = Math.max(0, c.keptLines - head.length);
  const end = offset + shown;
  const next = end < rows.length ? (shown ? end : 'raise-budget') : 'none';
  return [c.text, `Total matches: ${total}`, `Entries: ${shown ? offset + 1 : 0}-${end}/${rows.length}`,
    `Next offset: ${next}`, `Output truncated: ${c.truncated || c.clipped ? 'yes' : 'no'}.`].join('\n');
}

function boundedReport(body, footer, maxWords) {
  const footerLines = Array.isArray(footer) ? footer : [footer];
  const footerWords = footerLines.join('\n').trim().split(/\s+/).filter(Boolean).length;
  const room = Math.max(0, maxWords - footerWords);
  const c = cap(body, room, HARD_CHARS - footerLines.join('\n').length - 1);
  return { text: [...c.text ? [c.text] : [], ...footerLines].join('\n'), truncated: c.truncated || c.clipped, words: c.words + footerWords };
}

function run(options, injectedRoot) {
  const root = path.resolve(injectedRoot || path.dirname(path.dirname(fileURLToPath(import.meta.url))));
  const id = identity(root);
  let files = tracked(root);
  const lines = [];
  if (options.notes !== undefined) {
    const needle = options.notes.toLowerCase();
    let skipped = 0;
    for (const rel of files.filter(p => agentNote.test(p))) {
      let text; try { text = readable(root, rel); } catch { skipped++; continue; }
      for (const block of noteBlocks(text)) {
        const hit = block.body.findIndex(line => line.toLowerCase().includes(needle));
        if (hit < 0) continue;
        const excerpt = matchExcerpt(block.body[hit], needle, true);
        const detail = block.title.toLowerCase().includes(needle) ? `match ${block.start + hit}` : `match ${block.start + hit}: ${excerpt}`;
        const partial = block.end - block.start + 1 > HARD_LINES;
        const start = partial ? Math.max(block.start, block.start + hit - 20) : block.start;
        const end = partial ? Math.min(block.end, start + DEFAULT_AROUND - 1) : block.end;
        lines.push(`${rel}:${start}-${end} | ${block.title}${partial ? ' (section slice)' : ''} | ${detail}`);
      }
    }
    return pageReport([`Repository: ${id.repo}`, `Branch: ${id.branch}  HEAD: ${id.head}`,
      `Notes: ${JSON.stringify(options.notes)}; skipped: ${skipped}`, 'Excerpts only; open the source range before editing.'], lines, lines.length, options);
  }
  if (options.find !== undefined) {
    files = files.filter(p => sourceExt.test(p));
    if (options.path !== undefined) {
      const prefix = safeRel(root, options.path).replace(/\/$/, '');
      if (prefix) files = files.filter(file => file === prefix || file.startsWith(`${prefix}/`));
    }
    if (options.latest) files.reverse();
    let matches = 0; let skipped = 0;
    for (const rel of files) {
      let text; try { text = readable(root, rel); } catch { skipped += 1; continue; }
      const filenameHit = rel.includes(options.find);
      const hits = text.split(/\r?\n/).flatMap((line, i) => line.includes(options.find) ? [{ line: i + 1, text: line.trim() }] : []);
      matches += hits.length + Number(filenameHit);
      if (options.summary) {
        if (filenameHit || hits.length) lines.push(`${rel}:${hits[0]?.line || 1} | ${hits.length} matching lines${filenameHit ? '; filename match' : ''} | lines ${hits.slice(0, 3).map(h => h.line).join(',') || '-'} | ${hits.length ? matchExcerpt(hits[0].text, options.find) : ''}`);
      } else {
        if (filenameHit) lines.push(`${rel}: [filename match]`);
        lines.push(...hits.map(h => `${rel}:${h.line}: ${h.text}`));
      }
    }
    const head = [`Repository: ${id.repo}`, `Branch: ${id.branch}  HEAD: ${id.head}`, `Literal search: ${JSON.stringify(options.find)}`, `Files indexed: ${files.length}; skipped: ${skipped}`, options.summary ? 'File locators only; literal matches include comments, not proof of live schema.' : 'Search is literal; this is not complete dependency coverage.', ''];
    return pageReport(head, lines, matches, options);
  }
  const rel = safeRel(root, options.file);
  if (!files.includes(rel)) throw new Error('file is not a tracked, supported source path');
  const text = readable(root, rel); const source = text.split(/\r?\n/); const found = entries(text, rel);
  if (options.around !== undefined && options.around > source.length) throw new Error('around line is outside file');
  lines.unshift(`Repository: ${id.repo}`, `Branch: ${id.branch}  HEAD: ${id.head}`, `File: ${rel}`, `Lines: ${source.length}`);
  if (options.around !== undefined) {
    const start = Math.max(1, options.around - Math.floor(options.lines / 2)); const end = Math.min(source.length, start + options.lines - 1);
    lines.push(`Slice: lines ${start}-${end}`, ...source.slice(start - 1, end).map((l, i) => `${String(start + i).padStart(String(end).length, ' ')} | ${l}`));
  }
  if (options.around !== undefined) {
    lines.push(`Sections/declarations: ${found.length} recognised (details omitted to preserve requested slice)`);
  } else {
    lines.push('Sections/declarations (best effort):');
    return pageReport(lines, found.map(x => `  ${x.line} ${x.name}`), found.length, options);
  }
  lines.push('Coverage: tracked source index; dependencies and generated files are not expanded.');
  const preliminary = boundedReport(lines, [`Total matches: ${found.length}`, 'Output truncated: pending.'], options.maxWords);
  return preliminary.text.replace('Output truncated: pending.', `Output truncated: ${preliminary.truncated ? 'yes' : 'no'}.`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  try { const opts = parse(process.argv.slice(2)); if (opts.help) console.log(usage()); else console.log(run(opts)); }
  catch (error) { console.error(`agent-context: ${error.message}\n\n${usage()}`); process.exitCode = 2; }
}

export { parse, run, entries };
