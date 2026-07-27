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
from typing import Dict, Optional


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
    hiccup, so callers fall back to the search deep-link. Caches hits AND misses
    (misses as "") so a name is queried at most once per cache lifetime."""

    TOKEN_URL = "https://accounts.spotify.com/api/token"
    SEARCH_URL = "https://api.spotify.com/v1/search"

    def __init__(self, client_id: str, client_secret: str, session,
                 cache: Optional[Dict[str, str]] = None):
        self.client_id = (client_id or "").strip()
        self.client_secret = (client_secret or "").strip()
        self.session = session
        self.cache = cache if cache is not None else {}
        self._token: Optional[str] = None
        self._token_exp = 0.0

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
            return None
        if r.status_code != 200:
            return None
        j = r.json() or {}
        self._token = j.get("access_token")
        self._token_exp = time.time() + int(j.get("expires_in", 3600))
        return self._token

    def artist_url(self, name: Optional[str]) -> Optional[str]:
        name = (name or "").strip()
        if not name or not self.enabled:
            return None
        key = name.lower()
        if key in self.cache:                       # cached hit or prior miss ("")
            return self.cache[key] or None
        tok = self._get_token()
        if not tok:
            return None
        url = None
        try:
            r = self.session.get(
                self.SEARCH_URL,
                headers={"Authorization": f"Bearer {tok}"},
                params={"q": name, "type": "artist", "limit": 1},
                timeout=20,
            )
            if r.status_code == 200:
                items = (((r.json() or {}).get("artists") or {}).get("items")) or []
                if items:
                    url = (items[0].get("external_urls") or {}).get("spotify")
        except Exception:
            url = None
        self.cache[key] = url or ""                  # cache misses too
        time.sleep(0.1)                              # be polite to the API
        return url
