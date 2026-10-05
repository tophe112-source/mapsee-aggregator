#!/usr/bin/env python3
"""
test_ingest_barcelona.py - Barcelona's city agenda, read for its community
places, and the ways a row in it is not what it looks like.

AGENDA rows were read from the Ajuntament's CKAN datastore on 2026-10-03
(resource 877ccf66, trimmed to the columns barcelona_sources.json asks for);
FACILITIES from the culture-and-leisure register (f3721b17) on 2026-10-05.
Synthetic cases are built in code and say so. The expensive failures are the
quiet ones: a session written at the 03:00 placeholder clock, one row from
10:00 to 18:00 over the lunch gap, a Tuesday group on the 8 December holiday,
L'Auditori's symphony season or a museum's show filed under a "library", a
free talk that needs a sign-up written as a drop-in, a priced workshop
written as a session, and a price line 0227 reads as free.

    python test_ingest_barcelona.py
"""
import json
import os
import random
import re
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["MAPSEE_TODAY"] = "20261005"

from datetime import date, timedelta  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import mapsee_ingest_barcelona as T  # noqa: E402
import mapsee_supabase_sync as S  # noqa: E402
from mapsee_ingest import EventStore, VALID_CATEGORIES  # noqa: E402
from mapsee_supabase_sync import derive_categories  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
CFG = T.load_config(os.path.join(HERE, "barcelona_sources.json"))
SRC = CFG["sources"][0]
TZ = ZoneInfo(CFG["timezone"])
TODAY = T._today(TZ)

# ../mapsee migration 0227's free test, a SUBSET of tools/measure_deals.py's
# FREE (the alternatives these rows can hit) and its FREE_NEG veto. Catalan
# "gratuita" with its diaeresis is NOT read; that is why the adapter writes
# English. The full classify on the 2026-10-05 dry run (1,666 rows, run through
# the sync's to_row) disagreed with the adapter on 0 rows.
FREE_TAG = re.compile(
    r"\bgratuit(?:e|s|es)?\b|\bgratuit[ao]s?\b|\bgratis\b|\b(?:entrada|acceso|ingreso)\s+(?:libre|gratuit[ao])\b|"
    r"(?<![-\w/])free\s+(?:admission|entry|entrance|event|of\s+charge|to\s+(?:attend|all|everyone)|"
    r"for\s+(?:all|everyone|kids|children))\b|\b(?:admission|entry|entrance|cost|price)\s*(?:is|:|-)?\s*free\b|"
    r"\bis\s+free\b", re.I)
FREE_NEG = re.compile(r"\b(?:not|isn'?t|is\s+not)\s+(?:a\s+)?free\b|\bnon[- ]gratuit|\bpas\s+gratuit|\bno\s+es\s+gratis",
                      re.I)


def tagged_free(name, desc):
    text = f"{name}\n{desc}"
    return bool(FREE_TAG.search(text)) and not FREE_NEG.search(text)


