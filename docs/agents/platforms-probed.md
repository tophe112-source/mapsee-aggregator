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
| LibCal (Springshare) | per-tenant only | `ical_subscribe.php` needs a numeric `cid` looked up by hand; the v1.1 API is per-institution OAuth. One `ics_sources.json` entry at a time, never a platform sweep. robots.txt is per tenant too: 7 of the 82 configured add `Disallow: /` to the `*` group, and were retired 2026-09-30 (letters in `../mapsee/outreach/partners/`) |
| **Communico** events-page iCal | **WORKS, per tenant** | The API is per-customer (401), but each tenant's events page has a Subscribe button serving `https://<tenant>.libnet.info/feeds?data=<base64 JSON>` as iCal with no key, and robots.txt allows `/feeds`. 74 tenants configured by 2026-09-28; all 38 in a 2026-09-27 sample answered the production UA with 80-500 rows. Some tenants also answer on `attend.<library>.org`, which is the SAME calendar (498-500 of 500 UIDs shared, 4 pairs): configure one. Online sessions sit in LOCATION's branch half, see `adapters-and-sources.md` |
| LibraryMarket / Library Calendar | 403 Cloudflare on the site; the iCal export answers; **robots.txt refuses it** | An interstitial is a refusal. Do not work around it. Separately, a tenant's `/events/feed/ical` answered the production UA with 184-500 rows for 10 of the 11 configured tenants on 2026-09-27 (the 11th broke off mid-transfer), with no challenge. But the platform's robots.txt, checked 2026-09-30 on all 11, ends in a second `User-agent: *` group of `Disallow: /` and then an allow-list of named search engines, so answering is not permission. Retired 2026-09-30 with Santa Fe, whose own domain serves the same file (12 of 12 byte-identical), into `jsonld_sources.json`'s `_not_included`; `verify` refuses a new one. Only the vendor can change a file every tenant shares, so the letter asking for MapseeAggregator on that allow-list goes to the vendor, with one for each library: `../mapsee/outreach/partners/library-calendar-permission.md`. Online sessions carry LOCATION `US`, see `geocoding-and-addresses.md` |
| Google Calendar public iCal (`calendar.google.com/calendar/ical/<id>/public/basic.ics`) | answers; **robots.txt refuses it** | calendar.google.com/robots.txt is `Allow: /$`, `Disallow: /` (2026-09-30), so every export under it is off limits to us, including the 30 configured before anyone checked. The embed a site shows (`www.google.com/calendar/embed?src=<id>`, the Google Sites Calendar block among them) names the calendar id. The route robots.txt allows is the Calendar API v3 on www.googleapis.com, which serves no robots.txt and needs an API key. `mapsee_gcal.py` takes that route whenever GOOGLE_CALENDAR_API_KEY is set (2026-09-30), for configured calendars and for embeds discovery finds. A secret `private-...` address is not public and stays unreadable; the 2 configured (Richmond Yacht Club, Edsvikens Tennisklubb) were retired 2026-09-30 |
| CivicPlus iCalendar | **supported, per category** | `civicplus_feeds()` in `catalog_discover_osm.py` reads the category list off `/iCalendar.aspx` - there is no whole-calendar export, and `catID=0` returns a valid VCALENDAR with zero events, which is the worst answer because it verifies. Separately: carync.gov 403s the production UA (a WAF), so a site-level refusal is still possible and the ledger records that one |
| **Austin Pool Schedule** (Socrata) | good data, wrong shape | 46 city pools, coordinates, status, website, and weekday/weekend hours as text. It is `recurring_hours`, and the opendata adapter needs start/end EVENT columns. Wants a small civic-facility-hours adapter reading a column map into `recurring_days`, the way `mapsee_ingest_osm_food` does from `opening_hours`. Deferred until a second city's dataset exists to shape it against: one dataset is not a schema |
| Delaware State Park Programs (Socrata) | dead archive | 4,077 rows and ONE in the future |

Recreation-booking platforms, where most North American municipal community
centres keep their drop-in timetables. None had been probed before; all measured
2026-10-03 over 3-17 tenants each, 3,537 requests at 1/s per host. A centre whose
site links one of these is filed `offsite:<host>` by the OSM walk
(`REC_BOOKING_HOSTS`), and `ledger --offsite` says which of them routes.

| Platform | Result | What it means |
|---|---|---|
| **PerfectMind / Xplor Recreation** (`<city>.perfectmind.com`, BookMe4 widgets) | **WORKS, adapter built** | `/robots.txt` 404 on all 19 tenant hosts; the nextRec terms bind subscribers and name no automated reading. The widget JSON needs the start page's anti-forgery token (not a login). `BookingType` 2 is a drop-in class, 3 a course, 4 a facility schedule: 146 of 600 calendars are type 2, and an "ALL programs" widget is 87-97% courses. `mapsee_ingest_perfectmind.py`, `docs/agents/community-centres.md` |
| **ActiveNet / ActiveCommunities** (`anc.apm.activecommunities.com/<org>`) | richest feed; **refused by its Terms of Use** | The public online calendars' JSON gave 38,286 dated drop-ins in 90 days at 112 centres over 4 tenants (Seattle, Portland, Vancouver, Mississauga), 3% over $20. robots.txt has no rule (it 301s to the marketing site), but ACTIVE's Terms of Use forbid any automated retrieval without prior written consent. Not read. Consent letters: `../mapsee/outreach/partners/activenet-permission.md`. The documented routes need a key and permission to store a day's results |
| Amilia / SmartRec | course catalogue | 8,358 of 10,906 expanded rows (77%) are paid courses over 9 Quebec tenants. API v3 needs a token (401); the legacy `/PublicApi/` answers without one. Quebec City's free-play programme (536 rows) is the only carve-out |
| CivicRec / Rec1 (`secure.rec1.com`) | course catalogue | 153 of 176 sampled sessions (87%) paid, season sessions not dated rows, 20 at "Location TBD". www.civicrec.com answers 403 |
| RecDesk | facility diary | 8,273 rows over 4 tenants: 2.6% clearly drop-in, 16% private bookings (union meetings, AA), no price, address or coordinates |
| MyRec | facility diary | 5,707 rows over 4 tenants: 7% drop-in, 29%+ "Booked"/"Reserved"/practices, no address |
| Vermont Systems WebTrac (`myvscloud.com`) | **robots.txt refuses** | `User-agent: *` `Disallow: /` after an allow-list of search engines |
| CommunityPass (`register.capturepoint.com`) | **robots.txt refuses** | `Disallow: /` |
| Daxko / GroupEx PRO | **robots.txt refuses** | `operations.daxko.com` `Disallow: /`; `groupexpro.com` disallows `/schedule/`, the embed path. YMCA classes are member-only anyway |
| FrontDesk Suite (Ottawa drop-in reservations) | **robots.txt refuses** | BOM + `Disallow: /`, which `robots_txt.parse` read as allow-all until 2026-10-04 |

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
