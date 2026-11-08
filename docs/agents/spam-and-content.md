# Spam, advertisements and the content gate

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: a feed full of adverts, the spam predicate and purge, implausible end dates, what makes a content-based delete safe.
> Every note below was measured before it was written; keep the numbers when you edit.

- **A FEED THAT WORKS PERFECTLY CAN STILL BE FULL OF ADVERTISEMENTS, and
  verification will never say so.** The row that started this was
  "Shatru Nashak Sudarshan Chakra Maran Mantra ☎ +91 9965500027", a black-magic
  service running from 2026 to 2036 and pinned to a street in Kirkland. It came
  through `gamenight.host`, an ordinary Mobilizon instance that answers every
  probe. Verification proves a feed WORKS; it has nothing to say about what a
  stranger posted to it, and every open-registration platform we read — 71
  Mobilizon instances, Gancio, Mobilizon's peers — is one spam account away from
  the same thing. Measured across all of them on 2026-09-03: **956 of 8,113
  upcoming listings, 11.8%, are not events as published.**
  So the defence is TWO layers and they do different jobs.
  `mapsee_spam.py` in `EventStore.upsert` stops the ROW (it is the only place all
  44 adapters pass through, and it runs BEFORE the dedupe — a scam listing pinned
  to a real venue on a real night shares three of the fingerprint's four parts,
  so judging it later would fold an advert INTO somebody's actual event).
  `mapsee_spam_audit.py` scores the SOURCE, because a calendar somebody spammed
  and a spam host with a calendar attached want opposite decisions.

- **A HIGH SPAM RATE IS NOT ENOUGH TO RETIRE A SOURCE; ZERO UNIQUE SUPPLY IS.**
  Four instances went into `_not_included` on 2026-09-03 and the two borderline
  calls are the instructive ones. `mobilize.adminforge.de` is 51% advertising and
  STAYED, because the other half is 92 real listings from Bad Oldesloe that
  arrive nowhere else — the per-row gate is exactly the tool for that.
  `meet.debian.net` is 80% advertising and looked like the same case, with 58
  real Brussels and French cultural listings behind the spam. They are real, and
  they are FEDERATED: of its 273 distinct titles, the 55 that are not spam
  already come in from other instances in the same file, and all 218 unique to it
  are the spam. So the question to ask is not "how much of this is spam" but
  "what would we lose", and on a federated network those are very different
  numbers. `mobilizon.us` was retired years earlier on the first question alone.

- **THE HALF OF A CONTENT FILTER THAT FAILS SILENTLY IS THE HALF THAT REFUSES
  SOMETHING REAL.** Spam getting through is reported by a user; a real listing
  refused is invisible, and the symptom is a source quietly getting thinner. Two
  false positives were found before this shipped and both are pinned in
  `test_spam.py`. The naive phone-number regex — `\d[\d\s.()-]{7,}\d` — reads
  "Summer Season 2026-05-16 - 2026-05-18" as a sixteen-digit number, because a
  space and a hyphen are both plausible separators; the rule wants an explicit
  dialling prefix or an unbroken ten-digit run, and the text is read AS WRITTEN
  because normalising the separators out is what creates the bug. And the
  keyword list is deliberately tiny and deliberately specific: "marabout" and
  "vashikaran" are on it, "astrologer", "psychic", "tarot", "healing" and
  "spiritual" are NOT, because a tarot night at a bar is an ordinary listing and
  this must not become a filter with opinions about what people are into.

- **AN IMPLAUSIBLE END DATE IS A FACT WE CANNOT USE, NOT A VERDICT ON THE ROW —
  and the audit is what proved it.** The first version rejected any span over
  400 days as spam. Run across 74 instances it immediately found Extinction
  Rebellion France publishing "Organiser un évènement à La Perm" over 487 days —
  real, one instance away from a 447-day advert for Roman blinds. No threshold
  separates those, because the span is not what makes one an advert. So
  `implausible_end()` drops the END and keeps the EVENT, and the good part
  follows for free: the sync fills a default duration for a row with no end
  (`_compute_end`), which is what lets `mapsee_cleanup.py` reach it at all — its
  filter is `starts_at < cutoff AND (ends_at IS NULL OR ends_at < cutoff)`, so an
  end in 2036 was a pin NOTHING in this pipeline could ever remove. The limousine
  and printer-driver adverts on that instance all start in 2021-2024, so removing
  an end nobody could have meant makes the EXISTING cleanup delete them.
  Same family as the British Cycling year-2500 sentinel below and the MyListing
  unbounded recurrence above; this is the third door into that one room.

- **A GATE FIXES THE FUTURE ONLY** — `--only-new` is the default in CI and
  Wednesday's full run re-reads the SOURCES, so neither re-applies our own rules
  to rows already stored. That is the same trap `mapsee_reclassify.py` exists
  for, and `mapsee_spam_purge.py` is its equivalent here: it imports the SAME
  predicate rather than restating it, walks the `(external_source, starts_at)`
  index a window at a time (a `description=ilike` over the table is a sequential
  scan and comes back 57014 every time), and needs `--apply` before it writes,
  because it deletes on a content judgement. **Its window starts FIVE YEARS
  back, not thirty days**, and that is not a rounding-up: the rows it most needs
  are the ones with a PAST start that cleanup could not touch, so a window
  beginning near today misses exactly the population it was written for.

- **WHAT MAKES A CONTENT-BASED DELETE SAFE TO SCHEDULE IS NOT THE SCOPE FILTER,
  IT IS THE SHARE CEILING.** Once its switch is on (next note), `spam-purge.yml`
  runs `--apply` nightly with nobody watching, and the failure to fear is not a
  spam wave — it is a rule in
  `mapsee_spam.py` widened until it matches ordinary listings, which from the
  outside looks exactly like a very effective run. `--max-share` (5% of rows
  read, ignored below a 200-row sample) refuses to write when the matched
  fraction stops being plausible, and exits non-zero so the job goes red. It is
  a SHARE and not a count because a count has to be re-tuned as the catalogue
  grows and is wrong in both directions while you guess; the invariant being
  expressed is "spam is a minority of what we import". `too_many()` is a free
  function precisely so `test_spam.py` can grade it without a database — a
  tripwire reachable only through a live PostgREST call is one nobody tests.
  The workflow also RUNS `test_spam.py` on the runner before the purge step, so
  a red rule stops the delete rather than merely being noticed somewhere else.

- **THE PURGE IS OFF UNTIL SOMEBODY HAS READ ITS REPORT, AND ITS SCHEDULE IS OFF
  WITH IT.** `spam-purge.yml` deletes only when the repository variable
  `SPAM_PURGE_APPLY` is exactly `true`; while it is anything else the 07:40
  schedule does not run at all and a manual run reports. A report is not free:
  the full read-only walk on 2026-09-14 (05:48-06:09Z) read **744,093**
  aggregator rows in **1,261 s**, so a daily report nobody reads would be a daily
  table scan in the ingest window, and the rescued `--max-seconds 1000` stopped
  short of the end every time (it is 1800 against `timeout-minutes: 35`). What
  that walk would have done: delete **256** rows, **0.03%** of rows read and far
  under the ceiling — and **29 of the 256 were real events**, not adverts: 26 on
  Meetup (beach-volleyball sessions with a WhatsApp number, a storytelling
  slam's "Contact no", two real-estate investor meetings) and 3 on OpenAgenda
  (apprenticeship days with a booking line). The rules were at fault, not the
  sources, and the next note narrows them: replayed through the narrowed rules,
  the same walk gives **227** deletes, 0.031% — gamenight.host 115, mobilizon.fr
  20, mobilizon.ethibox.fr 15, papafagla.com 11, then single-digit marabout
  pages on wordpress, simdif and blogspot — and still clears **48** end dates.
  Read the clears
  before switching it on: they include real listings (openagenda exhibitions of
  401-487 days, Repair Lab, a library's opening hours, weekly football and
  basketball sessions), and a cleared end on a row whose start has passed is
  deleted by the 08:23 cleanup, which the share ceiling does not bound. A second
  full walk started at 06:12Z beside another died on `Read timed out` from the
  database: run one at a time, and not in the ingest window. An earlier "9.89%,
  over the ceiling" was the walk as rescued, which read only 2,285 rows — a
  `+00:00` cursor sent unquoted, then a 500-row instant it could not page past.

- **A NUMBER IN THE TITLE IS THE ADVERT ON MOBILIZON AND AN RSVP LINE ON MEETUP,
  so the rule asks where the row came from.** On the full walk (744,093 rows,
  2026-09-14) the phone-in-title rule matched **232** rows: **205** adverts and
  **27** real events, 24 on Meetup and 3 on OpenAgenda, each with an
  organiser's contact number. The table stores no adapter, so where the 227
  adverts came from had to be shown: **153** link to a Mobilizon instance
  (gamenight.host 115, mobilizon.fr 20, mobilizon.ethibox.fr 15, mobilizon.us
  3), and no config of any other adapter names any of the 86 Mobilizon and
  Gancio hosts. The other **74** link to the advertiser's own page or to
  nothing; asking the instances' public API (200 read-only searches) found
  **56** of them (26 of 32 link groups) on gamenight.host, mobilizon.ethibox.fr
  or mobilizon.us — 53 publishing that page as the `onlineAddress` the Mobilizon
  adapter turns into the link, and the 3 with no link matched by title. None of
  the remaining 6 hosts is named by any config, and only 6 of their 18 rows
  depend on the number rule; the rest carry a listed phrase.
  So a number now counts only from Mobilizon and Gancio — and from an UNKNOWN
  source, because the purge infers the source from the link and 55
  number-caught adverts link nowhere else. The bulk-account trade is read from
  titles only: its description scan matched 2 rows on the whole table, both
  real investor meetings about retirement accounts, and no advert. Replayed
  offline over that walk's 256 flags (the next walk could not start before the
  ingest window): **227** remain, **0** of the 29 real events and **all 227**
  adverts. The price is at the door: a number-only advert arriving through
  Meetup, an ICS feed or any other named non-open source now passes the gate;
  none of the 205 linked to one. `test_spam.py` carries the 29 real titles.

- **CUTTING A VOLUNTEER'S NUMBER LEAVES THEIR NAME (repair-cafe.ch,
  2026-10-24).** Over 321 upcoming session pages, 26 lines of the cafés' own
  text held a phone number or e-mail address. In 3 of them, cutting the
  contact left only a private volunteer's name: "Nur auf Voranmeldung:
  <number> <name>", "<name>: <number>" and "<name>, <number> oder <number>". A
  label was also left with nothing after it ("Email:").
  mapsee_ingest_repaircafe_ch's clean_text now drops, from any line that held
  a contact, a two- or three-word capitalised name and a label left alone.
  Lines with no contact keep their names ("Repair Café Murten", "Jegliche
  Haftung"). 6 of 216 descriptions changed, and none still contains a number
  or an '@'. `python test_ingest_repaircafe_ch.py`.

- **MOBILIZON'S searchEvents(beginsOn: now) ANSWERS EVENTS THAT BEGAN YEARS
  AGO WITH NO END, AND THE TABLE INSERTED AND DELETED THEM EVERY DAY
  (2026-10-27).** The 10-26 civic store held 240 Mobilizon rows that began
  more than a day before the run and had no endsOn (238 on 10-21, the same
  set). Median age was 796 days, the oldest 6,835. Casino, betting and IPTV
  ads were among them. The sync has no past filter and gives a row with no end
  its start plus a typical length. mapsee_cleanup then deletes it once it is a
  week past, and the next --only-new civic sync inserts it again: about 240
  inserts and 240 cascading deletes a day. The adapter now counts and skips an
  event that began more than STALE_AFTER (1 day) ago with no endsOn, or with
  an endsOn already past. An event whose end is still ahead is kept, however
  old, unless the store drops that end (over 400 days after the start): as
  written, this rule never skipped a row (the 2026-11-08 note below). It also
  has the ics adapter's 365-day cap (HORIZON_DAYS, or a site's
  within_days): 522 of 4,938 rows on 10-26, 441 of them one weekly 'Offener
  Techniktreff' running to 2038. test_ingest_mobilizon.py.

