#!/usr/bin/env python3
"""mapsee_dedupe_events: which rows are one event, which one survives, and what
the job sends to the database. No network: the rules are pure functions, and
the I/O half runs against a small in-memory PostgREST (FakeRest) that applies
the same filters the real one would, so a write that loses its scope or its
claimed_at guard reaches rows it must not.

Rows are the census's real rows (2026-10-13, ids shortened) plus the guards it
measured. One line per case; exits non-zero on any failure."""
import contextlib
import io
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qsl, unquote, urlsplit

import mapsee_dedupe_events as D

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}")


def row(i, title, start, end, lat, lon, made, link=None, **kw):
    r = {"id": i, "title": title, "starts_at": start, "ends_at": end, "lat": lat, "lon": lon,
         "created_at": made, "link": link, "claimed_at": None, "last_message_at": None, "promoted_until": None}
    r.update(kw)
    return r


NOW = datetime(2026, 10, 13, 16, tzinfo=timezone.utc)
NO_LINKS = {}


def decide(rows, titles_by_link=NO_LINKS, rsvp=()):
    by = {r["id"]: r for r in rows}
    edges = {}
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            arm = D.match(a, b, titles_by_link)
            if arm:
                edges[(a["id"], b["id"])] = arm
    return edges, D.plan(by, edges, set(rsvp), NOW)


print("the reported pair (Discover SLU renamed its event)")
slu = "discoverslu.com/events/halloween-pet-portraits-3-2"
old = row("5ec4afa8", "Halloween Pet Portraits", "2026-10-13T18:00:00+00:00", "2026-10-13T21:00:00+00:00",
          47.620128544072, -122.339691343269, "2026-09-10T12:10:52+00:00", slu)
new = row("36d74bf7", "Halloween Pet Portraits Fundraiser", "2026-10-13T18:00:00+00:00", "2026-10-13T21:00:00+00:00",
          47.620131471803, -122.339691742701, "2026-09-29T13:53:51+00:00", slu)
check("canonical link drops scheme, www and trailing slash",
      D.canonical_link("x\nTickets / info: https://www.discoverslu.com/events/halloween-pet-portraits-3-2/") == slu)
edges, p = decide([old, new])
check("same-page", edges == {("5ec4afa8", "36d74bf7"): "same-page"})
check("keeps the newest import (the source's current title)", p[0]["survivor"] == "36d74bf7")
check("hides the old title", p[0]["hide"] == [("5ec4afa8", "same-page")])