AGENDA = json.loads(r'''[{"_id": 44, "register_id": "\ufeff99400760037", "name": "Concert \"Nonet de Martinu\"", "start_date": "2026-11-26T03:00:00", "end_date": "2026-11-26T03:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>Dijous</div></td><td class=\"timetable-hour\" rowspan=1><div>a les 19.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=1><div>Entrada general: 10 €                <p style=\"margin:0\">(+ despeses de gestió)</p></div></td></tr></table>", "geo_epgs_4326_lat": "41.39877242493477", "geo_epgs_4326_lon": "2.185159128100962", "addresses_road_id": "178308", "addresses_road_name": "C Lepant", "addresses_start_street_number": "150", "addresses_zip_code": "8013", "addresses_district_name": "Eixample", "values_category": "Telèfons", "values_attribute_name": "Informació", "values_value": "932479300"}, {"_id": 95, "register_id": "\ufeff99400750685", "name": "Narració 'Contes a la mà', a càrrec de Mar González", "start_date": "2026-10-27T03:00:00", "end_date": "2026-11-24T03:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th><th class=\"description\">Observacions</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>27 octubre i <br>24 novembre</div></td><td class=\"timetable-hour\" rowspan=1><div>a les 17.30&nbsp;h</div></td><td class=\"timetable-price\" rowspan=1><div>Entrada Gratuïta</div></td><td class=\"timetable-description\" rowspan=1><div><p style=\"margin:0\">Places per ordre d'arribada</p></div></td></tr></table>", "geo_epgs_4326_lat": "41.40541560676076", "geo_epgs_4326_lon": "2.1767746457391186", "addresses_road_id": "268003", "addresses_road_name": "Carrer de Provença", "addresses_start_street_number": "480", "addresses_zip_code": "8025", "addresses_district_name": "Eixample"}, {"_id": 101, "register_id": "\ufeff99400745169", "name": "Teatre \"Descontes\", a càrrec de la companyia Creart Guinardó", "start_date": "2026-11-24T03:00:00", "end_date": "2026-11-24T03:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>Dimarts</div></td><td class=\"timetable-hour\" rowspan=1><div>a les 19.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=1><div>Entrada Gratuïta</div></td></tr></table>", "geo_epgs_4326_lat": "41.418169158350054", "geo_epgs_4326_lon": "2.1744079393214104", "addresses_road_id": "294808", "addresses_road_name": "Plaça de Salvador Riera", "addresses_start_street_number": "2", "addresses_zip_code": "8041", "addresses_district_name": "Horta-Guinardó"}, {"_id": 309, "register_id": "\ufeff99400784171", "name": "Concert \"Clàssic BCN\"", "start_date": "2026-12-14T03:00:00", "end_date": "2026-12-14T03:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th><th class=\"description\">Observacions</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>Dilluns</div></td><td class=\"timetable-hour\" rowspan=1><div>a les 19.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=1><div>Entrada Gratuïta</div></td><td class=\"timetable-description\" rowspan=1><div><p style=\"margin:0\">Cal inscripció prèvia</p><p style=\"margin:0\">Places limitades</p></div></td></tr></table>", "geo_epgs_4326_lat": "41.39132547357072", "geo_epgs_4326_lon": "2.156771608434526", "addresses_road_id": "268003", "addresses_road_name": "Carrer de Provença", "addresses_start_street_number": "187", "addresses_zip_code": "8036", "addresses_district_name": "Eixample"}, {"_id": 671, "register_id": "\ufeff99400784569", "name": "Espai 'Sala d’Informàtica'", "start_date": "2026-09-14T03:00:00", "end_date": "2026-12-18T03:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>De dilluns a divendres</div></td><td class=\"timetable-hour\" rowspan=1><div>de 10.00&nbsp;h a 13.00&nbsp;h i <br>de 16.30&nbsp;h a 20.30&nbsp;h</div></td><td class=\"timetable-price\" rowspan=1><div>Entrada Gratuïta</div></td></tr></table>", "geo_epgs_4326_lat": "41.415869059363075", "geo_epgs_4326_lon": "2.206836204315639", "addresses_road_id": "143609", "addresses_road_name": "Carrer de Josep Pla", "addresses_start_street_number": "174", "addresses_zip_code": "8020", "addresses_district_name": "Sant Martí"}, {"_id": 1103, "register_id": "\ufeff99400748337", "name": "Exposició 'Tàpies, Portabella. Política de l’amistat'", "start_date": "2026-09-29T03:00:00", "end_date": "2027-02-07T03:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th><th class=\"description\">Observacions</th></tr><tr><td class=\"timetable-day\" rowspan=1><div><p style=\"margin:0\">De dimarts a dissabte</p></div></td><td class=\"timetable-hour\" rowspan=1><div>de 10.00&nbsp;h a 19.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=2><div>Entrada general: 12 € <p style=\"margin:0\"><br></p><p style=\"margin:0\"><strong>Reduïda</strong>:8 €</p><p style=\"margin:0\">- estudiants</p><p style=\"margin:0\">- majors de 65 anys</p><p style=\"margin:0\">-<span style=\"color: rgb(0, 0, 0);\"> carnet de Biblioteques</span></p><p style=\"margin:0\"><br></p><p style=\"margin:0\"><strong>Articket</strong>: 38 €</p><p style=\"margin:0\"><br></p><p style=\"margin:0\"><strong>Gratuït</strong>:</p><p style=\"margin:0\">- persones a l'atur</p><p style=\"margin:0\">- menors fins als 16 anys</p><p style=\"margin:0\">- carnet Amics del Museu Tàpies</p></div></td><td class=\"timetable-description\" rowspan=2><div>Cal concertar hora<br>per a les visites<br>comentades i de<br>grup (+ de 15 persones).</div></td></tr><tr><td class=\"timetable-day\" rowspan=1><div><p style=\"margin:0\">Diumenges</p><p style=\"margin:0\">Tancat 25 i 26 de desembre,</p><p style=\"margin:0\"> 1 i 6 de gener</p></div></td><td class=\"timetable-hour\" rowspan=1><div>de 10.00&nbsp;h a 15.00&nbsp;h</div></td></tr></table>", "geo_epgs_4326_lat": "41.39155351265378", "geo_epgs_4326_lon": "2.1637754588196367", "addresses_road_id": "18505", "addresses_road_name": "Carrer d'Aragó", "addresses_start_street_number": "255", "addresses_zip_code": "8007", "addresses_district_name": "Eixample"}, {"_id": 1566, "register_id": "\ufeff99400777314", "name": "Grup de ping pong", "start_date": "2026-09-17T12:00:00", "end_date": "2026-12-22T12:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th><th class=\"description\">Observacions</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>Dimarts i Divendres</div></td><td class=\"timetable-hour\" rowspan=1><div>a les 10.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=1><div>Entrada Gratuïta</div></td><td class=\"timetable-description\" rowspan=1><div><p style=\"margin:0\">Sense inscripció prèvia</p></div></td></tr></table>", "geo_epgs_4326_lat": "41.418169158350054", "geo_epgs_4326_lon": "2.1744079393214104", "addresses_road_id": "294808", "addresses_road_name": "Plaça de Salvador Riera", "addresses_start_street_number": "2", "addresses_zip_code": "8041", "addresses_district_name": "Horta-Guinardó"}, {"_id": 2638, "register_id": "\ufeff99400731363", "name": "Xerrada 'Què hauríem de saber sobre els ictus?' *NOU*", "start_date": "2026-10-21T03:00:00", "end_date": "2026-10-21T03:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th><th class=\"description\">Observacions</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>Dimecres</div></td><td class=\"timetable-hour\" rowspan=1><div>de 18.00&nbsp;h a 19.30&nbsp;h</div></td><td class=\"timetable-price\" rowspan=1><div>Entrada general: 8.75 €   <p style=\"margin:0\"><br></p><p style=\"margin:0\">Reducció i subvenció dels imports als cursos, informeu-vos en el mateix centre.</p></div></td><td class=\"timetable-description\" rowspan=1><div><p style=\"margin:0\">Cal inscripció prèvia</p><p style=\"margin:0\">Places limitades</p></div></td></tr></table>", "geo_epgs_4326_lat": "41.40541560676076", "geo_epgs_4326_lon": "2.1767746457391186", "addresses_road_id": "268003", "addresses_road_name": "Carrer de Provença", "addresses_start_street_number": "480", "addresses_zip_code": "8025", "addresses_district_name": "Eixample"}, {"_id": 4298, "register_id": "\ufeff99400786012", "name": "Gam de Dol. Associació Petits amb llum", "start_date": "2026-09-19T03:00:00", "end_date": "2026-12-19T03:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>19 setembre</div></td><td class=\"timetable-hour\" rowspan=4><div>de 10.00&nbsp;h a 14.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=4><div>Entrada Gratuïta</div></td></tr><tr><td class=\"timetable-day\" rowspan=1><div>24 octubre</div></td></tr><tr><td class=\"timetable-day\" rowspan=1><div>28 novembre</div></td></tr><tr><td class=\"timetable-day\" rowspan=1><div>19 desembre</div></td></tr></table>", "geo_epgs_4326_lat": "41.418169158350054", "geo_epgs_4326_lon": "2.1744079393214104", "addresses_road_id": "294808", "addresses_road_name": "Plaça de Salvador Riera", "addresses_start_street_number": "2", "addresses_zip_code": "8041", "addresses_district_name": "Horta-Guinardó"}, {"_id": 4949, "register_id": "\ufeff99400784554", "name": "Trobada 'Casal Persones Grans'", "start_date": "2026-09-14T03:00:00", "end_date": "2026-12-16T06:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>De dilluns a divendres</div></td><td class=\"timetable-hour\" rowspan=1><div>de 10.00&nbsp;h a 13.00&nbsp;h i <br>de 16.00&nbsp;h a 18.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=1><div>Entrada Gratuïta</div></td></tr></table>", "geo_epgs_4326_lat": "41.415869059363075", "geo_epgs_4326_lon": "2.206836204315639", "addresses_road_id": "143609", "addresses_road_name": "Carrer de Josep Pla", "addresses_start_street_number": "174", "addresses_zip_code": "8020", "addresses_district_name": "Sant Martí", "values_category": "Telèfons", "values_attribute_name": "Informació", "values_value": "644 66 99 43"}, {"_id": 5595, "register_id": "\ufeff99400762344", "name": "Exposició Out-of-map. Narratives feministes de l'espai públic", "start_date": "2026-10-08T03:00:00", "end_date": "2026-11-07T03:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th><th class=\"description\">Observacions</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>De dilluns a divendres excepte 12 octubre</div></td><td class=\"timetable-hour\" rowspan=1><div>de 09.00&nbsp;h a 21.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=3><div>Entrada Gratuïta</div></td><td class=\"timetable-description\" rowspan=3><div><p style=\"margin:0\">Inauguració: dijous 8 d 'octubre a les 18 h</p></div></td></tr><tr><td class=\"timetable-day\" rowspan=1><div>Dissabte</div></td><td class=\"timetable-hour\" rowspan=1><div>de 10.00&nbsp;h a 14.00&nbsp;h i <br>de 16.00&nbsp;h a 20.00&nbsp;h</div></td></tr><tr><td class=\"timetable-day\" rowspan=1><div>Diumenge excepte 1 novembre</div></td><td class=\"timetable-hour\" rowspan=1><div>de 10.00&nbsp;h a 14.00&nbsp;h</div></td></tr></table>", "geo_epgs_4326_lat": "41.40541560676076", "geo_epgs_4326_lon": "2.1767746457391186", "addresses_road_id": "268003", "addresses_road_name": "Carrer de Provença", "addresses_start_street_number": "480", "addresses_zip_code": "8025", "addresses_district_name": "Eixample"}, {"register_id": "\ufeff99400784576", "name": "Mostra de cinema emergent", "start_date": "2026-10-28T03:00:00", "end_date": "2026-12-16T06:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>28 octubre, <br>25 novembre i <br>16 desembre</div></td><td class=\"timetable-hour\" rowspan=1><div>de 19.00&nbsp;h a 20.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=1><div>Entrada Gratuïta</div></td></tr></table>", "geo_epgs_4326_lat": "41.376969254254014", "geo_epgs_4326_lon": "2.1672378540759167", "addresses_road_id": "277809", "addresses_road_name": "Carrer de la Reina Amàlia", "addresses_start_street_number": "31", "addresses_zip_code": "8001", "addresses_district_name": "Ciutat Vella", "values_category": "", "values_attribute_name": "", "values_value": ""}, {"register_id": "\ufeff99400786915", "name": "Teatre Musical \" De 9 a 5\"", "start_date": "2026-10-24T03:00:00", "end_date": "2026-11-14T03:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th></tr><tr><td class=\"timetable-day\" rowspan=1><div><p style=\"margin:0\">Dissabte 24 d'octubre</p></div></td><td class=\"timetable-hour\" rowspan=2><div>a les 12.00&nbsp;h i <br>a les 18.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=2><div><p style=\"margin:0\">Donatiu voluntari en benefici de la companyia</p></div></td></tr><tr><td class=\"timetable-day\" rowspan=1><div><p style=\"margin:0\">Dissabte 14 de novembre</p></div></td></tr></table>", "geo_epgs_4326_lat": "41.42755131086321", "geo_epgs_4326_lon": "2.1784411209301746", "addresses_road_id": "121402", "addresses_road_name": "Carrer de Felip II", "addresses_start_street_number": "222", "addresses_zip_code": "8027", "addresses_district_name": "Sant Andreu", "values_category": "", "values_attribute_name": "", "values_value": ""}, {"register_id": "\ufeff99400772215", "name": "Xerrada Espai Gent Gran 'Envellir amb sentit: com sumar vida als anys'", "start_date": "2026-10-26T12:00:00", "end_date": "2026-10-26T12:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>Dilluns</div></td><td class=\"timetable-hour\" rowspan=1><div>a les 18.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=1><div>Entrada Gratuïta</div></td></tr></table>", "geo_epgs_4326_lat": "41.46126807109291", "geo_epgs_4326_lon": "2.17989262234208", "addresses_road_id": "351606", "addresses_road_name": "Carrer de Vallcivera", "addresses_start_street_number": "3", "addresses_zip_code": "8033", "addresses_district_name": "Nou Barris", "values_category": "", "values_attribute_name": "", "values_value": ""}, {"register_id": "\ufeff99400775602", "name": "Concert Districte Musical Jove DMJ Gràcia", "start_date": "2026-09-18T03:00:00", "end_date": "2026-11-27T03:00:00", "timetable": "<table class=\"timetable-table\"><tr class=\"timetable-header\"><th class=\"weekdays\">Dies</th><th class=\"hours\">Hores</th><th class=\"prices\">Preus</th><th class=\"description\">Observacions</th></tr><tr><td class=\"timetable-day\" rowspan=1><div>18 setembre</div></td><td class=\"timetable-hour\" rowspan=1><div>a les 18.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=1><div>Entrada Gratuïta</div></td><td class=\"timetable-description\" rowspan=1><div><p style=\"margin:0\">Espai Jove La Fontana</p></div></td></tr><tr><td class=\"timetable-day\" rowspan=1><div>13 novembre</div></td><td class=\"timetable-hour\" rowspan=3><div>a les 21.00&nbsp;h</div></td><td class=\"timetable-price\" rowspan=3><div>Entrada Gratuïta</div></td><td class=\"timetable-description\" rowspan=1><div><p style=\"margin:0\">Casal de Joves del Coll</p></div></td></tr><tr><td class=\"timetable-day\" rowspan=1><div>20 novembre</div></td><td class=\"timetable-description\" rowspan=1><div><p style=\"margin:0\">CC La Sedeta</p></div></td></tr><tr><td class=\"timetable-day\" rowspan=1><div>27 novembre</div></td><td class=\"timetable-description\" rowspan=1><div><p style=\"margin:0\">Espai Jove Fontana</p></div></td></tr></table>", "geo_epgs_4326_lat": "41.40325591053634", "geo_epgs_4326_lon": "2.1523241627536023", "addresses_road_id": "206403", "addresses_road_name": "Carrer Gran de Gràcia", "addresses_start_street_number": "190", "addresses_zip_code": "8012", "addresses_district_name": "Gràcia", "values_category": "", "values_attribute_name": "", "values_value": ""}]''')

