r"""The product's offer tags, for the adapters' tests: ../mapsee migration
0227's public.event_offers(), VENDORED VERBATIM from its Python twin
../mapsee/tools/measure_deals.py (rx, every pattern, body_text, not_this_price
and classify; lines 120-270 of that file on 2026-10-12, mapsee 2de7f72).

An adapter that writes "free" wording writes it FOR this predicate: "Free to
walk." (AVA) and "Free to register." (Luminate) matched none of its patterns, so
the rows that say them were never tagged free, while "Free to join." matches
`free\s+to\s+(?:attend|join|...)`. And a club's prose can earn a tag the walk
never offered: a hotel's "reduced rate" is `discount`, "not free" (about
parking) is FREE_NEG and vetoes the whole row.

    import offers_0227 as O
    tags = O.classify({"title": ..., "description": ...})[0]
    O.twin_drift()   # names whose pattern differs from ../mapsee's, or None

No network and nothing imported from the product: CI checks out this repo
alone. When ../mapsee IS beside this checkout (or beside a parent of it, as from
a worktree), twin_drift() compares every pattern, and a test should fail on
any difference.
"""
import os
import re

I = re.I


def rx(*parts):
    return re.compile("|".join(parts), I)


FREE = rx(
    r"(?<![-\w/])free\s+(?:admission|entry|entrance|event|of\s+charge|to\s+(?:attend|join|enter|participate|the\s+public|all|everyone)|and\s+open|for\s+(?:all|everyone|kids|children|the\s+public|families)|community\s+event|concert|show|screening|class|classes|workshop|tour|tours|tasting|session|lesson|drop[- ]in|museum|comedy|yoga|rsvp|with\s+(?:rsvp|registration|admission|entry)|tickets?|family\s+(?:day|fun))\b",
    r"\b(?:admission|entry|entrance|attendance|cost|price|cover(?:\s+charge)?)\s*(?:is|:|-|–)?\s*free\b",
    r"\bno\s+(?:cover|cover\s+charge|admission\s+(?:fee|charge)|entry\s+fee)\b",
    r"\bat\s+no\s+cost\b", r"\bfree\s*!", r"\bis\s+free\b(?!\s+(?:of|from|to\s+(?:use|choose)))",
    r"\bentr[ée]e\s+(?:libre|gratuite)\b", r"\bacc[èe]s\s+(?:libre|gratuit)\b", r"\bgratuit(?:e|s|es)?\b",
    r"\b(?:entrada|acceso|ingreso)\s+(?:libre|gratuit[ao]|gratis)\b", r"\bgratuit[ao]s?\b",
    r"\bingresso\s+(?:libero|gratuito)\b", r"\bgratis\b",
    r"\beintritt\s*(?::\s*)?(?:frei|kostenlos)\b", r"\bfreier\s+eintritt\b", r"\bkostenlos\b", r"\bkostenfrei\b",
    r"入場無料|参加無料|無料", r"무료",
)
FREE_TITLE = re.compile(r"(?<![-\w])free\b(?![-\w])(?!\s+(?:jazz|style|spirit|will|agent|fall|range|form|skate|swim|throw|kick|time|state|mason|masons|press|radio|speech|palestine|tibet))", I)
FREE_NEG = re.compile(r"\b(?:not|isn'?t|is\s+not)\s+(?:a\s+)?free\b|\bnon[- ]gratuit|\bpas\s+gratuit|\bno\s+es\s+gratis", I)
# "Free" that is not the price of THIS event, removed before FREE is tested. Read
# on 25 matches: "FIRST CLASS IS FREE" (an intro offer, tagged `intro`), "Chicas
# GRATIS hasta las 930", "free to attend for MSL members", "Free Shots" on a bar
# crawl. A page headed "Free things to do" that lists those is lying a little each
# time. Removing the phrase rather than vetoing the row keeps "Free admission.
# Under-5s free." free.
FREE_PERK = rx(r"\bfree\s+(?:shots?|drinks?|drink\s+tickets?|beers?|parking|wi-?fi|snacks?|coffee|tea|pizza|food|gifts?|swag|t-?shirts?|tote|valet|shuttle|consultations?|estimates?|delivery|shipping|samples?|refills?)\b",
               r"\b(?:parking|wi-?fi|food|drinks?|refreshments|snacks|coffee|tea|lunch|breakfast|dinner|pizza|childcare)(?:\s+and\s+\w+)?\s+(?:is\s+|are\s+|will\s+be\s+)?(?:provided,?\s+)?(?:free(?:\s+of\s+charge)?|for\s+free)\b")
