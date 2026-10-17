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

- **BENAROYA'S OPEN HOUSE HAS ONE PARENT AND 23 TIMED AGENDA ITEMS.** On
  2026-10-17 the official page was retrieved with the production user agent
  at **14:04:35 UTC**, then began redirecting to Queue-it, whose robots.txt
  disallows crawling. The captured schedule excerpt is
  `fixtures/benaroya-open-house.html`; its one-time config is
  `festival_sources.benaroya-snapshot.json`, deliberately absent from the
  scheduled `festival_sources.json`. No waiting-room bypass or automatic
  refresh is claimed. `parse_benaroya_open_house` produced the October 18
  **10:00–18:30 America/Los_Angeles** parent and **23** timed entries across
  **6** rooms; start-only items have no invented ends, DJ windows split, and
  the 9:00/9:30 advance activities are noted outside the bounded agenda.
  Free headline tickets begin at **9:00**, in person, **2 per person** while
  supplies last, for the **16:00** Symphony/Head and the Heart performance.
  Other concert sale cards are excluded. Live sync wrote **1** parent and
  read back all **23** agenda entries and the exact parent window at
  `/e/e2f20d84-5672-4b33-b42a-425bafd8663c`; its database offer tag is `free`.


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

On 2026-09-09, scheduled run 34397782370 preserved all 24 Jackson agenda
items and tracked 72 discovery candidates, but two candidates exposed fetch
bugs. Explicit HTTP/HTML charsets now decode legacy festival pages without
assuming ISO-8859-1 for bare `text/html`; robots policies follow at most five
redirects with the existing public-address and resource-budget checks.
Live rechecks decoded Emmabodafestivalen (5,876 characters, two requests) and
Blame My Roots Festival (107,779 characters, five requests). Fetch success
does not imply a supported upcoming schedule. Tests cover UTF-8 and legacy
charsets, private-address redirects and destination robots refusals.