print("\nkinds the census measured")
cpl = "chipublib.bibliocommons.com/events/6a985ed760ccaf01c022dbce"
a = row("5f394f03", "Mindful Yoga", "2026-10-13T15:30:00+00:00", "2026-10-13T16:30:00+00:00", 41.903937, -87.636761, "2026-09-15T13:11:39+00:00", cpl)
b = row("a61187ae", "Mindful Yoga", "2026-10-13T15:30:00+00:00", "2026-10-13T16:30:00+00:00", 41.9041255, -87.6365797, "2026-10-10T12:45:22+00:00", cpl)
check("a0: one library event, two adapters' keys", D.match(a, b, NO_LINKS) == "same-page")
q = "meetup.com/lets-get-quizical-quiz-in-berlin/events/316812402"
a = row("d9635f21", "QUIZ #95.2 @ Bar Degendorff - 15th of October (Start: 7:30pm)", "2026-10-15T17:30:00+00:00", "2026-10-15T20:30:00+00:00", 52.519146, 13.45195, "2026-10-02T17:47:05+00:00", q)
b = row("6b5d49b3", "QUIZ #95.3 @ Bar Degendorff - 15th of October (Start: 7:30pm)", "2026-10-15T17:30:00+00:00", "2026-10-15T19:30:00+00:00", 52.519146, 13.45195, "2026-10-05T19:59:02+00:00", q)
check("a: a renumbered Meetup quiz on its own page", D.match(a, b, NO_LINKS) == "same-page")
eng = "chipublib.bibliocommons.com/events/6a76369d4523092f003589a3"
timed = row("5db05f80", "Learn English/Aprende Inglés", "2026-10-14T15:00:00+00:00", "2026-10-14T18:00:00+00:00", 41.7818915, -87.7035377, "2026-10-12T15:51:17+00:00", eng)
clockless = row("add9716c", "Learn English/Aprende Ingles", "2026-10-14T05:00:00+00:00", "2026-10-15T04:59:59+00:00", 41.7819595, -87.703537, "2026-10-13T14:34:44+00:00", eng)
edges, p = decide([timed, clockless])
check("all-day copy of a timed programme", list(edges.values()) == ["all-day-copy"])
check("the timed row survives although it is OLDER", p[0]["survivor"] == "5db05f80")
a = row("aaf319df", "The Outsiders - New York", "2026-10-15T17:00:00+00:00", "2026-10-15T20:00:00+00:00", 40.75860334138, -73.987222760124, "2026-10-05T20:59:21+00:00", "seatgeek.com/the-outsiders-1-tickets/broadway/2026-10-15-1-pm/18149378")
b = row("a95ffd2f", "The Outsiders", "2026-10-15T17:00:00+00:00", "2026-10-15T20:00:00+00:00", 40.75860334138, -73.987222760124, "2026-10-11T13:01:39+00:00", "ticketmaster.com/the-outsiders-new-york-new-york-10-15-2026/event/z1r9uzruzqgz17jboy")
check("c: SeatGeek and Ticketmaster, city suffix", D.match(a, b, NO_LINKS) == "same-slot")
a = row("039290bc", "Winnipeg Jets at Chicago Blackhawks", "2026-10-16T00:00:00+00:00", "2026-10-16T03:00:00+00:00", 41.881294800595, -87.674217790667, "2026-09-24T17:44:34+00:00", "seatgeek.com/chicago-blackhawks-tickets/x/nhl/18377190")
b = row("0abfdfc1", "Chicago Blackhawks vs. Winnipeg Jets", "2026-10-16T00:00:00+00:00", "2026-10-16T03:00:00+00:00", 41.88124412, -87.67427375, "2026-09-30T12:55:17+00:00", "ticketmaster.com/chicago-blackhawks-vs-winnipeg-jets-chicago-illinois-10-15-2026/event/040064f0903a77c4")
check("c?: home and away written both ways", D.match(a, b, NO_LINKS) == "same-slot")
a = row("b08615b7", "Maybe Happy Ending", "2026-10-15T23:00:00+00:00", "2026-10-16T02:00:00+00:00", 40.756326325793, -73.983454559164, "2026-10-11T13:01:39+00:00", "ticketmaster.com/maybe-happy-ending-new-york-new-york-10-15-2026/event/z1r9uzruzqgz17jbbp")
b = row("e0489ba9", "Maybe Happy Ending", "2026-10-15T23:00:00+00:00", "2026-10-16T02:00:00+00:00", 40.756326325793, -73.983454559164, "2026-10-11T13:01:39+00:00", "ticketmaster.com/event/z7r9jz1a7-jbu")
check("c1: Ticketmaster lists one performance twice (made in one read, two pages)", D.match(a, b, NO_LINKS) == "same-slot")
a = row("822476b6", "Halloween Bar Crawl [20% DISCOUNT PROMO CODE WITHIN]", "2026-10-24T18:00:00+00:00", "2026-10-24T22:00:00+00:00", 38.907192, -77.03687, "2026-10-07T15:22:23+00:00", "meetup.com/dcdinnerbuddies/events/316788434", category="party")
b = row("92725a42", "Halloween Bar Crawl [20% DISCOUNT PROMO CODE WITHIN]", "2026-10-24T18:00:00+00:00", "2026-10-24T21:00:00+00:00", 38.907192, -77.03687, "2026-10-10T14:28:23+00:00", "meetup.com/citysocial/events/316787576", category="fitness")
check("c1: one crawl in two Meetup groups; end and category differ", D.match(a, b, NO_LINKS) == "same-slot")
sg = "seatgeek.com/digable-planets-tickets/seattle-washington-neptune-theatre-wa-2026-10-20-8-pm/concert/18282801"
tm = row("548e7596", "Digable Planets", "2026-10-21T03:00:00+00:00", "2026-10-21T06:00:00+00:00", 47.66126297, -122.31405191, "2026-07-11T05:05:35+00:00", "ticketmaster.com/digable-planets-seattle-washington-10-20-2026/event/0f0064bde2c683dd")
s1 = row("c3cfe55c", "Digable Planets", "2026-10-21T03:00:00+00:00", "2026-10-21T06:00:00+00:00", 47.661217046479, -122.314182641848, "2026-09-07T18:45:35+00:00", sg)
s2 = row("d3df6659", "Digable Planets with Supreme La Rock", "2026-10-21T03:00:00+00:00", "2026-10-21T06:00:00+00:00", 47.661217046479, -122.314182641848, "2026-10-12T21:47:04+00:00", sg)
edges, p = decide([tm, s1, s2])
check("three rows, one show: one survivor, two hidden", len(p) == 1 and p[0]["survivor"] == "d3df6659" and len(p[0]["hide"]) == 2)
rs = "runsignup.com/race/wa/seattle/greenlake5kandwellnessfestival"
a = row("61798044", "2026 Greenlake 5K and Wellness Festival", "2026-10-24T15:30:00+00:00", "2026-10-24T18:30:00+00:00", 47.681136735505, -122.326798279627, "2026-08-10T08:11:53+00:00", rs)
b = row("4dd31a12", "2026 Green Lake 5K & Wellness Festival", "2026-10-24T15:30:00+00:00", "2026-10-24T18:30:00+00:00", 47.681136735505, -122.326798279627, "2026-10-07T13:33:05+00:00", rs)
check("a spelling change (Greenlake / Green Lake, and / &)", D.match(a, b, NO_LINKS) == "same-page")

