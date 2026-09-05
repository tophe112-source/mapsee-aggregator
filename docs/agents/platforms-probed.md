# Platforms probed, and what they cost

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: you are about to spend a curation run on a platform somebody may already have measured.
> Every note below was measured before it was written; keep the numbers when you edit.

Read this before spending a curation run on any of them. `curation_ledger.json`
enforces it - `verify` skips a `fail` without a network call - but the ledger's
TTL is 90 days and several of these are permanent, so the reasoning lives here.
All probed 2026-09-03 with the production User-Agent.

| Platform | Result | What it means |
|---|---|---|
| **BiblioCommons** events gateway | **WORKS, adapter built** | `gateway.bibliocommons.com/v2/libraries/<slug>/events`, no key. Six systems configured; several others return 403 (see the config's `_not_included`) |
| **Trumba** | **WORKS, already configured** | `seattlegov-city-wide.ics` and `parks-recreation.ics` are live in `ics_sources.json`. The failure mode is GUESSING the slug: invented ones 410, and `.xml`/`.json` 404. Read the slug off the target site's own subscribe link |
| **Socrata** civic datasets | **WORKS, already configured** | `discover socrata` walks these. Note what it hunts is EVENTS - a facility-hours dataset (see Austin below) is invisible to it |
| LibCal (Springshare) | per-tenant only | `ical_subscribe.php` needs a numeric `cid` looked up by hand; the v1.1 API is per-institution OAuth. One `ics_sources.json` entry at a time, never a platform sweep |
| Communico | 401 | Per-customer API key. No public feed |
| LibraryMarket / Library Calendar | 403 Cloudflare | An interstitial is a refusal. Do not work around it |
| CivicPlus iCalendar | **supported, per category** | `civicplus_feeds()` in `catalog_discover_osm.py` reads the category list off `/iCalendar.aspx` - there is no whole-calendar export, and `catID=0` returns a valid VCALENDAR with zero events, which is the worst answer because it verifies. Separately: carync.gov 403s the production UA (a WAF), so a site-level refusal is still possible and the ledger records that one |
| **Austin Pool Schedule** (Socrata) | good data, wrong shape | 46 city pools, coordinates, status, website, and weekday/weekend hours as text. It is `recurring_hours`, and the opendata adapter needs start/end EVENT columns. Wants a small civic-facility-hours adapter reading a column map into `recurring_days`, the way `mapsee_ingest_osm_food` does from `opening_hours`. Deferred until a second city's dataset exists to shape it against: one dataset is not a schema |
| Delaware State Park Programs (Socrata) | dead archive | 4,077 rows and ONE in the future |

Two things that table is really saying:

- **A 403 IS A REFUSAL AND NOT AN OBSTACLE.** Four of the rows above are
  somebody declining. None was retried with a browser User-Agent and none should
  be - the DICE note above is the same rule, and the way into a 403 is to ASK the
  operator, which is what venue outreach is for.
- **`max(start_date)` IS NOT A LIVENESS TEST.** Delaware's dataset looked live
  because its furthest date was 2028; it has one row after today and 4,076
  before. The question is always the count AFTER now. Worth knowing:
  `catalog_curate verify` PASSED it, because its gate is "at least one future
  row" - which is the right gate for a small venue calendar and too weak for a
  4,000-row municipal archive. Not changed here, because re-cutting a shared
  threshold on one example is how you break the sources it was right for.