FACILITIES = json.loads(r'''[{"register_id": "\ufeff94109102254", "name": "Biblioteca", "addresses_road_id": "18505", "addresses_start_street_number": "255", "secondary_filters_name": "Biblioteques", "geo_epgs_4326_lat": "41.39155351265378", "geo_epgs_4326_lon": "2.1637754588196367"}, {"register_id": "\ufeff1206105105", "name": "Centre Cívic Sagrada Família", "addresses_road_id": "268003", "addresses_start_street_number": "480", "secondary_filters_name": "Centres cívics", "geo_epgs_4326_lat": "41.40541560676076", "geo_epgs_4326_lon": "2.1767746457391186"}, {"register_id": "\ufeff92086004112", "name": "Museu de la Música  -  L'Auditori", "addresses_road_id": "178308", "addresses_start_street_number": "150", "secondary_filters_name": "Museus", "geo_epgs_4326_lat": "41.3983980097949", "geo_epgs_4326_lon": "2.184885139917014"}, {"register_id": "\ufeff92086004112", "name": "Museu de la Música  -  L'Auditori", "addresses_road_id": "178308", "addresses_start_street_number": "150", "secondary_filters_name": "Museus municipals", "geo_epgs_4326_lat": "41.3983980097949", "geo_epgs_4326_lon": "2.184885139917014"}, {"register_id": "\ufeff99400707246", "name": "Persones Grans de Ca l'Isidret", "addresses_road_id": "143609", "addresses_start_street_number": "174", "secondary_filters_name": "Casals d'avis", "geo_epgs_4326_lat": "41.415848037532804", "geo_epgs_4326_lon": "2.2068153447737964"}, {"register_id": "\ufeff99400265137", "name": "Casal  Mas Guinardó", "addresses_road_id": "294808", "addresses_start_street_number": "2", "secondary_filters_name": "Casals de barri", "geo_epgs_4326_lat": "41.418169158350054", "geo_epgs_4326_lon": "2.1744079393214104"}, {"register_id": "\ufeff99400187152", "name": "Biblioteca La Sagrada Família – Josep Maria Ainaud de Lasarte", "addresses_road_id": "268003", "addresses_start_street_number": "480", "secondary_filters_name": "Biblioteques", "geo_epgs_4326_lat": "41.40541560676076", "geo_epgs_4326_lon": "2.1767746457391186"}, {"register_id": "\ufeff99400187152", "name": "Biblioteca La Sagrada Família – Josep Maria Ainaud de Lasarte", "addresses_road_id": "268003", "addresses_start_street_number": "480", "secondary_filters_name": "Biblioteques municipals", "geo_epgs_4326_lat": "41.40541560676076", "geo_epgs_4326_lon": "2.1767746457391186"}, {"register_id": "\ufeff93333143803", "name": "Espai de Documentació i Recerca: Biblioteca, Arxiu Històric i Fons Sonor", "addresses_road_id": "178308", "addresses_start_street_number": "150", "secondary_filters_name": "Arxius municipals", "geo_epgs_4326_lat": "41.3983980097949", "geo_epgs_4326_lon": "2.184885139917014"}, {"register_id": "\ufeff93333143803", "name": "Espai de Documentació i Recerca: Biblioteca, Arxiu Històric i Fons Sonor", "addresses_road_id": "178308", "addresses_start_street_number": "150", "secondary_filters_name": "Biblioteques", "geo_epgs_4326_lat": "41.3983980097949", "geo_epgs_4326_lon": "2.184885139917014"}, {"register_id": "\ufeff93333143803", "name": "Espai de Documentació i Recerca: Biblioteca, Arxiu Històric i Fons Sonor", "addresses_road_id": "178308", "addresses_start_street_number": "150", "secondary_filters_name": "Biblioteques municipals", "geo_epgs_4326_lat": "41.3983980097949", "geo_epgs_4326_lon": "2.184885139917014"}, {"register_id": "\ufeff98097151404", "name": "L'Auditori de Barcelona", "addresses_road_id": "178308", "addresses_start_street_number": "150", "secondary_filters_name": "Auditoris", "geo_epgs_4326_lat": "41.39877242493477", "geo_epgs_4326_lon": "2.185159128100962"}, {"register_id": "\ufeff98210124004", "name": "Casal Infantil Sagrada Família", "addresses_road_id": "268003", "addresses_start_street_number": "480", "secondary_filters_name": "Casals infantils", "geo_epgs_4326_lat": "41.405942852294395", "geo_epgs_4326_lon": "2.176606897474682"}, {"register_id": "\ufeff99400549066", "name": "Casal Ca l'Isidret", "addresses_road_id": "143609", "addresses_start_street_number": "174", "secondary_filters_name": "Casals de barri", "geo_epgs_4326_lat": "41.415869059363075", "geo_epgs_4326_lon": "2.206836204315639"}, {"register_id": "\ufeff92086021892", "name": "Museu Tàpies", "addresses_road_id": "18505", "addresses_start_street_number": "255", "secondary_filters_name": "Museus", "geo_epgs_4326_lat": "41.39155351265378", "geo_epgs_4326_lon": "2.1637754588196367"}, {"register_id": "\ufeff99400271344", "name": "Casal de Barri del Raval", "addresses_road_id": "277809", "addresses_start_street_number": "31", "secondary_filters_name": "Casals de barri", "geo_epgs_4326_lat": "41.376969254254014", "geo_epgs_4326_lon": "2.1672378540759167"}, {"register_id": "\ufeff99400514934", "name": "Centre Cívic Can Clariana Cultural", "addresses_road_id": "121402", "addresses_start_street_number": "222", "secondary_filters_name": "Centres cívics", "geo_epgs_4326_lat": "41.42755131086321", "geo_epgs_4326_lon": "2.1784411209301746"}, {"register_id": "\ufeff99400236720", "name": "Biblioteca Zona Nord - Mària Sánchez", "addresses_road_id": "351606", "addresses_start_street_number": "3", "secondary_filters_name": "Biblioteques municipals", "geo_epgs_4326_lat": "41.46126807109291", "geo_epgs_4326_lon": "2.17989262234208"}, {"register_id": "\ufeff92086004419", "name": "Centre Cívic La Sedeta", "addresses_road_id": "325005", "addresses_start_street_number": "321", "secondary_filters_name": "Centres cívics", "geo_epgs_4326_lat": "41.405576512012246", "geo_epgs_4326_lon": "2.167687591797475"}, {"register_id": "\ufeff99400010313", "name": "Casal Infantil Ciutat Meridiana", "addresses_road_id": "351606", "addresses_start_street_number": "3", "secondary_filters_name": "Casals infantils", "geo_epgs_4326_lat": "41.46086069284168", "geo_epgs_4326_lon": "2.1790156968437167"}, {"register_id": "\ufeff99400219575", "name": "Espai Jove La Fontana", "addresses_road_id": "206403", "addresses_start_street_number": "190", "secondary_filters_name": "Casals i espais per a joves", "geo_epgs_4326_lat": "41.40325591053634", "geo_epgs_4326_lon": "2.1523241627536023"}, {"register_id": "\ufeff76261104946", "name": "Espai Jove del Coll", "addresses_road_id": "106806", "addresses_start_street_number": "24", "secondary_filters_name": "Casals i espais per a joves", "geo_epgs_4326_lat": "41.41863651080554", "geo_epgs_4326_lon": "2.1497718264466146"}]''')


