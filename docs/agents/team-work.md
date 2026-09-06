# Small tasks, small context

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map.

The lead owns scope, architecture, review and integration. Use a cheaper available
model for a bounded implementation or read-only investigation; use the lead for
cross-project contracts, migrations, security and ambiguous failures. Do not fan
out agents to explore the same files. Start another agent only when its work can
proceed independently. Stop parallelism when integration becomes the bottleneck.

## Before delegation

Verify the absolute checkout, branch, HEAD and dirty paths. Tools can inherit
another task's working directory: set workdir explicitly on EVERY command and use
git -C for the intended checkout. Sibling paths in AGENTS.md describe the normal
layout, not necessarily an isolated worktree.

Read AGENTS.md, then use `node tools/agent-context.mjs --notes <symptom>`
to find relevant note headlines, excerpts and source ranges. Open the matching
note with `--file docs/agents/<topic>.md --around <line>`. Use
`node tools/agent-context.mjs --file <source>` to map declarations,
`--file <source> --around <line> --lines 40` for code, or
`--find <literal> --path <directory>` to locate callers. Default output is
1,500 words with a 16,000-character hard cap; omissions are explicit. This is a
locator, not a parser or a substitute for inspecting the full affected function.
Use rg for exact imports, references and invariants that a partial map misses.
Never send entire large files or a whole migration directory to an implementer.

Add `--summary` to a literal search for one locator per matching file; this
keeps repeated comments in one migration from crowding out the other migrations.
Use the reported `Next offset` with the same options to retrieve subsequent
pages. Offsets count output entries, not source lines, and require unchanged
source files; restart after edits. `raise-budget` means no entry fitted.
An excerpt is a locator, not a replacement for the source. Do not squash SQL
history or replace measured notes with generated summaries to save context.

Give each agent this capsule (usually under 500 words):

- Outcome: one observable behavior and its acceptance criterion.
- Checkout: absolute path, branch, base HEAD, existing dirty paths to preserve.
- Ownership: exact editable paths/symbols; name shared files owned by the lead.
- Context: relevant note, source slices, callers and any generated-file boundary.
- Invariants: behavior that must survive, including failure and fallback paths.
- Validation: exact targeted commands and a failing reproduction when available.
- Return: changed paths, reason, test results, unresolved risks; concise, no file dump.

Do not require an implementer to rediscover context the lead already verified.
Agents do not commit, merge, deploy, apply migrations or send external messages
unless their capsule explicitly delegates those actions. Ask the lead about a
contract change; do not silently widen ownership. On a failed approach, return
the failing evidence before spending another broad investigation.

## Review and integration

- **A capped search must offer a next page, not hide the rest of the history.**
  At Mapsee commit `08adc2b2`, `events_near` appears 182 times in 46 of 226
  migration files. Default raw output fits 106 matches; `--summary` fits all
  46 file locators. Both expose a next offset when capped. `--notes` finds
  measured invariants by headline or body and links to numbered source slices;
  it omits the generated INDEX duplicate. The portable check walks capped
  pages to exhaustion and verifies that no result disappears or repeats.
  This reduces retrieval volume without changing, executing or squashing SQL.

The lead reads the actual diff against the stated base and checks the caller,
data contract, error path and tests. Green tests alone are insufficient: verify
the fixture reaches the bug and tests production code rather than a copy.
Reject incidental refactors. Extract a module only when it gives a stable,
testable boundary; moving thousands of lines merely to shrink a file creates a
larger review and more merge conflicts.

Run focused checks after review, then the repository's required integration
gates once. Stage only owned paths, recheck main for concurrent changes, and
follow this repository's release instructions. Preserve others' uncommitted work.
Migration source, applied database state and successful product behavior are
three separate claims; the lead verifies each before calling a rollout complete.

Record total effort, including review and retries. Context words/characters are
proxies, not billed tokens; report real usage only when available. Measure runtime
COGS with request/row/byte counts and production telemetry separately. A smaller
prompt or a synthetic million-row fixture does not prove million-user capacity.

## Ingest entry points

| Task | Start | Targeted check |
|---|---|---|
| Ownership / existing IDs | fetch_import_state in mapsee_supabase_sync.py | python test_sync_lookup.py |
| Unchanged writes | skip_unchanged in mapsee_supabase_sync.py | python test_skip_unchanged.py |
| A feed adapter | one mapsee_ingest_*.py and its test | matching test_ingest_*.py |
| Scheduling / costs | .github/workflows/aggregate-events.yml and topic notes | relevant workflow contract tests |

Agents own one adapter or one sync function at a time. The lead owns shared
normalization, SQL contracts and workflow integration. Tests must not require
production keys. Bound lookups to the current feed and preserve claimed edits.
The context locator uses Node without packages; production ingestion remains
Python and does not depend on this development tool.
