#!/usr/bin/env python3
"""
test_curate_coverage.py — the coverage report is what AIMS the curation loop.

`catalog_curate.py coverage` is not a dashboard. Its "thin ground" block is the
list an autonomous run reads to pick targets, and `coverage --json` is what
curate-catalog.yml's gap sweep pins `--category` to. Every bug graded here was
silent in exactly the same way: the report printed a full, confident, 500-line
answer, and the twenty lines at the bottom that anything acts on were noise.

  * parkrun states `countries` as a {parkrun id: ISO} MAP because the adapter
    looks a feed row's code up in it. Iterating a dict yields its KEYS, so the
    report filed 20 sources under "97", "3", "85"... Twenty fake countries with
    one source each, against a "<= 2 sources is thin" rule and a 20-line cap:
    they filled EVERY ranked slot.
  * `default_country` is an ISO code too, so Germany was counted as "Germany"
    (27) plus "DE" (4), Canada as "Canada" (36) plus "CA" (1) — each half then
    reading as thinner than the country is.
  * a national adapter's placeholder metro ("(worldwide)", "(national)") is
    single-type by construction and for ever, so it can never be a target.
  * a thin CATEGORY could not appear in the ranked list at all — the three
    geographic tiers ahead of it always overflowed the cap — and the workflow's
    gap sweep pinned only to `zero_categories`, which has been empty for weeks.
    Between them, nothing in the loop has been aiming at a starved lens.

No network: every assertion runs off the committed configs and hand-built
fixtures.

Run: python test_curate_coverage.py
"""
import os
import sys

import catalog_curate as cc

fails = []


def check(label, cond, detail=""):
    if callable(cond):
        try:
            cond = cond()
        except Exception as exc:  # noqa: BLE001
            cond, detail = False, f"raised {type(exc).__name__}: {exc}"
    print(f"{'ok  ' if cond else 'FAIL'}  {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


# --- `countries` in either shape -------------------------------------------
check("a list of ISO codes reads as itself",
      cc._iso_countries(["us", "GB"]) == ["US", "GB"])
check("a {parkrun id: ISO} MAP reads its VALUES, not its ids",
      cc._iso_countries({"97": "GB", "98": "US"}) == ["GB", "US"])
check("empty and None are both no countries",
      cc._iso_countries(None) == [] and cc._iso_countries({}) == [])

parkrun = cc._rows_parkrun({"url": "https://images.parkrun.com/events.json",
                            "countries": {"97": "GB", "98": "US", "3": "AU"}})
got = sorted(r[2] for r in parkrun)
check("parkrun rows carry country NAMES, never parkrun's numeric ids",
      got == ["Australia", "United Kingdom", "United States"], got)

# --- the shipped configs, which is where it actually bit --------------------
rows = cc._coverage_rows()
numeric = sorted({c for _t, _n, _m, c, _k in rows if str(c).isdigit()})
check("no shipped config yields a numeric country", not numeric, numeric)
short = sorted({(c, t) for t, _n, _m, c, _k in rows
                if c and c != "?" and len(str(c)) <= 3})
check("no shipped config yields a bare ISO code beside its own country name",
      not short, short)

# --- a placeholder metro is not a target ------------------------------------
check("national adapters file themselves under a parenthesised placeholder",
      any(str(m).startswith("(") for _t, _n, m, _c, _k in rows))
check("...and no real metro name in the catalog looks like one",
      all(not str(m).startswith("(") or str(m).endswith(")")
          for _t, _n, m, _c, _k in rows))

# --- thin categories --------------------------------------------------------
# The live spread, as measured 2026-09-13. Median 81, so the floor is 40.5.
live = {"community": 1010, "market": 525, "arts": 170, "learning": 123,
        "fitness": 81, "outdoors": 26, "kids": 25, "running": 22, "volunteer": 10}
thin = cc.thin_categories(live)
check("the starved categories are named, thinnest first",
      thin == ["volunteer", "running", "kids", "outdoors"], thin)
check("a category at the median is not starved", "fitness" not in thin)
check("an EVEN catalog reports nothing, so the rule goes quiet on its own",
      cc.thin_categories({"a": 100, "b": 100, "c": 90, "d": 110}) == [])
check("no categories at all is not a crash",
      cc.thin_categories({}) == [] and cc.thin_categories(None) == [])

snap = cc.coverage_snapshot()
check("coverage --json publishes thin_categories for the gap sweep",
      isinstance(snap.get("thin_categories"), list))
check("...and it is a subset of the categories it counted",
      set(snap["thin_categories"]) <= set(snap["per_category"]))

# --- the workflow reads the fallback ----------------------------------------
wf = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       ".github", "workflows", "curate-catalog.yml"),
          encoding="utf-8").read()
check("the gap sweep falls back to thin_categories once nothing is at zero",
      "thin_categories" in wf and "zero_categories" in wf)

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
