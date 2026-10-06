#!/usr/bin/env python3
"""
test_ingest_madrid.py - the Ayuntamiento de Madrid's agenda, and the ways a row
in it is not the session it looks like.

Every item in FIXTURE was read from datos.madrid.es on 2026-10-03 (the JSON
files 300107 and 206974, trimmed to the fields the adapter reads, descriptions
cut at 240 characters unless the case needs more); every row in CSV_FIXTURE
was read on 2026-10-05 from the same datasets' CKAN datastore, which is what
the adapter would read with the publisher's permission (the JSON files
redirect into a path robots.txt Disallows, and the datastore is Disallowed by
name; that 2026-10-05 read is why the source is parked). Synthetic cases are built in code and say so. The expensive cases are the quiet ones: a workshop that needs
a place booked written as a drop-in because its text says "sin inscripción
previa" somewhere else, or says nothing at all; a ticket mistaken for a
registration; a weekly row written on every Saturday when its text names two;
a reading club's "every day" recurrence written as a daily session; a session
on 12 October, when the city is on holiday; a 5-euro concert that 0227 reads
as free, in the adapter's text or after the sync's 800-character cap has cut
the "(no es gratis)" off; a point in the wrong district; a datastore that
answers 403, a challenge or a redirect; and, first of all, a robots.txt that
says no. The source is PARKED: datos.madrid.es Disallows every path to these
rows (the datastore by name), so the config ships as
madrid_sources.json.pending-permission and the adapter reads nothing until the
config's `permission` records the Ayuntamiento's written yes. TWINS_FIXTURE
(the 2026-10-05 datastore rows the 2026-10-05 review named, descriptions cut)
pins the cases that review found.

    python test_ingest_madrid.py
"""
import io
import json
import os
import random
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, timedelta

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["MAPSEE_TODAY"] = "20261005"

from zoneinfo import ZoneInfo  # noqa: E402

import mapsee_ingest_madrid as T  # noqa: E402
import mapsee_supabase_sync as S  # noqa: E402
from mapsee_ingest import EventStore, VALID_CATEGORIES  # noqa: E402
from mapsee_supabase_sync import derive_categories  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "madrid_sources.json")
CONFIG = LIVE + ".pending-permission"
CFG = T.load_config(CONFIG)
TZ = ZoneInfo(CFG["timezone"])
TODAY = T._today(TZ)
HORIZON = TODAY + timedelta(days=int(CFG.get("horizon_days", 90)))
SKIP = {date.fromisoformat(x) for x in CFG["skip_dates"]}

# ../mapsee migration 0227 reads `offer:free` from a row's own text, Spanish
# included. A SUBSET of ../mapsee/tools/measure_deals.py's FREE and FREE_NEG -
# the alternatives these rows can hit. The full classify over the 2026-10-03
# replay (1,258 rows) and the replayed 2026-10-05 pull (975, current rules) agreed with the
# adapter's tier on every row.
FREE_TAG = T.TAGGED_FREE
FREE_NEG = T.FREE_NEG


def tagged_free(name, description):
    text = f"{name}\n{description}"
    return bool(FREE_TAG.search(text)) and not FREE_NEG.search(text)


def stored(ev):
    """What the SYNC writes: to_row caps the description at DESCRIPTION_MAX by
    trimming the head from its end."""
    return S.to_row(dict(ev.__dict__), "00000000-0000-0000-0000-000000000000")


