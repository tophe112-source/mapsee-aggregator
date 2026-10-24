#!/usr/bin/env python3
"""
mapsee_music_links.py — turn a performer's name into "listen" links so people
browsing upcoming shows can hear the artist before deciding to go.

Two tiers, both ToS-clean:

  * ALWAYS (no key, no network) — search deep-links that open the artist in the
    Spotify / YouTube Music app or web:
        https://open.spotify.com/search/<artist>
        https://music.youtube.com/search?q=<artist>
    Linking is not scraping; these always work and need nothing set up.

  * EXACT (optional, needs a free Spotify app) — resolve the artist's real
    Spotify page via the Web API Search endpoint (client-credentials). Linking
    OUT to an artist page is precisely the "link back to Spotify" that Spotify's
    Developer Terms require; we store only the URL and cache name->url to stay
    light. (Spotify removed the artist top-tracks endpoint in Feb 2026 — not
    needed here: the artist page itself shows popular tracks with previews.)

Ticketmaster attractions already carry spotify/youtube externalLinks, captured
upstream in the adapter; those exact links win over anything computed here.
"""
from __future__ import annotations

import time
import urllib.parse
from collections import Counter
from typing import Any, Dict, Optional


def _q(name: str) -> str:
    return urllib.parse.quote((name or "").strip())


def spotify_search_url(name: Optional[str]) -> Optional[str]:
    name = (name or "").strip()
    return f"https://open.spotify.com/search/{_q(name)}" if name else None


def youtube_search_url(name: Optional[str]) -> Optional[str]:
    name = (name or "").strip()
    return f"https://music.youtube.com/search?q={_q(name)}" if name else None


def bandcamp_search_url(name: Optional[str]) -> Optional[str]:
    # item_type=b scopes the results to ARTISTS/BANDS, so the link lands on the
    # act rather than a mix of tracks/albums/fans. Search-only: Bandcamp has no
    # public client-credentials API to resolve an exact artist page (unlike
    # Spotify), so this mirrors the YouTube Music search deep-link.
    name = (name or "").strip()
    return f"https://bandcamp.com/search?q={_q(name)}&item_type=b" if name else None


class SpotifyResolver:
    """Resolve an artist NAME to their canonical Spotify page URL via the Web API
    (client-credentials). Returns None gracefully with no creds or on any API
    hiccup, so callers fall back to the search deep-link.

    Caches what Spotify ANSWERED: a hit (the URL), and a 200 with no artist (a
    miss, kept MISS_TTL_S). Never a refusal or an error. Those used to be cached
    as "" misses, so a persisted cache would never have asked again once Spotify
    recovered; a legacy "" entry is therefore asked again.

    A search that answers nothing stops itself. On 2026-10-23, 24 of 24 syncs
    logged "resolved 0 exact artist pages" while the Meetup job asked ~5,700
    names a day (468-580 s a sync). After TRIP_FAILS_IN_ROW consecutive non-200
    answers, or TRIP_LOOKUPS_NO_HIT lookups without one hit, the rest of the
    process asks nothing and a ::warning:: carries the status counts."""

    TOKEN_URL = "https://accounts.spotify.com/api/token"
    SEARCH_URL = "https://api.spotify.com/v1/search"
    TRIP_FAILS_IN_ROW = 25
    TRIP_LOOKUPS_NO_HIT = 200
    MISS_TTL_S = 30 * 86400

    def __init__(self, client_id: str, client_secret: str, session,
                 cache: Optional[Dict[str, Any]] = None, pause_s: float = 0.1):
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.session = session
        self.cache = cache if cache is not None else {}
        self.pause_s = pause_s
        self._token: Optional[str] = None
        self._token_exp = 0.0
        self._token_status: Any = None
        self.lookups = 0
        self.hits = 0
        self.statuses: Counter = Counter()
        self._fails_in_row = 0
        self._tripped = False

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _get_token(self) -> Optional[str]:
        if self._token and time.time() < self._token_exp - 30:
            return self._token
        try:
            r = self.session.post(
                self.TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
                timeout=20,
            )
        except Exception:
            self._token_status = "token error"
            return None
        if r.status_code != 200:
            self._token_status = f"token {r.status_code}"
            return None
        j = r.json() or {}
        self._token = j.get("access_token")
        self._token_exp = time.time() + int(j.get("expires_in", 3600))
        return self._token

    def _fresh(self, value: Any, now: float) -> bool:
        """A cached hit, or a miss Spotify answered less than MISS_TTL_S ago."""
        if isinstance(value, str):
            return bool(value)
        at = value.get("miss") if isinstance(value, dict) else None
        return isinstance(at, (int, float)) and now - at < self.MISS_TTL_S

    def prune(self) -> None:
        """Drop expired misses and legacy "" entries, so the saved cache holds
        only what may still be served."""
        now = time.time()
        for key in [k for k, v in self.cache.items() if not self._fresh(v, now)]:
            del self.cache[key]

    def _count(self, status: Any, hit: bool = False) -> None:
        self.lookups += 1
        self.hits += int(hit)
        self.statuses[status] += 1
        self._fails_in_row = 0 if status == 200 else self._fails_in_row + 1
        if self._fails_in_row >= self.TRIP_FAILS_IN_ROW or \
                (self.lookups >= self.TRIP_LOOKUPS_NO_HIT and not self.hits):
            self._tripped = True
            print(f"::warning::Spotify: {self.lookups} lookups, {self.hits} hits, "
                  f"statuses {dict(self.statuses)} - disabled this run")

    def artist_url(self, name: Optional[str]) -> Optional[str]:
        name = (name or "").strip()
        if not name or not self.enabled:
            return None
        key = name.lower()
        cached = self.cache.get(key)
        if cached is not None and self._fresh(cached, time.time()):
            return cached if isinstance(cached, str) else None
        if self._tripped:
            return None
        tok = self._get_token()
        if not tok:
            self._count(self._token_status or "token error")
            return None
        url = None
        try:
            r = self.session.get(
                self.SEARCH_URL,
                headers={"Authorization": f"Bearer {tok}"},
                params={"q": name, "type": "artist", "limit": 1},
                timeout=20,
            )
            status = r.status_code
            if status == 200:
                items = (((r.json() or {}).get("artists") or {}).get("items")) or []
                if items:
                    url = (items[0].get("external_urls") or {}).get("spotify")
        except Exception:
            status = "error"
        self._count(status, hit=bool(url))
        if url:
            self.cache[key] = url
        elif status == 200:                          # answered: no such artist
            self.cache[key] = {"miss": int(time.time())}
        time.sleep(self.pause_s)                     # be polite to the API
        return url
