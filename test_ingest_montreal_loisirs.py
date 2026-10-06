#!/usr/bin/env python3
"""
test_ingest_montreal_loisirs.py - Montreal's Loisirs Montreal programme, and
the ways a session in it is not the drop-in it looks like.

Every row in FIXTURE was read from the City's CKAN datastore on 2026-10-03
(resource a7e11db5, trimmed to the columns the adapter reads, descriptions cut
at 330 characters; the three 897448 "Danse en ligne" rows were read 2026-10-05
and keep their whole 600-character text, because its last lines are the point);
synthetic cases are built in code and say so. The expensive cases are the quiet
ones: a club's empty listing or a court rental written as a drop-in, two free
swims merged into one row spanning the hours between them, a skate published
under two ids written twice, a Zumba on the Thursday its own text cancels, a
July badminton written every Sunday of the autumn, a Verdun school pinned 20 km
away in Pointe-aux-Trembles because the City geocoded "5e Avenue" without its
borough, a Thursday dance written on a date only the SUNDAY range covers, and a
price line that lets ../mapsee's 0227 call a session free - in the adapter's
text, or after the sync's 800-character cap has cut the "(pas gratuit)" off.

    python test_ingest_montreal_loisirs.py
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
os.environ["MAPSEE_TODAY"] = "20261004"

from zoneinfo import ZoneInfo  # noqa: E402

import mapsee_ingest_montreal_loisirs as T  # noqa: E402
import mapsee_supabase_sync as S  # noqa: E402
from mapsee_ingest import EventStore, VALID_CATEGORIES  # noqa: E402
from mapsee_supabase_sync import derive_categories  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)}")
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "montreal_loisirs_sources.json")
CFG = T.load_config(CONFIG)
SRC = CFG["sources"][0]
TZ = ZoneInfo(CFG["timezone"])
TODAY = T._today(TZ)

# ../mapsee migration 0227 reads `offer:free` from a row's own text, in French
# too. A SUBSET of ../mapsee/tools/measure_deals.py's FREE and its FREE_NEG veto
# - the alternatives these rows could hit. The full classify over the live dry
# run of 2026-10-04 (990 rows) disagreed with the adapter's tier on 0 rows.
FREE_TAG = re.compile(
    r"\bgratuit(?:e|s|es)?\b|\bentr[ée]e\s+(?:libre|gratuite)\b|\bacc[èe]s\s+(?:libre|gratuit)\b|\bgratis\b|"
    r"(?<![-\w/])free\s+(?:admission|entry|entrance|event|of\s+charge|to\s+(?:attend|join|enter|participate|"
    r"the\s+public|all|everyone)|for\s+(?:all|everyone|kids|children|the\s+public|families)|session|class|"
    r"drop[- ]in)\b|\b(?:admission|entry|entrance|cost|price)\s*(?:is|:|-|–)?\s*free\b|\bis\s+free\b", re.I)
FREE_NEG = re.compile(r"\b(?:not|isn'?t|is\s+not)\s+(?:a\s+)?free\b|\bnon[- ]gratuit|\bpas\s+gratuit", re.I)


def tagged_free(ev):
    text = f"{ev.name}\n{ev.description}"
    return bool(FREE_TAG.search(text)) and not FREE_NEG.search(text)


def stored_free(ev):
    """0227's verdict on what the SYNC writes: to_row caps the description at
    DESCRIPTION_MAX by trimming the head from its end."""
    row = S.to_row(dict(ev.__dict__), "00000000-0000-0000-0000-000000000000")
    text = f"{row.get('title') or ev.name}\n{row.get('description') or ''}"
    return bool(FREE_TAG.search(text)) and not FREE_NEG.search(text), row


FIXTURE = json.loads(r"""[
{"id_activite":"900315","categorie":"Sports et activités physiques","sous_categorie":"Activités aquatiques","nom":"Baignade libre Adultes","description":null,"promoteur":"Le Sud-Ouest","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"18","age_maximum":null,"date_debut":"2026-09-26","date_fin":"2027-01-29","id_seance":"678346","site_seance":"Piscine intérieure Saint-Charles","adresse_site_seance":"1055 rue d' Hibernia  , Montréal, H3K2V3","telephone_site_seance":"5148722501","heure_debut_seance":"12:00:00","heure_fin_seance":"14:00:00","jour_semaine_seance":"Lundi","latitude":"45.479654","longitude":"-73.563774"},
{"id_activite":"900315","categorie":"Sports et activités physiques","sous_categorie":"Activités aquatiques","nom":"Baignade libre Adultes","description":null,"promoteur":"Le Sud-Ouest","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"18","age_maximum":null,"date_debut":"2026-09-26","date_fin":"2027-01-29","id_seance":"678347","site_seance":"Piscine intérieure Saint-Charles","adresse_site_seance":"1055 rue d' Hibernia  , Montréal, H3K2V3","telephone_site_seance":"5148722501","heure_debut_seance":"20:30:00","heure_fin_seance":"22:00:00","jour_semaine_seance":"Lundi","latitude":"45.479654","longitude":"-73.563774"},
{"id_activite":"898830","categorie":"Sports collectifs","sous_categorie":"Soccer","nom":"Soccer pratique libre adulte récréatif","description":null,"promoteur":"Centre de loisirs de Lachine","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-08-31","date_fin":"2026-12-20","id_seance":"676553","site_seance":"Complexe sportif du Collège Sainte-Anne","adresse_site_seance":"50 12e Avenue  , Lachine, H8S3H6","telephone_site_seance":"5146373571","heure_debut_seance":"18:00:00","heure_fin_seance":"20:00:00","jour_semaine_seance":"Samedi","latitude":"45.431998","longitude":"-73.674664"},
{"id_activite":"898830","categorie":"Sports collectifs","sous_categorie":"Soccer","nom":"Soccer pratique libre adulte récréatif","description":null,"promoteur":"Centre de loisirs de Lachine","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-08-31","date_fin":"2026-12-20","id_seance":"676554","site_seance":"Complexe sportif du Collège Sainte-Anne","adresse_site_seance":"50 12e Avenue  , Lachine, H8S3H6","telephone_site_seance":"5146373571","heure_debut_seance":"20:00:00","heure_fin_seance":"22:00:00","jour_semaine_seance":"Samedi","latitude":"45.431998","longitude":"-73.674664"},
{"id_activite":"897488","categorie":"Sports et activités physiques","sous_categorie":"Sports de raquette","nom":"Pickleball pratique gratuite","description":"Dès le 4 mai à novembre, les lundis de 09h à 12h sur les terrains 1-2\nAucune réservation nécessaire.\nAucun prêt d'équipement (raquette ou balle).","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-05-04","date_fin":"2026-11-16","id_seance":"674710","site_seance":"Parc Wilson, terrain de sport","adresse_site_seance":"1075 avenue Brown  , Verdun, H4H2A7","telephone_site_seance":"5147657150","heure_debut_seance":"09:00:00","heure_fin_seance":"12:00:00","jour_semaine_seance":"Lundi","latitude":"45.448885","longitude":"-73.579001"},
{"id_activite":"897488","categorie":"Sports et activités physiques","sous_categorie":"Sports de raquette","nom":"Pickleball pratique gratuite","description":"Dès le 4 mai à novembre, les lundis de 09h à 12h sur les terrains 1-2\nAucune réservation nécessaire.\nAucun prêt d'équipement (raquette ou balle).","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-05-04","date_fin":"2026-11-16","id_seance":"674711","site_seance":"Parc Wilson, terrain de sport","adresse_site_seance":"1075 avenue Brown  , Verdun, H4H2A7","telephone_site_seance":"5147657150","heure_debut_seance":"09:00:00","heure_fin_seance":"12:00:00","jour_semaine_seance":"Lundi","latitude":"45.448885","longitude":"-73.579001"},
{"id_activite":"901149","categorie":"Sports et activités physiques","sous_categorie":"Sports sur glace","nom":"Patinage Libre","description":"La séance de patinage libre est ouverte à tous. Activité gratuite. \nInscription requise pour du prêt de patins selon la disponibilité.\nL'inscription n'est PAS requise pour ceux ayant déjà des patins.","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-10-06","date_fin":"2026-10-06","id_seance":"679594","site_seance":"Auditorium de Verdun","adresse_site_seance":"4110 boulevard LaSalle  , Verdun, H4G2A5","telephone_site_seance":"5147657130","heure_debut_seance":"13:30:00","heure_fin_seance":"15:00:00","jour_semaine_seance":"Mardi","latitude":"45.46223","longitude":"-73.563229"},
{"id_activite":"901187","categorie":"Sports et activités physiques","sous_categorie":"Sports sur glace","nom":"Patinage Libre","description":"La séance de patinage libre est ouverte à tous. Activité gratuite. \nInscription requise pour du prêt de patins selon la disponibilité.\nL'inscription n'est PAS requise pour ceux ayant déjà des patins.","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-10-06","date_fin":"2026-10-06","id_seance":"679651","site_seance":"Auditorium de Verdun","adresse_site_seance":"4110 boulevard LaSalle  , Verdun, H4G2A5","telephone_site_seance":"5147657130","heure_debut_seance":"13:30:00","heure_fin_seance":"15:00:00","jour_semaine_seance":"Mardi","latitude":"45.46223","longitude":"-73.563229"},
{"id_activite":"901138","categorie":"Sports et activités physiques","sous_categorie":"Sports sur glace","nom":"Patinage Libre","description":"La séance de patinage libre est ouverte à tous. Activité gratuite. \nInscription requise pour du prêt de patins selon la disponibilité.\nL'inscription n'est PAS requise pour ceux ayant déjà des patins.","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-10-06","date_fin":"2026-10-06","id_seance":"679583","site_seance":"Auditorium de Verdun","adresse_site_seance":"4110 boulevard LaSalle  , Verdun, H4G2A5","telephone_site_seance":"5147657130","heure_debut_seance":"08:30:00","heure_fin_seance":"09:30:00","jour_semaine_seance":"Mardi","latitude":"45.46223","longitude":"-73.563229"},
{"id_activite":"899689","categorie":"Sports et activités physiques","sous_categorie":"Activités de conditionnement physique","nom":"Zumba Gold (Verdun Actif - Programmation gratuite)","description":"Offert par danse Nuestroamericana dans le cadre de la programmation Verdun Actif\nActivité gratuite pour tous. Aucune réservation nécessaire.\nHoraire: Les jeudis \n9h30 à 11h30 / \n28 mai au 8 octobre.","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"16","age_maximum":null,"date_debut":"2026-05-28","date_fin":"2026-10-08","id_seance":"677759","site_seance":"Serres de Verdun","adresse_site_seance":"7000 boulevard LaSalle  , Verdun, H4H2T1","telephone_site_seance":"5147657150","heure_debut_seance":"09:30:00","heure_fin_seance":"11:30:00","jour_semaine_seance":"Jeudi","latitude":"45.440088","longitude":"-73.580123"},
{"id_activite":"899690","categorie":"Sports et activités physiques","sous_categorie":"Activités de conditionnement physique","nom":"Zumba Gold (Verdun Actif - Programmation gratuite)","description":"Offert par danse Nuestroamericana dans le cadre de la programmation Verdun Actif\nActivité gratuite pour tous. Aucune réservation nécessaire.\nHoraire: \nLes Jeudis 9h30 à 11h30 / \n28 mai au 8 octobre.","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"5","age_maximum":null,"date_debut":"2026-05-28","date_fin":"2026-10-08","id_seance":"677761","site_seance":"Serres de Verdun","adresse_site_seance":"7000 boulevard LaSalle  , Verdun, H4H2T1","telephone_site_seance":"5147657150","heure_debut_seance":"09:30:00","heure_fin_seance":"11:30:00","jour_semaine_seance":"Jeudi","latitude":"45.440088","longitude":"-73.580123"},
{"id_activite":"898840","categorie":"Activités de détente","sous_categorie":"Yoga","nom":"Yoga et Étirements  (plusieurs cours offerts)","description":"Yoga (plusieurs types de cours offerts)","promoteur":"Centre de loisirs de Lachine","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-09-21","date_fin":"2026-12-18","id_seance":"676571","site_seance":"Complexe sportif du Collège Sainte-Anne","adresse_site_seance":"50 12e Avenue  , Lachine, H8S3H6","telephone_site_seance":"5146373571","heure_debut_seance":"19:35:00","heure_fin_seance":"20:35:00","jour_semaine_seance":"Mardi","latitude":"45.431998","longitude":"-73.674664"},
{"id_activite":"900312","categorie":"Sports et activités physiques","sous_categorie":"Activités de conditionnement physique","nom":"Zumba","description":"Dans le cadre de la programmation Verdun Actif\nOffert par danZ Nuestroamericana\nL'activité est gratuite\nAucune réservation nécessaire.\n\n* Pas d’activité le 19 novembre *","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"6","age_maximum":null,"date_debut":"2026-10-13","date_fin":"2026-12-12","id_seance":"678335","site_seance":"École Notre-Dame-de-Lourdes","adresse_site_seance":"504 5e Avenue  , Verdun, H4G2Z1","telephone_site_seance":null,"heure_debut_seance":"18:30:00","heure_fin_seance":"19:30:00","jour_semaine_seance":"Jeudi","latitude":"45.636397","longitude":"-73.492344"},
{"id_activite":"900680","categorie":"Sports de raquette","sous_categorie":"Badminton","nom":"Badminton libre (7ans+) 05 juillet 10 h à 11 h 30","description":null,"promoteur":"CSMV - Activités libres","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"7","age_maximum":null,"date_debut":"2026-08-31","date_fin":"2026-12-20","id_seance":"678772","site_seance":"Complexe sportif Marie-Victorin","adresse_site_seance":"7000 boulevard Maurice-Duplessis  , Montréal, H1G0A1","telephone_site_seance":"5148687698","heure_debut_seance":"10:00:00","heure_fin_seance":"11:30:00","jour_semaine_seance":"Dimanche","latitude":"45.619563","longitude":"-73.610053"},
{"id_activite":"901148","categorie":"Sports et activités physiques","sous_categorie":"Sports sur glace","nom":"Patinage libre","description":"La séance de patinage libre est ouverte à tous. Activité gratuite. \nInscription requise pour du prêt de patins selon la disponibilité.\nL'inscription n'est PAS requise pour ceux ayant déjà des patins.","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-10-06","date_fin":"2026-10-06","id_seance":"679593","site_seance":"Auditorium de Verdun","adresse_site_seance":"4110 boulevard LaSalle  , Verdun, H4G2A5","telephone_site_seance":"5147657130","heure_debut_seance":"08:30:00","heure_fin_seance":"09:30:00","jour_semaine_seance":"Lundi","latitude":"45.46223","longitude":"-73.563229"},
{"id_activite":"901630","categorie":"Sports et activités physiques","sous_categorie":"Sports sur glace","nom":"Patinage libre lundi le 5 oct de 16h30 à 17h20","description":"Patinage libre pour tous, réservé aux citoyen.nes de Montréal. Free skate for all, reserved for City of Montreal residents.","promoteur":"Côte-des-Neiges - Notre-Dame-de-Grâce","est_inscription_obligatoire":"Vrai","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-10-05","date_fin":"2026-10-05","id_seance":"680224","site_seance":"Aréna Bill-Durnan","adresse_site_seance":"4988 rue Vézina  , Montréal, H3W1C1","telephone_site_seance":"5148726073","heure_debut_seance":"16:30:00","heure_fin_seance":"17:20:00","jour_semaine_seance":"Lundi","latitude":"45.493977","longitude":"-73.645956"},
{"id_activite":"900275","categorie":"Sports de raquette","sous_categorie":"Badminton","nom":"Badminton à l'heure","description":"Jouez au badminton en simple ou en double.  Pour ce faire, réservez un terrain sur la plateforme loisirs.montreal.ca. Sélectionnez Réservations d’espaces, puis Badminton. Utilisez les filtres pour sélectionner la plage horaire souhaitée. \n\nPour toute question, veuillez écrire à : verdun.cce@montreal.ca","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"18","age_maximum":null,"date_debut":"2026-10-17","date_fin":"2026-12-12","id_seance":"678292","site_seance":"École Notre-Dame-de-Lourdes","adresse_site_seance":"504 5e Avenue  , Verdun, H4G2Z1","telephone_site_seance":null,"heure_debut_seance":"09:15:00","heure_fin_seance":"10:15:00","jour_semaine_seance":"Samedi","latitude":"45.636397","longitude":"-73.492344"},
{"id_activite":"900250","categorie":"Sports et activités physiques","sous_categorie":"Sports de raquette","nom":"Pickleball à l'heure","description":"Pour réserver un terrain, vous devez détenir une carte biblio-loisirs.Il est possible d'effectuer une réservation 3 jours à l'avance.L'inscription doit se faire sur la plate-forme loisirs.montréal.ca.\nVous cliquez sur Réservations d'espaces pour accéder à la pastille de pickleball. \nEnsuite vous afficher les filtres. Le tarif es","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-10-04","date_fin":"2026-12-06","id_seance":"678263","site_seance":"École Île-des-Soeurs","adresse_site_seance":"530 rue De Gaspé  , Verdun, H3E1E7","telephone_site_seance":"5147657150","heure_debut_seance":"12:00:00","heure_fin_seance":"13:00:00","jour_semaine_seance":"Dimanche","latitude":"45.456128","longitude":"-73.547934"},
{"id_activite":"899156","categorie":"Sports sur glace","sous_categorie":"Hockey sur glace","nom":"Hockey sur glace 0-17 ans","description":null,"promoteur":"Association du hockey sur glace de Lachine inc.","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":null,"age_maximum":null,"date_debut":"2026-09-08","date_fin":"2027-04-18","id_seance":"676752","site_seance":"Aréna Pierre-\"Pete\"-Morin","adresse_site_seance":"1925 rue Saint-Antoine  , Lachine, H8S1V5","telephone_site_seance":"5146343471","heure_debut_seance":"20:00:00","heure_fin_seance":"22:00:00","jour_semaine_seance":"Lundi","latitude":"45.439912","longitude":"-73.681724"},
{"id_activite":"899527","categorie":"Sports et activités physiques","sous_categorie":"Activités de conditionnement physique","nom":"Yoga","description":"L'inscription à ce cours donne accès à tous les cours du club de conditionnement physique Saint-Laurent pour la session en cours.","promoteur":"Club de conditionnement physique Saint-Laurent inc","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"16","age_maximum":null,"date_debut":"2026-09-08","date_fin":"2026-10-24","id_seance":"677292","site_seance":"Centre des loisirs Saint-Laurent","adresse_site_seance":"1375 rue Grenet  , Saint-Laurent, H4L5K3","telephone_site_seance":"5148556110","heure_debut_seance":"18:15:00","heure_fin_seance":"19:05:00","jour_semaine_seance":"Mardi","latitude":"45.513798","longitude":"-73.690945"},
{"id_activite":"899563","categorie":"Sports et activités physiques","sous_categorie":"Sports de raquette","nom":"Pickleball ligue adultes  débutant/intermédiaire","description":"Ligue adultes débutant (2.5 à 2.9)\nLigue A. intermédiaire (3.0 à 3.5)\n6 séances Mardi 18h à 20h  / 20h à 22h \nEn cas de pluie Reprise 13 et 20 octobre","promoteur":"Camp Énergie","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"18","age_maximum":null,"date_debut":"2026-09-01","date_fin":"2026-10-20","id_seance":"677449","site_seance":"Parc Dan Hanganu, terrain de sport","adresse_site_seance":"260 rue Elgar  , Verdun, H3E1C9","telephone_site_seance":"5147657270","heure_debut_seance":"18:00:00","heure_fin_seance":"22:00:00","jour_semaine_seance":"Mardi","latitude":"45.457085","longitude":"-73.546913"},
{"id_activite":"895612","categorie":"Sports et activités physiques","sous_categorie":"Formation en sauvetage","nom":"Médaille de bronze","description":"Le cours Médaille et Croix de Bronze permet aux candidats sérieux et motivés d'acquérir une compréhension des quatre composantes de base du sauvetage: le jugement, les connaissances, les habiletés et la forme physique. Préalables: 1) Être âgé d'au moins 13 ans à l'examen final ou détenir la certification Étoile de bronze. 2) Dém","promoteur":"Le Plateau-Mont-Royal","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"13","age_maximum":null,"date_debut":"2026-08-31","date_fin":"2027-06-21","id_seance":"671290","site_seance":"Piscine intérieure Schubert","adresse_site_seance":"3950 boulevard Saint-Laurent  , Montréal, H2W1Y3","telephone_site_seance":"5148722587","heure_debut_seance":"17:00:00","heure_fin_seance":"19:00:00","jour_semaine_seance":"Mardi","latitude":"45.516553","longitude":"-73.578484"},
{"id_activite":"897515","categorie":"Activités récréatives et communautaires","sous_categorie":null,"nom":"Centre de prêt Station Énergie","description":"Au chalet Arthur-Therrien Prêt de matériel de sports et de loisirs \n\nDu 2 mai au 31 octobre les samedis et dimanches de 10 h à 17 h \n\nDu 5 Juin au 25 septembre les vendredis de 17h – 21h\n\nOffert par Camp Énergie \n\nDescription : \n\nPrêt d’équipement sportif: vélo d’apprentissage, trottinettes, jeu de bocce, raquettes et balles de ","promoteur":"Camp Énergie","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"6","age_maximum":null,"date_debut":"2026-05-02","date_fin":"2026-10-31","id_seance":"674708","site_seance":"Chalet du parc Arthur-Therrien","adresse_site_seance":"3750 boulevard Gaétan-Laberge  , Verdun, H4G3C1","telephone_site_seance":"5147657150","heure_debut_seance":"10:00:00","heure_fin_seance":"17:00:00","jour_semaine_seance":"Samedi","latitude":"45.465461","longitude":"-73.562565"},
{"id_activite":"900619","categorie":"Activités récréatives et communautaires","sous_categorie":"Apprentissage des langues","nom":"Groupe de conversation ANG","description":"Venez pratiquer l'anglais à la bibliothèque en compagnie d’autres personnes qui souhaitent améliorer leur conversation et leur vocabulaire. Rencontres informelles dans une atmosphère de partage et de bonne humeur.\n\nCome and practice English with other people who want to improve their conversation and vocabulary. Informal meeting","promoteur":"Bibliothèque Benny","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"14","age_maximum":null,"date_debut":"2026-08-31","date_fin":"2026-12-20","id_seance":"678709","site_seance":"Bibliothèque Benny","adresse_site_seance":"6400 avenue de Monkland  , Montréal, H4B1H3","telephone_site_seance":"5148724147","heure_debut_seance":"16:30:00","heure_fin_seance":"18:00:00","jour_semaine_seance":"Lundi","latitude":"45.466542","longitude":"-73.631017"},
{"id_activite":"900702","categorie":"Sports et activités physiques","sous_categorie":"Sports sur glace","nom":"Patinage libre tous","description":"Le port du casque et d’un protège cou est fortement recommandé pour les adultes et les jeunes pour les séances de patin libre.\n\nPatin libre pour aînés mardi, jeudi et vendredi 10 h - 11h\nPatin libre pour tous samedi 15 h30 - 17 h00 \nPatin libre pour tous dimanche 13 h15 - 14 h 30","promoteur":"L'Île-Bizard - Sainte-Geneviève","est_inscription_obligatoire":"Faux","est_annulee":"Vrai","age_minimum":null,"age_maximum":null,"date_debut":"2026-09-14","date_fin":"2027-03-31","id_seance":"678799","site_seance":"Aréna Vincent-Lecavalier","adresse_site_seance":"750 boulevard Jacques-Bizard  , L'Île-Bizard, H9C2Y2","telephone_site_seance":"5146205444","heure_debut_seance":"10:00:00","heure_fin_seance":"11:00:00","jour_semaine_seance":"Mardi","latitude":"45.50272","longitude":"-73.87627"},
{"id_activite":"899993","categorie":"Danse expressive","sous_categorie":"Danse latine","nom":"Lundis Salsa","description":"Offert par Wilfredo Mendoza l dans le cadre de la programmation Verdun Actif.\nLundis Salsa \nDu 18 mai au 12 octobre \nActivité 18h à 22h30","promoteur":"Arrondissement de Verdun","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"16","age_maximum":null,"date_debut":"2026-08-31","date_fin":"2026-12-20","id_seance":"678084","site_seance":"Serres de Verdun","adresse_site_seance":"7000 boulevard LaSalle  , Verdun, H4H2T1","telephone_site_seance":"5147657150","heure_debut_seance":"17:30:00","heure_fin_seance":"22:30:00","jour_semaine_seance":"Lundi","latitude":"45.440088","longitude":"-73.580123"},
{"id_activite":"898726","categorie":"Sports collectifs","sous_categorie":"Hockey balle","nom":"Hockey cosom libre","description":"Hockey cosom libre  (plusieurs type de cours offerts)","promoteur":"Centre de loisirs de Lachine","est_inscription_obligatoire":"Faux","est_annulee":"Faux","age_minimum":"5","age_maximum":null,"date_debut":"2026-09-22","date_fin":"2026-12-10","id_seance":"676414","site_seance":"École Paul-Jarry","adresse_site_seance":"676 11e Avenue  , Lachine, H8S3G9","telephone_site_seance":"514","heure_debut_seance":"18:30:00","heure_fin_seance":"21:30:00","jour_semaine_seance":"Mardi","latitude":"45.441726","longitude":"-73.673891"},
{"id_activite": "897448", "categorie": "Danse sociale", "sous_categorie": "Danse en ligne", "nom": "Danse en ligne / Cours de danse en ligne", "description": "Trois activités suivantes offertes par Johanne Riel dans le cadre de la programmation Verdun Actif. \nPratique libre et gratuite pour tous. Aucune réservation nécessaire\n\n\nDanse en ligne 13h à 16h (GRATUIT)\n12h30 à 16h30 (heure de possession du plancher)\nDimanches : du 17 mai au 11 octobre\n\nCours de danse en ligne 13h à 15h30 (GRATUIT)\n12h30 à 16h (heure de possession du plancher)\nJeudis : 28 mai au 3 septembre\n\nDanse en ligne 19h à 22h (GRATUIT)\n18h30 à 22h30 (heure de possession du plancher)\nVendredis : 29 mai au 4 septembre 19h à 22h", "promoteur": "Arrondissement de Verdun", "est_inscription_obligatoire": "Faux", "est_annulee": "Faux", "age_minimum": "16", "age_maximum": null, "date_debut": "2026-05-17", "date_fin": "2026-10-11", "id_seance": "674626", "site_seance": "Serres de Verdun", "adresse_site_seance": "7000 boulevard LaSalle  , Verdun, H4H2T1", "telephone_site_seance": "5147657150", "heure_debut_seance": "12:30:00", "heure_fin_seance": "16:00:00", "jour_semaine_seance": "Jeudi", "latitude": "45.44008816200075", "longitude": "-73.58012289436215"},
{"id_activite": "897448", "categorie": "Danse sociale", "sous_categorie": "Danse en ligne", "nom": "Danse en ligne / Cours de danse en ligne", "description": "Trois activités suivantes offertes par Johanne Riel dans le cadre de la programmation Verdun Actif. \nPratique libre et gratuite pour tous. Aucune réservation nécessaire\n\n\nDanse en ligne 13h à 16h (GRATUIT)\n12h30 à 16h30 (heure de possession du plancher)\nDimanches : du 17 mai au 11 octobre\n\nCours de danse en ligne 13h à 15h30 (GRATUIT)\n12h30 à 16h (heure de possession du plancher)\nJeudis : 28 mai au 3 septembre\n\nDanse en ligne 19h à 22h (GRATUIT)\n18h30 à 22h30 (heure de possession du plancher)\nVendredis : 29 mai au 4 septembre 19h à 22h", "promoteur": "Arrondissement de Verdun", "est_inscription_obligatoire": "Faux", "est_annulee": "Faux", "age_minimum": "16", "age_maximum": null, "date_debut": "2026-05-17", "date_fin": "2026-10-11", "id_seance": "674627", "site_seance": "Serres de Verdun", "adresse_site_seance": "7000 boulevard LaSalle  , Verdun, H4H2T1", "telephone_site_seance": "5147657150", "heure_debut_seance": "12:30:00", "heure_fin_seance": "16:30:00", "jour_semaine_seance": "Dimanche", "latitude": "45.44008816200075", "longitude": "-73.58012289436215"},
{"id_activite": "897448", "categorie": "Danse sociale", "sous_categorie": "Danse en ligne", "nom": "Danse en ligne / Cours de danse en ligne", "description": "Trois activités suivantes offertes par Johanne Riel dans le cadre de la programmation Verdun Actif. \nPratique libre et gratuite pour tous. Aucune réservation nécessaire\n\n\nDanse en ligne 13h à 16h (GRATUIT)\n12h30 à 16h30 (heure de possession du plancher)\nDimanches : du 17 mai au 11 octobre\n\nCours de danse en ligne 13h à 15h30 (GRATUIT)\n12h30 à 16h (heure de possession du plancher)\nJeudis : 28 mai au 3 septembre\n\nDanse en ligne 19h à 22h (GRATUIT)\n18h30 à 22h30 (heure de possession du plancher)\nVendredis : 29 mai au 4 septembre 19h à 22h", "promoteur": "Arrondissement de Verdun", "est_inscription_obligatoire": "Faux", "est_annulee": "Faux", "age_minimum": "16", "age_maximum": null, "date_debut": "2026-05-17", "date_fin": "2026-10-11", "id_seance": "674628", "site_seance": "Serres de Verdun", "adresse_site_seance": "7000 boulevard LaSalle  , Verdun, H4H2T1", "telephone_site_seance": "5147657150", "heure_debut_seance": "18:30:00", "heure_fin_seance": "22:30:00", "jour_semaine_seance": "Vendredi", "latitude": "45.44008816200075", "longitude": "-73.58012289436215"}
]
""")
BY = {r["id_seance"]: r for r in FIXTURE}


def run_events(rows):
    stats = {}
    return T.events(rows, SRC, CFG, TZ, TODAY, stats), stats


def at(evs, name, day=None, hm=None):
    return [e for e in evs if e.name == name and (day is None or e.start_local[:10] == day)
            and (hm is None or e.start_local[11:16] == hm)]


def synth(base, **over):
    """A fixture row with fields replaced - SYNTHETIC, said where used."""
    r = dict(BY[base])
    r.update(over)
    return r


# ------------------------------------------------------------- 1. the config
print("the config")
check("the licence line fits the sync's kept tail (<= 200) and names the licence",
      0 < len(CFG["attribution"]) <= 200 and "CC BY 4.0" in CFG["attribution"])
keys = (list(SRC["category_by_title_word"].values()) + list(SRC["category_by_subcategory"].values())
        + list(SRC["category_by_category"].values()) + [SRC["category_default"]])
check("every configured category is a real lens key", all(k in VALID_CATEGORIES for k in keys),
      [k for k in keys if k not in VALID_CATEGORIES])
days = CFG["skip_dates"]
check("skip_dates are real dates, in order, a year deep",
      days == sorted(days) and all(T.date.fromisoformat(d) for d in days) and days[-1] >= "2027-10-01", days)
check("skip_dates are Quebec's holidays on their days (Thanksgiving 2026 is a Monday, Good Friday 2027 a Friday)",
      T.date(2026, 10, 12).isoformat() in days and T.date.fromisoformat("2027-03-26").weekday() == 4
      and "2027-03-26" in days)
check("the reader is paced to robots.txt's Crawl-delay: 10",
      float(CFG["crawl_delay"]) >= 10 and T.MIN_INTERVAL_S >= 10)
check("the read names its columns, and none is a person's", SRC["fields"]
      and not [f for f in SRC["fields"] if re.search(r"email|courriel|contact|nom_resp", f)])

# ------------------------------------------------------------- 2. who is in
print()
print("what counts as a drop-in")
expect = {
    "678346": (True, "free play by its title"), "676553": (True, None), "674710": (True, "drop-in said in words"),
    "679594": (True, "drop-in said in words"),
    "678709": (True, "drop-in by its description's words (venez, ouvert a tous)"),
    "676571": (False, "a course, league or club session (its words)"),
    "676414": (False, "a course, league or club session (its words)"),
    "677449": (False, "a course, league or club session (its words)"),
    "678292": (False, "court or space booking, not a session"),
    "678263": (False, "court or space booking, not a session"),
    "677292": (False, "a course: 'l'inscription a ce cours'"),
    "671290": (False, "lifeguard qualification course"),
    "674708": (False, "equipment loan counter, not a session"),
    "676752": (False, "no drop-in evidence (a club's or course's listing)"),
    "678084": (False, "no drop-in evidence (a club's or course's listing)"),
}
for sid, (kept, why) in expect.items():
    r = BY[sid]
    got = T.verdict(r["nom"], r["description"] or "")
    check(f"{r['nom'][:44]!r}: {'kept' if kept else 'refused'}{' - ' + why if why else ''}",
          got[0] == kept and (why is None or got[1] == why), got)
check("'L'inscription n'est PAS requise' keeps a skate whose text also says 'Inscription requise' (for skate loans)",
      T.verdict("Patinage Libre", BY["679594"]["description"])[0])
check("a residents-only skate is refused even if its flag says no registration",
      not T.verdict("Patinage libre", "Patinage libre pour tous, réservé aux citoyen.nes de Montréal.")[0])
check("'(plusieurs type de cours offerts)' vetoes a 'libre' title - Lachine's directory rows",
      not T.verdict(BY["676414"]["nom"], BY["676414"]["description"])[0])

evs, st = run_events(FIXTURE)
check("est_inscription_obligatoire Vrai is refused, free-play title or not (a reserved place)",
      st.get("refused: registration required (est_inscription_obligatoire)") == 1
      and st.get("  of which a free-play title that needs a reserved place") == 1
      and not [e for e in evs if e.name.startswith("Patinage libre lundi le 5 oct")], st)
check("a cancelled session is refused", st.get("refused: cancelled (est_annulee)") == 1
      and not at(evs, "Patinage libre tous"), st)
for name in ("Badminton à l'heure", "Pickleball à l'heure", "Hockey sur glace 0-17 ans", "Yoga",
             "Médaille de bronze", "Centre de prêt Station Énergie", "Lundis Salsa"):
    check(f"no row written for {name!r}", not at(evs, name))

# ------------------------------------------------------------- 3. one row per stretch
print()
print("one row per contiguous session, on its date")
swim = at(evs, "Baignade libre Adultes", "2026-10-05")
check("TWO free swims on one Monday are TWO rows (12-14 and 20:30-22), never one 12-22 row",
      sorted((e.start_local[11:16], e.end_local[11:16]) for e in swim) == [("12:00", "14:00"), ("20:30", "22:00")],
      [(e.start_local, e.end_local) for e in swim])
check("each names the day's other stretch", all("Aussi ce jour-là" in e.description for e in swim))
check("Thanksgiving Monday 2026-10-12 is not written (statutory holiday)",
      not at(evs, "Baignade libre Adultes", "2026-10-12") and at(evs, "Baignade libre Adultes", "2026-10-19"))
check("weekly expansion stops at the horizon (today + 90 days), not the season's 2027-01-29",
      max(e.start_local[:10] for e in at(evs, "Baignade libre Adultes")) <= "2027-01-02")
soccer = at(evs, "Soccer pratique libre adulte récréatif", "2026-10-10")
check("back-to-back 18-20 and 20-22 join into ONE 18:00-22:00 row that lists its sessions",
      len(soccer) == 1 and soccer[0].end_local.endswith("22:00:00") and "Séances : 18 h à 20 h et 20 h à 22 h" in soccer[0].description,
      [(e.start_local, e.end_local) for e in soccer])
pk = at(evs, "Pickleball pratique gratuite")
check("one session published twice (674710/674711) is one row per Monday, ending with the season (11-16)",
      len(pk) == len({e.start_local for e in pk}) and max(e.start_local[:10] for e in pk) == "2026-11-16"
      and "2026-10-12" not in {e.start_local[:10] for e in pk}, [e.start_local for e in pk])
skate = at(evs, "Patinage Libre", "2026-10-06")
check("Verdun's skate under two ids (901149, 901187) is one 13:30 row, and its 08:30 skate another",
      sorted(e.start_local[11:16] for e in skate) == ["08:30", "13:30"], [e.start_local for e in skate])
check("a one-day row whose weekday contradicts its date (901148: 'Lundi' on Tuesday 10-06) is refused",
      st.get("one-day row whose weekday contradicts its date") == 1, st)
gold = [e for e in evs if e.name.startswith("Zumba Gold")]
check("Zumba Gold for 16+ and for 5+ at one hour (two ids) is one row on 10-08, its last Thursday",
      [e.start_local for e in gold] == ["2026-10-08T09:30:00"], [e.start_local for e in gold])
# SYNTHETIC: 678335 moved onto Auditorium de Verdun's point - its own point is
# refused below, and the closure clause is what this case is about.
zevs, _ = run_events([synth("678335", latitude="45.46223", longitude="-73.563229")])
zumba = {e.start_local[:10] for e in zevs}
check("'Pas d'activité le 19 novembre' is not written; the Thursdays either side are",
      "2026-11-19" not in zumba and {"2026-11-12", "2026-11-26"} <= zumba, sorted(zumba))
danse = {e.start_local[:10] for e in at(evs, "Danse en ligne / Cours de danse en ligne")}
check("897448: 'Jeudis : 28 mai au 3 septembre' and 'Vendredis : 29 mai au 4 septembre' end those days - "
      "the SUNDAY range ('du 17 mai au 11 octobre') does not let Thursday 10-08 or Friday 10-09 through",
      danse == {"2026-10-04", "2026-10-11"} and st.get("dates not written: outside the description's own dates") == 2,
      (sorted(danse), st.get("dates not written: outside the description's own dates")))
check("a range's weekday is read off its own line, before or after it, plural or with a colon",
      T.stated_ranges("du 2 mai au 31 octobre les samedis\ndu 5 juin au 25 septembre les vendredis",
                      T.date(2026, 5, 2), T.date(2026, 10, 31), 4) == [(T.date(2026, 6, 5), T.date(2026, 9, 25))]
      and T.stated_ranges("Du 18 mai au 12 octobre, sauf le lundi 12 octobre", T.date(2026, 5, 2),
                          T.date(2026, 12, 20), 3) == [(T.date(2026, 5, 18), T.date(2026, 10, 12))])
check("a title naming its own date ('... 05 juillet ...') is not written on the autumn's 12 Sundays",
      not [e for e in evs if e.name.startswith("Badminton libre (7ans+)")]
      and st.get("dates not written: the title names another date", 0) == 12, st)
salsa = synth("678084", description=BY["678084"]["description"] + "\nAucune réservation nécessaire.")  # SYNTHETIC
sevs, sst = run_events([salsa])
check("SYNTHETIC: 'Du 18 mai au 12 octobre' ends a row whose date_fin says 2026-12-20 (and 10-12 is a holiday)",
      [e.start_local[:10] for e in sevs] == ["2026-10-05"]
      and sst.get("dates not written: outside the description's own dates", 0) >= 9,
      ([e.start_local for e in sevs], sst))
spans = {}
for r in FIXTURE:
    spans.setdefault((r["site_seance"], T._norm(r["nom"])), []).append(
        (T._minutes(r["heure_debut_seance"]), T._minutes(r["heure_fin_seance"])))


def covered(e):
    """Every minute of the row is inside some published session of its title."""
    a = int(e.start_local[11:13]) * 60 + int(e.start_local[14:16])
    b = int(e.end_local[11:13]) * 60 + int(e.end_local[14:16])
    ss = spans[(e.venue_name, T._norm(e.name))]
    return all(any(x <= m < y for x, y in ss) for m in range(a, b))


check("NO row spans a gap: every minute of every row is inside a published session",
      all(covered(e) for e in evs), [e.start_local for e in evs if not covered(e)][:3])
folded = synth("679594", nom="Patinage libre", id_seance="x1")  # SYNTHETIC: another case
fevs, _ = run_events([BY["679594"], folded])
check("SYNTHETIC: 'Patinage Libre' and 'Patinage libre' at one rink and hour are one row", len(fevs) == 1, len(fevs))

# ------------------------------------------------------------- 4. identity
print()
print("identity")
check("source_ids and fingerprints are unique", len({e.source_id for e in evs}) == len(evs)
      == len({e.fingerprint for e in evs}))
shuffled = list(FIXTURE)
random.Random(7).shuffle(shuffled)
evs2, _ = run_events(shuffled)
check("a shuffled re-read writes identical rows",
      sorted((e.source_id, e.fingerprint, e.start_utc, e.end_utc, e.description) for e in evs)
      == sorted((e.source_id, e.fingerprint, e.start_utc, e.end_utc, e.description) for e in evs2))
with tempfile.TemporaryDirectory() as tmpdir:
    store = EventStore(os.path.join(tmpdir, "s.json"))
    for e in evs:
        store.upsert(e)
    for e in evs2:
        store.upsert(e)
    check("a second upsert of the same read adds 0 and rekeys 0",
          store.stats.get("added") == len(evs) and not store.stats.get("rekeyed"), store.stats)

# ------------------------------------------------------------- 5. the row
print()
print("the row")
box = CFG["bbox"]
check("every row is on the island, with the City's point marked exact",
      all(box[0] <= e.latitude <= box[2] and box[1] <= e.longitude <= box[3] and e.coords_exact for e in evs))
check("'504 5e Avenue, Verdun, H4G' at 45.636,-73.492 (20 km off, in Pointe-aux-Trembles) is refused, never written",
      not [e for e in evs if e.venue_name == "École Notre-Dame-de-Lourdes" or e.latitude > 45.6]
      and st.get("unplaceable (the City's point contradicts its own address)") == 1, st)
check("a tie keeps the point: Auditorium de Verdun (H4G: one peer near, one far) is written",
      at(evs, "Patinage Libre", "2026-10-06"))
pts = {(T._fsa(r), T._point(r)) for r in FIXTURE}
check("every written row is within 5 km of most other fixture sites sharing its postal area",
      all(2 * sum(1 for f, q in pts if f == e.postal_code[:3] and q != (round(e.latitude, 5), round(e.longitude, 5))
                  and T._km((e.latitude, e.longitude), q) > 5)
          <= sum(1 for f, q in pts if f == e.postal_code[:3] and q != (round(e.latitude, 5), round(e.longitude, 5)))
          for e in evs))
# SYNTHETIC postal areas: a majority decides; one peer that disagrees refuses both.
mk = lambda fsa, lat, lon: {"adresse_site_seance": f"1 rue X, Montréal, {fsa}1A1", "latitude": lat, "longitude": lon}
bad, alone = T.contradicted_points([mk("H8S", 45.432, -73.675), mk("H8S", 45.440, -73.682), mk("H8S", 45.4417, -73.6739),
                                    mk("H8S", 45.6455, -73.4899), mk("H9C", 45.50, -73.87)])
check("SYNTHETIC: three Lachine points outvote the fourth at 45.6455,-73.4899 (27 km); a lone site is unchecked",
      list(bad) == [("H8S", (45.6455, -73.4899))] and 25 < bad[("H8S", (45.6455, -73.4899))] < 30
      and alone == {("H9C", (45.5, -73.87))}, (bad, alone))
check("UTC is right on both sides of the clock change (EDT 10-05, EST 11-30)",
      at(evs, "Baignade libre Adultes", "2026-10-05", "12:00")[0].start_utc == "2026-10-05T16:00:00Z"
      and at(evs, "Baignade libre Adultes", "2026-11-30", "12:00")[0].start_utc == "2026-11-30T17:00:00Z")
check("address and postal code are split from the City's one string",
      swim[0].address == "1055 rue d' Hibernia, Montréal" and swim[0].postal_code == "H3K 2V3", (swim[0].address, swim[0].postal_code))
check("the licence line is the last paragraph of every row",
      all(e.description.rsplit("\n\n", 1)[-1] == CFG["attribution"] for e in evs))
check("French titles as published", at(evs, "Soccer pratique libre adulte récréatif") and at(evs, "Groupe de conversation ANG"))
conv = at(evs, "Groupe de conversation ANG")
check("a library conversation group is `learning`; a swim `fitness`; a pickleball `sports`",
      conv[0].category == "learning" and swim[0].category == "fitness" and pk[0].category == "sports")
check("after the sync's derive_categories every row's keys are lens keys",
      all(derive_categories(dict(e.__dict__))[0] in VALID_CATEGORIES for e in evs))
ke13, _ = run_events([synth("678709", age_minimum="7", age_maximum="13")])  # SYNTHETIC: the 7-13 library band
check("SYNTHETIC: a library programme for 7 to 13 reaches the kids door", ke13 and "kids" in ke13[0].categories,
      ke13[0].categories if ke13 else None)
one_line = ("Patin libre pour aînés mardi, jeudi et vendredi 10 h - 11h. Patin libre pour tous SAMEDI 15H30-17H00 "
            "et DIMANCHE DE 13H15 À 14H30.")  # 901343's text, one line, as published
tue, _ = run_events([synth("678799", est_annulee="Faux", age_minimum="6", description=one_line)])  # SYNTHETIC
sun, _ = run_events([synth("678799", est_annulee="Faux", age_minimum="6", description=one_line, jour_semaine_seance="Dimanche",
                           heure_debut_seance="13:15:00", heure_fin_seance="14:30:00")])  # SYNTHETIC
check("SYNTHETIC: a 6+ skate whose text gives Tuesday 10 h to seniors says so; its Sunday 13 h 15 'pour tous' does not",
      tue and all("(aînés, selon la description)" in e.description for e in tue)
      and sun and all("aînés, selon" not in e.description and "(6 ans et plus)" in e.description for e in sun),
      (tue[0].description[:90] if tue else None, sun[0].description[:90] if sun else None))

# ------------------------------------------------------------- 6. price
print()
print("the price, which 0227 reads")
check("a row says free only where its own text says so: Verdun's skate ('Activité gratuite') and pickleball",
      tagged_free(skate[0]) and tagged_free(pk[0]) and "Admission: free." in pk[0].description)
check("a swim the data gives no price for is not tagged free", not tagged_free(swim[0])
      and "Tarif non indiqué" in swim[0].description)
check("EVERY fixture row: 0227 says free exactly when the adapter's tier does",
      all(tagged_free(e) == ("Admission: free." in e.description) for e in evs),
      [e.name for e in evs if tagged_free(e) != ("Admission: free." in e.description)][:4])
fee = synth("678346", description="Coût : 3,50 $ par adulte. Gratuit pour les enfants.")  # SYNTHETIC
fe, _ = run_events([fee])
check("SYNTHETIC: a stated amount says '(pas gratuit)', and 'Gratuit pour les enfants' cannot make it free",
      fe and all("(pas gratuit)" in e.description and not tagged_free(e) for e in fe), fe[0].description if fe else None)
perk = synth("678346", description="Prêt de patins gratuit à l'accueil.")  # SYNTHETIC
pe, pst = run_events([perk])
check("SYNTHETIC: 'Prêt de patins gratuit' is not the session's price - the text is withheld, nothing tagged",
      pe and all(not tagged_free(e) and "patins" not in e.description for e in pe)
      and pst.get("source text withheld: it reads as free, not about the price"), pe[0].description if pe else None)
acc = synth("678346", description="Accès libre au bassin, venez nager.")  # SYNTHETIC
ae, _ = run_events([acc])
check("SYNTHETIC: 'Accès libre' (open access) does not tag an unpriced swim free", ae and not tagged_free(ae[0]))
for phrase in ("Gratuit pour les enfants de 5 ans et moins.", "Gratuit pour les résidents de Verdun.",
               "Gratuit pour les aînés le mardi.", "Gratuit avec la carte Accès Montréal.", "Enfants : gratuit."):
    tier = T.price_line("Baignade libre", phrase)[0]
    check(f"SYNTHETIC: {phrase!r} is free for SOME people: not the session's price", tier == "conditional", tier)
kid = synth("678346", description="Gratuit pour les enfants de 5 ans et moins.")  # SYNTHETIC
ke, kst = run_events([kid])
check("SYNTHETIC: ...and the row says nothing 0227 reads as free: the source text is withheld",
      ke and all(not tagged_free(e) and "enfants" not in e.description for e in ke)
      and kst.get("price: conditional"), ke[0].description if ke else None)
check("SYNTHETIC: \"l'air frais\" is not a fee, 'des frais' is",
      T.price_line("Patinage libre", "Venez prendre l'air frais sur la patinoire.")[0] == "unknown"
      and T.price_line("Patinage libre", "Des frais s'appliquent.")[0] == "fee")

print()
print("the price after the sync's 800-character cap")
check("the adapter's budget is the sync's DESCRIPTION_MAX", T.DESCRIPTION_MAX == S.DESCRIPTION_MAX)
check("every fixture row fits it, so the sync never cuts one",
      all(len(e.description) <= S.DESCRIPTION_MAX for e in evs), max(len(e.description) for e in evs))
after = [(e, *stored_free(e)) for e in evs]
check("EVERY fixture row, as the sync stores it: 0227 says free exactly when the adapter's tier does",
      all(f == ("Admission: free." in e.description) for e, f, _ in after),
      [e.name for e, f, _ in after if f != ("Admission: free." in e.description)][:4])
check("...and its licence line survives the sync", all(CFG["attribution"] in row["description"] for _, _, row in after))
dl = at(evs, "Danse en ligne / Cours de danse en ligne")
check("897448's 600-character text is shortened to fit, and still reads free in the stored row",
      dl and all(len(e.description) <= S.DESCRIPTION_MAX and stored_free(e)[0] for e in dl))
rules = (" Le bonnet de bain est obligatoire pour tous les baigneurs. Les enfants de moins de 8 ans doivent être"
         " accompagnés d'un adulte dans l'eau en tout temps. La douche savonneuse est obligatoire avant d'entrer"
         " dans le bassin. Les vestiaires ferment 15 minutes avant la fin de la séance. Les objets de valeur"
         " doivent être laissés dans les casiers prévus à cet effet. La direction se réserve le droit de refuser"
         " l'accès en cas de comportement inapproprié ou de capacité atteinte. Merci de votre collaboration.")
longfee = synth("678346", description="Coût : 4,25 $ par adulte, gratuit pour les enfants de 5 ans et moins." + rules)
lf, _ = run_events([longfee])  # SYNTHETIC: a long fee row - the "(pas gratuit)" veto must survive to_row
check("SYNTHETIC: a long fee row says '(pas gratuit)' in its FIRST paragraph, and the stored row is not free",
      lf and all("(pas gratuit)" in e.description.split("\n\n")[0] and not stored_free(e)[0]
                 and "(pas gratuit)" in stored_free(e)[1]["description"] for e in lf),
      [stored_free(e)[1]["description"][:300] for e in lf][:1])

# ------------------------------------------------------------- 7. the run
print()
print("the run")
import contextlib  # noqa: E402
import io  # noqa: E402


class Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeCkan:
    """The datastore, served from FIXTURE. `answer(n)` may return a status for
    the n-th call, to play a refusal; `grow` changes the total mid-read."""

    def __init__(self, answer=None, grow=False):
        self.headers, self.calls, self.answer, self.grow = {}, [], answer or (lambda n: None), grow

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params))
        status = self.answer(len(self.calls))
        if status:
            return Resp(status)
        o, n = int(params.get("offset", 0)), int(params.get("limit", T.PAGE_LIMIT))
        total = len(FIXTURE) + (1 if self.grow and len(self.calls) == 2 else 0)
        return Resp(200, {"success": True, "result": {"records": FIXTURE[o:o + n], "total": total}})


slept = []
fake = FakeCkan()
rd = T.Reader(fake, sleep=slept.append, clock=lambda: 0.0)
rows = rd.resource(SRC["resource_id"], fields=SRC["fields"], limit=10)
check("paging by offset reads every row in `_id` order, naming the columns",
      len(rows) == len(FIXTURE) and [c["offset"] for c in fake.calls] == [0, 10, 20]
      and all(c["sort"] == "_id asc" and "fields" in c for c in fake.calls), fake.calls)
check("requests are paced to the crawl delay (10 s apart)", slept == [10.0, 10.0], slept)
fake = FakeCkan(grow=True)
rows = T.Reader(fake, sleep=lambda s: None, clock=lambda: 0.0).resource(SRC["resource_id"], limit=10)
check("a table rebuilt mid-read restarts the walk instead of returning a seam", len(rows) == len(FIXTURE)
      and len(fake.calls) == 5, len(fake.calls))


def run(fake, *extra):
    real_session, real_reader = T.requests.Session, T.Reader
    T.requests.Session = lambda: fake
    T.Reader = lambda *a, **k: real_reader(*a, **dict(k, sleep=lambda s: None))
    out = io.StringIO()
    path = os.path.join(tmpdir, f"store{len(os.listdir(tmpdir))}.json")
    try:
        with contextlib.redirect_stdout(out):
            got = T.main(["--config", CONFIG, "--store", path, *extra])
    except BaseException as exc:                                  # noqa: BLE001
        got = exc
    finally:
        T.requests.Session, T.Reader = real_session, real_reader
    return got, out.getvalue(), path


def stored(path):
    return EventStore(path).records if os.path.exists(path) else {}


with tempfile.TemporaryDirectory() as tmpdir:
    code, out, path = run(FakeCkan())
    check("a whole run writes every row and exits 0", code == 0 and len(stored(path)) == len(evs),
          (code, len(stored(path)), out[-300:]))
    check("the run prints every refusal reason with its count",
          "refused: registration required (est_inscription_obligatoire): 1" in out
          and "refused: court or space booking, not a session: 2" in out, out[:600])
    fake = FakeCkan(lambda n: 403)
    code, out, path = run(fake)
    check("a 403 is a refusal: asked once, never retried, run exits 0 and still saves",
          code == 0 and len(fake.calls) == 1 and "REFUSED" in out and os.path.exists(path), (len(fake.calls), out))
    fake = FakeCkan(lambda n: 503 if n == 1 else None)
    code, out, path = run(fake)
    check("a 503 is retried, and the run then completes", code == 0 and len(stored(path)) == len(evs), out[-200:])
    fake = FakeCkan()
    code, out, path = run(fake, "--max-minutes", "0.0000001")
    check("--max-minutes: past the deadline no request starts, the run exits 0 and still saves",
          code == 0 and not fake.calls and "STOPPED" in out and os.path.exists(path), (fake.calls, out))

# ------------------------------------------------------------- 8. called off
# 2026-10-05 (the owner): "we don't want users to go to an event or center that
# is closed". est_annulee turns a session the City HAD listed into one it
# says is off; the row we wrote from it stays on the map unless the run
# tombstones the fingerprint THAT row has (EventStore.cancel).
print()
print("called off: est_annulee is a tombstone with the live row's fingerprint")
off_row = BY["678799"]                                      # real: Patinage libre tous, est_annulee Vrai
on_row = synth("678799", est_annulee="Faux")                # SYNTHETIC: the same row before the City called it off
live_evs, _ = run_events([on_row])
off_evs, st_off = run_events([off_row])
tombs = T.cancelled_events([off_row], off_evs, SRC, CFG, TZ, TODAY)
check("the real est_annulee row writes nothing and is counted",
      not off_evs and st_off.get("refused: cancelled (est_annulee)") == 1, st_off)
check("its tombstones are exactly the rows it wrote while it ran (every Tuesday in the window)",
      len(live_evs) >= 10 and sorted(e.fingerprint for e in tombs) == sorted(e.fingerprint for e in live_evs),
      (len(live_evs), len(tombs)))
check("same source and source_id as the live rows",
      sorted((e.source, e.source_id) for e in tombs) == sorted((e.source, e.source_id) for e in live_evs))
_tmp = tempfile.mkdtemp()
st_live, st_off_store = EventStore(os.path.join(_tmp, "a.json")), EventStore(os.path.join(_tmp, "b.json"))
for e in live_evs:
    st_live.upsert(e)
for e in tombs:
    st_off_store.cancel(e, "est_annulee")
check("THROUGH THE STORE: cancel's fingerprints are upsert's (events.external_id)",
      st_live.records and sorted(st_off_store.tombstones) == sorted(st_live.records) and not st_off_store.records,
      (len(st_live.records), len(st_off_store.tombstones)))
# SYNTHETIC: a cancelled 10:00-11:00 hour joined to a live 11:00-12:00 one.
# The stretch's identity is its FIRST clock, so the row we wrote was 10:00 and
# today's is 11:00: the 10:00 fingerprint is the one to tombstone.
nxt = synth("678799", est_annulee="Faux", id_seance="x1", heure_debut_seance="11:00:00", heure_fin_seance="12:00:00")
joined, _ = run_events([on_row, nxt])
now_evs, _ = run_events([off_row, nxt])
gone = T.cancelled_events([off_row, nxt], now_evs, SRC, CFG, TZ, TODAY)
check("a cancelled first hour of a stretch tombstones the 10:00 row and writes the 11:00 one",
      {e.start_local[11:16] for e in joined} == {"10:00"} and {e.start_local[11:16] for e in now_evs} == {"11:00"}
      and sorted(e.fingerprint for e in gone) == sorted(e.fingerprint for e in joined), (len(gone), len(joined)))
reg = synth("678799", est_inscription_obligatoire="Vrai")   # SYNTHETIC: cancelled AND registration-only
check("a cancelled session the policy refuses anyway gives no tombstone (no row was ever written)",
      T.cancelled_events([reg], [], SRC, CFG, TZ, TODAY) == [])
check("no est_annulee row: no second pass at all", T.cancelled_events([on_row], live_evs, SRC, CFG, TZ, TODAY) == [])

print()
print("complete reads, and only those, are marked")
from datetime import timedelta  # noqa: E402


class ShortCkan(FakeCkan):
    """CKAN claiming more rows than it serves: a read that stopped short."""

    def get(self, url, params=None, timeout=None):
        r = super().get(url, params, timeout)
        if r.status_code == 200:
            r._body["result"]["total"] += 5
        return r


with tempfile.TemporaryDirectory() as tmpdir:
    code, out, path = run(FakeCkan())
    saved = json.load(open(path, encoding="utf-8"))
    cr = (saved.get("complete_reads") or {}).get("montreal-loisirs") or {}
    check("a whole run marks montreal-loisirs complete over [today, today + horizon]",
          cr.get("from", "")[:10] == TODAY.isoformat()
          and cr.get("to", "")[:10] == (TODAY + timedelta(days=SRC.get("horizon_days", 90))).isoformat(), cr)
    check("and saves the fixture's est_annulee tombstones, counted in the run's output",
          len(saved.get("tombstones") or []) == len(live_evs)
          and f"rows cancelled (est_annulee: a tombstone for the row we wrote): {len(live_evs)}" in out,
          (len(saved.get("tombstones") or []), out[-400:]))
    code, out, path = run(ShortCkan())
    check("a read that got fewer rows than CKAN's total is NOT complete",
          not json.load(open(path, encoding="utf-8")).get("complete_reads") and "read NOT complete" in out, out[-300:])
    code, out, path = run(FakeCkan(lambda n: 403))
    check("a refused read is NOT complete", not json.load(open(path, encoding="utf-8")).get("complete_reads"))

print()
print("a session the City lists but we cannot place is SEEN, never absent")
full, _st = run_events(FIXTURE)
site0 = sorted({e.venue_name for e in full})[0]
# SYNTHETIC: one site's rows lose their point, as a row of the City's table can.
pointless = [dict(r, latitude=None, longitude=None) if r["site_seance"] == site0 else r for r in FIXTURE]
live2, st2 = run_events(pointless)
seen2 = T.seen_events(pointless, live2, SRC, CFG, TZ, TODAY, st2)
seen0 = T.seen_events(FIXTURE, full, SRC, CFG, TZ, TODAY, _st)
want = {e.fingerprint for e in full if e.venue_name == site0}
check("a site whose rows lost their point: exactly its sessions' fingerprints join the seen set",
      bool(want) and set(seen2) - set(seen0) == want, (len(want), len(set(seen2) - set(seen0))))
check("  ...and a fingerprint written live is never also seen",
      not (set(seen2) & {e.fingerprint for e in live2}) and not (set(seen0) & {e.fingerprint for e in full}))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
