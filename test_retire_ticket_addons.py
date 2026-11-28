#!/usr/bin/env python3
"""mapsee_retire_ticket_addons.py: which stored rows it hides, what it never
touches, and that one run can be reversed. Prints one line per case; exits
non-zero on a failure. Uses test_dedupe_events' fake PostgREST."""
import contextlib
import io
import os
import sys

with contextlib.redirect_stdout(io.StringIO()):
    import test_dedupe_events as T              # FakeRest; its own cases run on import, unheard
if T.fail:
    sys.exit("test_dedupe_events fails: fix that first")
os.environ.update(SUPABASE_URL="https://db.example", SUPABASE_SERVICE_ROLE_KEY="unused-test-key",
                  MAPSEE_TODAY="20261128")
import mapsee_retire_ticket_addons as R  # noqa: E402

ok = fail = 0


def check(label, cond):
    global ok, fail
    ok, fail = ok + bool(cond), fail + (not cond)
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")


TM = "https://www.ticketmaster.com/wamu-theater-amplified-access-steve-lacy/event/0F006505CBF9342A"
AMP = "WAMU Theater Amplified Access: Steve Lacy (Not an Event Ticket)"
IDS = ["bbbbbbbb-0000-4000-8000-00000000000%d" % n for n in range(1, 8)]


def rows():
    def one(i, title, url, **kw):
        e = T.ev(i, title, "2026-11-29T04:00:00+00:00", "2026-11-29T07:00:00+00:00", 47.596, -122.333,
                 "2026-09-29T13:03:00+00:00")
        e.update(description=f"Text.\n\nTickets / info: {url}" if url else "Text.", **kw)
        return e
    return [one(IDS[0], AMP, TM), one(IDS[1], "Steve Lacy: Oh yeah? Tour", TM.replace("CBF9342A", "934273DF")),
            one(IDS[2], "[This is Not an event] Superclub board game", "https://www.meetup.com/superclub/events/31"),
            one(IDS[3], "Parking permit Muse", "https://www.ticketmaster.nl/event/parking-permit-muse/12345"),
            one(IDS[4], AMP, TM, claimed_at="2026-10-01T00:00:00+00:00"),
            one(IDS[5], "KOLLER: KRYL | Parking ticket", "https://www.ticketmaster.cz/event/koller/9911",
                last_message_at="2026-11-20T00:00:00+00:00"),
            one(IDS[6], "Parking Committee", None)]


def run(fake, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = R.main(list(args), http=fake)
    return code, out.getvalue()


T.D._clock = lambda: 0.0
check("the predicate is the import's title rule on a Ticketmaster or Live Nation link only",
      R.is_addon(AMP, "ticketmaster.com/x") and R.is_addon("Parking permit Muse", "www.ticketmaster.nl/e/1")
      and R.is_addon(AMP, "concerts.livenation.com/e/1") and not R.is_addon(AMP, "meetup.com/g/events/1")
      and not R.is_addon("Steve Lacy: Oh yeah? Tour", "ticketmaster.com/x") and not R.is_addon(AMP, None))
fake = T.FakeRest(rows())
code, out = run(fake, "--days", "3")
check("a report lists the three unclaimed products (one has a chat: listed, never hidden) and writes nothing",
      code == 0 and T.patches(fake) == [] and "still visible: 3" in out and IDS[0] in out and IDS[3] in out
      and IDS[4] not in out and IDS[2] not in out)
fake = T.FakeRest(rows())
code, out = run(fake, "--apply", "--days", "3")
hid = sorted(i for i, e in fake.events.items() if e["hidden_at"])
check("--apply hides exactly those: not the show, the meet-up, the claimed row, the one with a chat, the linkless one",
      code == 0 and hid == sorted([IDS[0], IDS[3]]))
check("...with hidden_at alone (never cancelled_at, never duplicate_of), filtered on unclaimed, visible, nobody on it",
      all(set(b) == {"hidden_at"} and "claimed_at=is.null" in u and "hidden_at=is.null" in u
          and "last_message_at=is.null" in u and "external_source=eq.mapsee" in u for _, u, b in T.patches(fake))
      and fake.events[IDS[0]]["duplicate_of"] is None and fake.events[IDS[0]]["cancelled_at"] is None)
stamp = fake.events[IDS[0]]["hidden_at"]
fake.events[IDS[1]].update(hidden_at=stamp)                 # something else hid the show at the same second
before = len(T.patches(fake))
code, out = run(fake, "--unhide", "--stamp", stamp)
check("--unhide reports unless --apply", code == 0 and len(T.patches(fake)) == before and "hidden at" in out)
code, out = run(fake, "--unhide", "--stamp", stamp, "--apply")
check("--unhide --apply puts back that run's products and nothing else hidden at that stamp",
      code == 0 and fake.events[IDS[0]]["hidden_at"] is None and fake.events[IDS[3]]["hidden_at"] is None
      and fake.events[IDS[1]]["hidden_at"] == stamp)
fake = T.FakeRest(rows(), rsvps=[IDS[0]])
code, out = run(fake, "--apply", "--days", "3")
check("a product somebody RSVPed to is left", fake.events[IDS[0]]["hidden_at"] is None
      and fake.events[IDS[3]]["hidden_at"] and "2 left" in out)
fake = T.FakeRest(rows(), hook=lambda m, u, n: T.Resp(500, {"message": "boom"}) if "and=(starts_at" in u or "starts_at=gte" in u else None)
code, out = run(fake, "--apply", "--days", "3")
check("a listing that fails everywhere hides nothing and exits 1", code == 1 and T.patches(fake) == [] and "OWED" in out)


def one_day_down(m, u, n):
    if "and=(starts_at" in u and "2026-11-30" in u.split("and=(")[1][:60]:
        return T.Resp(500, {"message": "boom"})


fake = T.FakeRest(rows(), hook=one_day_down)
code, out = run(fake, "--apply", "--days", "3")
check("one day that cannot be listed costs only its own rows: the others are hidden, and the run still exits 1",
      code == 1 and fake.events[IDS[0]]["hidden_at"] and "2026-11-30 was not listed whole" in out)

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