print("\nfalse positives the census measured stay apart")
nyc = "data.cityofnewyork.us/d/tvpp-9vvx"
a = row("t1", "Tennis", "2026-10-13T19:00:00+00:00", "2026-10-13T22:00:00+00:00", 40.7, -73.9, "2026-10-01T06:30:00+00:00", nyc)
b = row("t2", "Tennis", "2026-10-13T19:00:00+00:00", "2026-10-13T22:00:00+00:00", 40.7, -73.9, "2026-10-01T06:30:00+00:00", nyc)
check("NYC Parks permits on one dataset link, made in one read", D.match(a, b, NO_LINKS) is None)
ig = "instagram.com/flopsaucemic"
a = row("fc5efca4", "Status Mic @ Secret Pour (in NYC)", "2026-10-13T23:00:00+00:00", "2026-10-14T02:00:00+00:00", 40.6937298, -73.9298052, "2026-08-29T09:57:24+00:00", ig)
b = row("dfef5d8a", "Flopsauce Mic @ Secret Pour (in NYC)", "2026-10-13T23:00:00+00:00", "2026-10-14T02:00:00+00:00", 40.6937298, -73.9298052, "2026-08-29T09:57:24+00:00", ig)
check("two mics sharing one Instagram link, made together", D.match(a, b, NO_LINKS) is None)
tor = "toronto.ca/data/parks/prd/facilities/complex/123"
a = row("p1", "Pickleball, ages 60+", "2026-10-14T14:00:00+00:00", "2026-10-14T16:00:00+00:00", 43.65, -79.38, "2026-09-01T07:00:00+00:00", tor)
b = row("p2", "Cards, ages 60+", "2026-10-14T14:00:00+00:00", "2026-10-14T16:00:00+00:00", 43.65, -79.38, "2026-09-01T07:00:00+00:00", tor)
check("two drop-ins on one centre's timetable page", D.match(a, b, NO_LINKS) is None)
a = row("l1", "Lane Swim Med Tue 17:30", "2026-10-13T16:30:00+00:00", "2026-10-13T17:30:00+00:00", 51.5, -0.1, "2026-09-01T07:00:00+00:00", "book.everyoneactive.com/session/1")
b = row("l2", "Lane Swim Slow Tue 17:30", "2026-10-13T16:30:00+00:00", "2026-10-13T17:30:00+00:00", 51.5, -0.1, "2026-09-20T07:00:00+00:00", "book.everyoneactive.com/session/2")
check("two lanes of one swim (med / slow)", D.match(a, b, NO_LINKS) is None)
a = row("s1", "Starbucks", "2026-10-14T13:00:00+00:00", "2026-10-15T01:00:00+00:00", 48.8566, 2.3522, "2026-09-01T07:00:00+00:00", "starbucks.fr/store")
b = row("s2", "Starbucks", "2026-10-14T13:00:00+00:00", "2026-10-15T01:00:00+00:00", 48.8590, 2.3522, "2026-09-20T07:00:00+00:00", "starbucks.fr/store")
check("two branches 267 m apart", D.match(a, b, NO_LINKS) is None)
a = row("e1", "EarlyON family drop-in", "2026-10-14T13:30:00+00:00", "2026-10-14T15:30:00+00:00", 43.6500, -79.3800, "2026-09-01T07:00:00+00:00", "toronto.ca/earlyon/1")
b = row("e2", "EarlyON family drop-in", "2026-10-14T13:30:00+00:00", "2026-10-14T15:30:00+00:00", 43.6536, -79.3800, "2026-09-20T07:00:00+00:00", "toronto.ca/earlyon/2")
check("one programme at two centres 400 m apart", D.match(a, b, NO_LINKS) is None)
rep = "seattlerep.org/plays/freak-the-mighty"
a = row("m1", "Freak the Mighty", "2026-10-17T21:00:00+00:00", "2026-10-17T23:30:00+00:00", 47.62, -122.35, "2026-09-01T07:00:00+00:00", rep)
b = row("m2", "Freak the Mighty", "2026-10-18T02:30:00+00:00", "2026-10-18T05:00:00+00:00", 47.62, -122.35, "2026-09-20T07:00:00+00:00", rep)
check("matinee and evening on one show page", D.match(a, b, NO_LINKS) is None)
a = row("w1", "Trivia Night", "2026-10-14T02:00:00+00:00", "2026-10-14T04:00:00+00:00", 47.6, -122.3, "2026-09-01T07:00:00+00:00", "bar.example/trivia")
b = row("w2", "Trivia Night", "2026-10-21T02:00:00+00:00", "2026-10-21T04:00:00+00:00", 47.6, -122.3, "2026-09-20T07:00:00+00:00", "bar.example/trivia")
check("weekly occurrences of one series", D.match(a, b, NO_LINKS) is None)
a = row("h1", "Adult Shinny (18+)", "2026-10-14T23:00:00+00:00", "2026-10-15T00:30:00+00:00", 43.66, -79.4, "2026-09-01T07:00:00+00:00", "anc.ca.apm.activecommunities.com/x/1")
b = row("h2", "Adult Shinny (18+) [Goalies]", "2026-10-14T23:00:00+00:00", "2026-10-15T00:30:00+00:00", 43.66, -79.4, "2026-09-20T07:00:00+00:00", "anc.ca.apm.activecommunities.com/x/2")
check("a goalies session beside the skaters' session", D.match(a, b, NO_LINKS) is None)
a = row("k1", "Arts and Crafts", "2026-09-04T00:00:00+00:00", "2026-09-04T01:00:00+00:00", 37.77, -122.42, "2026-08-01T07:00:00+00:00")
b = row("k2", "Tot Arts and Crafts", "2026-09-04T00:00:00+00:00", "2026-09-04T01:00:00+00:00", 37.77, -122.42, "2026-08-20T07:00:00+00:00")
check("no link: a containing title is not enough (Tot Arts and Crafts)", D.match(a, b, NO_LINKS) is None)
a = row("v1", "Knox", "2026-10-14T00:00:00+00:00", "2026-10-14T03:00:00+00:00", 40.7, -73.99, "2026-09-01T07:00:00+00:00", "ticketmaster.com/knox/event/1")
b = row("v2", "VIP Bowling Lane Add On - Knox - Not a Concert Ticket", "2026-10-14T00:00:00+00:00", "2026-10-14T03:00:00+00:00", 40.7, -73.99, "2026-09-20T07:00:00+00:00", "ticketmaster.com/vip/event/2")
check("a ticket add-on never matches (and so never survives)", D.match(a, b, NO_LINKS) is None)
gen = "nycgovparks.org/events"
a = row("g1", "Lane Swim", "2026-10-19T05:30:00+00:00", "2026-10-19T06:30:00+00:00", 51.5, -0.1, "2026-09-01T07:00:00+00:00", gen)
b = row("g2", "Lane Swim", "2026-10-19T05:30:00+00:00", "2026-10-19T06:30:00+00:00", 51.5, -0.1, "2026-09-20T07:00:00+00:00", gen)
check("a link whose path is a listing (/events) is not an event's page", D.match(a, b, NO_LINKS) is None)
busy = {"x.org/p": {"tennis", "basketball", "soccer"}}
a = row("n1", "Tennis", "2026-10-13T19:00:00+00:00", "2026-10-13T21:00:00+00:00", 40.7, -73.9, "2026-09-01T07:00:00+00:00", "x.org/p")
b = row("n2", "Tennis", "2026-10-13T19:00:00+00:00", "2026-10-13T21:00:00+00:00", 40.7, -73.9, "2026-09-20T07:00:00+00:00", "x.org/p")
check("a link carrying three titles is a listing page", D.match(a, b, busy) is None)
exhibit = row("x1", "Dinosaur Hall", "2026-09-01T16:00:00+00:00", "2026-12-31T01:00:00+00:00", 47.66, -122.31, "2026-08-01T07:00:00+00:00", "museum.org/dino")
talk = row("x2", "Dinosaur Hall: curator talk", "2026-10-14T01:00:00+00:00", "2026-10-14T02:00:00+00:00", 47.66, -122.31, "2026-09-20T07:00:00+00:00", "museum.org/dino")
check("a talk inside a months-long exhibition is not an all-day copy", D.match(exhibit, talk, NO_LINKS) is None)

