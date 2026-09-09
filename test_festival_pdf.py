import io
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from festival_pdf import FestivalPdfError, parse_jackson_pdf


HERE = Path(__file__).parent


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def synthetic_pdf():
    # Factual strings at measured template positions; the source PDF itself is
    # intentionally not checked into this public repository.
    rows = (
        (80, 440, "Red Rose Jazz Combo 4:30-6:00"), (80, 230, "Jose Juicy Gonzales Trio 7-9:00"),
        (154, 315, "The Heather Ward Quartet 6-8:00"),
        (229, 440, "Francesco Crosara Quartet 4:30-6:00"), (229, 230, "The New Triumph 6:30-8:30"), (229, 110, "The D'Vonne Lewis Quartet 9-10:30"),
        (301, 435, "5:00 WELCOME Eugenie Jones"), (301, 390, "David Holden Grown Foux 5:10-6:00"), (301, 320, "PIVOT DANCE EMPORIUM Moving to Jazz | 6:00"), (301, 265, "David Holden Grown Foux 6:30-8:00"),
        (377, 438, "Jazz Therapy 4:30-6:00"), (377, 265, "The D'Aaron Trio Special Guest Jazz Master Julian Priester 6:30-8:00"),
        (459, 320, "The Frank Kohl Trio 6-7:30"),
        (529, 360, "Sarah Shea & Chez Jazz 5-6:30"), (529, 198, "Eugenie Jones 7:30-9:00"),
        (604, 400, "The Joe Brazil Legacy Band 5-6:30"), (604, 225, "The Ari Joshua Quartet 7-9:00"),
        (684, 440, "Serica 4:30-6:00"), (684, 225, "The E. Pruitt Band 7-9:00"),
        (763, 400, "THE LISTENING ROOM Nathan Breedlove Sacred Conversations with a Jazz Master 6-7:00"),
        (847, 440, "Manazma Sheen 4:30-6:30"), (847, 225, "Alex Dugdale FADE-Tet 7-9:00"),
        (921, 400, "Sheila Kay & Friends 5-6:30"), (921, 225, "John Pinetree & The Yellin' Degenerates 7-9:00"),
    )
    writer = PdfWriter()
    page = writer.add_blank_page(1008, 612)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    commands = ["BT /F1 18 Tf 220 574 Td (13th ANNUAL JACKSON STREET JAZZ WALK | 12 Sept. 2026 | Seattle, WA) Tj ET"]
    headers = ("Casa Latina Conf Room", "Cheeky Cafe", "Wonder Ethiopian Restaurant Bar", "Pratt Fine Arts Center Plaza Mural Stage", "Pratt Fine Arts Center Plaza Court Stage", "Bell Jackson Apartments Atrium", "Bell Jackson Apartments Rooftop", "F45 Fitness Center", "Jackson St Apartments Street Stage", "Jackson St Apartments Network Lounge", "Jackson St Apartments Bruce Lee Lounge", "Central Area Sr Center Green Dolphin Rm")
    for x, text in zip((80, 154, 229, 301, 377, 459, 529, 604, 684, 763, 847, 921), headers):
        commands.append(f"BT /F1 5 Tf {x} 530 Td ({text}) Tj ET")
    for x, y, text in rows:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"BT /F1 9 Tf {x} {y} Td ({escaped}) Tj ET")
    stream = StreamObject()
    stream.set_data("\n".join(commands).encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    target = io.BytesIO()
    writer.write(target)
    return target.getvalue()


pdf = synthetic_pdf()
result = parse_jackson_pdf(pdf)
check(result["date"] == "2026-09-12", "date comes from the header")
check(len(result["agenda"]) == 24, "all 24 published blocks are present")
check(result["start"].startswith("2026-09-12T16:30:00-07:00"), "earliest published start")
check(result["end"].startswith("2026-09-12T22:30:00-07:00"), "latest published end")
check(any(i["title"] == "Welcome: Eugenie Jones" and "T17:00" in i["at"] for i in result["agenda"]), "welcome")
check(any("PIVOT DANCE EMPORIUM" in i["title"] and "T18:00" in i["at"] for i in result["agenda"]), "Pivot Dance")
check(len({i["id"] for i in result["agenda"]}) == 24, "stable IDs are unique")
check(all(set(i) >= {"id", "at", "title", "place"} for i in result["agenda"]), "agenda schema")

try:
    parse_jackson_pdf(pdf[:1000])
except FestivalPdfError:
    pass
else:
    raise AssertionError("truncated input must fail")

live = HERE / "tmp" / "festivals" / "schedule.pdf"
if live.exists():
    check(len(parse_jackson_pdf(live.read_bytes())["agenda"]) == 24, "live PDF completeness")

print("festival PDF parser: 24 complete agenda items, date/time/title/place and strict layout checks OK")