FIXTURE = json.loads(r"""{"A": [
{"@type": "TeatroPerformance", "id": "50433813", "title": "The Primitals", "description": "Yllana y Primital Bros se unen para sorprendernos con una divertidísima comedia musical a capela. Cuatro aborígenes de un planeta que podría ser el nuestro reclaman el escenario, dispuestos a conquistar al público, a carcajadas o a machetaz", "free": 1, "price": "", "dtstart": "2026-10-18 19:00:00.0", "dtend": "2026-10-18 23:59:00.0", "time": "19:00", "excluded-days": "", "event-location": "Centro Cultural El Pozo del tío Raimundo (Puente de Vallecas)", "address": {"district": {"@id": "/Distrito/PuenteDeVallecas"}, "area": {"postal-code": "28053", "street-address": "AVENIDA GLORIETAS 19"}}, "location": {"latitude": 40.37381624358889, "longitude": -3.6599325731670977}},
{"@type": "TeatroPerformance", "id": "50423847", "title": "El sueño", "description": "El sueño tiene como punto de partida un encuentro imaginario entre la escultora francesa Camille Claudel y el pintor y escultor suizo Alberto Giacometti. Esta propuesta poética-onírica coloca a dichos artistas 'veteranos' en un espacio efím", "free": 0, "price": "Entrada: 7  euros.", "dtstart": "2026-10-02 19:00:00.0", "dtend": "2026-10-23 23:59:00.0", "time": "19:00", "excluded-days": "", "recurrence": {"days": "FR", "frequency": "WEEKLY", "interval": 1}, "event-location": "CentroCentro", "address": {"district": {"@id": "/Distrito/Retiro"}, "area": {"postal-code": "28014", "street-address": "PLAZA CIBELES 1"}}, "location": {"latitude": 40.41902261618159, "longitude": -3.692188193693138}},
{"@type": "ProgramacionDestacadaAgendaCultura", "id": "50432799", "title": "Andrés Coll Cosmic Trio", "description": "CentroCentro es sede un año más de JAZZMADRID, el festival que cada otoño convierte Madrid en el epicentro europeo de este género musical reuniendo a las figuras imprescindibles del panorama actual. Andrés Coll Cosmic TrioEn colaboración co", "free": 0, "price": "Entrada general: 5 eurosDescuentos disponibles ", "dtstart": "2026-11-04 00:00:00.0", "dtend": "2026-11-04 23:59:00.0", "time": "", "excluded-days": "", "event-location": "CentroCentro", "address": {"district": {"@id": "/Distrito/Retiro"}, "area": {"postal-code": "28014", "street-address": "PLAZA CIBELES 1"}}, "location": {"latitude": 40.41902261618159, "longitude": -3.692188193693138}},
{"@type": "CursosTalleres", "id": "50363659", "title": "Ludotecas", "description": "Sábados 3, 10, 17 y 24 de octubre, de 10:30 a 13:30 horas.  Actividad gratuita y de carácter abierto, sin inscripción previa, para niños/as con edades comprendidas entre 4 y 10 años.  Aforo limitado.", "free": 1, "price": "", "dtstart": "2026-10-03 10:30:00.0", "dtend": "2026-10-24 23:59:00.0", "time": "10:30", "excluded-days": "31/10/2026;", "recurrence": {"days": "SA", "frequency": "WEEKLY", "interval": 1}, "audience": "Niños", "event-location": "Centro Sociocultural Alfonso XII (Fuencarral - El Pardo)", "address": {"district": {"@id": "/Distrito/Fuencarral-ElPardo"}, "area": {"postal-code": "28048", "street-address": "CALLE MIRA EL RIO 4"}}, "location": {"latitude": 40.519673311658686, "longitude": -3.7779055687726535}},
{"@type": "ActividadesDeportivas", "id": "50410916", "title": "Actividades Cuerpo y Mente - Deporte en la Calle - Distrito de Villaverde", "description": "", "free": 1, "price": "", "dtstart": "2026-09-07 18:00:00.0", "dtend": "2027-06-30 23:59:00.0", "time": "18:00", "excluded-days": "28/12/2026;4/1/2027;", "recurrence": {"days": "MO", "frequency": "WEEKLY", "interval": 1}, "event-location": "Instalación Deportiva Municipal Básica María de Villota", "address": {"district": {"@id": "/Distrito/Villaverde"}, "area": {"postal-code": "28021", "street-address": "CALLE ESTEFANITA 3"}}, "location": {"latitude": 40.34750435286862, "longitude": -3.6756560101604467}},
{"@type": "Exposiciones", "id": "50363653", "title": "Exposición: Contra mitos", "description": "Del 01 al 29 de octubre.  Revisión crítica de la mitología griega que examina el papel de la mujer en los mitos.  De lunes a viernes, de 9:00 a 21:00 horas. Sábados, domingos y festivos, de 9:30 a 20:00 horas.", "free": 1, "price": "", "dtstart": "2026-10-01 09:00:00.0", "dtend": "2026-10-29 23:59:00.0", "time": "09:00", "excluded-days": "", "recurrence": {"days": "MO,TU,WE,TH,FR,SA,SU", "frequency": "WEEKLY", "interval": 1}, "event-location": "Centro Sociocultural Alfonso XII (Fuencarral - El Pardo)", "address": {"district": {"@id": "/Distrito/Fuencarral-ElPardo"}, "area": {"postal-code": "28048", "street-address": "CALLE MIRA EL RIO 4"}}, "location": {"latitude": 40.519673311658686, "longitude": -3.7779055687726535}},
{"@type": "TeatroPerformance", "id": "50430740", "title": "La sombra de la Parca: relatos de vida y muerte", "description": "Edad: Todos los públicos, a partir de 12 años (menores acompañados por un adulto, un adulto por niño). Imprescindible inscripción previa.  Sinopsis: Desde que el mundo es mundo, la Humanidad ha intentado comprender a la Muerte. La hemos tem", "free": 1, "price": "", "dtstart": "2026-10-31 11:30:00.0", "dtend": "2026-10-31 23:59:00.0", "time": "11:30", "excluded-days": "", "audience": "Niños,Familias", "event-location": "Centro de Educación Ambiental y Cultural Maris Stella (Usera)", "address": {"district": {"@id": "/Distrito/Usera"}, "area": {"postal-code": "28026", "street-address": "CALLE DOCTOR TOLOSA LATOUR 16"}}, "location": {"latitude": 40.374166248648216, "longitude": -3.7032220308808577}},
{"@type": "CursosTalleres", "id": "50437359", "title": "CICLO PREPARANDO HALLOWEEN. Laboratorio infantil de creatividad.", "description": "Actividad para niñ@s de entre 5 y 12 años.* De 17:00 a 19:00 h.     Sábado 17. Entre momias y fantasmas: crearemos pequeñas esculturas de momias y un móvil de fantasmas para decorar la casa en Halloween.     Sábado 31. Espacios tenebrosos: recordamos los espacios donde pueden ocurrir historias de miedo y crearemos pequeños dioramas para llevarnos a casa.    * Actividad gratuita hasta completar aforo (20 plazas). Dirigido a niñ@s de entre 5 y 12 años, siendo necesario tener la edad indicada para participar en la actividad. Sin inscripción previa, por orden de llegada hasta completar las plazas disponibles. Necesar", "free": 1, "price": "", "dtstart": "2026-10-17 17:00:00.0", "dtend": "2026-10-31 23:59:00.0", "time": "17:00", "excluded-days": "24/10/2026;", "recurrence": {"days": "SA", "frequency": "WEEKLY", "interval": 1}, "audience": "Niños", "event-location": "Centro Sociocultural Montecarmelo (Fuencarral - El Pardo)", "address": {"district": {"@id": "/Distrito/Fuencarral-ElPardo"}, "area": {"postal-code": "28049", "street-address": "Carretera M-607 Km 13 L-10 "}}, "location": {"latitude": 40.515712291624084, "longitude": -3.6860532895354448}},
{"@type": "Musica", "id": "50420957", "title": "Concierto barroco", "description": "Programa: Haendel: Lascia ch’io pianga, Ombra mai fu, Sarabanda. Pachelbel, Canon. Stradella, Pietà, Signore. Bach: Aria suite en Re, Preludio nº 1 en Do Mayor (Clave bien temperado). Vivaldi: Primavera, Vieni, vieni. Albinoni, Adagio. Dura", "free": 1, "price": "", "dtstart": "2026-10-10 19:00:00.0", "dtend": "2026-10-10 23:59:00.0", "time": "19:00", "excluded-days": "", "event-location": "Centro Cultural Zazuar (Villa de Vallecas)", "address": {"district": {"@id": "/Distrito/VillaDeVallecas"}, "area": {"postal-code": "28031", "street-address": "CALLE ZAZUAR 4"}}, "location": {"latitude": 40.38281866921273, "longitude": -3.6061377076487906}},
{"@type": "CursosTalleres/Idiomas", "id": "12516239", "title": "Taller online 'Remedial English'", "description": "Remedial English es una actividad gratuita en línea dirigida a adultos con nivel A2/B1 que necesiten reforzar su inglés. Es un espacio de apoyo para superar obstáculos fonológicos y morfosintácticos que dificultan hablar, escribir y entende", "free": 0, "price": "", "dtstart": "2026-10-20 18:00:00.0", "dtend": "2027-06-02 23:59:00.0", "time": "18:00", "excluded-days": "", "recurrence": {"days": "TU", "frequency": "WEEKLY", "interval": 1}, "event-location": "Biblioteca Pública Municipal José Hierro (San Blas-Canillejas)", "address": {"district": {"@id": "/Distrito/SanBlas-Canillejas"}, "area": {"postal-code": "28022", "street-address": "CALLE MARIA SEVILLA DIAGO 15"}}, "location": {"latitude": 40.433291625973624, "longitude": -3.6105662311927453}},
{"@type": "DanzaBaile", "id": "50428616", "title": "Artrics - Artes Escénicas", "description": "Artes Escénicas - Teatro, escritura creativa y dramaturgia, danza, cuerpo y voz y montaje escénico.   Horario: Miércoles de 15:00 a 19:00 h.  Lugar: Salón de Actos del CSC Bohemios.  Plazos y solicitudes: Las solicitudes serán evaluadas por el equipo, siguiendo el orden de recepción. Los proyectos se desarrollarán entre octubre y junio.  Convocatoria abierta desde el 15 de sept", "free": 1, "price": "", "dtstart": "2026-10-01 15:00:00.0", "dtend": "2027-06-30 23:59:00.0", "time": "15:00", "excluded-days": "", "recurrence": {"days": "WE", "frequency": "WEEKLY", "interval": 1}, "audience": "Jovenes", "event-location": "Centro Sociocultural Bohemios (Villaverde)", "address": {"district": {"@id": "/Distrito/Villaverde"}, "area": {"postal-code": "28041", "street-address": "CALLE LOS BOHEMIOS 1"}}, "location": {"latitude": 40.35639654251886, "longitude": -3.6940501846610894}},
{"@type": "CursosTalleres", "id": "50419357", "title": "Taller 'Entre libros y palabras'", "description": "", "free": 1, "price": "", "dtstart": "2026-10-06 11:00:00.0", "dtend": "2026-10-06 23:59:00.0", "time": "11:00", "excluded-days": "", "event-location": "Biblioteca Pública Municipal María Lejárraga (Hortaleza)", "address": {"district": {"@id": "/Distrito/Hortaleza"}, "area": {"postal-code": "28050", "street-address": "CALLE PRINCESA DE EBOLI 29"}}, "location": {"latitude": 40.49041562546542, "longitude": -3.655356751728003}},
{"id": "12130154", "title": "Actividades mensuales de los centros de mayores del distrito Centro", "description": "", "free": 0, "price": "", "dtstart": "2025-11-01 00:00:00.0", "dtend": "2032-04-30 23:59:00.0", "time": "", "excluded-days": "1/5/2026;2/5/2026;3/5/2026;15/5/2026;16/5/2026;17/5/2026;22/5/2026;23/5/2026;24/5/2026;25/5/2026;29/5/2026;30/5/2026;31/5/2026;", "recurrence": {"days": "MO,TU,WE,TH,FR,SA", "frequency": "WEEKLY", "interval": 1}, "audience": "Mayores", "event-location": "", "@type": ""},
{"@type": "Fiestas", "id": "50434766", "title": "Fiestas del Pilar 2026", "description": "12:00 h. Homenaje a la Bandera y Actos a los Caídos, a cargo del REGIMIENTO DE ARTILLERÍA ANTIAÉREA (RAA 71) en la explanada frente a la puerta del Centro Cultural Vaguada en Avda Monforte de Lemos, nº 38.  12:45 h. A continuación, Conciert", "free": 1, "price": "", "dtstart": "2026-10-09 00:00:00.0", "dtend": "2026-10-09 23:59:00.0", "time": "", "excluded-days": "", "event-location": "Auditorio al aire libre. Parque de la Vaguada", "address": {"district": {"@id": "/Distrito/Fuencarral-ElPardo"}, "area": {"postal-code": "28029", "street-address": "PASEO VAGUADA 13"}}, "location": {"latitude": 40.48016602497203, "longitude": -3.7112978051427574}},
{"@type": "ProgramacionDestacadaAgendaCultura", "id": "50432832", "title": "Javier Vercher Cuarteto", "description": "CentroCentro es sede un año más de    JAZZMADRID   , el festival que cada otoño convierte Madrid en el epicentro europeo de este género musical reuniendo a las figuras imprescindibles del panorama actual.    Javier Vercher Cuarteto  10.11.2026  19.00 h   Javier Vercher, saxo  Iñigo Ruiz de Gordejuel", "free": 0, "price": "Entrada general: 5 euros", "dtstart": "2026-11-10 00:00:00.0", "dtend": "2026-11-10 23:59:00.0", "time": "", "excluded-days": "", "event-location": "CentroCentro", "address": {"district": {"@id": "/Distrito/Retiro"}, "area": {"postal-code": "28014", "street-address": "PLAZA CIBELES 1"}}, "location": {"latitude": 40.41902261618159, "longitude": -3.692188193693138}},
{"@type": "RecitalesPresentacionesActosLiterarios", "id": "50382707", "title": "Talleres Junior de cómic avanzado", "description": "Se divide en tres talleres diferentes y complementarios  Sesiones: 18 octubre, 15 noviembre y 13 diciembre.  Más información", "free": 0, "price": "30 euros (más gastos de gestión) las tres sesiones.   Sólo se devolverá el importe de la matrícula en caso de que la organización cancele la actividad.", "dtstart": "2026-10-18 00:00:00.0", "dtend": "2026-12-13 23:59:00.0", "time": "", "excluded-days": "", "recurrence": {"days": "MO,TU,WE,TH,FR,SA,SU", "frequency": "WEEKLY", "interval": 1}, "audience": "Familias", "event-location": "Matadero Madrid", "address": {"district": {"@id": "/Distrito/Arganzuela"}, "area": {"postal-code": "28045", "street-address": "PLAZA LEGAZPI 8"}}, "location": {"latitude": 40.39130985242181, "longitude": -3.6958028442054074}},
{"@type": "RecitalesPresentacionesActosLiterarios", "id": "50363310", "title": "Programas trimestrales en la Casa del Lector", "description": "", "free": 0, "price": "", "dtstart": "2026-09-01 00:00:00.0", "dtend": "2026-12-31 23:59:00.0", "time": "", "excluded-days": "", "recurrence": {"days": "MO,TU,WE,TH,FR,SA,SU", "frequency": "WEEKLY", "interval": 1}, "audience": "Familias", "event-location": "Casa del Lector", "address": {"district": {"@id": "/Distrito/Arganzuela"}, "area": {"postal-code": "28045", "street-address": "PASEO CHOPERA 14"}}, "location": {"latitude": 40.39245798191536, "longitude": -3.6972067902718897}},
{"@type": "ConferenciasColoquios", "id": "50358351", "title": "Visitas guiadas", "description": "Día 2: De santa Cristina a la Glorieta de San Vicente. Punto de encuentro en la puerta de la Iglesia de Santa Cristina, Paseo de Extremadura, 36 Día 16: Argüelles. Punto de encuentro calle Princesa junto a la Iglesia del Buen Suceso. Día 30", "free": 1, "price": "", "dtstart": "2026-10-02 11:30:00.0", "dtend": "2026-10-30 23:59:00.0", "time": "11:30", "excluded-days": "23/10/2026;9/10/2026;", "recurrence": {"days": "MO,TU,WE,TH,FR,SA,SU", "frequency": "WEEKLY", "interval": 1}, "event-location": "Centro Cultural La Vaguada (Fuencarral - El Pardo)", "address": {"district": {"@id": "/Distrito/Fuencarral-ElPardo"}, "area": {"postal-code": "28029", "street-address": "AVENIDA MONFORTE DE LEMOS 38"}}, "location": {"latitude": 40.47900813206291, "longitude": -3.709138377702435}},
{"@type": "Exposiciones", "id": "50434043", "title": "50 Aniversario de la Asociación Vecinal Solidaridad Cuatro Caminos-Tetuán", "description": "Material y fotografías históricas de la Asociación Vecinal Solidaridad Cuatro Caminos-Tetuán. En las plantas baja y primera del centro.", "free": 1, "price": "", "dtstart": "2026-10-19 00:00:00.0", "dtend": "2026-11-10 23:59:00.0", "time": "", "excluded-days": "", "recurrence": {"days": "MO,TU,WE,TH,FR,SA", "frequency": "WEEKLY", "interval": 1}, "event-location": "Centro Sociocultural José de Espronceda (Tetuán)", "address": {"district": {"@id": "/Distrito/Tetuan"}, "area": {"postal-code": "28039", "street-address": "CALLE ALMANSA 9"}}, "location": {"latitude": 40.44864332758775, "longitude": -3.704513191828797}},
{"@type": "Exposiciones", "id": "50438403", "title": "50 Aniversario de la Asociación Vecinal Solidaridad Cuatro Caminos-Tetuán", "description": "", "free": 1, "price": "", "dtstart": "2026-10-19 00:00:00.0", "dtend": "2026-11-10 23:59:00.0", "time": "", "excluded-days": "", "recurrence": {"days": "MO,TU,WE,TH,FR,SA", "frequency": "WEEKLY", "interval": 1}, "event-location": "Centro Sociocultural José de Espronceda (Tetuán)", "address": {"district": {"@id": "/Distrito/Tetuan"}, "area": {"postal-code": "28039", "street-address": "CALLE ALMANSA 9"}}, "location": {"latitude": 40.44864332758775, "longitude": -3.704513191828797}},
{"@type": "Exposiciones", "id": "50423877", "title": "Atlas de Identidades", "description": "Muestra colectiva de artistas africanos y afrodescendientes de la Asociación 'Akiba Art &amp;amp; AfricanCultures'.", "free": 1, "price": "", "dtstart": "2026-10-09 00:00:00.0", "dtend": "2026-10-30 23:59:00.0", "time": "", "excluded-days": "", "recurrence": {"days": "MO,TU,WE,TH,FR,SA", "frequency": "WEEKLY", "interval": 1}, "event-location": "Auditorio y sala de exposiciones Paco de Lucía (Latina)", "address": {"district": {"@id": "/Distrito/Latina"}, "area": {"postal-code": "28044", "street-address": "AVENIDA LAS AGUILAS 2"}}, "location": {"latitude": 40.38493929920335, "longitude": -3.7641963952601154}},
{"@type": "ProgramacionDestacadaAgendaCultura", "id": "50431924", "title": "Cine con música en directo: 'Siete ocasiones'", "description": "Adrián Begoña interpretará y acompañará al piano 'Siete ocasiones', una de las comedias más hilarantes de Buster Keaton. Buster encarna a un joven empresario que se encuentra en bancarrota y al que le llega una noticia sorprendente: un acau", "free": 1, "price": "", "dtstart": "2026-10-17 00:00:00.0", "dtend": "2026-10-17 23:59:00.0", "time": "", "excluded-days": "", "audience": "Familias", "event-location": "Centro Cultural San Juan Bautista (Ciudad Lineal)", "address": {"district": {"@id": "/Distrito/CiudadLineal"}, "area": {"postal-code": "28043", "street-address": "CALLE SAN NEMESIO 4"}}, "location": {"latitude": 40.45224710166, "longitude": -3.658029027767765}},
{"@type": "TeatroPerformance", "id": "50322750", "title": "El sueño de una noche de verano", "description": "Teatro.   Dirección y adaptación: Pedro Casas. Primera sesión 18:00h. Segunda sesión 19:45h.     Reparto de entradas desde una hora antes en el centro cultural. Máximo dos entradas por persona hasta completar aforo.", "free": 1, "price": "", "dtstart": "2026-10-01 18:00:00.0", "dtend": "2026-10-01 23:59:00.0", "time": "18:00", "excluded-days": "", "event-location": "Centro Cultural Casa de Vacas (Retiro)", "address": {"district": {"@id": "/Distrito/Retiro"}, "area": {"postal-code": "28009", "street-address": "PASEO COLOMBIA 1"}}, "location": {"latitude": 40.41906571939961, "longitude": -3.684053112760816}}
], "B": [
{"@type": "ProgramacionDestacadaAgendaCultura", "id": "50431924", "title": "Cine con música en directo: 'Siete ocasiones'", "description": "Adrián Begoña interpretará y acompañará al piano 'Siete ocasiones', una de las comedias más hilarantes de Buster Keaton. Buster encarna a un joven empresario que se encuentra en bancarrota y al que le llega una noticia sorprendente: un acau", "free": 1, "price": "", "dtstart": "2026-10-17 19:00:00.0", "dtend": "2026-10-17 23:59:00.0", "time": "19:00", "excluded-days": "", "audience": "Familias", "event-location": "Centro Cultural San Juan Bautista (Ciudad Lineal)", "address": {"district": {"@id": "/Distrito/CiudadLineal"}, "area": {"postal-code": "28043", "street-address": "CALLE SAN NEMESIO 4"}}, "location": {"latitude": 40.45224710166, "longitude": -3.658029027767765}},
{"@type": "Exposiciones", "id": "50322750", "title": "Eco-reciclarte", "description": "Desfile.   Reciclar, rediseñar y reutilizar.   Reparto de entradas desde una hora antes en el centro cultural. Máximo dos entradas por persona hasta completar aforo.", "free": 1, "price": "", "dtstart": "2026-10-17 12:00:00.0", "dtend": "2026-10-17 23:59:00.0", "time": "12:00", "excluded-days": "", "event-location": "Centro Cultural Casa de Vacas (Retiro)", "address": {"district": {"@id": "/Distrito/Retiro"}, "area": {"postal-code": "28009", "street-address": "PASEO COLOMBIA 1"}}, "location": {"latitude": 40.41906571939961, "longitude": -3.684053112760816}},
{"@type": "TeatroPerformance", "id": "50433813", "title": "The Primitals", "description": "Yllana y Primital Bros se unen para sorprendernos con una divertidísima comedia musical a capela. Cuatro aborígenes de un planeta que podría ser el nuestro reclaman el escenario, dispuestos a conquistar al público, a carcajadas o a machetaz", "free": 1, "price": "", "dtstart": "2026-10-18 19:00:00.0", "dtend": "2026-10-18 23:59:00.0", "time": "19:00", "excluded-days": "", "event-location": "Centro Cultural El Pozo del tío Raimundo (Puente de Vallecas)", "address": {"district": {"@id": "/Distrito/PuenteDeVallecas"}, "area": {"postal-code": "28053", "street-address": "AVENIDA GLORIETAS 19"}}, "location": {"latitude": 40.37381624358889, "longitude": -3.6599325731670977}}
]}""")
CSV_FIXTURE = json.loads(r"""[
{"ID-EVENTO": "50410916", "TITULO": "Actividades Cuerpo y Mente - Deporte en la Calle - Distrito de Villaverde", "PRECIO": null, "GRATUITO": "1", "DIAS-SEMANA": "L", "DIAS-EXCLUIDOS": "28/12/2026;4/1/2027;", "FECHA": "2026-09-07 00:00:00.0", "FECHA-FIN": "2027-06-30 23:59:00.0", "HORA": "18:00", "DESCRIPCION": null, "CONTENT-URL": "http://www.madrid.es/sites/v/index.jsp", "NOMBRE-INSTALACION": "Instalación Deportiva Municipal Básica María de Villota", "CLASE-VIAL-INSTALACION": "CALLE", "NOMBRE-VIA-INSTALACION": "ESTEFANITA", "NUM-INSTALACION": "3", "DISTRITO-INSTALACION": "VILLAVERDE", "CODIGO-POSTAL-INSTALACION": "28021", "LATITUD": "40.34750435286862", "LONGITUD": "-3.6756560101604467", "TIPO": "/contenido/actividades/ActividadesDeportivas", "AUDIENCIA": null},
{"ID-EVENTO": "50433813", "TITULO": "The Primitals", "PRECIO": null, "GRATUITO": "1", "DIAS-SEMANA": null, "DIAS-EXCLUIDOS": null, "FECHA": "2026-10-18 00:00:00.0", "FECHA-FIN": "2026-10-18 23:59:00.0", "HORA": "19:00", "DESCRIPCION": "Yllana y Primital Bros se unen para sorprendernos con una divertidísima comedia musical a capela. Cuatro aborígenes de un planeta que podría ser el nuestro reclaman el escenario, dispuestos a conquist", "CONTENT-URL": "http://www.madrid.es/sites/v/index.jsp", "NOMBRE-INSTALACION": "Centro Cultural El Pozo del tío Raimundo (Puente de Vallecas)", "CLASE-VIAL-INSTALACION": "AVENIDA", "NOMBRE-VIA-INSTALACION": "GLORIETAS", "NUM-INSTALACION": "19", "DISTRITO-INSTALACION": "PUENTE DE VALLECAS", "CODIGO-POSTAL-INSTALACION": "28053", "LATITUD": "40.37381624358889", "LONGITUD": "-3.6599325731670977", "TIPO": "/contenido/actividades/TeatroPerformance", "AUDIENCIA": null},
{"ID-EVENTO": "50363659", "TITULO": "Ludotecas", "PRECIO": null, "GRATUITO": "1", "DIAS-SEMANA": "S", "DIAS-EXCLUIDOS": "31/10/2026;", "FECHA": "2026-10-03 00:00:00.0", "FECHA-FIN": "2026-10-24 23:59:00.0", "HORA": "10:30", "DESCRIPCION": "Sábados 3, 10, 17 y 24 de octubre, de 10:30 a 13:30 horas.  Actividad gratuita y de carácter abierto, sin inscripción previa, para niños/as con edades comprendidas entre 4 y 10 años.  Aforo limitado.", "CONTENT-URL": "http://www.madrid.es/sites/v/index.jsp", "NOMBRE-INSTALACION": "Centro Sociocultural Alfonso XII (Fuencarral - El Pardo)", "CLASE-VIAL-INSTALACION": "CALLE", "NOMBRE-VIA-INSTALACION": "MIRA EL RIO", "NUM-INSTALACION": "4", "DISTRITO-INSTALACION": "FUENCARRAL-EL PARDO", "CODIGO-POSTAL-INSTALACION": "28048", "LATITUD": "40.519673311658686", "LONGITUD": "-3.7779055687726535", "TIPO": "/contenido/actividades/CursosTalleres", "AUDIENCIA": "/usuario/Niños"},
{"ID-EVENTO": "50437359", "TITULO": "CICLO PREPARANDO HALLOWEEN. Laboratorio infantil de creatividad.", "PRECIO": null, "GRATUITO": "1", "DIAS-SEMANA": "S", "DIAS-EXCLUIDOS": "24/10/2026;", "FECHA": "2026-10-17 00:00:00.0", "FECHA-FIN": "2026-10-31 23:59:00.0", "HORA": "17:00", "DESCRIPCION": "Actividad para niñ@s de entre 5 y 12 años.* De 17:00 a 19:00 h.     Sábado 17. Entre momias y fantasmas: crearemos pequeñas esculturas de momias y un móvil de fantasmas para decorar la casa en Halloween.     Sábado 31. Espacios tenebrosos: recordamos los espacios donde pueden ocurrir historias de miedo y crearemos pequeños dioramas para llevarnos a casa.    * Actividad gratuita hasta completar aforo (20 plazas). Dirigido a niñ@s de entre 5 y 12 años, siendo necesario tener la edad indicada para participar en la actividad. Sin inscripción previa, por orden de llegada hasta completar las plazas disponibles. Necesar", "CONTENT-URL": "http://www.madrid.es/sites/v/index.jsp", "NOMBRE-INSTALACION": "Centro Sociocultural Montecarmelo (Fuencarral - El Pardo)", "CLASE-VIAL-INSTALACION": "Carretera", "NOMBRE-VIA-INSTALACION": "M-607 Km 13 L-10", "NUM-INSTALACION": null, "DISTRITO-INSTALACION": "FUENCARRAL-EL PARDO", "CODIGO-POSTAL-INSTALACION": "28049", "LATITUD": "40.515712291624084", "LONGITUD": "-3.6860532895354448", "TIPO": "/contenido/actividades/CursosTalleres", "AUDIENCIA": "/usuario/Niños"},
{"ID-EVENTO": "50423847", "TITULO": "El sueño", "PRECIO": "Entrada: 7  euros.", "GRATUITO": "0", "DIAS-SEMANA": "V", "DIAS-EXCLUIDOS": null, "FECHA": "2026-10-02 00:00:00.0", "FECHA-FIN": "2026-10-23 23:59:00.0", "HORA": "19:00", "DESCRIPCION": "El sueño tiene como punto de partida un encuentro imaginario entre la escultora francesa Camille Claudel y el pintor y escultor suizo Alberto Giacometti. Esta propuesta poética-onírica coloca a dichos", "CONTENT-URL": "http://www.madrid.es/sites/v/index.jsp", "NOMBRE-INSTALACION": "CentroCentro", "CLASE-VIAL-INSTALACION": "PLAZA", "NOMBRE-VIA-INSTALACION": "CIBELES", "NUM-INSTALACION": "1", "DISTRITO-INSTALACION": "RETIRO", "CODIGO-POSTAL-INSTALACION": "28014", "LATITUD": "40.41902261618159", "LONGITUD": "-3.692188193693138", "TIPO": "/contenido/actividades/TeatroPerformance", "AUDIENCIA": null},
{"ID-EVENTO": "50430740", "TITULO": "La sombra de la Parca: relatos de vida y muerte", "PRECIO": null, "GRATUITO": "1", "DIAS-SEMANA": null, "DIAS-EXCLUIDOS": null, "FECHA": "2026-10-31 00:00:00.0", "FECHA-FIN": "2026-10-31 23:59:00.0", "HORA": "11:30", "DESCRIPCION": "Edad: Todos los públicos, a partir de 12 años (menores acompañados por un adulto, un adulto por niño). Imprescindible inscripción previa.  Sinopsis: Desde que el mundo es mundo, la Humanidad ha intent", "CONTENT-URL": "http://www.madrid.es/sites/v/index.jsp", "NOMBRE-INSTALACION": "Centro de Educación Ambiental y Cultural Maris Stella (Usera)", "CLASE-VIAL-INSTALACION": "CALLE", "NOMBRE-VIA-INSTALACION": "DOCTOR TOLOSA LATOUR", "NUM-INSTALACION": "16", "DISTRITO-INSTALACION": "USERA", "CODIGO-POSTAL-INSTALACION": "28026", "LATITUD": "40.374166248648216", "LONGITUD": "-3.7032220308808577", "TIPO": "/contenido/actividades/TeatroPerformance", "AUDIENCIA": "/usuario/Niños,/usuario/Familias"},
{"ID-EVENTO": "12130154", "TITULO": "Actividades mensuales de los centros de mayores del distrito Centro", "PRECIO": null, "GRATUITO": "0", "DIAS-SEMANA": "L,M,X,J,V,S", "DIAS-EXCLUIDOS": "1/5/2026;2/5/2026;3/5/2026;15/5/2026;16/5/2026;17/5/2026;22/5/2026;23/5/2026;24/5/2026;25/5/2026;29/5/2026;30/5/2026;31/5/2026;", "FECHA": "2025-11-01 00:00:00.0", "FECHA-FIN": "2032-04-30 23:59:00.0", "HORA": null, "DESCRIPCION": null, "CONTENT-URL": "http://www.madrid.es/sites/v/index.jsp", "NOMBRE-INSTALACION": null, "CLASE-VIAL-INSTALACION": null, "NOMBRE-VIA-INSTALACION": null, "NUM-INSTALACION": null, "DISTRITO-INSTALACION": null, "CODIGO-POSTAL-INSTALACION": null, "LATITUD": null, "LONGITUD": null, "TIPO": null, "AUDIENCIA": "/usuario/Mayores"},
{"ID-EVENTO": "12516239", "TITULO": "Taller online 'Remedial English'", "PRECIO": null, "GRATUITO": "0", "DIAS-SEMANA": "M", "DIAS-EXCLUIDOS": null, "FECHA": "2026-10-20 00:00:00.0", "FECHA-FIN": "2027-06-02 23:59:00.0", "HORA": "18:00", "DESCRIPCION": "Remedial English es una actividad gratuita en línea dirigida a adultos con nivel A2/B1 que necesiten reforzar su inglés. Es un espacio de apoyo para superar obstáculos fonológicos y morfosintácticos q", "CONTENT-URL": "http://www.madrid.es/sites/v/index.jsp", "NOMBRE-INSTALACION": "Biblioteca Pública Municipal José Hierro (San Blas-Canillejas)", "CLASE-VIAL-INSTALACION": "CALLE", "NOMBRE-VIA-INSTALACION": "MARIA SEVILLA DIAGO", "NUM-INSTALACION": "15", "DISTRITO-INSTALACION": "SAN BLAS-CANILLEJAS", "CODIGO-POSTAL-INSTALACION": "28022", "LATITUD": "40.433291625973624", "LONGITUD": "-3.6105662311927453", "TIPO": "/contenido/actividades/CursosTalleres/Idiomas", "AUDIENCIA": null}
]""")
BY = {i["id"]: i for i in FIXTURE["A"]}