FREE_COND = rx(
    r"\b(?:free|gratis|gratuit[oae]?s?|kostenlos)\s+(?:before|until|till|antes\s+de|hasta(?:\s+las)?|avant|bis)\s+\d",
    r"\b(?:ladies|girls|women|chicas|mujeres|damas|filles|femmes)\s+(?:get\s+in\s+|enter\s+|go\s+)?(?:free|gratis|gratuit)",
    r"\b(?:free|gratis|gratuit)\s+(?:for\s+)?(?:ladies|girls|women|chicas|mujeres|damas|filles|femmes)\b",
    r"\bfree\s+(?:to\s+(?:attend|join|enter)\s+)?(?:for|to)\s+(?:\w+\s+){0,2}(?:members|subscribers|students|seniors|residents|veterans|military|cardholders)\b",
    # "Free general admission for Bank of America cardholders" (Museums on Us) and
    # "free admission for members" are real offers to SOME people, not a free event.
    r"\bfree\s+(?:general\s+)?(?:admission|entry|entrance)\s+(?:for|to)\s+(?:\w+\s+){0,3}(?:members|subscribers|cardholders|students|seniors|residents|veterans|military)\b",
    r"\bfree\s+(?:for\s+)?(?:(?:kids|children)\s+under|under[- ]?\d+s?\b|those\s+(?:under|over|with)\b)",
    r"\b(?:members|students|seniors|kids|children|under[- ]?\d+s?)\s+(?:get\s+in\s+|enter\s+|go\s+)?free\b",
    r"\bfirst\s+\d+\s+(?:people|guests|attendees)\s+(?:get\s+in\s+)?free",
    r"\b(?:companions?|carers?|caregivers?|chaperones?)\s+(?:enter\s+|go\s+|get\s+in\s+|are\s+|is\s+)?free",
    r"以下無料|未満無料",
)

PWYW = rx(r"pay[- ]what[- ]you[- ](?:can|wish|want|will|feel)", r"pay[- ]as[- ]you[- ](?:feel|can)", r"\bpwy[cw]\b",
          r"sliding[- ]scale", r"\bsuggested\s+donation\b", r"\bby\s+donation\b", r"\bdonation[- ]based\b",
          r"\bprix\s+libre\b", r"\bparticipation\s+libre\b", r"\ba\s+la\s+gorra\b", r"\btaquilla\s+inversa\b",
          r"\bpay\s+if\s+you\s+can\b", r"\bif\s+you\s+can\s+pay\b")

