"""Admission regression cases: no inferred public eligibility or missing prices."""
from mapsee_admission import normalize_admission_facts as facts, admission_description


def main():
    cases = 0
    def check(raw, expected, **kwargs):
        nonlocal cases
        actual = facts(raw, **kwargs)
        assert actual == expected, (raw, kwargs, actual, expected)
        cases += 1

    for raw in (0, "0", "0.00", "$0.00", "Free", "Free admission", "no charge"):
        check(raw, {"free": True, "offer": {"price": "0"}})
    check({"price": "0", "priceCurrency": "USD"},
          {"free": True, "offer": {"price": "0", "currency": "USD", "url": "https://example.org/event"}},
          url="https://example.org/event")
    check([{ "price": 0 }, { "price": "0.0" }], {"free": True, "offer": {"price": "0"}})
    check([{"price": 0}, {"price": 10}], {"free": False})
    check([{}, {"price": 10}], {"free": False})
    check([{"price": 10}, {}], {"free": False})
    check({"offers": [{"price": 0}, {"price": 10}, {}]}, {"free": False})
    check({"price": 0, "offers": [{"price": 10}, {}]}, {"free": False})
    check({"price": 10, "availability": []}, {"free": False})
    check({"lowPrice": 0, "highPrice": 10}, {"free": False})
    check({"lowPrice": 10, "highPrice": 0}, {"free": False})
    check({"lowPrice": 0, "highPrice": 0}, {"free": True, "offer": {"price": "0"}})
    check({"lowPrice": 10}, {"free": False})
    check({"highPrice": 10}, {"free": False})
    check({"lowPrice": 10, "highPrice": "unknown"}, {"free": False})
    check({"lowPrice": 0, "highPrice": "unknown"}, None)
    check({"priceSpecification": {"price": 0}, "priceCurrency": "EUR"},
          {"free": True, "offer": {"price": "0", "currency": "EUR"}})
    check({"offers": [{"price": 0}, {"price": 0}]}, {"free": True, "offer": {"price": "0"}})
    check({"price": "12.50", "priceCurrency": "USD",
           "availability": "https://schema.org/InStock",
           "validFrom": "2026-10-01T09:30:00-07:00"},
          {"free": False, "offer": {"price": "12.5", "currency": "USD",
                                     "availability": "https://schema.org/InStock",
                                     "valid_from": "2026-10-01T09:30:00-07:00"}})
    for invalid_date in (None, True, "2026-10-01", "2026-02-30T09:30:00Z",
                         "2026-10-01T09:30:00", "2026-10-01T09:30:00+99:00"):
        check({"price": 12, "priceCurrency": "USD", "validFrom": invalid_date},
              {"free": False, "offer": {"price": "12", "currency": "USD"}})
    check({"offers": [{"price": 12, "priceCurrency": "USD",
                        "validFrom": "2026-10-01T09:30:00Z"},
                       {"price": 12, "priceCurrency": "USD",
                        "validFrom": "2026-10-02T09:30:00Z"}]},
          {"free": False, "offer": {"price": "12", "currency": "USD"}})
    check({"offers": [{"price": 12, "priceCurrency": "USD",
                        "validFrom": "2026-10-01T09:30:00Z"},
                       {"price": 12, "priceCurrency": "USD"}]},
          {"free": False, "offer": {"price": "12", "currency": "USD"}})
    check("USD 10.00", {"free": False, "offer": {"price": "10", "currency": "USD"}})
    check("€5", {"free": False, "offer": {"price": "5", "currency": "EUR"}})
    check("£5", {"free": False})
    check("£5", {"free": False, "offer": {"price": "5", "currency": "GBP"}}, currency_hint="GBP")
    check("GBP £5", {"free": False, "offer": {"price": "5", "currency": "GBP"}})
    check("$10", {"free": False})
    check("$10", {"free": False, "offer": {"price": "10", "currency": "CAD"}}, currency_hint="CAD")
    for raw in (None, "", "Free parking", "Free with purchase", "Free / Donation", "$0–10", "USD 0 EUR",
                True, False, -1, "NaN", "Infinity", [], {}, {"price": None}, {"lowPrice": 0},
                [{"price": 0}, {}], [{"price": 0}, {"price": ""}],
                "9" * 33):
        check(raw, None)
    for raw in ({"price": 0, "eligibleCustomerType": "Member"},
                {"price": 0, "name": "Student"}, {"price": 0, "name": "Under 18"},
                {"price": 0, "description": "Free for members only"},
                {"price": 0, "description": "Free for ages 18 and under"},
                {"price": 0, "category": "Free for city residents"},
                {"price": 0, "disambiguatingDescription": "Complimentary to seniors"}):
        check(raw, {"free": False, "restricted": True})
    for context in ("Free for members", "Members only", "First class free", "Free with a purchase",
                    "Free for children under 5", "Free before 6 pm", "Free before 18:00", "Free to attend for MSL members",
                    "Free general admission for Bank of America cardholders", "Students get in free",
                    "Free first class", "Admission requires a purchase", "Réservé aux membres",
                    "first 100 adult tickets FREE, kids always free!", "Club party FREE/Lady",
                    "FREE ENTRY WITH RSVP UNTIL 11:30pm $10 ON THE DOOR FROM 11:30pm",
                    "Wear a German-themed outfit and join for free!",
                    "There is no cover. Food or drink purchase at the bar then proceed to meeting area."):
        check(0, {"free": False, "restricted": True}, context=context)
    check(0, {"free": True, "offer": {"price": "0"}}, context="Members and nonmembers welcome")
    deep = {"price": 0}
    for _ in range(8):
        deep = {"offers": deep}
    check(deep, None)
    check([{"price": 0}] * 65, None)
    prose = "Description " * 100
    assert admission_description(prose, {"free": True})[:15] == "Free to attend."
    assert "not free" in admission_description("Free to attend.\n\n" + prose, {"free": False})[:40]
    assert admission_description("Free to attend.\n\nText", {"free": True}) == "Free to attend.\n\nText"
    assert admission_description(None, None) is None
    assert "Admission: EUR 10." in admission_description("Text", facts("EUR 10"))
    paid_prose = admission_description("Text", facts("EUR 10"))
    assert admission_description(paid_prose, facts("EUR 10")) == paid_prose
    assert "EUR 10" not in admission_description(paid_prose, facts("EUR 12"))
    print(f"Admission: {cases + 7} checks passed")


if __name__ == "__main__":
    main()