def items(files=None):
    return T.merge_files(files or [FIXTURE["A"], FIXTURE["B"]], {})


def run(its=None, cfg=None):
    st = {}
    return T.events(its if its is not None else items(), cfg or CFG, TZ, TODAY, st), st


def synth(base, **over):
    """A fixture item with fields replaced - SYNTHETIC, said where used."""
    r = json.loads(json.dumps(BY[base]))
    r.update(over)
    return T.clean_item(r)


def of(evs, ident):
    return sorted((e for e in evs if e.source_id.split("|")[0] == ident), key=lambda e: e.start_local)


def days(evs):
    return [e.start_local[:10] for e in evs]


EVS, ST = run()
CFG_EXH = dict(CFG, write_exhibitions=True)
EVS_EXH, ST_EXH = run(cfg=CFG_EXH)

# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------
check("the licence line fits the sync's kept tail (<= 200) and names CC BY 4.0",
      len(CFG["attribution"]) <= 200 and "CC BY 4.0" in CFG["attribution"], CFG["attribution"])
cats = set(CFG["category_by_type"].values()) | {CFG["category_default"]}
check("every configured category is a real lens key", cats <= set(VALID_CATEGORIES), cats - set(VALID_CATEGORIES))
check("skip_dates are real dates, in order, and reach past today + 90",
      CFG["skip_dates"] == sorted(CFG["skip_dates"]) and max(SKIP) >= HORIZON, (max(SKIP), HORIZON))