- **THE 10-27 STALE RULE NEVER SKIPPED A ROW: MOBILIZON SENDS THOSE ROWS AN
  END, AND THE STORE DROPS IT (2026-11-08).** The 240 rows of the 10-27 note
  had no end in the STORE, not in the server's answer, and the rule read the
  raw `endsOn`. On 2026-11-08, 0 of the 1,320 rows on the 66 first pages
  (limit 20) had no `endsOn` or a past one, and the full run at 01:04 UTC
  logged 0 "already over" on all 66 sites. That run's store still held 269
  rows begun more than a day before with no end (32 with coordinates; median
  482 days old, the oldest 6,848). Each had arrived with an `endsOn` ahead
  more than `MAX_SPAN_DAYS` (400) after its start; `EventStore.upsert` drops
  such an end (`mapsee_spam.implausible_end`), so the row reached the sync
  endless, got its start plus a few hours (already past), and was inserted
  and deleted again every day, as the 10-27 note describes. The adapter's new
  `START_TIME_ASC` order brought more of them within reach: an ascending walk
  starts with whatever began before today and has not ended, 312 such rows on
  those 66 first pages. At limit 100, page 1 held 100 of 100 on
  event.portes-imaginaire.org (16 more on page 2; median length 490 days, 71
  lasting more than 365 days, the 400-day count not taken; one beginning in
  the year 25), 83 on mobilizon.fr (median 262 days, 17 over a year, Spanish
  SEO filler among them), 77 on agenda.collectifs.net, 64 on libretic and 6
  on mobilisons. 30 of the 269 (15 with coordinates) came from those five
  running heads, which the latest-first order never reached; among the 15 are
  two loan-offer adverts (one with a personal address in the title), two
  divorce-ritual adverts and two Korean SEO posts, and `mapsee_spam` refused
  none. The 32 placed rows were mostly adverts: exam dumps, casinos, "AK74
  RIFLES FOR SALE", loan offers with an e-mail address in the title, ritual
  sellers. A few were real weekly activities filed as one event spanning
  years (Saturday football in Nantes, a climbing club, a repair lab). Neither
  kind could be shown. So `stale_or_far` now treats an end the store will
  drop as no end (`end_dropped`). The full run at 02:54 UTC refused 402 rows
  as over on 24 of 66 sites (335 distinct events, 70 with coordinates; span
  median 1,009 days), all 402 for a dropped end and none for a missing one,
  and the store held 0 rows begun over a day before with no end. A running
  event whose end lies within 400 days of its start is kept as before (245
  that day). The site line reads "N already over (begun, no end ahead; M of
  them with an end ahead over 400 days after the start, which the store
  drops)", then "K kept begun over a day ago with an end ahead". A page that
  kept nothing, had nothing beyond the horizon and held a row already over
  does not count against `max_pages`, for at most `OVER_PAGES_MAX` (5) such
  pages a site: 0 such pages at 01:04, 1 at 02:54 (Social Org Mx, a one-row
  instance). `python test_ingest_mobilizon.py`. UNMEASURED: the table's
  insert and delete counts after deploy (the store is not the table). A
  weekly activity filed as one multi-year event would need a standing-row
  model to be shown (`openactive-and-standing-rows.md`); this change does not
  attempt one.