def row(rid):
    for r in AGENDA:
        if T._rid(r["register_id"]) == rid:
            return dict(r)
    raise KeyError(rid)


def tt(*lines):
    """A synthetic timetable: each line is (weekdays, hours, prices, notes)."""
    head = ('<table class="timetable-table"><tr class="timetable-header"><th class="weekdays">Dies</th>'
            '<th class="hours">Hores</th><th class="prices">Preus</th><th class="description">Observacions</th></tr>')
    body = "".join(f'<tr><td class="timetable-day" rowspan=1><div>{a}</div></td><td class="timetable-hour" rowspan=1>'
                   f'<div>{b}</div></td><td class="timetable-price" rowspan=1><div>{c}</div></td>'
                   f'<td class="timetable-description" rowspan=1><div>{d}</div></td></tr>' for a, b, c, d in lines)
    return head + body + "</table>"


def synth(rid, name, start, end, table, at="mas"):
    """A synthetic agenda row at one of the fixture's community places."""
    place = {"mas": ("294808", "2", "41.418169158350054", "2.1744079393214104", "Plaça de Salvador Riera"),
             "sf": ("268003", "480", "41.40541560676076", "2.1767746457391186", "Carrer de Provença"),
             "isidret": ("143609", "174", "41.415869059363075", "2.206836204315639", "Carrer de Cristóbal de Moura")}[at]
    return {"_id": 1, "register_id": "\ufeff" + rid, "name": name, "start_date": start + "T03:00:00",
            "end_date": end + "T03:00:00", "timetable": table, "addresses_road_id": place[0],
            "addresses_start_street_number": place[1], "geo_epgs_4326_lat": place[2],
            "geo_epgs_4326_lon": place[3], "addresses_road_name": place[4], "addresses_zip_code": "8041",
            "addresses_district_name": "Horta-Guinardó"}


def run(rows, facilities=FACILITIES):
    stats = {}
    evs = T.events(rows, facilities, SRC, CFG, TZ, TODAY, stats)
    return evs, stats


def of(evs, title_part):
    return [e for e in evs if title_part in e.name]


EVS, STATS = run(AGENDA)

# ---------------------------------------------------------------------------
print("-- the placeholder clock is never a time")
teatre = of(EVS, "Descontes")
check("a one-day row is written at its timetable's 19:00, not start_date's 03:00",
      [e.start_local for e in teatre] == ["2026-11-24T19:00:00"], [e.start_local for e in teatre])
_TT = {T._rid(r["register_id"]): r["timetable"] for r in AGENDA}
_placeholder = [(e.name, e.start_local) for e in EVS if e.start_local[11:16] == "03:00" or (
    e.start_local[11:16] == "12:00" and "12.00" not in _TT[e.ticket_url.rsplit("_", 1)[-1][:-5]])]
check("no row starts at the 03:00 placeholder, nor at 12:00 unless its timetable says 12.00",
      not _placeholder and any(e.start_local[11:16] == "12:00" for e in EVS), _placeholder[:3])
undated, _ = run([synth("9001", "Xerrada 'Hora per determinar'", "2026-11-04", "2026-11-04",
                        tt(("Dimecres", "Per determinar", "Entrada Gratuïta", "")))])
check("synthetic: unreadable hours write a date-only row, not a clock",
      [e.start_local for e in undated] == ["2026-11-04"], [e.start_local for e in undated])

print("-- a weekly row is written on its dates, and never on a holiday")
ping = of(EVS, "ping pong")
days = sorted(e.start_local[:10] for e in ping)
expect = []
d = TODAY
while d <= date(2026, 12, 22):
    if d.weekday() in (1, 4) and d.isoformat() not in CFG["skip_dates"]:
        expect.append(d.isoformat())
    d += timedelta(days=1)
check("Grup de ping pong: every Tuesday and Friday from today to 22 Dec, at 10:00",
      days == expect and all(e.start_local.endswith("T10:00:00") for e in ping), (len(days), len(expect)))
check("...but not 8 Dec (Tuesday, Immaculada) nor 25 Dec (Friday, Christmas)",
      "2026-12-08" not in days and "2026-12-25" not in days, days[-4:])
check("...and no row before today (the season began 17 Sep)", days and days[0] >= TODAY.isoformat(), days[:2])
check("...each its own identity (venue|title|date|clock), none repeated",
      len({e.source_id for e in ping}) == len(ping), len(ping))

print("-- a day with a gap is two rows, never one spanning it")
casal, _ = run([synth("9020", "Grup de ganxet", "2026-09-14", "2026-12-18",
                      tt(("Dilluns i dimecres", "de 10.00 h a 13.00 h i de 16.00 h a 18.00 h", "Entrada Gratuïta", "")))])
spans = [(e.start_local[11:16], (e.end_local or "")[11:16]) for e in casal if e.start_local[:10] == "2026-10-07"]
check("synthetic: a Wednesday 10:00-13:00 and 16:00-18:00 is two rows",
      sorted(spans) == [("10:00", "13:00"), ("16:00", "18:00")], spans)
check("no row anywhere spans 13:00-16:00 on a day the source split",
      casal and not [e for e in casal if e.start_local[11:16] < "13:00" and (e.end_local or "")[11:16] > "16:00"])
check("each half names the other ('Also this day')",
      casal and all("Also this day" in e.description for e in casal), casal[0].description[:200] if casal else "")
check("12 Oct (a Monday holiday) is not written for a weekly pattern",
      casal and not [e for e in casal if e.start_local.startswith("2026-10-12")])
tours, _ = run([synth("9021", "Visites guiades al refugi", "2026-10-09", "2026-10-09",
                      tt(("Divendres", "de 16.30 h a 17.30 h i de 17.30 h a 18.30 h", "Entrada Gratuïta", "")))])
check("synthetic: two tours back to back (16:30-17:30, 17:30-18:30) are two rows, not one 16:30-18:30",
      sorted((e.start_local[11:16], e.end_local[11:16]) for e in tours) == [("16:30", "17:30"), ("17:30", "18:30")],
      [(e.start_local, e.end_local) for e in tours])