check("12 Oct and 9 Nov 2026 (Fiesta Nacional, Almudena) are listed, and are Mondays",
      {date(2026, 10, 12), date(2026, 11, 9)} <= SKIP and date(2026, 10, 12).weekday() == 0
      and date(2026, 11, 9).weekday() == 0)
check("PARKED: madrid_sources.json.pending-permission exists and no live madrid_sources.json beside it "
      "(robots.txt Disallows /api/3/action/datastore_search by name; flip this when the Ayuntamiento says yes)",
      os.path.exists(CONFIG) and not os.path.exists(LIVE))
check("PARKED: `permission` is empty, and the comment says why and how to switch it on",
      CFG.get("permission") == "" and "Disallow: /api/3/action/datastore_search" in CFG["_comment"]
      and "Contacta" in CFG["_comment"], CFG.get("permission"))
check("Crawl-delay 10, and every source is a datastore resource read through the CKAN API",
      CFG["crawl_delay"] == 10 and CFG["api"].endswith("/api/3/action/datastore_search")
      and all(s.get("resource_id") and "url" not in s for s in CFG["sources"]))
check("the licence line names both datasets (an item can be in 206974 only)",
      "Agenda de actividades y eventos" in CFG["attribution"] and "100 días" in CFG["attribution"], CFG["attribution"])
check("exhibitions are off by default (1,432 all-day rows from 64 exhibitions on the 2026-10-05 pull)",
      CFG.get("write_exhibitions") is False)

# ---------------------------------------------------------------------------
# two files, one item
# ---------------------------------------------------------------------------
st = {}
merged = T.merge_files([FIXTURE["A"], FIXTURE["B"]], st)
by_id = {}
for it in merged:
    by_id.setdefault(it["id"], []).append(it)
check("an id in both files is ONE item (The Primitals, 50433813)", len(by_id["50433813"]) == 1, by_id["50433813"])
check("when the files disagree on one date's clock, the clock wins (50431924: '' vs 19:00)",
      [i["time"] for i in by_id["50431924"]] == ["19:00"], by_id["50431924"])
check("two different titles under one id are two items (50322750: a play and 'Eco-reciclarte')",
      sorted(i["title"] for i in by_id["50322750"]) == ["Eco-reciclarte", "El sueño de una noche de verano"])
check("the merge counts what it did", st.get("items in both files (merged)") == 2
      and st.get("an id carrying two different titles (both kept)") == 1, st)
atlas = [i for i in merged if i["id"] == "50423877"][0]
check("a twice-escaped '&amp;amp;' reads '&'", "Art & " in atlas["description"] and "&amp;" not in atlas["description"],
      atlas["description"][:120])
coll = [i for i in merged if i["id"] == "50432799"][0]
check("a glued price field ('5 eurosDescuentos') is two sentences", coll["price"].startswith("Entrada general: 5 euros. Descuentos"),
      coll["price"])

# ---------------------------------------------------------------------------
# who may come
# ---------------------------------------------------------------------------
V = {i["id"]: T.verdict(i) for i in merged}
check("'Imprescindible inscripción previa' is refused (La sombra de la Parca)",
      V["50430740"] == (False, "registration required (inscripción previa)"), V["50430740"])
check("'sin inscripción previa' keeps a row although it contains 'inscripción previa' (Ludotecas)",
      V["50363659"][0] and "no registration" in V["50363659"][1], V["50363659"])
check("'Previas reserva de entradas' is a ticket, not a registration (Concierto barroco)", V["50420957"][0], V["50420957"])
check("SYNTHETIC: 'previa reserva de entradas' is a ticket too",
      T.verdict(synth("50433813", description="Concierto. Previa reserva de entradas."))[0])
check("SYNTHETIC: 'Imprescindible reserva previa' is a registration",
      not T.verdict(synth("50433813", description="Imprescindible reserva previa en el centro."))[0])
check("SYNTHETIC: 'Las inscripciones a las actividades se ...' is a registration (the CIEA's walks)",
      not T.verdict(synth("50433813", description="Forma de inscripción y normas: las inscripciones a las actividades se hacen en la web."))[0])
check("SYNTHETIC: 'No es necesaria la inscripción' keeps the row",
      T.verdict(synth("50433813", description="No es necesaria la inscripción."))[0])
check("SYNTHETIC: 'sin necesidad de realizar inscripción' is a negated registration, not a registration",
      T.verdict(synth("50433813", description="Concierto sin necesidad de realizar inscripción."))[0])
check("SYNTHETIC: a title naming a season workshop with no text is refused, whatever its type "
      "('Taller de iniciación a castañuelas. Turno de mañana', Música, 50417240)",
      T.verdict(synth("50433813", title="Taller de iniciación a castañuelas. Turno de mañana", description="",
                      **{"@type": "Musica"})) == (False, "a workshop, visit or club with no word on how to join"))
check("an online class pinned to a library is refused ('por videoconferencia', 'en línea')",
      V["12516239"] == (False, "online, not at the venue"), V["12516239"])
check("an application call is refused ('Convocatoria abierta ... solicitudes', Artrics)",
      V["50428616"] == (False, "an application call, not a session"), V["50428616"])
check("a workshop whose text says nothing about joining is refused (Taller 'Entre libros y palabras')",
      V["50419357"] == (False, "a workshop, visit or club with no word on how to join"), V["50419357"])
check("SYNTHETIC: the same workshop with 'Entrada libre hasta completar aforo' is kept",
      T.verdict(synth("50419357", description="Entrada libre hasta completar aforo."))[0])
check("a title naming a workshop needs the words too, whatever its type ('Talleres Junior de cómic', Recitales)",
      not V["50382707"][0], V["50382707"])
check("a concert with no text is an agenda event and kept (The Primitals)", V["50433813"] == (True, "an event in the city's agenda"))
check("'Deporte en la Calle' (empty text; sign-ups on inscripciones.pebetero.com) is refused",
      V["50410916"] == (False, "a programme that signs people up on another site (Deporte en la Calle)"), V["50410916"])
check("SYNTHETIC: any text that names an inscripciones.<site> form is refused, whatever its title",
      not T.verdict(synth("50433813", description="Más información en inscripciones.pebetero.com"))[0])
check("SYNTHETIC: a silent row at an Espacio de Igualdad is a workshop (73 of their 75 items had no text)",
      T.verdict(synth("50433813", description="", **{"event-location": "Espacio de Igualdad Nieves Torres. Chamartín"}))
      == (False, "a workshop, visit or club with no word on how to join"))
check("SYNTHETIC: ...but their public commemoration (ComemoracionesHomenajes) is an act anyone attends",
      T.verdict(synth("50433813", description="", **{"@type": "ComemoracionesHomenajes",
                                                    "event-location": "Espacio de Igualdad Nieves Torres. Chamartín"}))[0])
season = {"recurrence": {"days": "TU", "frequency": "WEEKLY", "interval": 1}, "description": "",
          "dtstart": "2026-10-06 00:00:00.0", "dtend": "2026-12-15 23:59:00.0", "time": "18:00"}
check("SYNTHETIC: a ten-week weekly season with no text is refused unless an audience watches it (sport: refused)",
      T.verdict(synth("50433813", title="Grupo de crianza", **dict(season, **{"@type": "ActividadesDeportivas"})))
      == (False, "a season of weekly sessions with no text at all (how to join is not said)"))
check("SYNTHETIC: ...a cine-club season with no text is kept (a screening is watched, not joined)",
      T.verdict(synth("50433813", title="Cineclub", **dict(season, **{"@type": "CineActividadesAudiovisuales"})))[0])

# ---------------------------------------------------------------------------
# when
# ---------------------------------------------------------------------------
MONDAYS = ["2026-10-05", "2026-10-19", "2026-10-26", "2026-11-02", "2026-11-16", "2026-11-23",
           "2026-11-30", "2026-12-07", "2026-12-14", "2026-12-21"]
dep_d, dep_c, _ = T.sessions(T.clean_item(BY["50410916"]), TODAY, HORIZON, SKIP, {})
check("weekly Mondays at 18:00, skipping 12 Oct and 9 Nov (holidays) and the City's excluded 28 Dec "
      "(Deporte en la Calle's recurrence; the item itself is refused for its web sign-up)",
      [x.isoformat() for x in dep_d] == MONDAYS and dep_c == 18 * 60, (dep_d, dep_c))
