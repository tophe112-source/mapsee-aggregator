"""Conservative admission facts from already downloaded publisher fields.

Unknown and partial prices stay unknown; restricted offers cannot claim public
free entry. This module never fetches a detail page or infers currency from a venue.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any, Optional

_CONDITIONAL = re.compile(
    r"\b(?:members?[- ]only|(?:free|complimentary)\s+(?:only\s+)?for\s+"
    r"(?:members?|cardholders?|subscribers?|students?|children|kids|under\b|"
    r"residents?|seniors?|adults?\s+ages|ages?\s+\d+|city residents?)|"
    r"complimentary\s+to\s+(?:members?|seniors?|residents?)|"
    r"(?:first|introductory)\s+(?:class|lesson|visit)\s+(?:is\s+)?free|"
    r"free\s+(?:with\s+(?:a\s+)?(?:purchase|paid)|before\s+\d+))\b", re.I)
_CONDITIONAL_NAME = re.compile(
    r"\b(?:members?|cardholders?|subscribers?|students?|first class|introductory|"
    r"early bird|under\s+\d+|children under|kids under)\b", re.I)
_RESTRICTED_ADMISSION = re.compile(
    r"\bfree\s+(?:(?:general\s+)?(?:admission|entry|entrance)|to\s+(?:attend|enter|join))?"
    r"\s*(?:for|to)\s+(?:\w+\s+){0,4}(?:members?|cardholders?|subscribers?|students?|"
    r"seniors?|residents?|veterans?|military)\b|"
    r"\b(?:members?|cardholders?|students?|seniors?|children|kids)\s+(?:get\s+in\s+|enter\s+)?free\b|"
    r"\b(?:free\s+(?:first|trial|introductory)\s+(?:class|lesson|session)|"
    r"(?:admission|entry)\s+requires?\s+(?:a\s+)?purchase)\b|"
    r"\br[ée]serv[ée]e?s?\s+aux\s+(?:membres|abonn[ée]s)\b", re.I)


def _restricted(text: str) -> bool:
    return bool(_CONDITIONAL.search(text) or _RESTRICTED_ADMISSION.search(text))
_FREE = re.compile(r"^(?:free(?:\s+(?:admission|entry|to attend))?|no charge)$", re.I)
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_COST = re.compile(r"^(?:(?P<currency>[A-Z]{3})\s*)?(?P<symbol>[$€£])?\s*"
                   r"(?P<price>\d+(?:\.\d+)?)(?:\s*(?P<tail>[A-Z]{3}))?$", re.I)


def _currency(value: Any) -> Optional[str]:
    return value.strip().upper() if isinstance(value, str) and re.fullmatch(
        r"[A-Za-z]{3}", value.strip()) else None


def _number(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool) or value is None:
        return None
    value = str(value).strip()
    if len(value) > 32 or not _NUMBER.fullmatch(value):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def _amount(value: Decimal) -> str:
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else str(value)


def normalize_admission_facts(raw: Any, *, url: Optional[str] = None,
                              currency_hint: Optional[str] = None,
                              context: str = "") -> Optional[dict]:
    """Return exact public admission facts, or None when evidence is incomplete.

    Every published offer must have a price to establish universal free entry.
    Mixed prices explicitly veto a global free label but do not invent one price.
    Traversal is bounded so pathological structured data cannot consume a run.
    """
    if _restricted(context or ""):
        return {"free": False, "restricted": True}
    prices = []
    visited = 0
    restricted = False

    def walk(value, inherited_currency=None, depth=0):
        nonlocal visited, restricted
        visited += 1
        if visited > 64 or depth > 6:
            return False
        if isinstance(value, list):
            if not value:
                return False
            complete = True
            for item in value:
                # Do not let an incomplete sibling hide a known positive-price
                # offer later in the list. That price alone vetoes global free.
                if visited >= 64:
                    complete = False
                    break
                complete = walk(item, inherited_currency, depth + 1) and complete
            return complete
        if isinstance(value, dict):
            # Eligible audiences or a named discounted tier cannot establish
            # admission available to everybody.
            if any(value.get(k) for k in ("eligibleCustomerType", "eligibleRegion", "eligibleQuantity")):
                restricted = True
                return False
            if _CONDITIONAL_NAME.search(str(value.get("name") or "")):
                restricted = True
                return False
            if _restricted(" ".join(str(value.get(k) or "") for k in
                                    ("description", "category", "disambiguatingDescription"))):
                restricted = True
                return False
            currency = _currency(value.get("priceCurrency")) or inherited_currency
            availability = value.get("availability")
            complete = availability is None or isinstance(availability, str)
            if availability is not None and not isinstance(availability, str):
                availability = None
            own = False
            if "price" in value:
                number = _number(value["price"])
                if number is None:
                    complete = False
                else:
                    prices.append((number, currency, availability))
                    own = True
            if "lowPrice" in value or "highPrice" in value:
                low, high = _number(value.get("lowPrice")), _number(value.get("highPrice"))
                if low is None or high is None:
                    complete = False
                    # One published positive bound is enough to veto global
                    # free, even if its AggregateOffer sibling is missing or
                    # malformed. Keep it only as evidence; never emit a range.
                    for bound in (low, high):
                        if bound is not None:
                            prices.append((bound, currency, availability))
                            own = True
                elif low > high:
                    complete = False
                    prices.extend(((low, currency, availability), (high, currency, availability)))
                else:
                    prices.extend(((low, currency, availability), (high, currency, availability)))
                    own = True
            for key in ("priceSpecification", "offers"):
                if key in value:
                    if not walk(value[key], currency, depth + 1):
                        complete = False
                    else:
                        own = True
            return own and complete
        if isinstance(value, str) and _FREE.fullmatch(value.strip()):
            prices.append((Decimal(0), inherited_currency, None))
            return True
        if isinstance(value, str):
            match = _COST.fullmatch(value.strip())
            if not match:
                return False
            if match["currency"] and match["tail"] and match["currency"].upper() != match["tail"].upper():
                return False
            currency = _currency(match["currency"]) or _currency(match["tail"]) or inherited_currency
            # Dollar is shared across currencies, and pound signs are also used
            # for currencies such as EGP and LBP. Only € identifies one currency.
            symbol_currency = {"€": "EUR"}.get(match["symbol"])
            if symbol_currency and currency and symbol_currency != currency:
                return False
            currency = currency or symbol_currency
            number = _number(match["price"])
        else:
            number, currency = _number(value), inherited_currency
        if number is None:
            return False
        prices.append((number, currency, None))
        return True

    complete = walk(raw, _currency(currency_hint))
    if not complete or not prices:
        if any(price > 0 for price, _, _ in prices):
            return {"free": False, **({"restricted": True} if restricted else {})}
        return {"free": False, "restricted": True} if restricted else None
    free = all(price == 0 for price, _, _ in prices)
    facts = {"free": free}
    amounts = {price for price, _, _ in prices}
    currencies = {currency for _, currency, _ in prices}
    if len(amounts) == 1 and (free or (len(currencies) == 1 and None not in currencies)):
        offer = {"price": _amount(prices[0][0])}
        if len(currencies) == 1 and None not in currencies:
            offer["currency"] = prices[0][1]
        if isinstance(url, str) and url.strip():
            offer["url"] = url.strip()
        statuses = {status for _, _, status in prices}
        if len(statuses) == 1 and None not in statuses:
            offer["availability"] = prices[0][2]
        facts["offer"] = offer
    return facts


def admission_description(description: Optional[str], facts: Optional[dict]) -> Optional[str]:
    """Put admission evidence before prose truncation and text offer tagging."""
    if not isinstance(facts, dict) or not isinstance(facts.get("free"), bool):
        return description
    marker = ("Free to attend." if facts["free"] else
              "Not a free public admission offer. Eligibility restrictions apply." if facts.get("restricted") else
              "Some admission options are not free.")
    prose = (description or "").strip()
    for previous in ("Free to attend.", "Some admission options are not free.",
                     "Not a free public admission offer. Eligibility restrictions apply."):
        if prose.startswith(previous):
            prose = prose[len(previous):].lstrip()
            if previous == "Some admission options are not free.":
                prose = re.sub(r"^Admission: [A-Z]{3} \d+(?:\.\d+)?\.\s*", "", prose, count=1)
    offer = facts.get("offer") or {}
    if not facts["free"] and offer.get("price") and offer.get("currency"):
        marker += f" Admission: {offer['currency']} {offer['price']}."
    return marker + ("\n\n" + prose if prose else "")
