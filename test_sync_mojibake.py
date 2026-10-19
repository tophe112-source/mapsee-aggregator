#!/usr/bin/env python3
"""UTF-8 read as Windows-1252, undone at the write boundary (_fix_mojibake).

A PJ Library listing arrived as "High Holidays â€“ preparing … itâ€™s time",
and shipped to the map that way. What this protects, each with its case:
  * the screenshot's own text comes out with an en dash and an apostrophe
  * accents the same way ("cafÃ©" -> "café"), including a feed that did it twice
  * a string that was never mojibake is untouched, even one with a lone Ã
  * the repair runs INSIDE _clean_text, before the entity decode
  * a title goes through too (to_row cleans name and description alike)

  python test_sync_mojibake.py
"""
import sys
import mapsee_supabase_sync as SY

fails = 0


def check(label, cond, detail=""):
    global fails
    print(("ok    " if cond else "FAIL  ") + label + ("" if cond else "   " + repr(detail)))
    if not cond:
        fails += 1


SHOT = "After the whirlwind of the High Holidays â€“ preparing for Rosh Hashanah â€“ itâ€™s time to refill your own cup."
check("the screenshot's text: an en dash and an apostrophe, nothing else changed",
      SY._fix_mojibake(SHOT) == "After the whirlwind of the High Holidays – preparing for Rosh Hashanah – it’s time to refill your own cup.", SY._fix_mojibake(SHOT))
check("accents: cafÃ© -> café, naÃ¯ve -> naïve", SY._fix_mojibake("cafÃ© naÃ¯ve") == "café naïve")
check("curly quotes and an em dash", SY._fix_mojibake("â€œHelloâ€ â€” she said") == "“Hello” — she said", SY._fix_mojibake("â€œHelloâ€ â€” she said"))
twice = "café".encode("utf-8").decode("cp1252").encode("utf-8").decode("cp1252")
check("a feed that did it twice is undone twice", SY._fix_mojibake(twice) == "café", (twice, SY._fix_mojibake(twice)))
check("a clean string is untouched", SY._fix_mojibake("Plain text – already right – it’s fine") == "Plain text – already right – it’s fine")
check("a lone Ã that is not valid UTF-8 is left alone", SY._fix_mojibake("Ã la carte") == "Ã la carte")
# Past the gate and still invalid: C3 A9 C3 is a real é and a dangling lead
# byte; a strict decode refuses it, a lenient one hands back U+FFFD.
check("a string that passes the gate but is not valid UTF-8 is left alone, never mangled",
      SY._fix_mojibake("Ã©Ã") == "Ã©Ã" and "�" not in SY._fix_mojibake("Ã©Ã"), SY._fix_mojibake("Ã©Ã"))
check("a character no cp1252 byte makes stops the repair, untouched", SY._fix_mojibake("â€“ 中文") == "â€“ 中文")
check("empty and None pass through", SY._fix_mojibake("") == "" and SY._clean_text(None) is None)
check("_clean_text makes the repair BEFORE decoding entities, so &amp; in the same text still decodes",
      SY._clean_text("Bake &amp; Yoga â€“ moms only") == "Bake & Yoga – moms only", SY._clean_text("Bake &amp; Yoga â€“ moms only"))

print("\n%s" % ("%d failed" % fails if fails else "all ok"))
sys.exit(1 if fails else 0)