mon_evs = of(run([synth("50410916", title="SYNTHETIC Petanca de los lunes",
                        description="Petanca para todos los públicos.")])[0], "50410916")
check("SYNTHETIC: the same recurrence as a kept item is one row per Monday at 18:00",
      days(mon_evs) == MONDAYS and all(e.start_local[11:16] == "18:00" for e in mon_evs), days(mon_evs))
lud = of(EVS, "50363659")
check("'Sábados 3, 10, 17 y 24 de octubre' are the sessions: 10, 17, 24 Oct at 10:30-13:30 (3 Oct is past)",
      days(lud) == ["2026-10-10", "2026-10-17", "2026-10-24"]
      and all(e.start_local[11:16] == "10:30" and (e.end_local or "")[11:16] == "13:30" for e in lud),
      [(e.start_local, e.end_local) for e in lud])
hal = of(EVS, "50437359")
check("'Sábado 17 ... Sábado 31' writes those two Saturdays, not the 24th the recurrence also covers",
      days(hal) == ["2026-10-17", "2026-10-31"] and all((e.end_local or "")[11:16] == "19:00" for e in hal),
      [(e.start_local, e.end_local) for e in hal])
hal2 = synth("50437359", **{"excluded-days": ""})
d4, _, sh4 = T.sessions(hal2, TODAY, HORIZON, SKIP, {})
check("SYNTHETIC: without the City's own exclusion of 24 Oct, the text's two Saturdays are still the only ones",
      [x.isoformat() for x in d4] == ["2026-10-17", "2026-10-31"] and sh4 == "the dates the text names", (d4, sh4))
sal = synth("50410916", title="SYNTHETIC Petanca de los lunes", description="Todas las sesiones serán de 18:00 a 19:00 horas, salvo el 19 de octubre "
                                     "que será de 17 a 18:30 horas.")
sal_evs = of(run([sal])[0], "50410916")
check("SYNTHETIC: 'salvo el 19 de octubre que será de 17 a 18:30' moves that session only "
      "(a reading club's 23 Oct and 20 Nov, live 2026-10-05)",
      [(e.start_local[5:16], (e.end_local or "")[11:16]) for e in sal_evs][1:3]
      == [("10-19T17:00", "18:30"), ("10-26T18:00", "19:00")], [(e.start_local, e.end_local) for e in sal_evs][:3])
shut = synth("50410916", title="SYNTHETIC Petanca de los lunes", description="Todos los lunes de 18 a 19 horas, excepto el 19 de octubre. "
                                      "No habrá sesión el 26 de octubre.")
shut_days = days(of(run([shut])[0], "50410916"))
check("SYNTHETIC: 'excepto el 19 de octubre' and 'No habrá sesión el 26 de octubre' close those Mondays - "
      "never read as the only two sessions", "2026-10-19" not in shut_days and "2026-10-26" not in shut_days
      and shut_days[:2] == ["2026-10-05", "2026-11-02"] and len(shut_days) == 8, shut_days)
sue = of(EVS, "50423847")
check("a weekly play: Fridays 9, 16, 23 Oct at 19:00 (2 Oct is past)", days(sue) == ["2026-10-09", "2026-10-16", "2026-10-23"],
      days(sue))
check("every day of four months with no clock is the City's placeholder, refused ('Programas trimestrales')",
      not of(EVS, "50363310")
      and ST.get("refused: every day of a run, no clock and no dates (the City's placeholder)", 0) >= 1, ST)
two_days = synth("50363310", recurrence={"days": "TU,TH", "frequency": "WEEKLY", "interval": 1},
                 dtstart="2026-10-06 00:00:00.0", dtend="2026-11-26 23:59:00.0", time="")
check("SYNTHETIC: Tuesdays and Thursdays for eight weeks with no clock is a period, refused",
      T.sessions(two_days, TODAY, HORIZON, SKIP, {})[2] == "refused: a period with no clock, not a timetable")
check("one clock on every day for four weeks is a period, refused ('Visitas guiadas' on 2, 16 and 30 Oct)",
      not of(EVS, "50358351") and V["50358351"] == (False, "a workshop, visit or club with no word on how to join")
      and T.sessions(BY["50358351"], TODAY, HORIZON, SKIP, {})[2]
      == "refused: one clock on 3+ days a week for weeks - a period, not a timetable", V["50358351"])
check("nth_weekdays: 'el tercer jueves de cada mes' is 15 Oct, 19 Nov, 17 Dec",
      T.nth_weekdays("se reuniran el tercer jueves de cada mes", date(2026, 10, 1), date(2026, 12, 31))
      == {date(2026, 10, 15), date(2026, 11, 19), date(2026, 12, 17)})
check("nth_weekdays: 'el primer y tercer martes de cada mes' and 'el ultimo viernes de cada mes'",
      T.nth_weekdays("el primer y tercer martes de cada mes", date(2026, 10, 1), date(2026, 10, 31))
      == {date(2026, 10, 6), date(2026, 10, 20)}
      and T.nth_weekdays("el ultimo viernes de cada mes", date(2026, 11, 1), date(2026, 12, 31))
      == {date(2026, 11, 27), date(2026, 12, 25)})
nth_item = synth("50410916", description="Nos reunimos el segundo lunes de cada mes.")
d2, c2, sh2 = T.sessions(nth_item, TODAY, HORIZON, SKIP, {})
check("SYNTHETIC: a weekly Monday row whose text says 'el segundo lunes de cada mes' writes 9 Nov? no - "
      "12 Oct and 9 Nov are holidays: 14 Dec only", [x.isoformat() for x in d2] == ["2026-12-14"], (d2, sh2))
mon_item = synth("50410916", description="Una vez al mes, en la biblioteca.")
check("SYNTHETIC: 'una vez al mes' with no dates given is refused, not written weekly",
      T.sessions(mon_item, TODAY, HORIZON, SKIP, {})[2].startswith("refused: monthly"))
check("named_dates: 'Del 01 al 29 de octubre' is a range, never two sessions",
      T.named_dates("del 01 al 29 de octubre. de lunes a viernes", date(2026, 10, 1), date(2026, 10, 29)) == set())
check("named_dates: '15, 22 y 29 de octubre ... 5, 12 y 19 de noviembre' is six dates",
      T.named_dates("15, 22 y 29 de octubre: de 10:00 a 11:30. 5, 12 y 19 de noviembre", date(2026, 10, 15),
                    date(2026, 11, 19)) == {date(2026, 10, 15), date(2026, 10, 22), date(2026, 10, 29),
                                            date(2026, 11, 5), date(2026, 11, 12), date(2026, 11, 19)})
pil = of(EVS, "50434766")
check("a one-day row with no clock and many in its text is ALL-DAY: a bare date (Fiestas del Pilar)",
      len(pil) == 1 and pil[0].start_local == "2026-10-09" and pil[0].end_local is None, [(e.start_local, e.end_local) for e in pil])
ver = of(EVS, "50432832")
check("a one-day row with no clock and ONE in its text ('10.11.2026  19.00 h') starts then (Javier Vercher)",
      [e.start_local for e in ver] == ["2026-11-10T19:00:00"], [e.start_local for e in ver])
span_item = synth("50432832", description="Concierto. De 17:00 a 19:00 h, en la sala.")
d3, c3, _ = T.sessions(span_item, TODAY, HORIZON, SKIP, {})
check("SYNTHETIC: 'de 17:00 a 19:00 h' on a one-day row with no clock starts at 17:00, never 19:00", c3 == 17 * 60, c3)
exh = of(EVS, "50363653")
check("an exhibition is not written while write_exhibitions is false", not exh
      and ST.get("refused: an exhibition (write_exhibitions is false in the config)", 0) >= 1, ST)
exh = of(EVS_EXH, "50363653")
check("with write_exhibitions, an exhibition is one ALL-DAY row per open day, holidays included ('festivos' open)",
      len(exh) == 25 and exh[0].start_local == "2026-10-05" and "2026-10-12" in days(exh)
      and all(len(e.start_local) == 10 and e.end_local is None for e in exh), (len(exh), days(exh)[:3]))
fold = [e for e in EVS_EXH if "Aniversario" in e.name]
check("an exhibition published under two ids is one row a day, keyed on the lower id, with the text the other lacks",
      fold and all(e.source_id.startswith("50434043|") for e in fold)
      and len(fold) == len({e.start_local for e in fold}) and all(len(e.description) > 300 for e in fold),
      [(e.source_id, len(e.description)) for e in fold][:3])


def no_gap(e):
    """A timed row ends on its own day after it starts, or has no end; an
    all-day row is a bare date with no end."""
    if len(e.start_local) == 10:
        return e.end_local is None
    return e.end_local is None or (e.end_local[:10] == e.start_local[:10] and e.end_local > e.start_local)


check("NO row spans a gap: every row is one session on one day", all(no_gap(e) for e in EVS + EVS_EXH),
      [(e.name, e.start_local, e.end_local) for e in EVS + EVS_EXH if not no_gap(e)][:3])
check("nothing is written before today or past today + 90",
      all(TODAY.isoformat() <= e.start_local[:10] <= HORIZON.isoformat() for e in EVS + EVS_EXH))

# ---------------------------------------------------------------------------
# the price, and 0227
# ---------------------------------------------------------------------------
fee = [e for e in EVS if "(no es gratis)" in e.description]
check("fee rows: the price is the FIRST paragraph and ends '(no es gratis)' (El sueño 7 euros, Javier Vercher 5 euros)",
      {e.source_id.split("|")[0] for e in fee} >= {"50423847", "50432832"}
      and all(e.description.split("\n\n")[0].startswith("Precio: ") for e in fee), [e.name for e in fee])
check("0227 reads no fee row as free", not any(tagged_free(e.name, e.description) for e in fee))
mixed = synth("50423847", free=0, price="Gratuito para menores de 12 años; resto 5 euros")
check("SYNTHETIC: an amount beside 'gratuito para menores' is a fee: '(no es gratis)', never free",
      T.price_line(mixed)[0] == "fee" and "(no es gratis)" in T.price_line(mixed)[1], T.price_line(mixed))
flagged = synth("50423847", free=1, price="Entrada: 7 euros")
check("SYNTHETIC: an amount wins over the City's free flag", T.price_line(flagged)[0] == "fee", T.price_line(flagged))
free = [e for e in EVS if e.description.startswith("Actividad gratuita")]
check("free rows say 'Actividad gratuita' from the City's flag, and 0227 reads them free",
      free and all(tagged_free(e.name, e.description) for e in free), len(free))
unk = synth("50433813", free=0, price="", description="Esta actividad gratuita es para los socios del club de lectura y sus familias. " * 1)
evs_u, st_u = run([unk])
check("SYNTHETIC: neither flag nor price says free but the text does - the text is withheld, 'Precio no indicado.'",
      evs_u and evs_u[0].description.startswith("Precio no indicado.") and "gratuita" not in evs_u[0].description
      and not tagged_free(evs_u[0].name, evs_u[0].description), [e.description[:120] for e in evs_u])
long_fee = synth("50423847", description="Una obra de teatro gratuita para el público infantil. " * 40)
evs_l, _ = run([long_fee])
rows_l = [stored(e) for e in evs_l]
check("SYNTHETIC: a fee row with a 2,000-character text keeps '(no es gratis)' and the licence after the sync's cap",
      rows_l and all("(no es gratis)" in r["description"] and CFG["attribution"] in r["description"]
                     and not tagged_free(r["title"], r["description"]) for r in rows_l),
      [r["description"][:200] for r in rows_l][:1])
check("every fixture row's stored text keeps its price line and the licence (to_row)",
      all(e.description.split("\n\n")[0] in stored(e)["description"] and CFG["attribution"] in stored(e)["description"]
          for e in EVS + EVS_EXH))