DISCOUNT = rx(
    r"\b\d{1,2}\s?%\s?(?:off|discount|de\s+(?:réduction|remise|descuento)|rabatt|sconto)\b", r"(?<![\w-])-\s?\d{1,2}\s?%",
    r"\bhalf[- ](?:price|off|priced)\b", r"\b(?:2|two)[- ]for[- ](?:1|one)\b", r"\b2\s?x\s?1\b", r"\bbogo\b",
    r"\bbuy\s+one,?\s+get\s+one\b", r"\bdiscount(?:s|ed)?\b", r"\breduced\s+(?:price|prices|admission|entry|rate|rates|tickets?)\b",
    r"\bearly[- ]?bird\b", r"\b(?:student|day[- ]of)\s+rush\b", r"\brush\s+tickets?\b", r"\b(?:ticket\s+)?lottery\s+tickets?\b",
    r"\b(?:promo|discount|coupon)\s+code\b", r"\buse\s+(?:the\s+)?code\b", r"\bcoupons?\b", r"\bspecial\s+offers?\b",
    r"\b(?:students?|seniors?|military|veterans?|teachers?|members?|kids?|children|under[- ]\d+s?)\s+(?:discount|price|pricing|rates?|tickets?)\b",
    r"(?:£|\$|€)\s?\d+(?:\.\d\d)?\s*(?:\(|/|,)?\s*(?:concs?\.?|concessions?)\b", r"\bconcessions?\s*[:\-]?\s*(?:£|\$|€)\s?\d",
    r"\bcheap\s+(?:seats|tickets|drinks|eats)\b",
    r"\b(?:ticket|drink|food|lunch|dinner|daily|special|package|group)\s+deals?\b",
    r"\bréduction\b(?!\s+(?:des?\s+(?:risques|stress|déchets)))", r"\d+\s?%\s+de\s+remise\b", r"\bremise\s+de\s+\d",
    r"\btarif\s+r[ée]duit\b", r"\bdemi[- ]tarif\b", r"\b2\s+pour\s+1\b", r"\bbon\s+plan\b",
    r"\bdescuentos?\b", r"\bprecio\s+reducido\b", r"\bentrada\s+reducida\b", r"\bpromoci[oó]n\b",
    r"\brabatt\b", r"\berm[äa]s?(?:s|ß)igt\b", r"\bsonderangebot\b", r"\bzum\s+halben\s+preis\b",
    r"\bscont[oi]\b", r"\bridott[oa]\b",
    r"割引|半額|お得", r"할인|반값",
)
# Boilerplate that CONTAINS a discount word, removed before DISCOUNT is tested. Each
# phrase carries the token itself, so removing it is exactly "this match does not
# count" - which is also the only form the SQL port (0227) can express.
DISCOUNT_NEG = rx(r"\bgroup\s+(?:discounts?|rates?)\b", r"\bdiscount(?:s|ed)?\s+to\s+face\s+value\b",
                  r"\b(?:wic|fmnp|snap)\b[^.]{0,40}coupons?", r"\bcoupons?\s+for\s+wic\b",
                  r"\bnot\s+(?:be\s+)?discounted\b", r"\bno\s+discounts?\b")

HAPPY = rx(r"\bhappy\s*[- ]?hours?\b", r"\bhora\s+feliz\b", r"\bdrink\s+specials?\b", r"\bdrinks?\s+deals?\b",
           r"(?:\$|£|€)\s?\d+(?:\.\d\d)?\s+(?:drinks?|beers?|pints?|cocktails?|margs?|margaritas?|wines?|tacos?|oysters?|wings?|slices?|burgers?|shots?|well\s+drinks?|mimosas?|apps?|appetizers?|pitchers?)\b",
           r"\b(?:taco|wing|burger|oyster|pizza)\s+(?:monday|tuesday|wednesday|thursday|night)s?\b",
           r"\bbottomless\s+(?:mimosas?|brunch|drinks?)\b",
           r"\bhalf[- ]price\s+(?:wine|bottles?|apps?|appetizers?|pizzas?|burgers?|drinks?|oysters?)\b", r"\bindustry\s+night\b",
           r"ハッピーアワー")
PRICE_TOKEN = re.compile(r"(?:\$|£|€|¥|￥)\s?\d|\d\s?(?:€|eur\b)|\b\d{1,2}\s?%\s?off\b|\bhalf[- ](?:price|off)|1/2\s*(?:off|price)|"
                         r"\bbogo\b|\b2[- ]for[- ]1\b|\b2\s?x\s?1\b|\bspecials?\b|\bdeals?\b|\boffers?\b|\bpriced\b|\bcheap\b|"
                         r"\bdiscount|\bschooners?\b|\bpints?\s+(?:for|at)\b", I)