check("...and 'Visites' is a learning title word, not the community default",
      tours and all(e.category == "learning" for e in tours), [e.category for e in tours])

print("-- explicit dates, ordinals, exceptions, imprecision")
contes = of(EVS, "Contes a la mà")
check("Narració on '27 octubre i 24 novembre': exactly those two dates at 17:30",
      sorted(e.start_local for e in contes) == ["2026-10-27T17:30:00", "2026-11-24T17:30:00"],
      [e.start_local for e in contes])
check("...and 'Places per ordre d'arribada' is said, as no registration",
      all("No registration needed" in e.description for e in contes), contes[0].description[:160] if contes else "")
gam = of(EVS, "Gam de Dol")
check("Gam de Dol: one dated line per month; only the ones in the window, 10:00-14:00",
      sorted(e.start_local[:10] for e in gam) == ["2026-10-24", "2026-11-28", "2026-12-19"],
      [e.start_local for e in gam])
lo, hi = date(2026, 9, 1), date(2027, 6, 30)
spec = T.parse_days("4rt dimecres del mes", lo, hi)
check("synthetic: '4rt dimecres del mes' covers 28 Oct and 25 Nov only",
      [x for x in (date(2026, 10, 21), date(2026, 10, 28), date(2026, 11, 25), date(2026, 11, 4)) if spec.covers(x)]
      == [date(2026, 10, 28), date(2026, 11, 25)])
spec = T.parse_days("Ultim dissabte del mes", lo, hi)
check("synthetic: 'Ultim dissabte del mes' is 31 Oct, not 24 Oct",
      spec.covers(date(2026, 10, 31)) and not spec.covers(date(2026, 10, 24)))
spec = T.parse_days("Diumenges\nTancat 25 i 26 de desembre,\n1 i 6 de gener", lo, hi)
check("'Tancat 25 i 26 de desembre, 1 i 6 de gener': both days of each list are closed",
      spec.ok and spec.weekdays == {6} and {date(2027, 1, 1), date(2027, 1, 6)} <= spec.except_dates,
      sorted(spec.except_dates))
spec = T.parse_days("De dilluns a divendres excepte 12 octubre", lo, hi)
check("'De dilluns a divendres excepte 12 octubre' skips the 12th, keeps the 13th",
      not spec.covers(date(2026, 10, 12)) and spec.covers(date(2026, 10, 13)))
imp, st = run([synth("9002", "Dijous de cuina i jocs!!", "2026-09-17", "2026-12-17",
                     tt(("Dijous cada quinze dies", "de 17.00 h a 19.00 h", "Entrada Gratuïta", "")))])
check("synthetic: 'cada quinze dies' is refused, never written weekly",
      not imp and any("precisely" in k for k in st), st)
wrong, st = run([synth("9003", "Xerrada 'El dia equivocat'", "2026-10-05", "2026-10-05",
                       tt(("Dimecres", "a les 18.30 h", "Entrada Gratuïta", "")))])
check("synthetic: a Monday row whose Dies says Dimecres is refused, not guessed",
      not wrong and any("contradicts" in k for k in st), st)
sunday, _ = run([synth("9004", "Diumenges de jocs de taula", "2026-10-04", "2026-11-01",
                       tt(("Diumenge", "de 09.00 h a 21.00 h", "Entrada Gratuïta", "")))])
check("synthetic: a 12-hour Sunday is one row each Sunday, not a month-long run",
      len(sunday) == 3 and all(len(e.start_local) > 10 for e in sunday),
      [(e.start_local, e.end_local) for e in sunday])

tour, _ = run([synth("9016", "Visites guiades al refugi", "2026-10-09", "2026-10-09",
                     tt(("Divendres", "a les 16.30 h i a les 17.30 h", "Entrada general: 6.5 €", "Visites per a públic general"),
                        ("Divendres", "de 09.30 h a 11.30 h", "Consulta el preu per a grups i escoles",
                         "Visites per a escoles (ESO i Batxillerat)")))])
check("synthetic: a timetable line 'Visites per a escoles' is skipped; the public's 16:30 and 17:30 stay",
      sorted(e.start_local[11:16] for e in tour) == ["16:30", "17:30"]
      and not any("09:30" in e.description for e in tour), [(e.start_local, e.description[:80]) for e in tour])

print("-- a run is one date-only row")
short, _ = run([synth("9015", "Exposició 'Tardes'", "2026-10-13", "2026-11-12",
                      tt(("De dilluns a dijous", "de 17.00 h a 20.00 h", "Entrada Gratuïta", "")))])
check("synthetic: an exhibition open three hours a day is still one run, not 20 daily rows",
      [(e.start_local, e.end_local) for e in short] == [("2026-10-13", "2026-11-12")],
      [(e.start_local, e.end_local) for e in short][:3])
expo = of(EVS, "Out-of-map")
check("an exhibition with opening hours is ONE row, 8 Oct to 7 Nov, date-only",
      [(e.start_local, e.end_local) for e in expo] == [("2026-10-08", "2026-11-07")],
      [(e.start_local, e.end_local) for e in expo])
check("...whose text gives the opening hours", expo and "De dilluns a divendres excepte 12 octubre de 09.00" in
      expo[0].description, expo[0].description[:300] if expo else "")
longrun, st = run([synth("9005", "Exposició 'Permanent'", "2025-09-08", "2027-06-30",
                         tt(("De dilluns a divendres", "de 10.00 h a 20.00 h", "Entrada Gratuïta", "")))])
check("synthetic: a run longer than 400 days starts today (the store would drop its end)",
      [(e.start_local, e.end_local) for e in longrun] == [(TODAY.isoformat(), "2027-06-30")],
      [(e.start_local, e.end_local) for e in longrun])

print("-- where: the City's own facility register")
check("a row at Provença 480 is named for the centre civic, not the library upstairs",
      all(e.venue_name == "Centre Cívic Sagrada Família" for e in contes + expo), {e.venue_name for e in contes + expo})
check("...with the library listed as the same building",
      expo and "Biblioteca La Sagrada Família" in expo[0].description)
check("...placed on the facility's point, coords_exact",
      all(e.coords_exact and abs(e.latitude - 41.40541560676076) < 1e-9 for e in contes + expo))
check("L'Auditori's season (its documentation library is filed municipal) is refused as a museum building",
      not of(EVS, "Martinu") and STATS.get("refused: the building is also a museum (its programme, not the centre's)", 0) >= 1,
      STATS)
check("Fundació Tàpies (a museum's plain 'Biblioteques') is not a community place",
      not of(EVS, "Tàpies, Portabella"))
noroad = synth("9006", "Festival a Sitges", "2026-10-10", "2026-10-10", tt(("Dissabte", "a les 20.00 h", "", "")))
noroad.update(addresses_road_id="", addresses_start_street_number="")
EMPTY = {"register_id": "\ufeff1", "name": "Casal Sense Adreça", "addresses_road_id": "",
         "addresses_start_street_number": "", "secondary_filters_name": "Centres cívics",
         "geo_epgs_4326_lat": "41.418169158350054", "geo_epgs_4326_lon": "2.1744079393214104"}
idx, _m = T.facility_index([EMPTY], CFG["venue_kinds"], [])
check("synthetic: a facility with no road id is not indexed under an empty address", ("", "") not in idx, list(idx))
_real_index = T.facility_index
T.facility_index = lambda *a: ({("", ""): [_real_index([dict(EMPTY, addresses_road_id="1",
                                                        addresses_start_street_number="1")],
                                                  CFG["venue_kinds"], [])[0][("1", "1")][0]]}, {})
gen, st = run([noroad])
T.facility_index = _real_index
check("synthetic: an agenda row with no road id never joins, even to an empty-keyed facility", not gen, st)
gen, st = run([noroad], FACILITIES + [{"register_id": "\ufeff1", "name": "Biblioteca", "addresses_road_id": "",
                                        "addresses_start_street_number": "", "secondary_filters_name": "Centres cívics",
                                        "geo_epgs_4326_lat": "41.418169158350054",
                                        "geo_epgs_4326_lon": "2.1744079393214104"}])
