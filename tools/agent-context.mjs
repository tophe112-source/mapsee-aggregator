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

function usage() {
  return `Usage: node tools/${TOOL} --file <path> [--around <line>] [--lines <count>] [--max-words <n>]
       node tools/${TOOL} --find <literal> [--path <directory>] [--latest] [--max-words <n>]

Build a bounded, best-effort map from tracked source files. --file is required
for the default code map; --find searches literal text in tracked code and SQL
migrations. Known secret/generated paths are refused; this is not a secret scanner.
Output is intentionally capped. Read AGENTS.md, then its topic note, then use
this tool for a small source slice and run the relevant test named there.
Options: --around LINE --lines COUNT (default ${DEFAULT_AROUND}, hard max ${HARD_LINES})
         --max-words N (default ${DEFAULT_WORDS}, hard max ${HARD_WORDS})
         --path DIR limits --find to a repository directory; --latest reverses path order
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
    else if (a === '--around') out.around = Number(next());
    else if (a === '--lines') out.lines = Number(next());
    else if (a === '--max-words') out.maxWords = Number(next());
    else if (a === '--path') out.path = next();
    else if (a === '--latest') out.latest = true;
    else throw new Error(`unknown option: ${a}`);
  }
  if (out.file && out.find) throw new Error('--file and --find are mutually exclusive');
  if (out.path !== undefined && out.find === undefined) throw new Error('--path requires --find');
  if (out.latest && out.find === undefined) throw new Error('--latest requires --find');
  if (!out.file && out.find === undefined) throw new Error('--file is required (or use --find <literal>)');
  if (out.find !== undefined && out.find.length === 0) throw new Error('--find requires a non-empty literal');
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
  return r.stdout.split('\0').filter(Boolean).filter(p => !generated.test(p) && !lock.test(p) && !secret.test(p) && !binaryExt.test(p) && sourceExt.test(p));
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
  return { text: kept.join('\n'), truncated, clipped, words };
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
  if (options.find !== undefined) {
    if (options.path !== undefined) {
      const prefix = safeRel(root, options.path).replace(/\/$/, '');
      if (prefix) files = files.filter(file => file === prefix || file.startsWith(`${prefix}/`));
    }
    if (options.latest) files.reverse();
    let matches = 0; let skipped = 0;
    for (const rel of files) {
      let text; try { text = readable(root, rel); } catch { skipped += 1; continue; }
      if (rel.includes(options.find)) { matches += 1; lines.push(`${rel}: [filename match]`); }
      text.split(/\r?\n/).forEach((line, i) => { if (line.includes(options.find)) { matches += 1; lines.push(`${rel}:${i + 1}: ${line.trim()}`); } });
    }
    const head = [`Repository: ${id.repo}`, `Branch: ${id.branch}  HEAD: ${id.head}`, `Literal search: ${JSON.stringify(options.find)}`, `Files indexed: ${files.length}; skipped: ${skipped}`, 'Search is literal; this is not complete dependency coverage.', ''];
    const preliminary = boundedReport([...head, ...lines], [`Total matches: ${matches}`, 'Output truncated: pending.'], options.maxWords);
    const status = preliminary.truncated ? 'yes' : 'no';
    return `${preliminary.text.replace('Output truncated: pending.', `Output truncated: ${status}.`)}`;
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
    lines.push(...(found.length ? found.map(x => `  ${x.line} ${x.name}`) : ['  (none recognised)']));
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