check("the stored verdict matches the adapter's tier on every row",
      all(tagged_free(stored(e)["title"], stored(e)["description"]) == e.description.startswith("Actividad gratuita")
          for e in EVS + EVS_EXH))

# ---------------------------------------------------------------------------
# where, and which source
# ---------------------------------------------------------------------------
box = CFG["bbox"]
check("every row carries the City's point, marked exact, inside the municipality",
      all(e.coords_exact and box[0] <= e.latitude <= box[2] and box[1] <= e.longitude <= box[3] for e in EVS))
check("an item with no location is never written (centros de mayores' monthly programme, 12130154)",
      not of(EVS, "12130154"))
far = synth("50433813", id="99999901", title="SYNTHETIC far", location={"latitude": 40.60, "longitude": -3.60})
near = [synth("50433813", id=f"9999990{n}", title=f"SYNTHETIC near {n}", **{"event-location": f"Venue {n}"},
              location={"latitude": 40.3738 + n / 1000, "longitude": -3.6599}) for n in (2, 3)]
evs_f, st_f = run([far] + near)
check("SYNTHETIC: a point 25 km from both other venues of its postal code is refused, the two near ones written",
      {e.name for e in evs_f} == {"SYNTHETIC near 2", "SYNTHETIC near 3"}
      and st_f.get("unplaceable (the City's point contradicts its own postal code)") == 1, (st_f, [e.name for e in evs_f]))
src = {e.source_id.split("|")[0]: e.source for e in EVS}
check("a centro cultural is madrid:centros; CentroCentro is madrid:agenda",
      src.get("50433813") == "madrid:centros" and src.get("50363659") == "madrid:centros"
      and src.get("50423847") == "madrid:agenda" and src.get("50432799") == "madrid:agenda", src)
check("every row's category, and every derived category, is a real lens key",
      all(e.category in VALID_CATEGORIES and all(c in VALID_CATEGORIES for c in e.categories) for e in EVS)
      and all(derive_categories(dict(e.__dict__))[0] in VALID_CATEGORIES for e in EVS))

# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------
check("source_ids and fingerprints are unique", len({e.source_id for e in EVS_EXH}) == len(EVS_EXH)
      and len({e.fingerprint for e in EVS_EXH}) == len(EVS_EXH))
shuffled_a, shuffled_b = list(FIXTURE["A"]), list(FIXTURE["B"])
random.Random(5).shuffle(shuffled_a)
random.Random(6).shuffle(shuffled_b)
again, _ = run(T.merge_files([shuffled_a, shuffled_b], {}), CFG_EXH)
check("a shuffled re-read writes identical rows",
      sorted((e.source_id, e.fingerprint, e.description) for e in again)
      == sorted((e.source_id, e.fingerprint, e.description) for e in EVS_EXH))
with tempfile.TemporaryDirectory() as tmp:
    store = EventStore(os.path.join(tmp, "s.json"))
    for e in EVS_EXH:
        store.upsert(e)
    store2 = EventStore(os.path.join(tmp, "s.json"))
    store.save()
    store2 = EventStore(os.path.join(tmp, "s.json"))
    for e in again:
        store2.upsert(e)
    check("a second upsert of the same read adds 0 rows", store2.stats.get("added", 0) == 0
          and len(store2.records) == len(EVS_EXH), (store2.stats, len(store2.records)))

# ---------------------------------------------------------------------------
# the datastore's CSV rows are the JSON items
# ---------------------------------------------------------------------------
CSV = {r["ID-EVENTO"]: T.item_from_row(r) for r in CSV_FIXTURE}
dep_csv = CSV["50410916"]
check("CSV 'DIAS-SEMANA' L is the JSON's weekly MO, and its exclusions and clock carry over",
      dep_csv.get("recurrence") == {"days": "MO", "frequency": "WEEKLY", "interval": 1}
      and dep_csv["excluded-days"] == "28/12/2026;4/1/2027;" and dep_csv["time"] == "18:00", dep_csv)
s_csv = T.sessions(T.clean_item(dep_csv), TODAY, HORIZON, SKIP, {})
s_json = T.sessions(T.clean_item(BY["50410916"]), TODAY, HORIZON, SKIP, {})
check("the same item read from the CSV and from the JSON gives the same sessions (Deporte en la Calle)",
      s_csv == s_json and len(s_csv[0]) == 10, (s_csv, s_json))
check("CSV fields: type, free flag, street, district, postal code, point",
      dep_csv["@type"] == "ActividadesDeportivas" and dep_csv["free"] == 1
      and T._street(dep_csv) == "Calle Estefanita 3" and T._district(dep_csv) == "Villaverde"
      and T._pc(dep_csv) == "28021" and T._point(dep_csv) is not None, dep_csv)
check("CSV: a row with no LATITUD has no location, and is never written (12130154)",
      "location" not in CSV["12130154"] and not of(run([T.clean_item(CSV["12130154"])])[0], "12130154"))
check("CSV: '/usuario/Niños,/usuario/Familias' is the audience 'Niños,Familias', which adds the kids door",
      any(r.get("AUDIENCIA") for r in CSV_FIXTURE)
      and all("/" not in it["audience"] for it in CSV.values())
      and T.category({"@type": "TeatroPerformance", "audience": "Niños,Familias"}, CFG) == ("theater", ["kids"]))
csv_evs, csv_st = run(T.merge_files([[T.item_from_row(r) for r in CSV_FIXTURE]], {}))
check("the CSV rows' verdicts match the JSON's: registration, online and no-location rows are not written",
      not of(csv_evs, "50430740") and not of(csv_evs, "12516239") and not of(csv_evs, "12130154")
      and days(of(csv_evs, "50437359")) == ["2026-10-17", "2026-10-31"], [e.source_id for e in csv_evs])