check("synthetic: a row with no road id never joins (an empty key is not an address)", not gen, st)
gen_named = synth("9007", "Concert al conservatori", "2026-10-10", "2026-10-10",
                  tt(("Dissabte", "a les 20.00 h", "Entrada Gratuïta", "")))
gen_named.update(addresses_road_id="555", addresses_start_street_number="9")
gen, st = run([gen_named], FACILITIES + [{"register_id": "\ufeff2", "name": "Biblioteca", "addresses_road_id": "555",
                                           "addresses_start_street_number": "9",
                                           "secondary_filters_name": "Biblioteques municipals",
                                           "geo_epgs_4326_lat": "41.418169158350054",
                                           "geo_epgs_4326_lon": "2.1744079393214104"}])
check("synthetic: a facility named only 'Biblioteca' (a museum's or a school's) names nothing", not gen, st)

print("-- what is kept: what anyone can turn up to")
check("Sala d'Informàtica's opening hours are refused", not of(EVS, "Informàtica"))
check("a 8.75 EUR talk with the 'Reduccio i subvencio dels imports als cursos' line is refused",
      not of(EVS, "ictus"))
reg = row("99400784171")
reg.update(addresses_road_id="294808", addresses_start_street_number="2",
           geo_epgs_4326_lat="41.418169158350054", geo_epgs_4326_lon="2.1744079393214104")
got, st = run([reg])
check("a free concert whose note says 'Cal inscripció prèvia' is refused (moved to Mas Guinardó)",
      not got and any(k.startswith("refused: registration") for k in st), st)
neg, _ = run([synth("9008", "Concert al pati", "2026-10-17", "2026-10-17",
                    tt(("Dissabte", "a les 12.00 h", "Entrada Gratuïta", "No cal reserva prèvia.")))])
check("synthetic: 'No cal reserva prèvia' contains 'cal reserva prèvia' and is KEPT", len(neg) == 1)
grp, _ = run([synth("9009", "Exposició 'Grups'", "2026-10-10", "2026-10-30",
                    tt(("De dilluns a divendres", "de 10.00 h a 20.00 h", "Entrada Gratuïta",
                        "Grups d'escolars amb reserva prèvia")))])
check("synthetic: a group-booking line is not a registration for everyone", len(grp) == 1)
for name, note, why in (("Taller 'Dansa urbana'", "", "30 € mensual"),
                        ("Taller 'Jocs de memòria'", "10 sessions", "Entrada Gratuïta"),
                        ("Activitat familiar", "Activitat per a famílies inscrites al casal", "Entrada Gratuïta"),
                        ("Xerrada online 'Cultiu urbà'", "", "Entrada Gratuïta"),
                        ("Patis escolars oberts al barri - Escola X", "", ""),
                        ("27è Concurs 'Relats'", "", "Entrada Gratuïta")):
    multi = name.startswith("27")
    got, st = run([synth("9010", name, "2026-10-06", "2026-12-15" if multi else "2026-10-06",
                         tt(("De dilluns a divendres" if multi else "Dimarts",
                             "de 09.00 h a 21.00 h" if multi else "de 17.00 h a 18.00 h", why, note)))])
    check(f"synthetic: refused - {name} / {note or why}", not got, st)

print("-- the price is a contract with 0227")
fee, _ = run([synth("9011", "Espectacle 'Amb preu'", "2026-10-17", "2026-10-17",
                    tt(("Dissabte", "a les 18.00 h", "Entrada general: 12 € Gratis per a menors de 3 anys", "")))])
check("synthetic: a fee row whose cell says 'Gratis per a menors' ends '(not free)'; 0227 does not read it free",
      fee and "(not free)" in fee[0].description and not tagged_free(fee[0].name, fee[0].description),
      fee[0].description[:200] if fee else "")
check("a free row says 'Admission: free.' and 0227 reads it", teatre and teatre[0].description.startswith(
    "Admission: free.") and tagged_free(teatre[0].name, teatre[0].description))
some, _ = run([synth("9012", "Espectacle 'Per a alguns'", "2026-10-17", "2026-10-17",
                     tt(("Dissabte", "a les 18.00 h", "Entrada gratuïta per a socis", "")))])
check("synthetic: 'gratuïta per a socis' is not free", some and not tagged_free(some[0].name, some[0].description)
      and "Admission: free" not in some[0].description)
pw, _ = run([synth("9013", "Concert 'Inversa'", "2026-10-17", "2026-10-17",
                   tt(("Dissabte", "a les 20.00 h", "Taquilla inversa", "")))])
check("synthetic: 'Taquilla inversa' is pay what you can, never free",
      pw and "pay what you can" in pw[0].description and not tagged_free(pw[0].name, pw[0].description))
check("every written row's 0227 reading agrees with its own price line",
      all(tagged_free(e.name, e.description) == e.description.startswith("Admission: free.") for e in EVS))
long_note = "Observació llarga sobre l'espectacle i el seu contingut. " * 30
cut, _ = run([synth("9014", "Espectacle 'Llarg'", "2026-10-17", "2026-10-17",
                    tt(("Dissabte", "a les 18.00 h", "Entrada general: 9 €", long_note)))])
stored = S.to_row(cut[0].as_record("2026-10-05T00:00:00Z"), "h")["description"] if cut else ""
check("synthetic: a 1,600-character note still leaves '(not free)' and the licence after the sync's cap",
      "(not free)" in stored and "CC BY 4.0" in stored and len(cut[0].description) <= T.DESCRIPTION_MAX,
      (len(cut[0].description) if cut else 0, stored[:120]))

print("-- categories, identity, the link")
for e in EVS:
    primary, _extras = derive_categories(e.as_record("2026-10-05T00:00:00Z"))
    if primary not in VALID_CATEGORIES:
        check(f"derive_categories gives a real lens key for {e.name}", False, primary)
        break
else:
    check("derive_categories gives a real lens key for every row", True)
check("a Narració is kids, a Teatre is theater",
      contes and contes[0].category == "kids" and teatre and teatre[0].category == "theater",
      (contes[0].category if contes else None, teatre[0].category if teatre else None))
# "Petits" is a name as often as an audience (integration review, 2026-10-05).
for _t in ("Diada per a la Commemoració del Dol Gestacional i Perinatal de Petits amb Llum",
           "Gam de Dol. Associació Petits amb llum"):
    check(f"a group NAMED Petits is not the kids door: {_t[:40]}", "kids" not in T.category(_t, {}, {})[1])
check("...while 'per als petits' still is",
      "kids" in T.category("Música per als petits", {}, {})[1])
check("the link is the City's Guia page for the register id",
      teatre and teatre[0].ticket_url.startswith("https://guia.barcelona.cat/ca/detall/")
      and teatre[0].ticket_url.endswith("_99400745169.html"), teatre[0].ticket_url if teatre else "")
shuffled = list(AGENDA)
random.Random(4).shuffle(shuffled)
again, _ = run(shuffled)
check("a shuffled re-read gives the same identities",
      sorted(e.source_id for e in again) == sorted(e.source_id for e in EVS))
with tempfile.TemporaryDirectory() as tmp:
    store = EventStore(os.path.join(tmp, "s.json"))
    for e in EVS:
        store.upsert(e)
    n = len(store.records)
    for e in again:
        store.upsert(e)
    check("a second upsert adds 0 and rekeys 0", len(store.records) == n and store.stats.get("rekeyed", 0) == 0,
          (n, len(store.records), store.stats))
check("no two rows share a fingerprint", len({e.fingerprint for e in EVS}) == len(EVS))

print("-- a weekday that labels a date is not a weekly day (review 2)")
lo9, hi9 = date(2026, 10, 24), date(2026, 11, 14)
spec = T.parse_days("Dissabte 24 d'octubre", lo9, hi9)
check("synthetic: 'Dissabte 24 d'octubre' is that one date, no weekly Saturday",
      spec.ok and spec.dates == {date(2026, 10, 24)} and not spec.weekdays, (sorted(spec.weekdays), spec.dates))
spec = T.parse_days("Dilluns 24 d'octubre", lo9, hi9)
check("synthetic: 'Dilluns 24 d'octubre' (a Saturday) is refused, not written on Mondays",
      not spec.ok and not spec.weekdays, (spec.why, sorted(spec.weekdays)))
