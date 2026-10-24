#!/usr/bin/env python3
"""
test_spotify_resolver.py — the exact-artist lookup must stop asking a search
that answers nothing, must not remember a refusal as a miss, and must never
ask about an event title.

THE FAILURE THIS PREVENTS. On 2026-10-23 every one of 24 syncs logged
"Spotify: resolved 0 exact artist pages" while the Meetup job asked ~5,700
names a day, 468-580 s per sync on its critical path. Those names were Meetup
EVENT TITLES (Meetup records carry no lineup), and every non-200 answer was
cached as "" - so a cache kept between runs would have stopped asking for good
once Spotify recovered.

No network: a stub session answers every request. Run: python test_spotify_resolver.py
"""
import json
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mapsee_supabase_sync as S                          # noqa: E402
from mapsee_music_links import SpotifyResolver            # noqa: E402

ARTIST = "https://open.spotify.com/artist/4Z8W4fKeB5YxbusRsdQVPb"


class Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


class Session:
    """`search` is the status the search answers with, 'hit', 'empty' or 'raise'."""

    def __init__(self, search):
        self.search = search
        self.asked = []

    def post(self, url, **kw):
        return Resp(200, {"access_token": "t", "expires_in": 3600})

    def get(self, url, params=None, **kw):
        self.asked.append(params["q"])
        if self.search == "raise":
            raise ConnectionError("reset")
        if self.search == "hit":
            return Resp(200, {"artists": {"items": [{"external_urls": {"spotify": ARTIST}}]}})
        if self.search == "empty":
            return Resp(200, {"artists": {"items": []}})
        return Resp(self.search)


def resolver(search, cache=None):
    return SpotifyResolver("id", "secret", Session(search), cache, pause_s=0)


def ask(r, n, prefix="band"):
    out = StringIO()
    with redirect_stdout(out):
        urls = [r.artist_url(f"{prefix} {i}") for i in range(n)]
    return urls, out.getvalue()


def main():
    checks = []

    # ---- 1. A refusal trips the breaker after 25 in a row, and is never cached.
    r = resolver(403)
    _, said = ask(r, 40)
    checks.append((len(r.session.asked) == 25,
                   f"a 403 search is asked 25 times, not 40 ({len(r.session.asked)})"))
    checks.append((r.cache == {}, f"...and not one refusal is cached as a miss ({len(r.cache)})"))
    checks.append(("::warning::Spotify: 25 lookups, 0 hits, statuses {403: 25} - disabled this run" in said,
                   f"...and the warning names the status ({said.strip()!r})"))

    # ---- 2. An exception is no answer either.
    r = resolver("raise")
    ask(r, 30)
    checks.append((len(r.session.asked) == 25 and r.cache == {},
                   f"a search that raises trips at 25 and caches nothing ({len(r.session.asked)}, {len(r.cache)})"))

    # ---- 3. A search that works is never stopped.
    r = resolver("hit")
    urls, said = ask(r, 260)
    checks.append((len(r.session.asked) == 260 and all(u == ARTIST for u in urls) and not said,
                   f"260 lookups that all hit run to the end, silently ({len(r.session.asked)})"))
    checks.append((r.cache.get("band 7") == ARTIST, "...and a hit is cached as its URL"))

    # ---- 4. Answers with no artist: cached as dated misses, and 200 of them in
    # a row with not one hit stop the run.
    r = resolver("empty")
    _, said = ask(r, 250)
    checks.append((len(r.session.asked) == 200 and "200 lookups, 0 hits" in said,
                   f"200 answered lookups without a hit stop the run ({len(r.session.asked)})"))
    miss = r.cache.get("band 3")
    checks.append((isinstance(miss, dict) and abs(miss.get("miss", 0) - time.time()) < 60,
                   f"...and an answered miss is cached with its time ({miss})"))

    # ---- 5. After the trip, what is already known is still served.
    r = resolver(403, {"known act": ARTIST})
    ask(r, 25)
    checks.append((r.artist_url("Known Act") == ARTIST and len(r.session.asked) == 25,
                   "a cached hit is still served once the breaker has tripped"))

    # ---- 6. A miss expires; a legacy "" (once written for a 403 too) is asked again.
    now = time.time()
    cache = {"old miss": {"miss": int(now - 31 * 86400)}, "new miss": {"miss": int(now - 86400)},
             "legacy": "", "hit": ARTIST}
    r = resolver("hit", cache)
    for name in ("old miss", "new miss", "legacy", "hit"):
        r.artist_url(name)
    checks.append((r.session.asked == ["old miss", "legacy"],
                   f"a 31-day-old miss and a legacy '' are asked again, a 1-day-old one is not ({r.session.asked})"))
    r = resolver("hit", {"old": {"miss": 0}, "legacy": "", "fresh": {"miss": int(now)}, "hit": ARTIST})
    r.prune()
    checks.append((sorted(r.cache) == ["fresh", "hit"],
                   f"prune keeps only what may still be served ({sorted(r.cache)})"))

    # ---- 7. Only a named performer is looked up. A Meetup row is a title.
    os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"] = "id", "secret"
    session = Session("hit")
    recs = [
        {"name": "Jazz Jam Night at the Corner Bar", "category": "music",
         "sources": [{"source": "meetup", "source_id": "1"}]},
        {"name": "Khruangbin: A La Sala Tour", "category": "music", "lineup": ["Khruangbin", "Men I Trust"]},
        {"name": "Already Linked", "category": "music", "lineup": ["Someone"], "spotify_url": ARTIST + "x"},
        {"name": "Friday Lecture", "category": "learning", "lineup": ["A Speaker"]},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "spotify_cache.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"stale": "", "kept": ARTIST}, fh)
        out = StringIO()
        with redirect_stdout(out):
            S._enrich_music_links(recs, session, cache_path=path)
        saved = json.load(open(path, encoding="utf-8"))
    checks.append((session.asked == ["Khruangbin"],
                   f"only lineup[0] is asked; the title, a linked row and a lecture are not ({session.asked})"))
    checks.append(("spotify_url" not in recs[0] and recs[1].get("spotify_url") == ARTIST,
                   "the Meetup title keeps its search link; the headliner gets the exact page"))
    checks.append((sorted(saved) == ["kept", "khruangbin"],
                   f"the saved cache holds the new hit and drops a legacy '' ({sorted(saved)})"))
    checks.append(("Spotify: resolved 1 exact artist pages from 1 lookups (statuses {200: 1}; "
                   "1 music rows without a lineup not asked" in out.getvalue(),
                   f"the summary line counts what was and was not asked ({out.getvalue().strip()!r})"))

    failed = 0
    for ok, why in checks:
        failed += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'}  {why}")
    print(f"\n{len(checks)} cases, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