INTRO = rx(r"\b(?:first|1st|trial|intro(?:ductory)?|taster)\s+(?:class|lesson|session|visit|week|month|workout|attendance|ride|climb|dance)\s+(?:is\s+)?(?:free|on\s+us|gratis|just\s+(?:\$|£|€)\s?\d+|half)",
           # "free first" needs its noun: bare, it was 4 of 4 wrong in the 72,204-event
           # sample ("Free First Thursday" at the Burke, "Free, first come, first served").
           r"\bfree\s+(?:trial|intro(?:ductory)?|taster)\s+(?:class|lesson|session|week|visit)?",
           r"\bfree\s+first\s+(?:class|lesson|session|week|visit)\b",
           r"\bnew\s+(?:students?|members?|customers?|clients?)\s+[^.]{0,40}?(?:free|\$\s?\d+|£\s?\d+|€\s?\d+|\d+\s?%\s?off)",
           r"\bprimera\s+clase\s+(?:gratis|gratuita)\b", r"\bclase\s+de\s+prueba\s+gratis\b", r"\bcours\s+d'essai\s+gratuit\b",
           r"\bpremier\s+cours\s+(?:gratuit|offert)\b", r"\bprobetraining\b", r"\bprobestunde\b", r"\bschnupper(?:stunde|training|kurs)\b")

FLEA = rx(r"\bflea\s+markets?\b", r"\bswap\s+meets?\b", r"\bvide[- ]greniers?\b", r"\bbraderies?\b", r"\bbrocantes?\b",
          r"\bflohm[äa]rkte?\b", r"\brastro\b", r"\bmercadillos?\b", r"\bmercatin[oi]\b",
          r"\b(?:yard|garage|rummage|estate|stoop|car[- ]boot|boot)\s+sales?\b",
          r"\bthrift\s+(?:market|sale|fair)s?\b", r"\bclothing\s+swaps?\b")
SALE = rx(r"\bsample\s+sales?\b", r"\bwarehouse\s+sales?\b", r"\bclearance\b", r"\bclosing[- ]down\s+sale\b",
          r"\bgoing\s+out\s+of\s+business\b", r"\bliquidation\b",
          r"\b(?:book|plant|vinyl|record|seed|bake|art|pottery|craft|holiday|tent|sidewalk|end[- ]of[- ]season|annual)\s+sales?\b",
          r"\bsoldes\b", r"\bd[ée]stockage\b", r"\bventes?\s+priv[ée]es?\b", r"\bbuchverkauf\b", r"\bb[üu]cherflohmarkt\b",
          r"セール", r"세일")
SALE_TITLE = re.compile(r"(?<!on\s)(?<!pre-)(?<!pre)\bsales?\b(?!\s+(?:tax|team|pitch|funnel|training|skills|kickoff|meetup|ops|leaders|networking))", I)
SALE_NEG = re.compile(r"\b(?:on|pre)[- ]?sale\b|\bpresales?\b|\ball\s+sales\s+(?:are\s+)?final\b|\bsales\s+final\b|\bpublic\s+on\s*sale", I)

MONEY = re.compile(r"(?:(?:US|A|C|NZ)?\$|£|€)\s?(\d{1,4}(?:[.,]\d{2})?)(?!\s?(?:k|m|million|billion|bn)\b)|(\d{1,4}(?:[.,]\d{2})?)\s?(?:€|eur\b|euros?\b)", I)
MONEY_CODE = re.compile(r"\b(?:USD|CAD|AUD|NZD|GBP|EUR|CHF)\s?(\d{1,4}(?:[.,]\d{2})?)\b")
MONEY_BIG = re.compile(r"[¥￥₩]\s?\d|\d\s?(?:円|원)")   # JPY/KRW: priced, never "cheap"

DEAL = {"free", "pwyw", "discount", "happyhr", "intro", "sale"}
NONFREE_DEAL = DEAL - {"free"}


