# Member landmarks: the Free group of mapsee's Things form, read as a feed

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: a landmark a member added that never reached the map, one that stayed on it after being withdrawn or reported, a landmark drawn with the wrong glyph or under the wrong Free filter, `landmarks_catalog_feed`, `user-landmark:<id>`.
> Every note below was measured before it was written; keep the numbers when you edit.

- **A MEMBER'S LANDMARK IS A LIST THE CATALOG READS, NOT A ROW ON THE MAP
  (2026-11-01).** `../mapsee` 0250 writes what a member posts under the Things
  form's Free group into `public.user_landmarks`, and nothing on the map reads
  that table. `mapsee_ingest_user_landmarks.py` is the only reader: it walks
  `landmarks_catalog_feed()` — a service_role-only RPC, keyset-paged on id,
  **500** rows a page, a short page ends the walk — and writes each live row as
  the row `mapsee_ingest_osm_amenities` writes for a fountain: `pin_only`,
  `coords_exact`, an all-week `recurring_days`, the type's own glyph in `icon`,
  external_id **`user-landmark:<id>`** (0250's `landmark_external_id()`, the one
  join between the two tables; the adapter refuses a feed row whose key differs
  from that recipe rather than writing under it). One source name,
  `user-landmark`, for every row: `mapsee_spam.source_family` must see a NAMED
  family or `EventStore.cancel` answers `untrusted` and a withdrawn landmark
  stays on the map for ever — `test_ingest_user_landmarks.py` pins the real
  store answering `cancelled`. The feed is read from mapsee's own database, so
  `user-landmarks.yml` costs seconds a day; it runs at **07:20**, after the
  06:17 import, and syncs with `--skip-unchanged` and never `--only-new`
  (the text, the photo and the glyph are the member's and change when they
  edit). The test is **83 cases**; **99** scripts are the gate now.

- **THE FEED SAYS WHAT TO CANCEL, AND THE ADAPTER CHECKS THE ARITHMETIC AGAIN.**
  An upsert cannot delete, so every row comes back with a state — `live`,
  `withdrawn`, `hidden`, `banned` — and the three that are not live become
  `store.cancel()` tombstones with the database's own reason (`withdrawn`,
  `consensus`, `banned`, `account_deleted`). The verdict is 0250's: at least
  **3** ⚠️ votes, ⚑ reports (0004) or poor spot ratings (0219: 1–2 ★ or
  `closed`) and more down than up hides a landmark; **2** such strikes in **90**
  days ban the member and read every landmark of theirs as `banned`; **3**
  distinct reporters on the catalog row hide it through 0004 as before, and the
  feed folds that in on its next read. `verdict()` re-applies the count rule to
  the `up`/`down` the feed returns, so a row the server called live with three
  down and no up is still cancelled here — a query is a request and the answer
  is somebody else's, the OSM adapters' rule. Unreadable counts never cancel a
  live row; an unknown state is never upserted. The spam predicate runs with the
  source UNKNOWN on purpose so the phone-in-title rule applies (a member source
  IS open registration whatever its family name says); a link or a phone number
  in a title and three links in a description are refused a second time after
  the posting RPC refused them, counted and logged, never written and never
  cancelled either.

- **THE GLYPH IS THE CONTRACT WITH THE PRODUCT'S FREE FILTER.** `../mapsee`'s
  `landmarkType(row)` maps a pin's `icon` back to one of the **14** keys in
  `site/js/place-categories.js` by emoji (variation selector stripped), so a
  glyph that drifts is a pin the Bathrooms chip can no longer turn off. `KINDS`
  carries the 14 keys and glyphs verbatim (🚰 🚻 🎨 📚 🐕 🛝 🏋️ 🔧 🥫 🎁 🧺 🌳
  🪑 📍), the test pins each one and that no two share a glyph, and when
  `../mapsee` is checked out beside this repo the test reads the live file and
  compares — the one cross-repo check here that runs without a network.
  Categories follow the OSM Kinds for the same thing (a toilet is `outdoors`, a
  playground `kids`, a food bank `volunteer`; `other` for the catch-all), so
  the row lands on a key a lens already opens onto. The attribution line is
  `Shared by a mapsee member. If it is not here, report it from this card.` and
  `parseImportedDesc` keys on its first five words (`member`): change a word
  here and the civic card credits OpenStreetMap for what a member added.
