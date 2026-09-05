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
  41 adapters pass through, and it runs BEFORE the dedupe — a scam listing pinned
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
  IT IS THE SHARE CEILING.** `spam-purge.yml` runs `--apply` nightly with nobody
  watching, and the failure to fear is not a spam wave — it is a rule in
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