def body_text(r):
    """Title + the free-text part of the description. The aggregator appends a
    machine tail (📍 address, Tickets / info, 🔎 search, 🎵 listen) that never
    carries a price; Ticketmaster's "Approx. price:" line sits ABOVE it."""
    d = r.get("description") or ""
    body = d.split("\n\n📍")[0]
    body = re.split(r"\n\nTickets / info:|\n\n🔎 More on this|\n\n🎵 Listen|\n\n📺 Watch", body)[0]
    return (r.get("title") or "") + "\n" + body


def not_this_price(s):
    """Strip the phrases where "free" is an intro offer, a perk or a subset."""
    return FREE_COND.sub(" ", FREE_PERK.sub(" ", INTRO.sub(" ", s)))


def classify(r):
    """The offer tags. MIRRORED BY public.event_offers() in migration 0227: change
    one and change the other, then re-run the parity run the migration describes."""
    t = r.get("title") or ""
    txt = body_text(r)
    c = set()
    if not FREE_NEG.search(txt) and (FREE.search(not_this_price(txt)) or FREE_TITLE.search(not_this_price(t))):
        c.add("free")
    if PWYW.search(txt):
        c.add("pwyw")
    if DISCOUNT.search(DISCOUNT_NEG.sub(" ", txt)):
        c.add("discount")
    if HAPPY.search(txt) and PRICE_TOKEN.search(txt):
        c.add("happyhr")
    if INTRO.search(txt):
        c.add("intro")
    if FLEA.search(txt):
        c.add("flea")
    if SALE.search(SALE_NEG.sub(" ", txt)) or SALE_TITLE.search(SALE_NEG.sub(" ", t)):
        c.add("sale")
    amts = [float((m.group(1) or m.group(2)).replace(",", ".")) for m in MONEY.finditer(txt)]
    amts += [float(m.group(1).replace(",", ".")) for m in MONEY_CODE.finditer(txt)]
    if amts or MONEY_BIG.search(txt):
        c.add("priced")
    pos = [a for a in amts if a > 0]
    if pos and min(pos) <= 10:
        c.add("cheap")
    return c, txt



_NAMES = ("FREE", "FREE_TITLE", "FREE_NEG", "FREE_PERK", "FREE_COND", "PWYW", "DISCOUNT", "DISCOUNT_NEG",
          "HAPPY", "PRICE_TOKEN", "INTRO", "FLEA", "SALE", "SALE_TITLE", "SALE_NEG", "MONEY", "MONEY_CODE",
          "MONEY_BIG")


def twin_path():
    """../mapsee/tools/measure_deals.py beside this checkout or beside any
    directory above it (a worktree sits inside the checkout), or None."""
    here = os.path.dirname(os.path.abspath(__file__))
    d = here
    while True:
        p = os.path.join(os.path.dirname(d), "mapsee", "tools", "measure_deals.py")
        if os.path.exists(p):
            return p
        if os.path.dirname(d) == d:
            return None
        d = os.path.dirname(d)


def twin_drift():
    """The pattern names that differ from the twin's, [] when none, or None when
    the twin is not checked out."""
    p = twin_path()
    if p is None:
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("_measure_deals_twin", p)
    md = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(md)
    g = globals()
    return [n for n in _NAMES
            if (getattr(md, n).pattern, getattr(md, n).flags) != (g[n].pattern, g[n].flags)]


# The six tags 0227 writes (classify() above also says priced, cheap and flea,
# which the trigger does not store), as the adapters' tests ask for them.
TAGS_0227 = ("free", "pwyw", "discount", "happyhr", "intro", "sale")


def tags(title, description):
    """The offer tags 0227 would store for this title and description."""
    c, _ = classify({"title": title, "description": description})
    return {t for t in c if t in TAGS_0227}


def tagged_free(title, description):
    """Would 0227 tag this row free (and so put it on /c/<city>/free)?"""
    return "free" in tags(title, description)


drift = twin_drift