spec = T.parse_days("Dissabtes del 3 d'octubre al 19 de desembre", date(2026, 9, 1), date(2026, 12, 31))
check("synthetic: weekdays bounded by a date range are refused, never written on every day of it",
      not spec.ok, (spec.why, len(spec.dates)))
nine = of(EVS, "De 9 a 5")
check("Teatre Musical 'De 9 a 5' (Dissabte 24 d'octubre, Dissabte 14 de novembre): 4 rows on those 2 days only",
      sorted(e.start_local for e in nine if "_99400786915" in e.ticket_url) == [
          "2026-10-24T12:00:00", "2026-10-24T18:00:00", "2026-11-14T12:00:00", "2026-11-14T18:00:00"],
      sorted(e.start_local for e in nine))
check("...and 'Donatiu voluntari' is pay what you can, not 'Price not stated', never free",
      all("pay what you can" in e.description and not tagged_free(e.name, e.description)
          for e in nine if "_99400786915" in e.ticket_url), [e.description[:60] for e in nine])

print("-- a run is an exhibition only, and never one listing its own dates (review 2)")
mostra = of(EVS, "cinema emergent")
check("'Mostra de cinema emergent' on 28 octubre, 25 novembre i 16 desembre: 3 timed rows, not a 7-week run",
      sorted((e.start_local, e.end_local) for e in mostra) == [
          ("2026-10-28T19:00:00", "2026-10-28T20:00:00"), ("2026-11-25T19:00:00", "2026-11-25T20:00:00"),
          ("2026-12-16T19:00:00", "2026-12-16T20:00:00")], [(e.start_local, e.end_local) for e in mostra])
ranged, _ = run([synth("9022", "Exposició 'Del cinc al nou'", "2026-10-05", "2026-10-09",
                       tt(("Del 5 al 9 d'octubre", "de 10.00 h a 14.00 h", "Entrada Gratuïta", "")))])
check("synthetic: an exhibition whose Dies is a range ('Del 5 al 9 d'octubre') is still one run",
      [(e.start_local, e.end_local) for e in ranged] == [("2026-10-05", "2026-10-09")],
      [(e.start_local, e.end_local) for e in ranged])
vid, _ = run([synth("9023", "IV Mostra de vídeo-performances", "2026-11-30", "2026-12-11",
                    tt(("De dilluns a divendres", "de 09.00 h a 22.00 h", "Entrada Gratuïta", "")))])
check("synthetic: 'IV Mostra ...' open the building's hours is an exhibition run, not refused as opening hours",
      [(e.start_local, e.end_local) for e in vid] == [("2026-11-30", "2026-12-11")], [e.start_local for e in vid])
check("the fixture's 'Trobada 'Casal Persones Grans'' (Mon-Fri, a seniors' room) is refused as a space's hours",
      not of(EVS, "Casal Persones Grans")
      and STATS.get("refused: a space's opening hours (a room or space open on weekdays)", 0) >= 1, STATS)
for name, hours in (("Trobada 'Punt TIC'", "de 10.00 h a 13.00 h i de 16.00 h a 18.00 h"),
                    ("Trobada 'Espai Familiar'", "de 10.00 h a 13.00 h i de 16.30 h a 20.30 h"),
                    ("Sala de joc", "de 16.00 h a 19.45 h")):
    got, st = run([synth("9024", name, "2026-09-14", "2026-12-18",
                         tt(("De dilluns a divendres", hours, "Entrada Gratuïta", "")))])
    check(f"synthetic: {name} on weekdays ({hours[:22]}...) is a space's hours: no row, no run", not got, st)
camp, st = run([synth("9025", "Campanya solidària 'Cap infant sense joguina'", "2026-12-01", "2026-12-30",
                      tt(("De dilluns a divendres", "de 09.30 h a 14.30 h i de 16.00 h a 21.30 h", "", "")))])
check("synthetic: a collection point open the building's hours for a month is refused, not a run",
      not camp and any(k.startswith("refused: opening hours") for k in st), st)
wed, _ = run([synth("9026", "Espai de joc", "2026-10-07", "2026-10-28",
                    tt(("Dimecres", "de 17.00 h a 18.30 h", "Entrada Gratuïta", "")))])
check("synthetic: an 'Espai de joc' on Wednesdays only is a session, written each Wednesday", len(wed) == 4,
      [e.start_local for e in wed])

print("-- the row's own point names its building (review 2)")
vall = [e for e in EVS if "_99400772215" in e.ticket_url]
check("Vallcivera 3: the library's talk is named for the library at its point, not the casal 86 m away",
      vall and all(e.venue_name == "Biblioteca Zona Nord - Mària Sánchez" for e in vall)
      and all(abs(e.latitude - 41.4612) < 2e-4 for e in vall), [(e.venue_name, e.latitude) for e in vall])
check("...not on the kids door, and the casal is not 'the same building'",
      vall and all("kids" not in e.categories and e.category != "kids" for e in vall)
      and not any("Ciutat Meridiana" in e.description for e in vall),
      [(e.category, e.categories) for e in vall])
LIB = {"register_id": "﻿8001", "name": "Biblioteca Prova", "addresses_road_id": "294808",
       "addresses_start_street_number": "2", "secondary_filters_name": "Biblioteques municipals",
       "geo_epgs_4326_lat": "41.41833", "geo_epgs_4326_lon": "2.1744079393214104"}   # 18 m north of Mas Guinardó
near, _ = run([dict(synth("9027", "Xerrada 'Prova'", "2026-10-07", "2026-10-07",
                          tt(("Dimecres", "a les 18.00 h", "Entrada Gratuïta", ""))), geo_epgs_4326_lat="41.41833")],
              FACILITIES + [LIB])
check("synthetic: a row on the library's point, 18 m from the casal de barri: one building, the casal names it",
      [e.venue_name for e in near] == ["Casal Mas Guinardó"] and "Biblioteca Prova" in near[0].description,
      [(e.venue_name, e.description[:120]) for e in near])
star, _m3 = T.facility_index([dict(LIB, name="Casal de Gent Gran d'Horta *Feliu i Codina",
                                   secondary_filters_name="Casals d'avis")], CFG["venue_kinds"], [])
check("synthetic: the register's stray '*' is not part of a venue name",
      [f["name"] for v in star.values() for f in v] == ["Casal de Gent Gran d'Horta Feliu i Codina"], star)

print("-- a line held at another facility is not pinned here (review 2)")
dmj = of(EVS, "DMJ Gràcia")
check("DMJ Gràcia: the lines at 'CC La Sedeta' and 'Casal de Joves del Coll' are skipped; 27 Nov at La Fontana stays",
      [e.start_local for e in dmj] == ["2026-11-27T21:00:00"]
      and all(e.venue_name == "Espai Jove La Fontana" for e in dmj), [(e.start_local, e.venue_name) for e in dmj])
check("...and the skipped lines' notes are not printed", dmj and "Sedeta" not in dmj[0].description,
      dmj[0].description[:200] if dmj else "")
idx2, _m2 = T.facility_index(FACILITIES, CFG["venue_kinds"], CFG["museum_kinds"])
M2 = T.place_matcher(idx2)
check("synthetic: 'la Sedeta' in prose (no kind word) names no facility; 'Al CC La Sedeta' does",
      not T.places_named("Festa Major de la Sedeta i Sant Antoni", M2) and T.places_named("Al CC La Sedeta", M2))

print("-- a museum's address (review 2)")
CONVENT = {"addresses_road_id": "85400", "addresses_start_street_number": "36",
           "geo_epgs_4326_lat": "41.38741922402338", "geo_epgs_4326_lon": "2.181725967522791"}
conv_fac = [dict(CONVENT, register_id="﻿8002", name="Centre Cívic Convent de Sant Agustí",
                 secondary_filters_name="Centres cívics"),
            dict(CONVENT, register_id="﻿8003", name="Museu de la Xocolata", secondary_filters_name="Museus")]
gamer = dict(synth("9028", "Tardes Gamer", "2026-10-16", "2026-10-16",
                   tt(("Divendres", "de 17.00 h a 19.00 h", "Entrada Gratuïta", ""))), **CONVENT)