print("\nwho survives")
a = row("o1", "Open Mic", "2026-10-14T02:00:00+00:00", "2026-10-14T04:00:00+00:00", 47.6, -122.3, "2026-09-01T07:00:00+00:00", "m.com/x", claimed_at="2026-09-05T00:00:00+00:00")
b = row("o2", "Open Mic Night", "2026-10-14T02:00:00+00:00", "2026-10-14T04:00:00+00:00", 47.6, -122.3, "2026-09-20T07:00:00+00:00", "m.com/x")
_, p = decide([a, b])
check("a claimed row is kept, though older", p[0]["survivor"] == "o1" and p[0]["hide"] == [("o2", "same-page")])
a["claimed_at"] = None
_, p = decide([a, b], rsvp={"o1"})
check("a row somebody is going to is kept, though older", p[0]["survivor"] == "o1" and [h for h, _ in p[0]["hide"]] == ["o2"])
_, p = decide([a, b], rsvp={"o1", "o2"})
check("people on both rows: nothing hidden, both left for a person", p[0]["hide"] == [] and len(p[0]["left"]) == 1)
x = row("z1", "Fall Fundraiser Gala", "2026-10-20T01:00:00+00:00", "2026-10-20T04:00:00+00:00", 47.6, -122.3, "2026-09-01T07:00:00+00:00", "a.org/gala")
y = row("z2", "Fall Fundraiser Gala", "2026-10-20T01:00:00+00:00", "2026-10-20T04:00:00+00:00", 47.6, -122.3, "2026-09-10T07:00:00+00:00", "b.org/e/1")
z = row("z3", "Fundraiser Gala - Fall 2026 Edition", "2026-10-20T01:00:00+00:00", "2026-10-20T04:00:00+00:00", 47.6, -122.3, "2026-09-20T07:00:00+00:00", "a.org/gala")
edges, p = decide([x, y, z])
check("star, not chain: z3~z1 (same page), z1~z2 (equal title), z3 and z2 do not match",
      ("z2", "z3") not in edges and ("z1", "z3") in edges and ("z1", "z2") in edges)
check("the member with no direct match to the survivor is left, not hidden",
      p[0]["survivor"] == "z3" and [h for h, _ in p[0]["hide"]] == ["z1"] and p[0]["left"] == ["z2"])
many = [row(f"c{i}", "Karaoke", "2026-10-20T03:00:00+00:00", "2026-10-20T05:00:00+00:00", 47.6, -122.3, f"2026-09-{i+1:02d}T07:00:00+00:00", f"site{i}.com/k") for i in range(13)]
try:
    decide(many)
    check("thirteen rows in one cluster refuse", False)
except ValueError:
    check("thirteen rows in one cluster refuse", True)

print("\ncandidates (the grid the job pairs rows on before reading any link)")
pairs = D.candidate_pairs([old, new])
check("the reported pair is a candidate", pairs == [("36d74bf7", "5ec4afa8")])
far = row("far", "Halloween Pet Portraits", "2026-10-13T18:00:00+00:00", "2026-10-13T21:00:00+00:00",
          47.6216, -122.3397, "2026-09-10T12:10:52+00:00")         # ~165 m north
check("a row 165 m away is not", D.candidate_pairs([old, far]) == [])
north = [row("n1", "Fika", "2026-10-14T08:00:00+00:00", "2026-10-14T09:00:00+00:00", 78.2232, 15.6301, "2026-09-01T07:00:00+00:00"),
         row("n2", "Fika", "2026-10-14T08:00:00+00:00", "2026-10-14T09:00:00+00:00", 78.2232, 15.6359, "2026-09-02T07:00:00+00:00")]