# ---------------------------------------------------------------------------
# the 2026-10-05 review's cases: twins, placeholders, a date's own clock, doors
# ---------------------------------------------------------------------------
TWINS_FIXTURE = json.loads(r"""[
{"ID-EVENTO": "50438944", "TITULO": "Fiestas del Pilar 2026 en Salamanca", "PRECIO": null, "GRATUITO": "1", "DIAS-SEMANA": null, "DIAS-EXCLUIDOS": null, "FECHA": "2026-10-10 00:00:00.0", "FECHA-FIN": "2026-10-10 23:59:00.0", "HORA": "21:00", "DESCRIPCION": "Escenario principal  Concierto Vibra Mahou Javi Chapela.", "NOMBRE-INSTALACION": "Parque Eva Duarte", "CLASE-VIAL-INSTALACION": "CALLE", "NOMBRE-VIA-INSTALACION": "DOCTOR GOMEZ ULLA", "NUM-INSTALACION": "9", "DISTRITO-INSTALACION": "SALAMANCA", "CODIGO-POSTAL-INSTALACION": "28028", "LATITUD": "40.42985671996897", "LONGITUD": "-3.668031432543624", "TIPO": "/contenido/actividades/Fiestas", "AUDIENCIA": null},
{"ID-EVENTO": "50438921", "TITULO": "Fiestas del Pilar 2026 en Salamanca", "PRECIO": null, "GRATUITO": "1", "DIAS-SEMANA": null, "DIAS-EXCLUIDOS": null, "FECHA": "2026-10-10 00:00:00.0", "FECHA-FIN": "2026-10-10 23:59:00.0", "HORA": null, "DESCRIPCION": "Escenario fuente   10:30 horas. Masterclass aerobic 11:15 horas. Masterclass yoga 12:00 horas. Despierta mayor 17:00 horas. Bailes deportivos   Carpa infantil    11:00 horas. Talleres y actividades in", "NOMBRE-INSTALACION": "Parque Eva Duarte", "CLASE-VIAL-INSTALACION": "CALLE", "NOMBRE-VIA-INSTALACION": "DOCTOR GOMEZ ULLA", "NUM-INSTALACION": "9", "DISTRITO-INSTALACION": "SALAMANCA", "CODIGO-POSTAL-INSTALACION": "28028", "LATITUD": "40.42985671996897", "LONGITUD": "-3.668031432543624", "TIPO": "/contenido/actividades/Fiestas", "AUDIENCIA": null},
{"ID-EVENTO": "50418639", "TITULO": "Germinal - Sesión I", "PRECIO": null, "GRATUITO": "0", "DIAS-SEMANA": null, "DIAS-EXCLUIDOS": null, "FECHA": "2026-10-10 00:00:00.0", "FECHA-FIN": "2026-10-10 23:59:00.0", "HORA": "19:00", "DESCRIPCION": "Festival de Cortometrajes Nosotras Contamos 2026.   Programa:    Casi septiembre  (Lucía G. Romero, 2025, 29')Alejandra, una joven que vive en un camping con su familia de forma permanente y precaria, tendrá que luchar contra su miedo al abandono cuand", "NOMBRE-INSTALACION": "Cineteca Madrid", "CLASE-VIAL-INSTALACION": "PLAZA", "NOMBRE-VIA-INSTALACION": "LEGAZPI", "NUM-INSTALACION": "8", "DISTRITO-INSTALACION": "ARGANZUELA", "CODIGO-POSTAL-INSTALACION": "28045", "LATITUD": "40.39130985242181", "LONGITUD": "-3.6958028442054074", "TIPO": "/contenido/actividades/ProgramacionDestacadaAgendaCultura", "AUDIENCIA": null},
{"ID-EVENTO": "50408873", "TITULO": "Germinal - Sesión I", "PRECIO": null, "GRATUITO": "0", "DIAS-SEMANA": null, "DIAS-EXCLUIDOS": null, "FECHA": "2026-10-10 00:00:00.0", "FECHA-FIN": "2026-10-10 23:59:00.0", "HORA": null, "DESCRIPCION": "Programa:    Casi septiembre  (Lucía G. Romero, 2025, 29')Alejandra, una joven que vive en un camping con su familia de forma permanente y precaria, tendrá que luchar contra su miedo al abandono cuand", "NOMBRE-INSTALACION": "Cineteca Madrid", "CLASE-VIAL-INSTALACION": "PLAZA", "NOMBRE-VIA-INSTALACION": "LEGAZPI", "NUM-INSTALACION": "8", "DISTRITO-INSTALACION": "ARGANZUELA", "CODIGO-POSTAL-INSTALACION": "28045", "LATITUD": "40.39130985242181", "LONGITUD": "-3.6958028442054074", "TIPO": "/contenido/actividades/ProgramacionDestacadaAgendaCultura", "AUDIENCIA": null},
{"ID-EVENTO": "50377292", "TITULO": "Compañía Juan Berlanga", "PRECIO": null, "GRATUITO": "0", "DIAS-SEMANA": "L,M,X,J,V,S,D", "DIAS-EXCLUIDOS": null, "FECHA": "2026-12-04 00:00:00.0", "FECHA-FIN": "2026-12-05 23:59:00.0", "HORA": null, "DESCRIPCION": null, "NOMBRE-INSTALACION": "Centro Danza Matadero - CDM", "CLASE-VIAL-INSTALACION": "PASEO", "NOMBRE-VIA-INSTALACION": "CHOPERA", "NUM-INSTALACION": "14", "DISTRITO-INSTALACION": "ARGANZUELA", "CODIGO-POSTAL-INSTALACION": "28045", "LATITUD": "40.39245798191536", "LONGITUD": "-3.6972067902718897", "TIPO": "/contenido/actividades/DanzaBaile", "AUDIENCIA": null},
{"ID-EVENTO": "50382740", "TITULO": "Cía. Juan Berlanga", "PRECIO": "Desde 18 euros", "GRATUITO": "0", "DIAS-SEMANA": "S,D", "DIAS-EXCLUIDOS": null, "FECHA": "2026-12-04 00:00:00.0", "FECHA-FIN": "2026-12-05 23:59:00.0", "HORA": "20:00", "DESCRIPCION": "Centro Danza Matadero presenta el estreno absoluto de El salto de Butes, el nuevo montaje de Juan Berlanga (Premio Max 2026 a Mejor Intérprete masculino de Danza).  Desde la raíz de la danza española ", "NOMBRE-INSTALACION": "Centro Danza Matadero - CDM", "CLASE-VIAL-INSTALACION": "PASEO", "NOMBRE-VIA-INSTALACION": "CHOPERA", "NUM-INSTALACION": "14", "DISTRITO-INSTALACION": "ARGANZUELA", "CODIGO-POSTAL-INSTALACION": "28045", "LATITUD": "40.39245798191536", "LONGITUD": "-3.6972067902718897", "TIPO": "/contenido/actividades/DanzaBaile", "AUDIENCIA": null},
{"ID-EVENTO": "50409231", "TITULO": "Andrea Jiménez. Contra Antígona", "PRECIO": null, "GRATUITO": "0", "DIAS-SEMANA": "L,M,X,J,V,S,D", "DIAS-EXCLUIDOS": null, "FECHA": "2026-11-26 00:00:00.0", "FECHA-FIN": "2026-12-05 23:59:00.0", "HORA": null, "DESCRIPCION": null, "NOMBRE-INSTALACION": "Centro de Cultura Contemporánea CondeDuque", "CLASE-VIAL-INSTALACION": "CALLE", "NOMBRE-VIA-INSTALACION": "CONDE DUQUE", "NUM-INSTALACION": "9", "DISTRITO-INSTALACION": "CENTRO", "CODIGO-POSTAL-INSTALACION": "28015", "LATITUD": "40.42739911262292", "LONGITUD": "-3.710589286287491", "TIPO": "/contenido/actividades/TeatroPerformance", "AUDIENCIA": null},
{"ID-EVENTO": "50408165", "TITULO": "Mi madre y el dinero", "PRECIO": "14 euros", "GRATUITO": "0", "DIAS-SEMANA": "L,M,X,J,V,S,D", "DIAS-EXCLUIDOS": null, "FECHA": "2026-11-17 00:00:00.0", "FECHA-FIN": "2026-11-18 23:59:00.0", "HORA": null, "DESCRIPCION": null, "NOMBRE-INSTALACION": "Centro de Cultura Contemporánea CondeDuque", "CLASE-VIAL-INSTALACION": "CALLE", "NOMBRE-VIA-INSTALACION": "CONDE DUQUE", "NUM-INSTALACION": "9", "DISTRITO-INSTALACION": "CENTRO", "CODIGO-POSTAL-INSTALACION": "28015", "LATITUD": "40.42739911262292", "LONGITUD": "-3.710589286287491", "TIPO": "/contenido/actividades/TeatroPerformance", "AUDIENCIA": null},
{"ID-EVENTO": "50434813", "TITULO": "Dibuja Madrid", "PRECIO": null, "GRATUITO": "1", "DIAS-SEMANA": "V,S", "DIAS-EXCLUIDOS": null, "FECHA": "2026-10-16 00:00:00.0", "FECHA-FIN": "2026-10-17 23:59:00.0", "HORA": "19:00", "DESCRIPCION": "Dibuja Madrid. Madrid a dos tiempos: entender el pasado para dibujar el presente.  Programación:   Viernes 16 de octubre, a las 19:00 horas, charla 'Latido castizo: De embajadores a Puerta de Toledo por el alma del Rastro', en el Centro Cultural Clara del Rey - Museo ABC (calle Amaniel 29-31, 28015, Madrid). Sábado 17 de octubre a las 10.00 horas: comenzamos la actividad de dibujo en el Mercado de San Fernando (calle de Embajadores, 41, 28012, Madrid).   Entrada libre hasta completar aforo.", "NOMBRE-INSTALACION": "Centro Cultural Clara del Rey - Museo ABC (Centro)", "CLASE-VIAL-INSTALACION": "CALLE", "NOMBRE-VIA-INSTALACION": "AMANIEL", "NUM-INSTALACION": "29", "DISTRITO-INSTALACION": "CENTRO", "CODIGO-POSTAL-INSTALACION": "28015", "LATITUD": "40.42741340671151", "LONGITUD": "-3.7095381101739444", "TIPO": "/contenido/actividades/CursosTalleres", "AUDIENCIA": null},
{"ID-EVENTO": "50425452", "TITULO": "Acto comunitario en Plaza Prosperidad por el Día Mundial de la Salud Mental", "PRECIO": null, "GRATUITO": "1", "DIAS-SEMANA": null, "DIAS-EXCLUIDOS": null, "FECHA": "2026-10-14 00:00:00.0", "FECHA-FIN": "2026-10-14 23:59:00.0", "HORA": "11:00", "DESCRIPCION": null, "NOMBRE-INSTALACION": "Espacio de Igualdad Nieves Torres. Chamartín", "CLASE-VIAL-INSTALACION": "CALLE", "NOMBRE-VIA-INSTALACION": "ENRIQUE JARDIEL PONCELA", "NUM-INSTALACION": "8", "DISTRITO-INSTALACION": "CHAMARTIN", "CODIGO-POSTAL-INSTALACION": "28016", "LATITUD": "40.46088981165102", "LONGITUD": "-3.6700689174427326", "TIPO": "/contenido/actividades/ComemoracionesHomenajes", "AUDIENCIA": null},
{"ID-EVENTO": "50389351", "TITULO": "Amílcar", "PRECIO": null, "GRATUITO": "0", "DIAS-SEMANA": null, "DIAS-EXCLUIDOS": null, "FECHA": "2026-10-09 00:00:00.0", "FECHA-FIN": "2026-10-09 23:59:00.0", "HORA": "19:30", "DESCRIPCION": null, "NOMBRE-INSTALACION": "Cineteca Madrid", "CLASE-VIAL-INSTALACION": "PLAZA", "NOMBRE-VIA-INSTALACION": "LEGAZPI", "NUM-INSTALACION": "8", "DISTRITO-INSTALACION": "ARGANZUELA", "CODIGO-POSTAL-INSTALACION": "28045", "LATITUD": "40.39130985242181", "LONGITUD": "-3.6958028442054074", "TIPO": "/contenido/actividades/ProgramacionDestacadaAgendaCultura", "AUDIENCIA": null}
]""")
TW = T.merge_files([[T.item_from_row(r) for r in TWINS_FIXTURE]], {})
TWB = {i["id"]: i for i in TW}
TW_EVS, TW_ST = run(TW)


def at(evs, ident):
    return [(e.start_local, e.description.split("\n\n")[1] if "\n\n" in e.description else "") for e in of(evs, ident)]


pilar = [e for e in TW_EVS if e.name == "Fiestas del Pilar 2026 en Salamanca"]
check("an all-day item and a timed one with the same title, venue and date but DIFFERENT texts are two rows "
      "(Fiestas del Pilar en Salamanca: the 21:00 concert, and the all-day programme from 10:30)",
      sorted(e.start_local for e in pilar) == ["2026-10-10", "2026-10-10T21:00:00"]
      and all(("Javi Chapela" in e.description) == (len(e.start_local) > 10) for e in pilar)
      and TW_ST.get("twins NOT folded: an all-day item and a timed one with different texts") == 1,
      [(e.source_id, e.description[:80]) for e in pilar])
germ = [e for e in TW_EVS if e.name == "Germinal - Sesión I"]
check("an all-day twin whose text the timed one holds is folded into it: one row at 19:00 (Germinal - Sesión I)",
      [e.start_local for e in germ] == ["2026-10-10T19:00:00"] and germ[0].source_id.startswith("50408873|")
      and "Nosotras Contamos" in germ[0].description, [(e.source_id, e.start_local) for e in germ])
ber = [e for e in TW_EVS if "Juan Berlanga" in e.name]
check("the every-day placeholder leaves no all-day Friday beside a show another id times on Saturday only "
      "(Compañía / Cía. Juan Berlanga: 5 Dec 20:00, and nothing on 4 Dec)",
      [e.start_local for e in ber] == ["2026-12-05T20:00:00"]
      and TW_ST.get("all-day placeholder days dropped: another id times the same show") == 1,
      [(e.source_id, e.start_local) for e in ber])
check("every day of 26 Nov - 5 Dec with no clock and no text is the City's placeholder: refused "
      "('Andrea Jiménez. Contra Antígona', 10 all-day rows before, Monday 30 Nov included)",
      not of(TW_EVS, "50409231") and T.sessions(TWB["50409231"], TODAY, HORIZON, SKIP, {})[2]
      == "refused: every day of a run, no clock and no dates (the City's placeholder)")
check("...but a two-day run (17-18 Nov, 'Mi madre y el dinero') is under the threshold: two all-day rows",
      [e.start_local for e in of(TW_EVS, "50408165")] == ["2026-11-17", "2026-11-18"],
      [e.start_local for e in of(TW_EVS, "50408165")])
dib = of(TW_EVS, "50434813")
check("a named date with its own clock keeps it: 'Dibuja Madrid' Friday 16 Oct 19:00 (HORA), Saturday 17 Oct "
      "10:00 ('Sábado 17 de octubre a las 10.00 horas'), never 19:00 on the Saturday",
      [e.start_local for e in dib] == ["2026-10-16T19:00:00", "2026-10-17T10:00:00"], [e.start_local for e in dib])
check("dated_clocks: a date given two different clocks is given none",
      T.dated_clocks("16 de octubre a las 19:00 horas. 16 de octubre a las 11:00 horas", date(2026, 10, 1),
                     date(2026, 10, 31)) == {})
check("a title that puts the act in a square its venue is not is refused ('Acto comunitario en Plaza Prosperidad', "
      "pinned at the Espacio de Igualdad Nieves Torres, 1.9 km away)",
      not of(TW_EVS, "50425452") and T.verdict(TWB["50425452"])
      == (False, "the title places it in a square or street that is not its venue"))
check("SYNTHETIC: ...and a title that names the venue's own park is kept ('Concierto en el Parque Eva Duarte')",
      T.verdict(dict(TWB["50438944"], title="Concierto en el Parque Eva Duarte"))[0])


def door(item):
    ev = run([item])[0]
    return [derive_categories(dict(e.__dict__))[0] for e in ev][:1] or [T.category(item, CFG)[0] + " (not written)"]


check("the City's highlights type is not a genre: a JAZZMADRID set is music after the sync (Andrés Coll Cosmic Trio)",
      door(T.clean_item(BY["50432799"])) == ["music"], door(T.clean_item(BY["50432799"])))
check("...a Cineteca film is theater, even when its text names a cantautor (SYNTHETIC text on 'Amílcar')",
      door(dict(TWB["50389351"], description="Documental sobre un cantautor y su gira.")) == ["theater"]
      and T.category(TWB["50418639"], CFG)[0] == "theater")
street = dict(TWB["50438944"], **{"@type": "ActividadesCalleArteUrbano"})
check("SYNTHETIC: the street-arts afternoon: 'Tributo a Hombres G' is music, 'Carrera de Orientación Deportiva' "
      "sports, 'Pintacaras de Halloween' community - never arts",
      T.category(dict(street, title="Los Burlancaster. Tributo a Hombres G", description=""), CFG)[0] == "music"
      and T.category(dict(street, title="Carrera de Orientación Deportiva Puente de Vallecas", description=""), CFG)[0]
      == "sports"
      and T.category(dict(street, title="Pintacaras de Halloween", description=""), CFG)[0] == "community")
check("an exhibition in the highlights is still arts", T.category(dict(TWB["50418639"], title="Exposición: X"), CFG)[0]
      == "arts")
check("the review's rows: every source_id and fingerprint unique, no row spans a gap",
      len({e.source_id for e in TW_EVS}) == len(TW_EVS) and len({e.fingerprint for e in TW_EVS}) == len(TW_EVS)
      and all(no_gap(e) for e in TW_EVS))