got, st = run([gamer], conv_fac)
check("synthetic: Comerç 36 (museum_shared_kept): the centre civic's 'Tardes Gamer' is kept, the museum named",
      len(got) == 1 and "Museu de la Xocolata" in got[0].description, st)
other = dict(gamer, addresses_start_street_number="38")
got, st = run([other], [dict(f, addresses_start_street_number="38") for f in conv_fac])
check("synthetic: the same building at an address nobody reviewed is refused as the museum's",
      not got and any("museum" in k for k in st), st)
BARCA = [{"register_id": "﻿8004", "name": "Casal de l'Avi Barça", "addresses_road_id": "100708",
          "addresses_start_street_number": "12", "secondary_filters_name": "Casals d'avis",
          "geo_epgs_4326_lat": "41.379090515776426", "geo_epgs_4326_lon": "2.1202855135589282"},
         {"register_id": "﻿8005", "name": "Museu Barça Immersive Tour", "addresses_road_id": "100708",
          "addresses_start_street_number": "12", "secondary_filters_name": "Museus",
          "geo_epgs_4326_lat": "41.380676388525416", "geo_epgs_4326_lon": "2.120354439986183"}]
match = dict(synth("9029", "Bàsquet 'F.C. Barcelona - Baskonia'", "2026-10-18", "2026-10-18",
                   tt(("Diumenge", "a les 19.00 h", "Entrada Gratuïta", ""))),
             addresses_road_id="100708", addresses_start_street_number="12",
             geo_epgs_4326_lat="41.37971200115785", geo_epgs_4326_lon="2.119391576311853")
got, st = run([match], BARCA)
check("synthetic: a match 102 m from Casal de l'Avi Barça, at the museum's number, is refused",
      not got and any("museum" in k for k in st), st)

print("-- the fixes of review 1, pinned (review 2)")
spec = T.parse_days("Del 5 al 9 d'octubre", lo, hi)
check("synthetic: 'Del 5 al 9 d'octubre' is 5 dates, the 5th included",
      spec.ok and spec.dates == {date(2026, 10, d) for d in range(5, 10)}, sorted(spec.dates))
spec = T.parse_days("Diumenges\nTancat del 27 de desembre al 3 de gener", lo, hi)
check("synthetic: 'Tancat del 27 de desembre al 3 de gener' closes every day of it",
      spec.ok and spec.weekdays == {6} and {date(2026, 12, 27), date(2027, 1, 3)} <= spec.except_dates
      and len(spec.except_dates) == 8, sorted(spec.except_dates))
spec = T.parse_days("Dijous 5, 12 i 19", lo, hi)
check("synthetic: day numbers with no month ('Dijous 5, 12 i 19') are refused, not read as every Thursday",
      not spec.ok and spec.why == "unread day number", (spec.ok, spec.why, sorted(spec.weekdays)))
check("synthetic: a summer line 'De l'1 abril al 30 setembre' writes nothing in an October-March row",
      T.parse_period("De l'1 abril al 30 setembre", date(2026, 10, 1), date(2027, 3, 31)) == [],
      T.parse_period("De l'1 abril al 30 setembre", date(2026, 10, 1), date(2027, 3, 31)))
check("synthetic: 'Cal demanar cita prèvia' and 'Cal concertar hora' are registrations",
      T.registration(["Cal demanar cita prèvia"])[0] is True and T.registration(["Cal concertar hora."])[0] is True)
wrapped = "Cal concertar hora\nper a les visites\ncomentades i de\ngrup (+ de 15 persones)."
check("synthetic: the wrapped note 'Cal concertar hora / per a les visites / comentades i de / grup' is about groups",
      T.registration(T.sentences(wrapped))[0] is not True and len(T.sentences(wrapped)) == 1,
      T.sentences(wrapped))
note, _ = run([synth("9030", "Xerrada 'Nota'", "2026-10-07", "2026-10-07",
                     tt(("Dimecres", "a les 18.00 h", "Entrada Gratuïta", "Activitat amb una durada d'1 hora i\nun aforament màxim de 20 persones")))])
check("synthetic: a note wrapped mid-sentence is printed as one sentence ('1 hora i un aforament')",
      note and "1 hora i un aforament" in note[0].description, note[0].description[:200] if note else "")
check("synthetic: a schools-only line's notes are not printed",
      tour and not any("escoles" in e.description for e in tour), [e.description[:160] for e in tour])
fp = {}
for later in (0, 7, 30):
    got = T.events([synth("9005", "Exposició 'Permanent'", "2025-09-08", "2027-06-30",
                          tt(("De dilluns a divendres", "de 10.00 h a 20.00 h", "Entrada Gratuïta", "")))],
                   FACILITIES, SRC, CFG, TZ, TODAY + timedelta(days=later), {})
    fp[later] = [(e.fingerprint, e.source_id) for e in got]
check("synthetic: a run longer than 400 days keeps ONE fingerprint as today moves (0, 7, 30 days on)",
      len(fp[0]) == 1 and fp[0] == fp[7] == fp[30], fp)
spec = T.parse_days("Diumenge i festius", lo, hi)
hol, _ = run([synth("9031", "Ballada de sardanes", "2026-11-29", "2026-12-13",
                    tt(("Diumenge i festius", "a les 12.00 h", "Entrada Gratuïta", "")))])
check("synthetic: 'Diumenge i festius' is written on the 6 Dec Sunday holiday and on Tuesday 8 Dec",
      spec.on_holidays and {"2026-12-06", "2026-12-08"} <= {e.start_local[:10] for e in hol},
      sorted(e.start_local[:10] for e in hol))
sun, _ = run([synth("9032", "Ballada de sardanes", "2026-11-29", "2026-12-13",
                    tt(("Diumenge", "a les 12.00 h", "Entrada Gratuïta", "")))])
check("...while plain 'Diumenge' skips the 6 Dec holiday and never adds a Tuesday",
      sorted(e.start_local[:10] for e in sun) == ["2026-11-29", "2026-12-13"], [e.start_local for e in sun])

print("-- the reader: a refusal is final")


class Resp:
    def __init__(self, code, body=None, headers=None):
        self.status_code, self._b, self.headers = code, body, headers or {}

    def json(self):
        return self._b


class Sess:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def get(self, *a, **k):
        self.calls += 1
        return self.answers.pop(0)


for code in (403, 429):
    s = Sess([Resp(code)] * 3)
    rd = T.Reader(s, sleep=lambda x: None)
    try:
        rd.call({})
        ok = False
    except T.Refused:
        ok = s.calls == 1
    except Exception:                                             # noqa: BLE001
        ok = False
    check(f"HTTP {code} is refused after one request, never retried", ok, s.calls)
s = Sess([Resp(302, headers={"location": "/challenge"}), Resp(200, {"success": True, "result": {}})])
rd = T.Reader(s, sleep=lambda x: None)
try:
    rd.call({})
    ok = False
except T.Refused:
    ok = rd.refused is not None and s.calls == 1
except Exception:                                                 # noqa: BLE001
    ok = False
check("a redirect to /challenge is a refusal, not a page to follow", ok)
s = Sess([Resp(200, ValueError("not JSON"), {"content-type": "text/html; charset=utf-8"}),
          Resp(200, {"success": True, "result": {}})])
rd = T.Reader(s, sleep=lambda x: None)
try:
    rd.call({})
    ok = False
except T.Refused:
    ok = s.calls == 1
except Exception:                                                 # noqa: BLE001
    ok = False
check("a 200 that is a page (the portal's 'Bot Detection' challenge) is a refusal, asked once", ok, s.calls)
s = Sess([Resp(503), Resp(200, {"success": True, "result": {"records": [], "total": 0}})])
check("a 503 is retried", T.Reader(s, sleep=lambda x: None).call({}) == {"records": [], "total": 0} and s.calls == 2)
rd = T.Reader(Sess([]), sleep=lambda x: None, clock=lambda: 100.0, deadline=50.0)
try:
    rd.call({})
    ok = False
except T.OutOfTime:
    ok = rd.requests == 0
check("past --max-minutes no request starts", ok)

print()
print(f"{len(fails)} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
