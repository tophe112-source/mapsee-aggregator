#!/usr/bin/env python3
"""
meetup_token.py — mint a short-lived Meetup GraphQL access token via the
self-signed JWT (server-to-server) OAuth flow, and print it to stdout.

Meetup access tokens expire after 1 hour, so a scheduled job can't rely on a
single static token stored as a secret — it must mint a fresh one each run.
This flow needs NO interactive login: you sign a JWT with your OAuth consumer's
private key and exchange it for an access token.

One-time setup (requires a Meetup Pro subscription to create the consumer):
  1. meetup.com/api/oauth/consumers -> create an OAuth consumer; note the
     CLIENT KEY.
  2. Create a signing key for that consumer; note the SIGNING KEY ID (kid) and
     download the RSA PRIVATE KEY (PEM).
  3. Find your own numeric MEMBER ID (the authorized user the token acts as).

Provide these as environment variables (store them as GitHub repo secrets):
  MEETUP_CLIENT_KEY       the consumer's client key   -> JWT "iss"
  MEETUP_MEMBER_ID        your numeric member id      -> JWT "sub"
  MEETUP_SIGNING_KEY_ID   the signing key id          -> JWT header "kid"
  MEETUP_PRIVATE_KEY      the RSA private key PEM (raw, or base64 of the PEM)

If any are unset, this prints nothing and exits 0, so the pipeline just skips
Meetup instead of failing.

    export MEETUP_OAUTH_TOKEN="$(python meetup_token.py)"
"""
from __future__ import annotations

import base64
import os
import sys
import time

TOKEN_URL = "https://secure.meetup.com/oauth2/access"
JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
AUDIENCE = "api.meetup.com"


def _load_private_key(raw: str) -> str:
    """Accept a PEM directly, or a base64-encoded PEM (handy for single-line
    secrets), and normalize escaped newlines."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if "BEGIN" in raw:
        return raw.replace("\\n", "\n")
    try:                                   # secret stored as base64 of the PEM
        decoded = base64.b64decode(raw).decode("utf-8")
        if "BEGIN" in decoded:
            return decoded
    except Exception:
        pass
    return raw.replace("\\n", "\n")


def mint(client_key: str, member_id: str, kid: str, private_key_pem: str) -> str:
    import jwt          # PyJWT (needs 'cryptography' for RS256)
    import requests

    now = int(time.time())
    assertion = jwt.encode(
        {"sub": member_id, "iss": client_key, "aud": AUDIENCE, "exp": now + 120},
        private_key_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": JWT_GRANT, "assertion": assertion},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"[meetup-token] HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return ""
    tok = (resp.json() or {}).get("access_token") or ""
    if not tok:
        print(f"[meetup-token] no access_token in response: {resp.text[:200]}", file=sys.stderr)
    return tok


def main() -> int:
    client_key = os.environ.get("MEETUP_CLIENT_KEY", "").strip()
    member_id = os.environ.get("MEETUP_MEMBER_ID", "").strip()
    kid = os.environ.get("MEETUP_SIGNING_KEY_ID", "").strip()
    private_key = _load_private_key(os.environ.get("MEETUP_PRIVATE_KEY", ""))

    if not (client_key and member_id and kid and private_key):
        print("[meetup-token] JWT secrets not fully set — skipping Meetup.", file=sys.stderr)
        return 0

    try:
        tok = mint(client_key, member_id, kid, private_key)
    except ImportError:
        print("[meetup-token] needs PyJWT + cryptography (pip install pyjwt cryptography).", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"[meetup-token] mint failed: {exc}", file=sys.stderr)
        return 0

    if tok:
        sys.stdout.write(tok)     # bare token on stdout for the workflow to capture
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