cells = [int(r["lon"] // D.GRID_DEG) for r in north]
check(f"at 78.2 N a pair {D.metres(*north):.0f} m apart, {cells[1] - cells[0]} grid cells apart in longitude, is still found",
      D.candidate_pairs(north) == [("n1", "n2")] and D.metres(*north) < D.RADIUS_M and cells[1] - cells[0] >= 2)
other = row("o", "Halloween Pet Portraits", "2026-10-13T19:00:00+00:00", "2026-10-13T22:00:00+00:00",
            47.620128544072, -122.339691343269, "2026-09-10T12:10:52+00:00")
check("another start at the same place is not", D.candidate_pairs([old, other]) == [])
check("two carried rows are not paired again (their own window saw them)",
      D.candidate_pairs([], carry=[old, new]) == [])
check("the window order is -1, 0..20, then -2..-60 (the dearest and least productive last)",
      D.window_order(21, 60) == [-1] + list(range(21)) + list(range(-2, -61, -1)))


# --------------------------------------------------------------------------- #
# the I/O half: a small PostgREST
# --------------------------------------------------------------------------- #
class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


def _split(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        depth += ch == "("
        depth -= ch == ")"
        cur += ch
    return out + ([cur] if cur else [])


def _val(col, v):
    if v is None:
        return None
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")) if col.endswith("_at") else str(v)


def _test(r, col, opv):
    v = r.get(col)
    if opv == "is.null":
        return v is None
    if opv == "not.is.null":
        return v is not None
    if opv == "not.is.true":
        return v is not True
    if opv == "is.false":
        return v is False
    op, val = opv.split(".", 1)
    if op == "in":
        return v in val[1:-1].split(",")
    if v is None:
        return False
    a, b = _val(col, v), _val(col, val)
    return {"eq": a == b, "gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]


def _cond(r, expr):
    if expr.startswith("or("):
        return any(_cond(r, e) for e in _split(expr[3:-1]))
    if expr.startswith("and("):
        return all(_cond(r, e) for e in _split(expr[4:-1]))
    col, opv = expr.split(".", 1)
    return _test(r, col, opv)


class FakeRest:
    """events and event_rsvps, with the filters the job uses. `hook(method,
    url, n)` may mutate the tables before a call is answered, or return a
    Resp to answer it instead."""

    def __init__(self, events, rsvps=(), has_column=True, hook=None):
        self.events = {e["id"]: dict(e) for e in events}
        self.rsvps = [{"event_id": i} for i in rsvps]
        self.has_column = has_column
        self.hook = hook
        self.calls = []

    def _answer(self, method, url, body=None):
        self.calls.append((method, url, body))
        if self.hook:
            got = self.hook(method, url, len(self.calls))
            if got is not None:
                return got
        parts = urlsplit(url)
        q = parse_qsl(parts.query, keep_blank_values=True)
        table = parts.path.rsplit("/", 1)[-1]
        if "duplicate_of" in parts.query + json.dumps(body or {}) and not self.has_column:
            return Resp(400, {"code": "PGRST204" if body else "42703",
                              "message": "column events.duplicate_of does not exist"})
        rows = list(self.events.values()) if table == "events" else list(self.rsvps)
        meta = {}
        for k, v in q:
            if k in ("select", "order", "limit", "offset"):
                meta[k] = v
                continue
            expr = v[1:-1] if k == "and" else None
            rows = [r for r in rows if (all(_cond(r, e) for e in _split(expr)) if expr is not None
                                        else _test(r, k, v))]
        if method == "GET":
            for key in reversed((meta.get("order") or "").split(",")):
                if key:
                    col = key.split(".")[0]
                    rows.sort(key=lambda r: (_val(col, r.get(col)) is None, _val(col, r.get(col)) or ""))
            off = int(meta.get("offset", 0))
            rows = rows[off:off + int(meta.get("limit", 10 ** 9))]
            cols = (meta.get("select") or "").split(",")
            return Resp(200, [{c: r.get(c) for c in cols} for r in rows])
        for r in rows:
            r.update(body)
        return Resp(200, [{"id": r["id"]} for r in rows])

    def get(self, url, headers=None, timeout=None):
        return self._answer("GET", url)

    def patch(self, url, headers=None, data=None, timeout=None):
        self.prefer = (headers or {}).get("Prefer")
        return self._answer("PATCH", url, json.loads(data))


def ev(i, title, start, end, lat, lon, made, link=None, **kw):
    e = {"id": i, "title": title, "starts_at": start, "ends_at": end, "lat": lat, "lon": lon,
         "created_at": made, "claimed_at": None, "last_message_at": None, "promoted_until": None,
         "external_source": "mapsee", "hidden_at": None, "cancelled_at": None, "pin_only": False,
         "recurring_hours": None, "is_private": False, "duplicate_of": None,
         "description": f"Something.\nTickets / info: https://www.{link}/" if link else "Something."}
    e.update(kw)
    return e


SLU_OLD = "5ec4afa8-3c59-4826-8dfe-6e5aeef468fb"
SLU_NEW = "36d74bf7-1d76-4f88-8cf2-65b2c95c5b8b"


def slu_rows():
    return [ev(SLU_OLD, "Halloween Pet Portraits", "2026-10-13T18:00:00+00:00", "2026-10-13T21:00:00+00:00",
               47.620128544072, -122.339691343269, "2026-09-10T12:10:52+00:00", slu),
            ev(SLU_NEW, "Halloween Pet Portraits Fundraiser", "2026-10-13T18:00:00+00:00",
               "2026-10-13T21:00:00+00:00", 47.620131471803, -122.339691742701, "2026-09-29T13:53:51+00:00", slu),
            ev("99999999-0000-4000-8000-000000000001", "Trivia Night", "2026-10-14T02:00:00+00:00",
               "2026-10-14T04:00:00+00:00", 47.61, -122.33, "2026-09-01T07:00:00+00:00", "bar.example/trivia"),
            # a user's own event, at the same slot with the same title: never read, never written
            ev("99999999-0000-4000-8000-000000000002", "Halloween Pet Portraits", "2026-10-13T18:00:00+00:00",
               "2026-10-13T21:00:00+00:00", 47.620128544072, -122.339691343269, "2026-09-01T07:00:00+00:00",
               external_source=None)]


def run(fake, *args):
    os.environ.update(SUPABASE_URL="https://db.example", SUPABASE_SERVICE_ROLE_KEY="unused-test-key",
                      MAPSEE_TODAY="20261013")
    out = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "r.json")
        with contextlib.redirect_stdout(out):
            code = D.main(list(args) + ["--report", path], http=fake)
        report = json.load(open(path)) if os.path.exists(path) else {}
    return code, out.getvalue(), report


slept = []
D._sleep = slept.append
ticks = iter(range(10 ** 9))
D._clock = lambda: 0.0                               # unless a case moves time


def patches(fake):
    return [c for c in fake.calls if c[0] == "PATCH"]


print("\nreport mode")
fake = FakeRest(slu_rows())
code, out, rep = run(fake, "--days", "2", "--lookback", "1")
check("report mode sends 0 PATCH", patches(fake) == [] and code == 0)
check("it would hide the old title, naming the new one",
      [(h["id"], h["survivor"], h["arm"]) for h in rep["hidden"]] == [(SLU_OLD, SLU_NEW, "same-page")])
check("every request is scoped to imported rows (or reads event_rsvps by id)",
      all("external_source=eq.mapsee" in u or "/event_rsvps?" in u for _, u, _ in fake.calls))
check("the user's own event was never listed", "99999999-0000-4000-8000-000000000002" not in json.dumps(rep))
check("no listing sends offset= or count=",
      not any(("offset=" in u or "count=" in u) for _, u, _ in fake.calls if "/events?" in u))
check("the first listing starts at yesterday's midnight (MAPSEE_TODAY=20261013, window -1)",
      "starts_at.gte.2026-10-12T00%3A00%3A00.000000Z" in next(u for _, u, _ in fake.calls if "and=(" in u))

print("\napply")
fake = FakeRest(slu_rows())
code, out, rep = run(fake, "--apply", "--days", "2", "--lookback", "1")
w = patches(fake)
check("one PATCH", len(w) == 1 and code == 0)
url, body = w[0][1], w[0][2]
check("the PATCH carries the scope and both guards",
      all(s in url for s in ("external_source=eq.mapsee", "claimed_at=is.null", "hidden_at=is.null",
                             f"id=in.({SLU_OLD})")))
check("the body is exactly hidden_at and duplicate_of",
      set(body) == {"hidden_at", "duplicate_of"} and body["duplicate_of"] == SLU_NEW)
check("the stamp is the run's now (MAPSEE_TODAY at 12:00Z)", body["hidden_at"] == "2026-10-13T12:00:00+00:00")
check("the old row is hidden and names the survivor",
      fake.events[SLU_OLD]["hidden_at"] and fake.events[SLU_OLD]["duplicate_of"] == SLU_NEW)
check("the survivor and the user's event are untouched",
      fake.events[SLU_NEW]["hidden_at"] is None
      and fake.events["99999999-0000-4000-8000-000000000002"]["hidden_at"] is None)
check("return=representation, so the count is what the database did", fake.prefer == "return=representation")
code2, out2, rep2 = run(fake, "--apply", "--days", "2", "--lookback", "1")
check("a second run finds nothing (the copy is no longer listed)", patches(fake) == w and rep2["hidden"] == [])

print("\nthe database decides at write time")
claimed_since = FakeRest(slu_rows(), hook=lambda m, u, n: (
    fake_c.events[SLU_OLD].update(claimed_at="2026-10-13T11:00:00+00:00") if m == "PATCH" else None))
fake_c = claimed_since
code, out, rep = run(fake_c, "--apply", "--days", "2", "--lookback", "1")
check("a copy claimed after the listing is not hidden (claimed_at=is.null in the filter)",
      fake_c.events[SLU_OLD]["hidden_at"] is None and "skipped 1" in out)


def hide_survivor(m, u, n):
    if m == "GET" and "select=id&hidden_at=is.null" in u:
        gone.events[SLU_NEW]["hidden_at"] = "2026-10-13T11:00:00+00:00"


gone = FakeRest(slu_rows(), hook=hide_survivor)
code, out, rep = run(gone, "--apply", "--days", "2", "--lookback", "1")
check("a survivor hidden before the write: its cluster is not written",
      patches(gone) == [] and gone.events[SLU_OLD]["hidden_at"] is None and "survivor gone 1" in out)
seen_rsvp = []


def late_rsvp(m, u, n):
    if "/event_rsvps?" in u:
        seen_rsvp.append(u)
        if len(seen_rsvp) == 2:                    # the re-read just before the write
            late.rsvps.append({"event_id": SLU_OLD})


late = FakeRest(slu_rows(), hook=late_rsvp)
code, out, rep = run(late, "--apply", "--days", "2", "--lookback", "1")
check("an RSVP arriving before the write: that row is skipped",
      patches(late) == [] and late.events[SLU_OLD]["hidden_at"] is None and "late RSVP 1" in out)
engaged = FakeRest(slu_rows(), rsvps=[SLU_OLD])
code, out, rep = run(engaged, "--days", "2", "--lookback", "1")
check("a row people are on survives, though older", [h["id"] for h in rep["hidden"]] == [SLU_NEW])

print("\nthe column, and refusals")
nocol = FakeRest(slu_rows(), has_column=False)
code, out, rep = run(nocol, "--apply", "--days", "2", "--lookback", "1")
check("a missing column downgrades --apply to a report and exits 1",
      patches(nocol) == [] and code == 1 and "duplicate_of is missing" in out and rep["mode"] == "report")
code, out, rep = run(FakeRest(slu_rows(), has_column=False), "--days", "2", "--lookback", "1")
check("without --apply a missing column is not a failure", code == 0 and len(rep["hidden"]) == 1)
crowd = [ev(f"aaaaaaaa-0000-4000-8000-{i:012d}", f"Show {i}", "2026-10-13T20:00:00+00:00", "2026-10-13T22:00:00+00:00",
            40.0 + i * 0.01, -74.0, "2026-09-01T07:00:00+00:00", f"tix.example/show-{i}") for i in range(30)]
crowd += [ev(f"bbbbbbbb-0000-4000-8000-{i:012d}", f"Show {i}", "2026-10-13T20:00:00+00:00", "2026-10-13T22:00:00+00:00",
             40.0 + i * 0.01, -74.0, "2026-09-20T07:00:00+00:00", f"tix.example/show-{i}") for i in range(30)]
share = FakeRest(crowd)
code, out, rep = run(share, "--apply", "--days", "1", "--lookback", "0")
check("30 hides in 60 rows (limit max(25, 15%)) refuse the window, exit 1",
      patches(share) == [] and code == 1 and "REFUSED" in out and rep["refused"])
code, out, rep = run(FakeRest(crowd), "--days", "1", "--lookback", "0", "--max-share", "0.6")
check("the same window under a deliberate --max-share 0.6 reports 30", code == 0 and len(rep["hidden"]) == 30)

print("\npaging, timeouts and the budget")
D.PAGE = 3
tie = [ev(f"cccccccc-0000-4000-8000-{i:012d}", f"Drop-in {i}", "2026-10-13T17:00:00+00:00" if i < 5 else
          "2026-10-13T19:00:00+00:00", "2026-10-13T23:00:00+00:00", 41.0 + i * 0.01, -87.0,
          "2026-09-01T07:00:00+00:00") for i in range(8)]
pager = FakeRest(tie + slu_rows())
code, out, rep = run(pager, "--days", "1", "--lookback", "0")
w0 = next(x for x in rep["windows"] if x["day"] == "2026-10-13")
upcoming = [u for m, u, _ in pager.calls if "and=(starts_at.gte.2026-10-13T12%3A00" in u or "or(starts_at.gt." in u]
check("ten rows on five tied starts list once each; from now on, 4 pages of 3",
      w0["rows"] == 10 and len(upcoming) == 4)
check("the second page asks starts_at.gte.<cursor> beside or(starts_at.gt, id.gt)",
      re.search(r"and=\(starts_at\.gte\.2026-10-13T17%3A00%3A00\.000000Z,or\(starts_at\.gt\.2026-10-13T17%3A00%3A00"
                r"\.000000Z,id\.gt\.cccccccc-0000-4000-8000-000000000002\),starts_at\.lt\.", upcoming[1]) is not None)
D.PAGE = 1000
started = [ev("ffffffff-0000-4000-8000-000000000001", "Still on", "2026-10-12T20:00:00+00:00", "2026-10-14T02:00:00+00:00",
              45.0, -73.0, "2026-09-01T07:00:00+00:00"),
           ev("ffffffff-0000-4000-8000-000000000002", "Over", "2026-10-12T05:00:00+00:00", "2026-10-12T06:00:00+00:00",
              45.1, -73.0, "2026-09-01T07:00:00+00:00")]


def span_hours(u):
    m = re.search(r"and=\(starts_at\.gte\.([^,]+),starts_at\.lt\.([^)]+)\)", unquote(u))
    if not m:
        return None
    a, b = (datetime.fromisoformat(x.replace("Z", "+00:00")) for x in m.groups())
    return (b - a).total_seconds() / 3600


def slow_slices(m, u, n):
    h = span_hours(u)
    if m == "GET" and h is not None and "2026-10-12" in unquote(u) and h > 1:
        return Resp(500, {"code": "57014", "message": "canceling statement due to statement timeout"})


del slept[:]
halving = FakeRest(started + slu_rows(), hook=slow_slices)
code, out, rep = run(halving, "--days", "1", "--lookback", "1")
w1 = next(x for x in rep["windows"] if x["day"] == "2026-10-12")
spans = [span_hours(u) for _, u, _ in halving.calls if span_hours(u) is not None and "2026-10-12" in unquote(u)]
check("a started day lists only its rows still running (one of two)", w1["rows"] == 1 and code == 0)
check("a statement timeout halves the started slice (6, 3, 1.5 h) instead of re-asking it",
      spans[:4] == [6.0, 3.0, 1.5, 0.75] and halving.calls and slept == [] and rep["owed_windows"] == [])


def always_slow(m, u, n):
    if m == "GET" and span_hours(u) is not None and "starts_at.gte.2026-10-12" in unquote(u):
        return Resp(500, {"code": "57014", "message": "canceling statement due to statement timeout"})


floor = FakeRest(started + slu_rows(), hook=always_slow)
code, out, rep = run(floor, "--apply", "--days", "1", "--lookback", "1")
check("a started day that times out even at 15-minute slices is owed as one part, and exits 1",
      rep["owed_windows"] == ["2026-10-12 00:00-24:00Z (timeout)"] and code == 1)
check("... and the next window still lists and hides", floor.events[SLU_OLD]["duplicate_of"] == SLU_NEW)


def span(u):
    m = re.search(r"starts_at\.gte\.([^,]+)(?:,or\([^)]*\))?,starts_at\.lt\.([^)]+)\)", unquote(u))
    return tuple(datetime.fromisoformat(x.replace("Z", "+00:00")) for x in m.groups()) if m else None


def today_started_slow(m, u, n):
    got = span(u)
    if (m == "GET" and got and got[0] >= datetime(2026, 10, 13, tzinfo=timezone.utc)
            and got[1] <= datetime(2026, 10, 13, 12, tzinfo=timezone.utc)):
        return Resp(500, {"code": "57014", "message": "canceling statement due to statement timeout"})


part = FakeRest(started + slu_rows(), hook=today_started_slow)
code, out, rep = run(part, "--apply", "--days", "1", "--lookback", "0")
w0 = next(x for x in rep["windows"] if x["day"] == "2026-10-13")
check("today's started part times out: it is owed, and today's rows from now on are still used",
      rep["owed_windows"] == ["2026-10-13 00:00-12:00Z (timeout)"] and w0["status"] == "partial"
      and part.events[SLU_OLD]["duplicate_of"] == SLU_NEW and code == 1)
del slept[:]


def timeouts(m, u, n):
    if m == "GET" and "and=(starts_at.gte.2026-10-14" in u:
        return Resp(500, {"code": "57014", "message": "canceling statement due to statement timeout"})


slow = FakeRest(slu_rows(), hook=timeouts)
code, out, rep = run(slow, "--apply", "--days", "2", "--lookback", "1")
check("57014 three times on a day ahead: re-asked after 2 s and 4 s, then that day is owed",
      rep["owed_windows"] == ["2026-10-14 00:00-24:00Z (timeout)"] and slept[:2] == [2.0, 4.0]
      and sum("and=(starts_at.gte.2026-10-14" in u for _, u, _ in slow.calls) == 3)
check("... the other windows still run and hide, and the run exits 1",
      slow.events[SLU_OLD]["duplicate_of"] == SLU_NEW and code == 1)
D._clock = lambda: float(next(ticks))
code, out, rep = run(FakeRest(slu_rows()), "--days", "21", "--lookback", "60", "--max-seconds", "5")
check("an exhausted budget lists the remaining windows as BACKLOG and exits 1",
      code == 1 and "BACKLOG" in out and len(rep["backlog"]) > 70 and rep["windows"][0]["day"] == "2026-10-12")
D._clock = lambda: 0.0

print("\nthe carry (a one-day row starts the UTC day before its timed twin)")
syd = "library.example.au/events/storytime-77"
sydney = [ev("dddddddd-0000-4000-8000-000000000001", "Storytime", "2026-10-13T13:00:00+00:00",
             "2026-10-14T12:59:59+00:00", -33.8731, 151.2065, "2026-10-12T07:00:00+00:00", syd),
          ev("dddddddd-0000-4000-8000-000000000002", "Storytime", "2026-10-14T00:00:00+00:00",
             "2026-10-14T01:00:00+00:00", -33.8731, 151.2066, "2026-10-01T07:00:00+00:00", syd)]
fake = FakeRest(sydney)
code, out, rep = run(fake, "--days", "3", "--lookback", "0")
got = [(h["id"][-1], h["survivor"][-1], h["arm"], h["day"]) for h in rep["hidden"]]
check("a Sydney one-day row in window d matches its timed twin in window d+1",
      got == [("1", "2", "all-day-copy", "2026-10-14")])

print("\nunhide")
STAMP = "2026-10-13T12:00:00+00:00"
hid = slu_rows()
hid[0].update(hidden_at=STAMP, duplicate_of=SLU_NEW)
hid.append(ev("eeeeeeee-0000-4000-8000-000000000001", "Other run", "2026-10-15T18:00:00+00:00",
              "2026-10-15T19:00:00+00:00", 47.6, -122.3, "2026-09-01T07:00:00+00:00",
              hidden_at="2026-10-12T12:00:00+00:00", duplicate_of=SLU_NEW))
hid.append(ev("eeeeeeee-0000-4000-8000-000000000002", "Taken down", "2026-10-15T18:00:00+00:00",
              "2026-10-15T19:00:00+00:00", 47.6, -122.3, "2026-09-01T07:00:00+00:00", hidden_at=STAMP))
fake = FakeRest(hid)
code, out, _ = run(fake, "--unhide", "--stamp", STAMP)
first = next((u for m, u, _ in fake.calls if "duplicate_of=not.is.null" in u), "")
check("unhide reports unless --apply", patches(fake) == [] and code == 0 and "Copies this job hid that match: 1" in out)
check("its filter has duplicate_of=not.is.null and the quoted stamp ('+' is %2B)",
      "hidden_at=eq.2026-10-13T12%3A00%3A00%2B00%3A00" in first and "external_source=eq.mapsee" in first)
code, out, _ = run(fake, "--unhide", "--stamp", STAMP, "--apply")
check("--apply clears hidden_at and duplicate_of on that run's copies only",
      fake.events[SLU_OLD]["hidden_at"] is None and fake.events[SLU_OLD]["duplicate_of"] is None
      and fake.events["eeeeeeee-0000-4000-8000-000000000001"]["hidden_at"] == "2026-10-12T12:00:00+00:00"
      and fake.events["eeeeeeee-0000-4000-8000-000000000002"]["hidden_at"] == STAMP
      and all(set(b) == {"hidden_at", "duplicate_of"} for _, _, b in patches(fake)))
try:
    with contextlib.redirect_stdout(io.StringIO()):
        D.main(["--unhide"], http=FakeRest([]))
    check("--unhide without --stamp or --id refuses", False)
except SystemExit as exc:
    check("--unhide without --stamp or --id refuses", "never un-hides everything" in str(exc))

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