# ---------------------------------------------------------------------------
# HTTP: the CKAN datastore, refusals, pacing, the deadline
# ---------------------------------------------------------------------------
MADRID_ROBOTS = """User-agent: *
# ===============================
# API CKAN
# ===============================
Disallow: /api/
# bloquear endpoints concretos del datastore
Disallow: /api/3/action/datastore_search
Disallow: /dataset/*/resource/*/download/*
Disallow: /*?
Crawl-delay: 10
"""
OPEN_ROBOTS = "User-agent: *\nDisallow: /admin/\nCrawl-delay: 20\n"


class Resp:
    def __init__(self, code, body=None, text=None, headers=None):
        self.status_code, self._body, self.headers = code, body, headers or {}
        self.text = text if text is not None else json.dumps(body or {})
        self.content = self.text.encode("utf-8")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeCkan:
    """The datastore_search API over CSV_FIXTURE: the first resource holds
    every row, the second the first three (the files overlap). robots.txt is
    served apart (`robots`: datos.madrid.es's own lines unless told) and
    counted in `robots_calls`, so `calls` and `plan` are the API's alone."""

    def __init__(self, plan=None, total_fn=None, robots=None):
        self.calls, self.plan, self.headers, self.total_fn = [], plan or {}, {}, total_fn
        self.robots = robots if robots is not None else Resp(200, text=MADRID_ROBOTS)
        self.robots_calls = []

    def get(self, url, params=None, timeout=None, allow_redirects=True):
        if url.endswith("/robots.txt"):
            self.robots_calls.append(url)
            if isinstance(self.robots, Exception):
                raise self.robots
            return self.robots
        self.calls.append((url, dict(params or {})))
        got = self.plan.get(len(self.calls))
        if got is not None:
            return got
        rows = CSV_FIXTURE if params["resource_id"] == CFG["sources"][0]["resource_id"] else CSV_FIXTURE[:3]
        total = self.total_fn(len(self.calls), rows) if self.total_fn else len(rows)
        page = rows[params["offset"]:params["offset"] + params["limit"]]
        return Resp(200, {"success": True, "result": {"records": page, "total": total}})


class Clock:
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


clk = Clock()
fk = FakeCkan()
r = T.Reader(fk, CFG["api"], sleep=clk.sleep, clock=clk)
got = r.resource(CFG["sources"][0]["resource_id"], limit=3)
check("paging: 8 rows at limit 3 are three requests in _id order, offsets 0, 3, 6, every row once",
      [c[1]["offset"] for c in fk.calls] == [0, 3, 6] and all(c[1]["sort"] == "_id asc" for c in fk.calls)
      and [x["ID-EVENTO"] for x in got] == [x["ID-EVENTO"] for x in CSV_FIXTURE], fk.calls)
check("every request after the first waits for robots.txt's Crawl-delay (10 s)",
      len(clk.slept) == 2 and all(abs(s - 10.0) < 1e-6 for s in clk.slept), clk.slept)
fk = FakeCkan(total_fn=lambda n, rows: len(rows) + (1 if n >= 2 else 0))
r = T.Reader(fk, CFG["api"], sleep=clk.sleep, clock=clk)
got = r.resource(CFG["sources"][0]["resource_id"], limit=3)
check("a table that changes size mid-read is read again from offset 0, never stitched",
      [c[1]["offset"] for c in fk.calls] == [0, 3, 0, 3, 6] and len(got) == len(CSV_FIXTURE),
      [c[1]["offset"] for c in fk.calls])
fk = FakeCkan(total_fn=lambda n, rows: len(rows) + n)
r = T.Reader(fk, CFG["api"], sleep=clk.sleep, clock=clk)
try:
    r.resource(CFG["sources"][0]["resource_id"], limit=3)
    ok = False
except RuntimeError:
    ok = True
check("a table that keeps changing size is given up after a second try", ok and len(fk.calls) == 4,
      [c[1]["offset"] for c in fk.calls])

# robots_gate, alone
PROBE = f"{CFG['api']}?resource_id={CFG['sources'][0]['resource_id']}&limit=5000"


def gate(fk, permission=""):
    r = T.Reader(fk, CFG["api"], sleep=lambda s: None, clock=Clock())
    try:
        return r.robots_gate(PROBE, permission), r
    except T.Refused as exc:
        return f"REFUSED {exc}", r


said, r = gate(FakeCkan())
check("robots_gate: datos.madrid.es's own robots.txt refuses the datastore (the line that names it), "
      "one request, and nothing more is asked of the host",
      said.startswith("REFUSED") and "/api/3/action/datastore_search" in said and r.requests == 1
      and r.refused, said)
said, r = gate(FakeCkan(), permission="datos.madrid.es, 2026-10-20: datastore_search, daily")
check("robots_gate: the same Disallow, with the publisher's written permission recorded, reads (and says so)",
      said.startswith("robots.txt says") and "written permission" in said and not r.refused, said)
said, r = gate(FakeCkan(robots=Resp(200, text=OPEN_ROBOTS)))
check("robots_gate: a robots.txt that allows the path reads, and its Crawl-delay (20) paces every request",
      said.startswith("robots.txt allows") and r.min_interval == 20.0, (said, r.min_interval))
said, r = gate(FakeCkan(robots=Resp(503, text="busy")), permission="a yes")
check("robots_gate: an unreachable robots.txt is a refusal, permission or not (RFC 9309: assume Disallow: /)",
      said.startswith("REFUSED") and "unreachable" in said, said)
said, r = gate(FakeCkan(robots=ConnectionError("reset")), permission="a yes")
check("robots_gate: a robots.txt that cannot be fetched at all is a refusal too", said.startswith("REFUSED"), said)
said, r = gate(FakeCkan(robots=Resp(200, text="<html><title>Just a moment...</title></html>")), permission="a yes")
check("robots_gate: a challenge in place of robots.txt is a refusal", said.startswith("REFUSED"), said)

GRANTED = "TEST ONLY: a recorded yes"


def run_main(fk, *extra, permission=GRANTED):
    """main() over a copy of the parked config, with `permission` set (the
    state the source runs in once the Ayuntamiento says yes) unless told."""
    real_session, real_sleep = T.requests.Session, T.time.sleep
    T.requests.Session = lambda: fk
    T.time.sleep = lambda s: None
    out = io.StringIO()
    tmp = tempfile.mkdtemp()
    path, cfg_path = os.path.join(tmp, "store.json"), os.path.join(tmp, "madrid_sources.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(dict(CFG, permission=permission), f, ensure_ascii=False)
    try:
        with redirect_stdout(out):
            code = T.main(["--config", cfg_path, "--store", path, *extra])
    finally:
        T.requests.Session, T.time.sleep = real_session, real_sleep
    return code, out.getvalue(), path


def stored_records(path):
    return EventStore(path).records if os.path.exists(path) else {}


fk = FakeCkan()
code, out, path = run_main(fk, permission="")
check("AS SHIPPED (no permission): robots.txt is asked once, the datastore never, 0 rows, exit 0, the store saved",
      code == 0 and len(fk.robots_calls) == 1 and not fk.calls and "REFUSED" in out
      and not stored_records(path) and os.path.exists(path), (fk.robots_calls, fk.calls, out[-300:]))
fk = FakeCkan()
code, out, path = run_main(fk)
check("with permission: robots.txt, then both resources (2 requests), every row written, exit 0",
      code == 0 and len(fk.robots_calls) == 1 and len(fk.calls) == 2
      and len(stored_records(path)) == len(csv_evs) and "written permission" in out,
      (code, len(fk.calls), len(stored_records(path)), len(csv_evs), out[-300:]))
check("the run prints every refusal reason with its count",
      "refused: registration required (inscripción previa): 1" in out and "refused: online, not at the venue: 1" in out,
      out[:600])
fk = FakeCkan({1: Resp(403, text="Forbidden")})
code, out, path = run_main(fk)
check("a 403 is a refusal: asked once, the second resource never asked, exit 0, the store still saved",
      code == 0 and len(fk.calls) == 1 and "REFUSED" in out and os.path.exists(path), (fk.calls, out[-200:]))
fk = FakeCkan({2: Resp(403, text="Forbidden")})
code, out, path = run_main(fk)
check("a 403 on the SECOND resource: the first one's rows are written, the run says PARTIAL, nothing more is asked",
      code == 0 and len(fk.calls) == 2 and "PARTIAL RUN" in out and len(stored_records(path)) == len(csv_evs),
      (len(fk.calls), len(stored_records(path)), out[-300:]))
fk = FakeCkan({1: Resp(503, text="busy")})
code, out, path = run_main(fk)
check("a 503 is retried, and the run then completes", code == 0 and len(stored_records(path)) == len(csv_evs), out[-300:])
fk = FakeCkan({1: Resp(200, text="<html><title>Just a moment...</title></html>")})
code, out, path = run_main(fk)
check("a bot challenge is a refusal, never parsed or retried", code == 0 and len(fk.calls) == 1 and "REFUSED" in out,
      (fk.calls, out[-200:]))
fk = FakeCkan({1: Resp(302, text="", headers={"Location": "https://datos.madrid.es/dataset/x/resource/y/download/z"})})
code, out, path = run_main(fk)
check("a redirect is not followed (it leads where robots.txt Disallows): that resource fails, the other is read",
      code == 0 and len(fk.calls) == 2 and all(c[0] == CFG["api"] for c in fk.calls) and "FAILED" in out,
      (fk.calls, out[-300:]))
fk = FakeCkan()
code, out, path = run_main(fk, "--max-minutes", "0.0000001")
check("--max-minutes: past the deadline no request starts (not even robots.txt), exit 0, the store still saved",
      code == 0 and not fk.calls and not fk.robots_calls and "STOPPED" in out and os.path.exists(path),
      (fk.calls, fk.robots_calls, out))

# COMPLETE READS (2026-10-05, the owner: a session taken off must not stay on
# the map). Only a read of EVERY resource, whole, may let absence cancel.
def reads(path):
    return json.load(open(path, encoding="utf-8")).get("complete_reads") or {}


code, out, path = run_main(FakeCkan())
got = {k: (v["from"][:10], v["to"][:10]) for k, v in reads(path).items()}
want = (TODAY.isoformat(), (TODAY + timedelta(days=int(CFG.get("horizon_days", 90)))).isoformat())
check("COMPLETE: both resources whole marks madrid:centros and madrid:agenda, over [today, horizon]",
      got == {"madrid:centros": want, "madrid:agenda": want}, got)
code, out, path = run_main(FakeCkan({2: Resp(403, text="Forbidden")}))
check("NOT complete: the second resource refused (a PARTIAL RUN)", reads(path) == {}, reads(path))
code, out, path = run_main(FakeCkan({1: Resp(302, text="", headers={"Location": "https://datos.madrid.es/x"})}))
check("NOT complete: one resource failed", reads(path) == {}, reads(path))
code, out, path = run_main(FakeCkan(total_fn=lambda n, rows: len(rows) + 2))
check("NOT complete: a resource came back short of CKAN's total", reads(path) == {} and "read NOT complete" in out,
      (reads(path), out[-300:]))
code, out, path = run_main(FakeCkan(), permission="")
check("NOT complete: the parked config (no permission) reads nothing", reads(path) == {}, reads(path))


class EmptySecond(FakeCkan):
    def get(self, url, params=None, timeout=None, allow_redirects=True):
        r = super().get(url, params, timeout, allow_redirects)
        if params and params.get("resource_id") == CFG["sources"][-1]["resource_id"]:
            return Resp(200, {"success": True, "result": {"records": [], "total": 0}})
        return r


code, out, path = run_main(EmptySecond())
check("NOT complete: one resource came back empty (a broken export, not a City that cancelled all)",
      reads(path) == {}, (reads(path), out[-200:]))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