- **A MOBILIZON SPAM AUDIT SAMPLES THE COMING EVENTS FIRST FROM 2026-11-08,
  SO ITS RATES DO NOT COMPARE WITH EARLIER AUDITS.** `audit_site` in
  `mapsee_spam_audit.py` reads Mobilizon through the adapter's own
  `MOB.fetch_page`, which asks for `START_TIME_ASC`, asks the same page once
  more without the order when an instance refuses it (HTU Events, an older
  Mobilizon), and never asks twice after a 403 or 429. The old loop sent
  `MOB.QUERY` as is, and with the order in that query an instance that
  refused it audited 0 rows and reported the GraphQL error.
  Offline, with HTU Events' real error text: the old audit sent 1 POST and
  read 0; through `fetch_page`, 3 POSTs for 2 pages read 4 of 4, and a 403 is
  still read 0 after 1 POST. `test_ingest_mobilizon.py` pins it in three
  checks: a refusing instance reads 5 of 5 with its POSTs [sorted, unsorted,
  unsorted, unsorted], an accepting one is read sorted, and a 403 reads 0
  after 1 request; the old audit fails the first. So an audit from this date
  samples each instance's running head first. Page 1 of
  event.portes-imaginaire.org is 100 already-begun rows of 100, 71 of them
  lasting more than 365 days, and the audit scores a row over 400 days as
  "undated" unless a content rule refuses it first (that count was not
  taken). Earlier audits, the Mobilizon part of 2026-09-03's 11.8% among
  them, sampled latest start first, so their rates are not the same
  measurement. UNMEASURED: a live audit with the new order.

