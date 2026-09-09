# Festival discovery and internal schedules

> Part of mapsee-aggregator's agent notes. See `AGENTS.md` for the map.

- **One festival, 24 agenda items, 12 stages, 9 HTTP requests.** Measured on
  2026-09-09 against the Jackson Street organizer's current schedule PDF and
  Givebutter's public calendar button. Both specify 2026-09-12, 16:30–22:30 in
  America/Los_Angeles. Two cells print start times only (welcome and Pivot Dance);
  their grid end boundary is the next published slot on the same stage: 17:10
  and 18:30 respectively. Explicit boundaries prevent the existing calendar
  fallback from extending the 18:00 dance to 22:30 because another stage has a
  simultaneous start. The pin is the festival's
  Pratt welcome-stage anchor, OpenStreetMap way 228732858, not an assertion
  that all performances happen at that coordinate. Each agenda item names its
  actual stage. Festival/year IDs and per-item IDs survive time corrections.

`events.agenda` and `agenda_tz` already exist (Mapsee migrations 0176/0184).
The ingest model now preserves them; `agenda_rev` remains database-owned.
Omitted agendas leave existing schedules alone; an explicit `[]` clears one.
Mixed-shape sync batches are separated so omission never becomes null. The
normal sync's claimed-event ownership check still runs before any write.

## Running and verifying

```
pip install -r requirements-festivals.txt
python catalog_discover_festivals.py --limit 20
python mapsee_ingest_festivals.py --max-candidates 8 --max-seconds 240
python mapsee_supabase_sync.py --store festival_events.json --dry-run
python mapsee_supabase_sync.py --store festival_events.json --skip-unchanged
python verify_festival_sync.py
```

The last two commands need the same three existing secrets as the other jobs.
Never add `--only-new`: it freezes existing agendas. Read-back verifies the
actual stored agenda, timezone and parent window, not just a successful POST.

`.github/workflows/festival-schedules.yml` runs at 05:23 and 17:23 UTC and can
be dispatched manually. The extraction phase has a 240-second budget; discovery
has a 240-second MusicBrainz budget and processes at most 20 details, spaced 1.1 seconds
apart. Known verified candidates receive a separate allowance of 8 refreshes
before the rotating 8-candidate verification window. Registered sources run
first. The overall job has a 20-minute ceiling including setup and sync.

The job saves discovery cursors and review state through Actions cache with an
`always()` save and retains report/store/read-back artifacts for 30 days.
Cache eviction causes safe rediscovery, not different event identities.
Configured parser failures or discovery failures make the run red, after any
successful extractions have synced. Unsupported candidates appear in the job
summary and state artifact as pending; they are not silently marked imported.

## Coverage and improvement

MusicBrainz's worldwide Festival event search supplies nominations and official
homepage/schedule links. When that service is unavailable, an independent
Wikidata music-festival query supplies up to 20 official websites under a
45-second timeout, preserving both discovery cursors. The first production
MusicBrainz search timed out after 10 seconds; the independent Wikidata probe
returned 5 festival sites in 4 seconds on 2026-09-09. Neither is an exhaustive directory of neighborhood
festivals. Search date windows stay fixed while paging; current organizer
pages are the authority for schedules. No private API or paid search is needed.

Automatic acceptance currently requires an official JSON-LD Festival or
MusicFestival with precise parent coordinates, a resolved timezone, parent
start/end and 1–60 explicitly timed subEvents with stages. The source-specific
Jackson PDF parser additionally handles its verified 12-column layout.
Arbitrary image/PDF schedules and unstructured HTML remain pending.

To improve coverage, inspect `festival_schedule_state.json` in the latest
artifact, add a bounded parser or an approved source to `festival_sources.json`,
add a failing fixture, run the four festival tests and the existing CI gate,
then land it. The automation gathers evidence and refreshes schedules; it does
not modify its own code or promote uncertain OCR to production events.

Robots refusals, inaccessible pages, ambiguous dates/DST clocks, missing
stages, unexpected PDF layouts and more than 60 agenda items all defer the
source. A failed fetch never publishes an empty agenda. Schedule cancellation
status outside supported structured data requires review; this importer does
not infer cancellation from a disappeared page or erase production events.
