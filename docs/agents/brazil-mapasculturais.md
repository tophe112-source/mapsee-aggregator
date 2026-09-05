# Brazil: Mapas Culturais and what an empty country meant

> Part of mapsee-aggregator's agent notes — see `AGENTS.md` for the map. Read this file when the bug is about: an accepted filter that never ran, `0,0` coordinates, interpolated placeholders, `Etc/UTC`, measured negatives.
> Every note below was measured before it was written; keep the numbers when you edit.

- **A FILTER CAN BE ACCEPTED AND NEVER RUN, and the count it returns is then the
  WHOLE ARCHIVE.** Mapas Culturais (Brazil's state and municipal cultural
  registers, and the only reason this repo has anything in the country) takes
  `_startsOn=GTE(today)` on `/api/eventOccurrence/find`. Ceará applies it —
  16,587 occurrences, 329 returned. Espírito Santo ACCEPTS AND IGNORES it and
  returned 1,575: its entire history, of which ELEVEN were future and the oldest
  was dated **1911**. João Pessoa answered 34, of which zero were future. All
  three answer 200, and on a small instance an unfiltered archive is
  indistinguishable from a filtered result — which is how "1,575 upcoming
  events" became the headline number of the first audit and was wrong by two
  orders of magnitude. The tell is free: ask `@count=1` WITH the filter and
  again WITHOUT, and equal counts mean it did nothing. That check is an
  ACCELERATOR, never the guarantee — every row is re-checked client-side
  against `rule.startsOn`, because that is the only thing that cannot be
  silently wrong. Same family as BikeReg's search endpoint answering 200 with a
  hidden hundred-row ceiling, and as wisconsinbikefed's beautifully-formed iCal
  in which every event was past. `test_ingest_mapasculturais.py`.

- **AND THE DATE IS IN THE BLOB, NOT THE COLUMN.** `_startsOn`/`_startsAt` are
  real columns and they are NULL on whole instances — all 1,575 of Espírito
  Santo's, all 34 of João Pessoa's — while the `rule` JSON carries the value on
  every row of every instance seen. That is WHY the server filter can pass a
  2023 event: it tests an empty column. Two spellings of one fact, chosen only
  after finding the records where they disagree, which is the rule MyListing and
  SLU already paid for.

- **`{"latitude": "0", "longitude": "0"}` IS THE COMMONEST COORDINATE IN THAT
  SOURCE AND IT IS A STRING, so it passes every presence check.** 55 of the 345
  genuinely-future occurrences are at null island — a space whose registrant
  never dragged the pin. `"0"` is truthy, so an audit asking
  `if loc.get("latitude")` counts them all as placed: the first pass reported
  Ceará at 328 of 329 placeable when the truth is 281, and reported two
  instances as live supply when neither can draw a single row. Sergipe and Pará
  are declined in `_not_included` on the PLACEABLE count, not the event count —
  the Seattle Parks Foundation rule, which is that "returns future events" and
  "puts anything on the map" are different questions. It is the WP Event Manager
  `"-"` and this platform's own `undefined` in another costume: a falsy-LOOKING
  value that is truthy.

- **A PLATFORM CAN INTERPOLATE ITS OWN MISSING-VALUE PLACEHOLDER INTO AN
  ADDRESS.** `endereco` arrives as "Rua Dragão do Mar, , Praia de Iracema,
  Fortaleza, undefined, CE, 60060-390" — 167 of Ceará's 329 live rows — and
  `value` is the second one it writes. Both are truthy and would reach the row
  as street text. The address itself comes in TWO layouts on the same instance
  (UF last, and UF second-last with the CEP after it), so it is read outward
  from the UF token, whose shape is unmistakable, rather than by counting
  commas. Same discipline as MyListing's "Bainbridge Island, Washington 98110".

- **`timezoneName` SAID `Etc/UTC` ON ALL 1,938 OCCURRENCES AND IT IS NOT TRUE.**
  The times in `rule` are naive local clock times — a Fortaleza cinema session
  whose own `_startsOn` was serialised `America/Fortaleza` still reports
  `Etc/UTC` in the field named for the timezone. Read the field and a 19:40 show
  is served at 16:40. The adapter emits `start_local` and lets the sync turn the
  venue's coordinates into a zone, which is what WP Event Manager's naive stamps
  already needed; Brazil spans four zones, so a country-wide constant is not
  available either. A field named for a fact is not the fact.

- **BRAZIL WAS EMPTY BECAUSE NOTHING EVER ASKED.** Not a bug and not a dead
  feed: `metros_global.json` held 165 metros across 28 countries with **none in
  South America**, and no `*_sources.json` contained a single Brazilian entry —
  zero textual matches, zero coordinate pairs inside the country's bbox. So the
  international sweep never queried it, the OSM place adapters had no area
  there, and `discover osm` walks the same metro list, which means curation
  could not have found it either. The country is densely mapped: 90
  `amenity=marketplace` in the Rio box and 94 in Brasília's, comparable with the
  densest European cities already configured, plus 615 playgrounds, 355
  artworks, 268 toilets and 241 outdoor gyms in Rio alone. A gap that looks like
  a coverage failure can be a config file that was never given a row.

- **PARKRUN AND MOBILIZON ARE MEASURED NEGATIVES IN BRAZIL, written down so
  nobody re-derives them.** parkrun's live feed is 2,965 events across exactly
  20 country codes and Brazil is not one of them. `mobilizon.com.br` is the only
  Brazilian instance in the joinmobilizon directory and it holds ONE future
  event, carrying no coordinates. `dados.gov.br` answers 401 — the national CKAN
  needs a key. None of these is a bug to fix; all three cost a probe to
  re-check, which is why the numbers are in the configs.
