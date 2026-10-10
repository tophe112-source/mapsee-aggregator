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
| Eventbrite metro discovery (`/d/<metro>/all-events/`, `/d/<metro>/free--events/`) | robots.txt allows; **HTTP 405 to GitHub's runners** | `mapsee_ingest_eventbrite.py` reads only event ids off these pages and hydrates each through the official API v3, which answers from anywhere. Discovery does not: run 105's extra-sources job (2026-10-01) logged `ny--new-york p1 HTTP 405 - discovery blocked from this IP` and imported 0. A 405 is a refusal; the adapter's own answer is a sweep from the owner's home connection (`eventbrite_scheduled.ps1`), so whether Eventbrite rows arrive depends on that machine, not on CI. The same `/d/wa--seattle/free--events/` page answered a cloud container 200 with 20 listings (2026-10-05), so the block is about GitHub's address ranges, not the User-Agent. `free--events` is how discovery would spend the 2,000-calls/hour token on free listings first; it is not wired, because only the home sweep could use it |
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
| **PerfectMind / Xplor Recreation** (`<city>.perfectmind.com`, BookMe4 widgets) | **WORKS, adapter built** | `/robots.txt` 404 on all 19 tenant hosts; the nextRec terms bind subscribers and name no automated reading. The widget JSON answered without the start page's anti-forgery token on all 15 tenants (0 of 2,230 requests needed it); the adapter reads the token only as a fallback. `BookingType` 2 is a drop-in class, 3 a course, 4 a facility schedule: 146 of 600 calendars are type 2, and an "ALL programs" widget is 87-97% courses. `mapsee_ingest_perfectmind.py`, `docs/agents/community-centres.md` |
| **ActiveNet / ActiveCommunities** (`anc.apm.activecommunities.com/<org>`) | richest feed; **refused by its Terms of Use**; holds Seattle-area Seattle, Shoreline, Auburn, Edmonds, Mill Creek, Marysville and Snohomish County, and DC-area Howard County, Takoma Park and McLean's courses (2026-10-04) | The public online calendars' JSON gave 38,286 dated drop-ins in 90 days at 112 centres over 4 tenants (Seattle, Portland, Vancouver, Mississauga), 3% over $20. robots.txt has no rule (it 301s to the marketing site), but ACTIVE's Terms of Use forbid any automated retrieval without prior written consent. Not read. Consent letters: `../mapsee/outreach/partners/activenet-permission.md`. The documented routes need a key and permission to store a day's results |
| Amilia / SmartRec | course catalogue | 8,358 of 10,906 expanded rows (77%) are paid courses over 9 Quebec tenants. API v3 needs a token (401); the legacy `/PublicApi/` answers without one. Quebec City's free-play programme (536 rows) is the only carve-out |
| CivicRec / Rec1 (`secure.rec1.com`) | course catalogue | 153 of 176 sampled sessions (87%) paid, season sessions not dated rows, 20 at "Location TBD". www.civicrec.com answers 403 |
| RecDesk | facility diary | 8,273 rows over 4 tenants: 2.6% clearly drop-in, 16% private bookings (union meetings, AA), no price, address or coordinates |
| MyRec | facility diary | 5,707 rows over 4 tenants: 7% drop-in, 29%+ "Booked"/"Reserved"/practices, no address |
| Vermont Systems WebTrac (`myvscloud.com`) | **robots.txt refuses** | `User-agent: *` `Disallow: /` after an allow-list of search engines; on the DC metro's 11 tenants (DC DPR, Arlington, Alexandria, Montgomery Rec, Prince George's Parks ...) `/robots.txt` answers 403 (2026-10-04). The biggest single blocker around Washington: letters in `../mapsee/outreach/partners/webtrac-permission.md` |
| CommunityPass (`register.capturepoint.com`) | **robots.txt refuses** | `Disallow: /` |
| Daxko / GroupEx PRO | **robots.txt refuses** | `operations.daxko.com` `Disallow: /`; `groupexpro.com` disallows `/schedule/`, the embed path. YMCA classes are member-only anyway |
| FrontDesk Suite (Ottawa drop-in reservations) | **robots.txt refuses** | BOM + `Disallow: /`, which `robots_txt.parse` read as allow-all until 2026-10-04 |
| **datos.madrid.es** (Ayuntamiento de Madrid CKAN) | **robots.txt refuses, parked** | `Disallow: /api/3/action/datastore_search` by name (2026-10-05), beside `/api/`, `/*?` and `/dataset/*/resource/*/download/*`, where the `/egob/catalogo/*.json` files redirect. The portal's OpenAPI spec lists no datastore call, so the CKAN exemption does not reach it. `mapsee_ingest_madrid.py` and its rules exist; the read waits for written permission in `madrid_sources.json.pending-permission`'s `permission` |

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

City open data, council what's-on sites and a community-club platform that the
community-centre survey of 2026-10-03 could not read, or should not. Production
User-Agent; nothing was retried with another. What each held is in
`community-centres.md`.

| Platform | Result | What it means |
|---|---|---|
| **ottrec** (`data.ottrec.ca/export/latest.json`, Ottawa) | **no open licence, not read** | An unofficial compilation of ottawa.ca's drop-in schedules: 25,396 occurrences in 90 days at 132 facilities with coordinates. robots.txt allows the export, but the data is "Compiled data (c) Patrick Gaskin" and "Facility information and schedules (c) City of Ottawa", so it needs the author's and the City's yes. ottawa.ca's own drop-in pools page served a bot challenge (a 212-byte interstitial) |
| **onePA** (`www.onepa.gov.sg`, People's Association, Singapore) | **terms forbid republishing; not built** | robots.txt allows `/events/*` (it disallows only `/search` and `/cart`), and the event sitemap lists 2,175 pages of server-rendered JSON. The terms of use say "no part(s) of this Website shall be reproduced, republished ... without prior permission", the same kind of clause that keeps ActiveNet out. The owner decides, or PA is asked. data.gov.sg publishes only the clubs' locations |
| OurAuckland (`ourauckland.aucklandcouncil.govt.nz/events/`) | **bot challenge on robots.txt** | Cloudflare "Just a moment..." with a 403 on `/robots.txt` itself, so permission cannot be established. Auckland Council says its community centres' programmes live there |
| Taiwan Ministry of Culture 藝文活動 (`cloud.culture.tw`) | **robots.txt refuses** | `Disallow: /` for `*`. One GET was made before the gate, which the survey disclosed: 4 of 8,106 future occurrences were at community-activity-centre venues and 8,010 were on sale, so it is not a community source anyway |
| Toronto Festivals and Events (CKAN `festivals-events`) | **robots.txt refuses; no API route** | The JSON is a download under `/dataset/*/resource/*/download/*`, which robots.txt disallows, and the resource is not `datastore_active`, so `datastore_search` cannot reach it. The drop-in tables are read through the datastore (`toronto_rec_sources.json`) |
| City and national open-data catalogues | **refused, not retried** | open.hamilton.ca's Hub search: 403 with a Cloudflare challenge. dados.cm-lisboa.pt: 403 with a challenge, and robots.txt `Disallow: /api/`. dati.comune.roma.it: robots.txt `Disallow: /` for the whole host. datos.gob.mx and datosabiertos.gob.ec: 403. Houston's Hub search API: 401. nycgovparks.org/bigapps: CloudFront 403 "Request blocked"; the same events are configured through Socrata `w3wp-dpdi` |
| Cardiff Hubs (`cardiffhubs.co.uk`) | **robots.txt refuses** | `Disallow: /` for `*` on the council's community-hub site |