- **A WHOLE MOBILIZON INSTANCE CAN BE ADVERTISING, AND NEITHER verify NOR THE
  SPAM PREDICATE SEES IT (2026-11-08).** meetup.furryfederation.com is
  FediDB's busiest Mobilizon host that no config knew (status_count 40,669,
  39,660 users). It answered 42 upcoming events on 2026-11-08 and all 42 were
  adverts: Vietnamese betting sites (RR88, KO66, ABC8), a cocaine/MDMA
  listing, 7 "International Conference on ..." predatory-conference calls and
  SEO copy. `mapsee_spam.is_spam` refused 0 of 42, because none carries a
  phone number or a scam phrase. mobilizon.public.cat's 20 read were IPTV and
  e-bike adverts and its one future event an LED-driver advert; its decline
  records 149 answered to searchEvents(beginsOn: now), the first 100 being 13
  distinct title-and-start pairs of adverts and no event. Both passed
  `verify_mobilizon` live, because verify proves upcoming events exist and
  nothing else, and discovery's mirror and quiet gates do not catch an
  all-advert instance either. The configured events.rennes.tech carries the
  same e-bike adverts (9 events, 0 future). The defence is the editorial no:
  both hosts are in `mobilizon_sources.json`'s `_not_included` (11 entries),
  and it had to be there before the discovery code shipped, because Mobilizon
  discovery runs on the daily curate schedule and merge needs no human.
  `python test_discover_fedi.py` reads the real file and fails while either
  host is missing (123 cases, 0 failed). UNMEASURED: the spam share across
  the other 16 proposed Mobilizon and 125 Gancio candidates.
