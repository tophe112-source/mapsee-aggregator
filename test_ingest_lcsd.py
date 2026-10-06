#!/usr/bin/env python3
"""
test_ingest_lcsd.py - Hong Kong LCSD's walk-in sports sessions and cultural
programme, and the ways a row from them is not what it looks like.

SP and FAC were read from data.smartplay.lcsd.gov.hk (2026-10-03) and
www.lcsd.gov.hk/datagovhk/facility (2026-10-05), trimmed to the fields the
adapter reads; CUL from events.xml / eventDates.xml / venues.xml (2026-10-03),
descriptions cut at 120 characters. Synthetic rows are built in code and say
so. The expensive cases are the quiet ones: a ballot course written as a
session; a session on the holiday its own EN_DAY excludes; two back-to-back
hours written as two pins, or a pool's 11:00 and 20:00 swims as one row
spanning the afternoon; a sports centre pinned 3,000 km away by its own
"144-14-90", or in Kowloon when it is in Yuen Long; Photon's Kowloon BAY Park
for Kowloon Park; eventDates.xml's every-day list for a Monday class;
"19:30, 15/11/2026" read as the 30th; a two-night show folded into one
multi-day row; a pool's admission fee read as free - in the adapter's text or
after the sync's cap. And, from a review of the 2026-10-05 run (FX_CUL2, live):
a weekly lecture's one 19:30 lent to all 50 days eventDates lists; a list
across two months, "8/11/ 2026" and dotted "22.9.2026" left unread, so every
night got every clock; two shows in two rooms of one theatre folded into one
record, losing the free one; a free masterclass "by registration" written as a
walk-in; SmartPlay placed by Photon with no facility list to check it; a 12 MB
body trickling past --max-minutes. Two checks need mapsee_supabase_sync's
CIVIC_TIMETABLE_SOURCES to name "lcsd-smartplay".

    python test_ingest_lcsd.py
"""
import contextlib
import io
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import date, timedelta

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                             # noqa: BLE001
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["MAPSEE_TODAY"] = "20261005"

from zoneinfo import ZoneInfo  # noqa: E402

import mapsee_ingest_lcsd as T  # noqa: E402
import mapsee_supabase_sync as S  # noqa: E402
from mapsee_ingest import EventStore, VALID_CATEGORIES  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(f"{'ok  ' if cond else 'FAIL'} {label}{'' if cond else '   ' + str(detail)[:400]}")
    if not cond:
        fails.append(label)


HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "lcsd_sources.json")
CFG = T.load_config(CONFIG)
SP_SRC = next(s for s in CFG["sources"] if s["kind"] == "smartplay")
CUL_SRC = next(s for s in CFG["sources"] if s["kind"] == "culture")
TZ = ZoneInfo(CFG["timezone"])
TODAY = T._today(TZ)

# ../mapsee migration 0227 reads `offer:free` from a row's own text. A SUBSET of
# ../mapsee/tools/measure_deals.py's FREE and its FREE_NEG veto - the English
# alternatives these rows can hit. The full classify over the live dry run of
# 2026-10-05 (2,862 rows) agreed with the adapter's tier on every row.
FREE_TAG = re.compile(
    r"(?<![-\w/])free\s+(?:admission|entry|entrance|event|of\s+charge|to\s+(?:attend|join|enter|participate|"
    r"the\s+public|all|everyone)|for\s+(?:all|everyone|kids|children|the\s+public|families)|concert|show|"
    r"session|class|drop[- ]in)\b|\b(?:admission|entry|entrance|attendance|cost|price)\s*(?:is|:|-|–)?\s*free\b"
    r"|\bis\s+free\b|\bat\s+no\s+cost\b", re.I)
FREE_NEG = re.compile(r"\b(?:not|isn'?t|is\s+not)\s+(?:a\s+)?free\b", re.I)


def tagged_free(ev):
    text = f"{ev.name}\n{ev.description}"
    return bool(FREE_TAG.search(text)) and not FREE_NEG.search(text)


def stored(ev):
    """(0227's verdict, row) on what the SYNC writes, after its 800-char cap."""
    row = S.to_row(dict(ev.__dict__), "00000000-0000-0000-0000-000000000000")
    text = f"{row.get('title') or ev.name}\n{row.get('description') or ''}"
    return bool(FREE_TAG.search(text)) and not FREE_NEG.search(text), row


FX_SP = json.loads(r"""{"smartplay":[{"ACTIVITY_NO":"ST260015HES","EN_PGM_NAME":"Healthy Elderly Scheme - Table-tennis Fun Day for Elderly","TC_PGM_NAME":"活力長者計劃 - 乒乓球同樂","EN_ACT_TYPE_NAME":"Healthy Elderly Scheme","EN_DISTRICT":"Sha Tin","PGM_START_DATE":"2026-10-05","PGM_END_DATE":"2026-10-28","EN_DAY":"Mon,Wed (Exclude 19 Oct 2026)","PGM_START_TIME":"10:00","PGM_END_TIME":"12:00","EN_VENUE":"Yuen Chau Kok Sports Centre","EN_TARGET_GRP":"Elderly","MIN_AGE":60,"MAX_AGE":199,"FEE":0.0,"QUOTA":16,"ENROL_METHOD":"WALKIN","EN_NOTES":null,"EN_URL":"SP:ST260015HES&&lang=en"},{"ACTIVITY_NO":"TM260434HES","EN_PGM_NAME":"Healthy Elderly Scheme-Indoor Gateball","TC_PGM_NAME":"活力長者計劃-室內門球同樂","EN_ACT_TYPE_NAME":"Healthy Elderly Scheme","EN_DISTRICT":"Tuen Mun","PGM_START_DATE":"2026-10-07","PGM_END_DATE":"2026-10-28","EN_DAY":"Wed","PGM_START_TIME":"07:00","PGM_END_TIME":"08:00","EN_VENUE":"Tai Hing Sports Centre","EN_TARGET_GRP":"Elderly","MIN_AGE":60,"MAX_AGE":199,"FEE":0.0,"QUOTA":20,"ENROL_METHOD":"WALKIN","EN_NOTES":null,"EN_URL":"SP:TM260434HES&&lang=en"},{"ACTIVITY_NO":"TM260435HES","EN_PGM_NAME":"Healthy Elderly Scheme-Indoor Gateball","TC_PGM_NAME":"活力長者計劃-室內門球同樂","EN_ACT_TYPE_NAME":"Healthy Elderly Scheme","EN_DISTRICT":"Tuen Mun","PGM_START_DATE":"2026-10-07","PGM_END_DATE":"2026-10-28","EN_DAY":"Wed","PGM_START_TIME":"08:00","PGM_END_TIME":"09:00","EN_VENUE":"Tai Hing Sports Centre","EN_TARGET_GRP":"Elderly","MIN_AGE":60,"MAX_AGE":199,"FEE":0.0,"QUOTA":20,"ENROL_METHOD":"WALKIN","EN_NOTES":null,"EN_URL":"SP:TM260435HES&&lang=en"},{"ACTIVITY_NO":"TW261104HFX","EN_PGM_NAME":"Hydro Fitness Fun Day","TC_PGM_NAME":"水中健體同樂日","EN_ACT_TYPE_NAME":"Hydro Fitness","EN_DISTRICT":"Tsuen Wan","PGM_START_DATE":"2026-10-10","PGM_END_DATE":"2026-10-10","EN_DAY":"Sat","PGM_START_TIME":"17:00","PGM_END_TIME":"18:00","EN_VENUE":"Tsuen King Circuit Wu Chung Swimming Pool","EN_TARGET_GRP":"General","MIN_AGE":14,"MAX_AGE":199,"FEE":0.0,"QUOTA":15,"ENROL_METHOD":"WALKIN","EN_NOTES":null,"EN_URL":"SP:TW261104HFX&&lang=en"},{"ACTIVITY_NO":"TW261105HFX","EN_PGM_NAME":"Hydro Fitness Fun Day","TC_PGM_NAME":"水中健體同樂日","EN_ACT_TYPE_NAME":"Hydro Fitness","EN_DISTRICT":"Tsuen Wan","PGM_START_DATE":"2026-10-10","PGM_END_DATE":"2026-10-10","EN_DAY":"Sat","PGM_START_TIME":"20:00","PGM_END_TIME":"21:00","EN_VENUE":"Tsuen King Circuit Wu Chung Swimming Pool","EN_TARGET_GRP":"General","MIN_AGE":14,"MAX_AGE":199,"FEE":0.0,"QUOTA":15,"ENROL_METHOD":"WALKIN","EN_NOTES":null,"EN_URL":"SP:TW261105HFX&&lang=en"},{"ACTIVITY_NO":"TW261106HFX","EN_PGM_NAME":"Hydro Fitness Fun Day","TC_PGM_NAME":"水中健體同樂日","EN_ACT_TYPE_NAME":"Hydro Fitness","EN_DISTRICT":"Tsuen Wan","PGM_START_DATE":"2026-10-10","PGM_END_DATE":"2026-10-10","EN_DAY":"Sat","PGM_START_TIME":"21:00","PGM_END_TIME":"22:00","EN_VENUE":"Tsuen King Circuit Wu Chung Swimming Pool","EN_TARGET_GRP":"General","MIN_AGE":14,"MAX_AGE":199,"FEE":0.0,"QUOTA":15,"ENROL_METHOD":"WALKIN","EN_NOTES":null,"EN_URL":"SP:TW261106HFX&&lang=en"},{"ACTIVITY_NO":"TW261101HFX","EN_PGM_NAME":"Hydro Fitness Fun Day","TC_PGM_NAME":"水中健體同樂日","EN_ACT_TYPE_NAME":"Hydro Fitness","EN_DISTRICT":"Tsuen Wan","PGM_START_DATE":"2026-10-10","PGM_END_DATE":"2026-10-10","EN_DAY":"Sat","PGM_START_TIME":"11:00","PGM_END_TIME":"12:00","EN_VENUE":"Tsuen King Circuit Wu Chung Swimming Pool","EN_TARGET_GRP":"General","MIN_AGE":14,"MAX_AGE":199,"FEE":0.0,"QUOTA":15,"ENROL_METHOD":"WALKIN","EN_NOTES":null,"EN_URL":"SP:TW261101HFX&&lang=en"},{"ACTIVITY_NO":"SSP260544HES","EN_PGM_NAME":"Healthy Elderly Scheme - Baduanjin Play-in","TC_PGM_NAME":"活力長者計劃 - 八段錦同樂","EN_ACT_TYPE_NAME":"Healthy Elderly Scheme","EN_DISTRICT":"Sham Shui Po","PGM_START_DATE":"2026-10-02","PGM_END_DATE":"2026-10-30","EN_DAY":"Fri","PGM_START_TIME":"14:00","PGM_END_TIME":"16:00","EN_VENUE":"Tung Chau Street Park","EN_TARGET_GRP":"Elderly","MIN_AGE":60,"MAX_AGE":199,"FEE":0.0,"QUOTA":20,"ENROL_METHOD":"WALKIN","EN_NOTES":["Participants should wear proper sportswear and clean non-marking rubber-outsole sports shoes."],"EN_URL":"SP:SSP260544HES&&lang=en"},{"ACTIVITY_NO":"SSP260545HES","EN_PGM_NAME":"Healthy Elderly Scheme - Baduanjin Play-in","TC_PGM_NAME":"活力長者計劃 - 八段錦同樂","EN_ACT_TYPE_NAME":"Healthy Elderly Scheme","EN_DISTRICT":"Sham Shui Po","PGM_START_DATE":"2026-10-02","PGM_END_DATE":"2026-10-30","EN_DAY":"Fri","PGM_START_TIME":"14:00","PGM_END_TIME":"16:00","EN_VENUE":"Tung Chau Street Park","EN_TARGET_GRP":"Elderly","MIN_AGE":60,"MAX_AGE":199,"FEE":0.0,"QUOTA":20,"ENROL_METHOD":"WALKIN","EN_NOTES":["Participants should wear proper sportswear and clean non-marking rubber-outsole sports shoes."],"EN_URL":"SP:SSP260545HES&&lang=en"},{"ACTIVITY_NO":"YTM260358MAT","EN_PGM_NAME":"Kung Fu Corner","TC_PGM_NAME":"功夫閣","EN_ACT_TYPE_NAME":"Martial Arts","EN_DISTRICT":"Yau Tsim Mong","PGM_START_DATE":"2026-10-11","PGM_END_DATE":"2026-10-11","EN_DAY":"Sun","PGM_START_TIME":"14:30","PGM_END_TIME":"16:30","EN_VENUE":"Kowloon Park (Sculpture Walk)","EN_TARGET_GRP":"General","MIN_AGE":0,"MAX_AGE":199,"FEE":0.0,"QUOTA":150,"ENROL_METHOD":"WALKIN","EN_NOTES":null,"EN_URL":"SP:YTM260358MAT&&lang=en"},{"ACTIVITY_NO":"TP261044CAR","EN_PGM_NAME":"Tai Po District Water Safety Fun Day 2026","TC_PGM_NAME":"大埔區水上安全同樂日2026","EN_ACT_TYPE_NAME":"Carnivals/Sports Carnivals","EN_DISTRICT":"Tai Po","PGM_START_DATE":"2026-10-03","PGM_END_DATE":"2026-10-03","EN_DAY":"Sat","PGM_START_TIME":"15:00","PGM_END_TIME":"18:00","EN_VENUE":"Tai Po Swimming Pool","EN_TARGET_GRP":"General","MIN_AGE":0,"MAX_AGE":199,"FEE":0.0,"QUOTA":500,"ENROL_METHOD":"WALKIN","EN_NOTES":["Please note the Notes on Enrolment in case of inclement weather .","Public swimming pool admission fee is required"],"EN_URL":"SP:TP261044CAR&&lang=en"},{"ACTIVITY_NO":"EN260112EXC","EN_PGM_NAME":"Excursion for Elderly","TC_PGM_NAME":"長者旅行","EN_ACT_TYPE_NAME":"Land Excursion","EN_DISTRICT":"Eastern","PGM_START_DATE":"2026-10-15","PGM_END_DATE":"2026-10-15","EN_DAY":"Thu","PGM_START_TIME":"09:00","PGM_END_TIME":"17:00","EN_VENUE":"Stanley, The Peak","EN_TARGET_GRP":"Elderly","MIN_AGE":60,"MAX_AGE":199,"FEE":0.0,"QUOTA":96,"ENROL_METHOD":"BALLOT","EN_NOTES":["Bring your own meal & see \"Notes for Participants\".Meet at 9:00am in Mt Parker Road","This activity is organized by Eastern District Leisure Services Office (Tel: 2564 2264)."],"EN_URL":"SP:EN260112EXC&&lang=en"},{"ACTIVITY_NO":"KWT260054BMS","EN_PGM_NAME":"Body-Mind Stretch Training Course for Persons with Chronic Illness","TC_PGM_NAME":"長期病患者身心伸展訓練班","EN_ACT_TYPE_NAME":"Body-Mind Stretch","EN_DISTRICT":"Kwai Tsing","PGM_START_DATE":"2026-10-16","PGM_END_DATE":"2026-11-20","EN_DAY":"Fri","PGM_START_TIME":"18:00","PGM_END_TIME":"20:00","EN_VENUE":"Cheung Fat Sports Centre","EN_TARGET_GRP":"Persons with Visceral Disability / Chronic Illness","MIN_AGE":15,"MAX_AGE":99,"FEE":0.0,"QUOTA":15,"ENROL_METHOD":"FCFS","EN_NOTES":["\r\n"],"EN_URL":"SP:KWT260054BMS&&lang=en"}],"facilities":[{"Name_en":"Hong Kong Park Sports Centre","District_en":"Central & Western","Latitude":"22-16-38","Longitude":"114-9-33","Phone":"2521 5072","Address_en":""},{"Name_en":"Hong Kong Squash Centre","District_en":"Central & Western","Latitude":"22-16-39","Longitude":"114-9-36","Phone":"2521 5072","Address_en":""},{"Name_en":"Shek Tong Tsui Sports Centre","District_en":"Central & Western","Latitude":"22-17-9","Longitude":"114-8-10","Phone":"2858 0541","Address_en":""},{"Name_en":"Sheung Wan Sports Centre","District_en":"Central & Western","Latitude":"22-17-10","Longitude":"114-8-59","Phone":"2853 2574","Address_en":""},{"Name_en":"Fung Kam Street Sports Centre","District_en":"Yuen Long","Latitude":"22-26-35","Longitude":"114-1-59","Phone":"2475 2334","Address_en":""},{"Name_en":"Long Ping Sports Centre","District_en":"Yuen Long","Latitude":"22-27-1","Longitude":"114-1-23","Phone":"2475 2404","Address_en":""},{"Name_en":"Tin Shui Sports Centre","District_en":"Yuen Long","Latitude":"22-27-17","Longitude":"113-59-53","Phone":"2446 6609","Address_en":""},{"Name_en":"Tin Shui Wai Sports Centre","District_en":"Yuen Long","Latitude":"22-27-18","Longitude":"114-0-24","Phone":"2446 4778","Address_en":""},{"Name_en":"Leung Tin Sports Centre","District_en":"Tuen Mun","Latitude":"22-24-24","Longitude":"113-57-54","Phone":"2467 1594","Address_en":""},{"Name_en":"Tai Hing Sports Centre","District_en":"Tuen Mun","Latitude":"22-24-12","Longitude":"113-58-23","Phone":"2463 1260","Address_en":""},{"Name_en":"The Jockey Club Tuen Mun Butterfly Beach Sports Centre","District_en":"Tuen Mun","Latitude":"22-22-43","Longitude":"113-57-53","Phone":"2465 7610","Address_en":""},{"Name_en":"TUEN MUN SWIMMING POOL SQUASH COURTS","District_en":"Tuen Mun","Latitude":"22-23-1","Longitude":"113-58-12","Phone":"24517278","Address_en":""},{"Name_en":"Yau Oi Sports Centre","District_en":"Tuen Mun","Latitude":"22-23-8","Longitude":"113-58-18","Phone":"2450 8850","Address_en":""},{"Name_en":"Heng On Sports Centre","District_en":"Sha Tin","Latitude":"22-25-0","Longitude":"114-13-40","Phone":"2642 0203","Address_en":""},{"Name_en":"Hin Keng Sports Centre","District_en":"Sha Tin","Latitude":"22-21-47","Longitude":"114-10-17","Phone":"2605 8407","Address_en":""},{"Name_en":"Ma On Shan Sports Centre","District_en":"Sha Tin","Latitude":"22-25-34","Longitude":"114-13-46","Phone":"2631 1597","Address_en":""},{"Name_en":"Mei Lam Sports Centre","District_en":"Sha Tin","Latitude":"22-22-45","Longitude":"114-10-32","Phone":"2695 9318","Address_en":""},{"Name_en":"Boundary Street Sports Centre No. 1","District_en":"Yau Tsim Mong","Latitude":"22-19-32","Longitude":"114-10-15","Phone":"2380 9751","Address_en":""},{"Name_en":"Boundary Street Sports Centre No. 2","District_en":"Yau Tsim Mong","Latitude":"22-19-34","Longitude":"114-10-14","Phone":"2380 9751","Address_en":""},{"Name_en":"Fa Yuen Street Sports Centre","District_en":"Yau Tsim Mong","Latitude":"22-19-15","Longitude":"114-10-15","Phone":"2395 1501","Address_en":""},{"Name_en":"Kowloon Park Sports Centre","District_en":"Yau Tsim Mong","Latitude":"22-18-7","Longitude":"114-10-12","Phone":"2724 3120","Address_en":""},{"Name_en":"Tsuen King Circuit Sports Centre","District_en":"Tsuen Wan","Latitude":"22-22-40","Longitude":"114-6-40","Phone":"2405 6960","Address_en":""},{"Name_en":"Tsuen Wan West Sports Centre","District_en":"Tsuen Wan","Latitude":"22-22-14","Longitude":"114-6-2","Phone":"2412 0904","Address_en":""},{"Name_en":"Wai Tsuen Sports Centre","District_en":"Tsuen Wan","Latitude":"22-22-20","Longitude":"114-7-21","Phone":"2415 2621","Address_en":""},{"Name_en":"Cheung Sha Wan Sports Centre","District_en":"Sham Shui Po","Latitude":"22-20-17","Longitude":"114-9-12","Phone":"2741 7287","Address_en":""},{"Name_en":"Cornwall Street Squash and Table Tennis Centre","District_en":"Sham Shui Po","Latitude":"22-20-21","Longitude":"114-10-22","Phone":"2337 4392","Address_en":""},{"Name_en":"Lai Chi Kok Park Sports Centre","District_en":"Sham Shui Po","Latitude":"22-20-27","Longitude":"114-8-18","Phone":"2745 2796","Address_en":""},{"Name_en":"Pei Ho Street Sports Centre","District_en":"Sham Shui Po","Latitude":"22-19-45","Longitude":"114-9-40","Phone":"2729 1010","Address_en":""},{"Name_en":"Cheung Chau Sports Centre","District_en":"Islands","Latitude":"22-12-27","Longitude":"114-1-52","Phone":"2981 6285","Address_en":""},{"Name_en":"Mui Wo Sports Centre","District_en":"Islands","Latitude":"22-16-1","Longitude":"113-59-47","Phone":"2984 2334","Address_en":""},{"Name_en":"Peng Chau Sports Centre","District_en":"Islands","Latitude":"22-17-6","Longitude":"114-2-17","Phone":"2983 8420","Address_en":""},{"Name_en":"Praya Street Sports Centre","District_en":"Islands","Latitude":"22-12-24","Longitude":"114-1-42","Phone":"2981 5409","Address_en":""},{"Name_en":"Tung Chung Man Tung Road Sports Centre","District_en":"Islands","Latitude":"22-17-26","Longitude":"113-56-38","Phone":"2109 2421","Address_en":""},{"Name_en":"Ping Shan Tin Shui Wai Sports Centre","District_en":"Yuen Long","Latitude":"22-26-51","Longitude":"114-0-17","Phone":"2350 9455","Address_en":""},{"Name_en":"Tin Fai Road Sports Centre","District_en":"Yuen Long","Latitude":"22-27-53","Longitude":"113-59-49","Phone":"2473 0229","Address_en":""},{"Name_en":"Yuen Chau Kok Sports Centre","District_en":"Sha Tin","Latitude":"22-22-47","Longitude":"114-12-16","Phone":"2509 9108","Address_en":"35 Ngan Shing Street, Sha Tin, NT."},{"Name_en":"Sham Shui Po Sports Centre","District_en":"Sham Shui Po","Latitude":"22-33-04","Longitude":"144-14-90","Phone":"2360 2276","Address_en":""},{"Name_en":"Tai Kiu Market Squash Courts","District_en":"Yuen Long","Latitude":"22-18-44","Longitude":"114-11-26","Phone":"2475 3620","Address_en":""},{"Name_en":"Yuen Long Jockey Club Squash Courts","District_en":"Yuen Long","Latitude":"22-18-44","Longitude":"114-7-50","Phone":"2474 4913","Address_en":""},{"Name_en":"Cheung Chau Sports Ground","District_en":"Islands","Latitude":"22-12-24","Longitude":"114-1-59","Phone":"2852 4845 / 2986 8604","Address_en":""},{"Name_en":"Fat Kwong Street Sports Centre","District_en":"Kowloon City","Latitude":"22-18-53","Longitude":"114-10-52","Phone":"","Address_en":""},{"Name_en":"Fu Heng Sports Centre","District_en":"Tai Po","Latitude":"22-27-30","Longitude":"114-10-17","Phone":"","Address_en":""},{"Name_en":"Luen Wo Hui Sports Centre","District_en":"North","Latitude":"22-30-1","Longitude":"114-8-42","Phone":"","Address_en":""},{"Name_en":"Po Lam Sports Centre","District_en":"Sai Kung","Latitude":"22-19-33","Longitude":"114-15-18","Phone":"","Address_en":""},{"Name_en":"Chai Wan Sports Centre","District_en":"Eastern","Latitude":"22-15-53","Longitude":"114-14-24","Phone":"","Address_en":""},{"Name_en":"Aberdeen Sports Centre","District_en":"Southern","Latitude":"22-14-58","Longitude":"114-9-16","Phone":"","Address_en":""},{"Name_en":"Choi Hung Road Sports Centre","District_en":"Wong Tai Sin","Latitude":"22-20-14","Longitude":"114-11-47","Phone":"","Address_en":""},{"Name_en":"Cheung Fat Sports Centre","District_en":"Kwai Tsing","Latitude":"22-21-45","Longitude":"114-6-10","Phone":"","Address_en":""},{"Name_en":"Harbour Road Sports Centre","District_en":"Wan Chai","Latitude":"22-16-53","Longitude":"114-10-35","Phone":"","Address_en":""},{"Name_en":"Chun Wah Road Sports Centre","District_en":"Kwun Tong","Latitude":"22-19-21","Longitude":"114-13-14","Phone":"","Address_en":""}]}""")
FX_CUL = json.loads(r"""{"events":{"186062":{"titlee":"Roald Dahl's Charlie and the Chocolate Factory","titlec":"","cat2":"inc4sc1","predateE":"04-05, 10-12, 17-19, 24-26/11/2026 (Tue-Thu) 19:00\n06, 13, 20, 27/11/2026 (Fri) 19:30\n07, 14, 21, 28/11/2026 (Sat) 14:30, 19:30\n08, 15, 22, 29/11/2026 (Sun) 11:30, 16:30","progtimee":"Not Applicable","venueid":"50110015","pricee":"$1,288, $1,188, $1,088, $988, $888, $688, $588, $488, $388","agelimite":"","desce":"","urle":"","presenterorge":"Presented by Broadway International Group","enquiry":""},"185783":{"titlee":"Asia+ Festival 2026: \"Teh Dar\" by Lune Production","titlec":"","cat2":"inc4sc10","predateE":"14/11/2026 (Sat) 19:30, 15/11/2026 (Sun) 15:00","progtimee":"Approximately 1 hour with no interval","venueid":"76810048","pricee":"$400, 320, 260, 200","agelimite":"","desce":"Deep in the mist-shrouded highlands of Vietnam, ancient traditions pulse with vitality. Teh Dar is a celebration of moun","urle":"https://www.asiaplus.gov.hk/2026/en/teh-dar","presenterorge":"Presented by Leisure and Cultural Services Department","enquiry":"2370 1044"},"185886":{"titlee":"'Inseparable Ties: Cohesion as Told by Hong Kong Historic Buildings' Photo Exhibition","titlec":"","cat2":"inc4sc4","predateE":"19/09/2026-18/10/2026 (Sat-Sun) 09:00-22:00","progtimee":"Not Applicable","venueid":"35517495","pricee":"Free Activities","agelimite":"","desce":"","urle":"","presenterorge":"Presented by the Antiquities and Monuments Office and Art Exhibitions China","enquiry":"2694 2560"},"184454":{"titlee":"(Buddhism Class)","titlec":"","cat2":"inc4sc14","predateE":"13 Jul- 14 Dec 2026 (Every Mon) 1930-2130; Except 10 Aug, 14 Sep, 19 Oct, 26 Oct, 2 Nov 2026","progtimee":"Not Applicable","venueid":"76810200","pricee":"","agelimite":"","desce":"","urle":"https://www.lcsd.gov.hk/clpss/en/search/culture/DetailForm.do?id=184454","presenterorge":"Chi Hong Ching Yuen Limited","enquiry":"9268 7796 / 2413 3728 / 9215 1619"},"188329":{"titlee":"AUSupreme Supreme Doctor Sharing Session: Break The Vicious Cycle Of 'The More Exhausted You Are, The Harder It Is To Fall Asleep, And Sleep Deprivation During Illness'","titlec":"","cat2":"inc7sc8","predateE":"24/10/2026(Sat) 7:30pm","progtimee":"Not Applicable","venueid":"6210513","pricee":"Admission by enrollment","agelimite":"","desce":"","urle":"https://www.lcsd.gov.hk/clpss/en/search/culture/DetailForm.do?id=188329","presenterorge":"Organised by the Truth & Faith International Limited","enquiry":"8108 2733"},"188319":{"titlee":"All Hearts in New Exhibition","titlec":"","cat2":"inc7sc8","predateE":"9/10/2026(Fri)-14/10/2026(Wed) 10am-8pm; 15/10/2026(Thu) 10am-4:30pm","progtimee":"Not Applicable","venueid":"6210646","pricee":"Free admission (first-come-first-served)","agelimite":"","desce":"","urle":"https://www.lcsd.gov.hk/clpss/en/search/culture/DetailForm.do?id=188319","presenterorge":"Organised by the Chinese Ink Painting Institute Hong Kong","enquiry":"9823 9175"},"186878":{"titlee":"'The Connected Stage' Series \"Kylián and Sangba\"","titlec":"","cat2":"inc4sc3","predateE":"30-31/10/2026 (Fri - Sat) 20:00","progtimee":"Not Applicable","venueid":"826817417","pricee":"$450, $360, $280","agelimite":"Age Limit: 6 (Person below this age not admitted)","desce":"","urle":"https://www.art-mate.net/doc/94124","presenterorge":"Presented by City Contemporary Dance Company","enquiry":"2329 7803"},"188445":{"titlee":"2026-27 \"18dART — Community Arts Scheme in Eastern District\" — “Fun Drumming in the East 2.0” Drum Music Project — Cajón Workshops","titlec":"","cat2":"inc9sc2","predateE":"15/10/2026-28/01/2027","progtimee":"Each workshops lasts approximately 1 hour.","venueid":"8332","pricee":"Free Activities","agelimite":"","desce":"For programme information, please refer to the Chinese version.","urle":"https://www.cpo.gov.hk/event/en-ed-2627/","presenterorge":"Presented by Hong Kong Drum Ensemble","enquiry":"5943 1323"},"185801":{"titlee":"FWD Insurance Presents Andy Hui 《On Stage》 Live in Concert 2026","titlec":"","cat2":"inc4sc9","predateE":"08-10/10/2026 (Thu-Sat) 20:15","progtimee":"About 2 hours 30 minutes","venueid":"1117413","pricee":"$1280, $780, $580","agelimite":"6 (Person below this age not admitted)","desce":"","urle":"https://www.lcsd.gov.hk/en/hkc/whatsnew.html","presenterorge":"Presented by Duncan Repertoire of Entertainment, Artistes Management (Dream) Limited; Co-presented by Entertainment Impact, Ovation Entertainment Limited, Tiger Star Culture Group Limited, VEOSKY (HK) Cultural Development Co., Limited, Golden Deer Jr Entertainment Limited, Toptop Productions Limited, Sunny Side Up Music Limited, VMS Investment Group Limited","enquiry":"7057 9768"},"188610":{"titlee":"Cultural Playground@Yuen Long Exhibition","titlec":"","cat2":"inc9sc3","predateE":"01-02/10/2026 (Thu-Fri)\n04-08/10/2026 (Sun-Thu)","progtimee":"Not Applicable","venueid":"87315528","pricee":"Free Activities","agelimite":"","desce":"","urle":"","presenterorge":"Presented by The Absolutely Fabulous Theatre Connection (AFTEC)","enquiry":""}},"dates":{"186062":["20261104","20261105","20261106","20261107","20261108","20261110","20261111","20261112","20261113","20261114","20261115","20261117","20261118","20261119","20261120","20261121","20261122","20261124","20261125","20261126","20261127","20261128","20261129"],"185783":["20261114","20261115"],"185886":["20260919","20260920","20260921","20260922","20260923","20260924","20260925","20260926","20260927","20260928","20260929","20260930","20261001","20261002","20261003","20261004","20261005","20261006","20261007","20261008","20261009","20261010","20261011","20261012","20261013","20261014","20261015","20261016","20261017","20261018"],"184454":["20261005","20261006","20261007","20261008","20261009","20261010","20261011","20261012"],"188329":["20261024"],"188319":["20261009","20261010","20261011","20261012","20261013","20261014","20261015"],"186878":["20261030","20261031"],"188445":["20261015","20261016","20261017","20261018","20261019","20261020","20261021","20261022","20261023","20261024","20261025","20261026","20261027","20261028","20261029","20261030","20261031","20261101","20261102","20261103","20261104","20261105","20261106","20261107","20261108","20261109","20261110","20261111","20261112","20261113","20261114","20261115","20261116","20261117","20261118","20261119","20261120","20261121","20261122","20261123","20261124","20261125","20261126","20261127","20261128","20261129","20261130","20261201","20261202","20261203","20261204","20261205","20261206","20261207","20261208","20261209","20261210","20261211","20261212","20261213","20261214","20261215","20261216","20261217","20261218","20261219","20261220","20261221","20261222","20261223","20261224","20261225","20261226","20261227","20261228","20261229","20261230","20261231","20270101","20270102","20270103","20270104","20270105","20270106","20270107","20270108","20270109","20270110","20270111","20270112","20270113","20270114","20270115","20270116","20270117","20270118","20270119","20270120","20270121","20270122","20270123","20270124","20270125","20270126","20270127","20270128"],"185801":["20261008","20261009","20261010"],"188610":["20261001","20261002","20261004","20261005","20261006","20261007","20261008"]},"venues":{"50110015":{"venuee":"Hong Kong Cultural Centre (Grand Theatre)","latitude":"22.29386","longitude":"114.17053"},"76810048":{"venuee":"Tuen Mun Town Hall (Auditorium)","latitude":"22.391810","longitude":"113.976771"},"35517495":{"venuee":"Tai Po Civic Centre (Foyer Exhibition Area)","latitude":"22.45175","longitude":"114.16815"},"76810200":{"venuee":"Tuen Mun Town Hall (Lecture Room (2))","latitude":"22.391810","longitude":"113.976771"},"6210513":{"venuee":"Hong Kong Central Library (G/F Lecture Theatre)","latitude":"","longitude":""},"6210646":{"venuee":"Hong Kong Central Library (G/F Exhibition Gallery)","latitude":"","longitude":""},"826817417":{"venuee":"East Kowloon Cultural Centre (The Hall)","latitude":"22.32427","longitude":"114.21494"},"8332":{"venuee":"Venues in Eastern district","latitude":"","longitude":""},"1117413":{"venuee":"Hong Kong Coliseum (Arena)","latitude":"","longitude":""},"87315528":{"venuee":"Yuen Long Theatre (Exhibition Corner)","latitude":"22.44152","longitude":"114.02289"}}}""")
SP = FX_SP["smartplay"]
FAC = FX_SP["facilities"]
for r in SP:
    if r.get("EN_URL"):
        r["EN_URL"] = r["EN_URL"].replace("SP:", "https://www.smartplay.lcsd.gov.hk/programme/detail?activityNo=")
BY = {r["ACTIVITY_NO"]: r for r in SP}

# Photon's own answers, 2026-10-05 (Kowloon Park's is Kowloon BAY Park's point).
GEO = {
    "Tsuen King Circuit Wu Chung Swimming Pool": (22.377947, 114.104233),
    "Tung Chau Street Park": (22.32637, 114.159403),
    "Kowloon Park": (22.32694, 114.20714),
    "Hong Kong Park": (22.23477, 114.17082),
    "Sham Shui Po Sports Centre": (22.3306, 114.1594),      # SYNTHETIC answer
    "Hong Kong Coliseum": (22.301318, 114.181981),
    "Hong Kong Central Library": (22.279927, 114.189617),
    "Tai Po Swimming Pool": (22.4497, 114.1690),            # SYNTHETIC answer
}
asked = []


def geocode(q):
    asked.append(q)
    return GEO.get(q, (None, None))


def synth(base, **over):
    """A fixture row with fields replaced - SYNTHETIC, said where used."""
    r = dict(BY[base])
    r.update(over)
    return r


def places(cfg=CFG, geo=geocode):
    return T.Places(FAC, cfg, geo)


def sp_events(rows, cfg=CFG):
    stats = {}
    return T.smartplay_events(rows, SP_SRC, cfg, places(cfg), TZ, TODAY, stats), stats


def to_xml(tag_root, tag, recs, many=None):
    out = [f"<?xml version='1.0' encoding='UTF-8'?>\n<{tag_root}>"]
    for i, rec in recs.items():
        out.append(f'<{tag} id="{i}">')
        if many:
            out += [f"<{many}><![CDATA[{v}]]></{many}>" for v in rec]
        else:
            out += [f"<{k}><![CDATA[{v}]]></{k}>" if v else f"<{k} />" for k, v in rec.items()]
        out.append(f"</{tag}>")
    out.append(f"</{tag_root}>")
    return "\n".join(out).encode("utf-8")


def cul_events(events=None, today=TODAY):
    ev = T.xml_records(to_xml("events", "event", events or FX_CUL["events"]), "event")
    dt = T.xml_records(to_xml("event_dates", "event", FX_CUL["dates"], many="indate"), "event")
    vn = T.xml_records(to_xml("venues", "venue", FX_CUL["venues"]), "venue")
    stats = {}
    return T.culture_events(ev, dt, vn, CUL_SRC, CFG, places(), TZ, today, stats), stats


def at(evs, sid_prefix):
    return [e for e in evs if e.source_id.startswith(sid_prefix)]


# ------------------------------------------------------------- 1. the config
print("the config")
check("the attribution fits the sync's kept tail (<= 200) and names LCSD and DATA.GOV.HK",
      0 < len(CFG["attribution"]) <= 200 and "Leisure and Cultural Services" in CFG["attribution"]
      and "DATA.GOV.HK" in CFG["attribution"])
keys = ([k for _p, k in SP_SRC["category_by_title"]] + list(SP_SRC["category_by_type"].values())
        + [SP_SRC["category_default"]] + [k for _p, k in CUL_SRC["category_by_title"]]
        + list(CUL_SRC["category_by_cat2"].values()) + [CUL_SRC["category_default"]])
check("every configured category is a real lens key", all(k in VALID_CATEGORIES for k in keys),
      [k for k in keys if k not in VALID_CATEGORIES])
check("the source prefixes are lcsd-smartplay and lcsd-culture, and the cultural programme is read first",
      SP_SRC["source"] == "lcsd-smartplay" and CUL_SRC["source"] == "lcsd-culture"
      and [s["kind"] for s in CFG["sources"]] == ["culture", "smartplay"])
# A walk-in session is a timetable row, not a show: without this the sync gives
# every one a violet big-venue pin and a "More on this show" web search, and the
# party-door guard for civic timetables does not apply. The cultural programme
# is an agenda of shows and stays out.
check("the sync treats lcsd-smartplay as a civic timetable (mapsee_supabase_sync.CIVIC_TIMETABLE_SOURCES), "
      "and lcsd-culture as shows",
      S._from_civic_timetable({"source": "lcsd-smartplay"}) and not S._from_civic_timetable({"source": "lcsd-culture"}),
      S.CIVIC_TIMETABLE_SOURCES)

# ------------------------------------------------------------- 2. readers
print("small readers")
check("DMS '22-18-53' is 22.314722", abs(T.dms("22-18-53") - 22.314722) < 1e-6, T.dms("22-18-53"))
check("sixty seconds is a whole minute: '114-9-60' == '114-10-0'", T.dms("114-9-60") == T.dms("114-10-0"))
check("'144-14-90' (Sham Shui Po Sports Centre's) is not a reading", T.dms("144-14-90") is None)
d, x, why = T.parse_days("Mon,Wed (Exclude 19 Oct 2026)", date(2026, 10, 5), date(2026, 10, 28))
check("EN_DAY: weekdays and the excluded date", d == {0, 2} and x == {date(2026, 10, 19)} and why is None, (d, x, why))
d, x, why = T.parse_days("Mon,Tue,Thu (Exclude 12 Oct 2026,19 Oct 2026,26 Oct 2026)", date(2026, 10, 5), date(2026, 10, 29))
check("EN_DAY: three excluded dates", x == {date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26)}, x)
check("EN_DAY: a range wraps the week", T.parse_days("Sat-Mon", date(2026, 10, 1), date(2026, 10, 30))[0] == {5, 6, 0})
check("EN_DAY: an unread clause is refused, not guessed",
      T.parse_days("Mon (Starting 5 Oct)", date(2026, 10, 1), date(2026, 10, 30))[0] is None
      and T.parse_days("Mon (Exclude public holidays)", date(2026, 10, 1), date(2026, 10, 30))[0] is None)


def times(text, day):
    g, ex, ev = T.parse_predate(text, 2026)
    return T.times_for(day, g, ev)


teh = FX_CUL["events"]["185783"]["predateE"]
check("'14/11/2026 (Sat) 19:30, 15/11/2026 (Sun) 15:00': the 14th at 19:30, the 15th at 15:00",
      times(teh, date(2026, 11, 14)) == [(1170, None)] and times(teh, date(2026, 11, 15)) == [(900, None)],
      (times(teh, date(2026, 11, 14)), times(teh, date(2026, 11, 15))))
check("... and '30, 15/11' is not a date list: nothing on the 30th", times(teh, date(2026, 11, 30)) == [],
      times(teh, date(2026, 11, 30)))
ch = FX_CUL["events"]["186062"]["predateE"]
check("a comma list of days and ranges ('04-05, 10-12, 17-19, 24-26/11/2026') binds its own clock",
      times(ch, date(2026, 11, 11)) == [(1140, None)] and times(ch, date(2026, 11, 7)) == [(870, None), (1170, None)],
      (times(ch, date(2026, 11, 11)), times(ch, date(2026, 11, 7))))
ah = FX_CUL["events"]["188319"]["predateE"]
check("'9/10/2026(Fri)-14/10/2026(Wed) 10am-8pm; 15/10/2026(Thu) 10am-4:30pm'",
      times(ah, date(2026, 10, 9)) == [(600, 1200)] and times(ah, date(2026, 10, 15)) == [(600, 990)],
      (times(ah, date(2026, 10, 9)), times(ah, date(2026, 10, 15))))
lan = "17/09-07/10/2026 (Thu-Wed) / Lighting-up Time: 6:30pm - 11pm (extended to midnight on 24-27.9)"
check("'6:30pm - 11pm' is a span and '24-27.9' is not a clock", times(lan, date(2026, 10, 1)) == [(1110, 1380)],
      times(lan, date(2026, 10, 1)))
check("'1930-2130' is a span", times("01/10/2026 (Thu) 1930-2130", date(2026, 10, 1)) == [(1170, 1290)])
check("durations: '2 hours with a 15 min intermission' is 120, '1hr30mins' 90, 'Not Applicable' none",
      T.duration_minutes("Approximately 2 hours with a 15 min intermission") == 120
      and T.duration_minutes("1hr30mins") == 90 and T.duration_minutes("Not Applicable") is None)

# ------------------------------------------------------------- 3. places
print("places")
P = places()
check("a point that contradicts its own district is refused: Sham Shui Po (144-14-90), "
      "Tai Kiu Market and Yuen Long JC Squash Courts (in Kowloon)",
      {"sham shui po sports centre", "tai kiu market squash courts", "yuen long jockey club squash courts"}
      <= set(P.refused), P.refused)
check("... and nothing else is: Islands' Tung Chung, Cheung Chau and Peng Chau stand",
      len(P.refused) == 3, P.refused)
P8 = T.Places(FAC, dict(CFG, district_radius_km={}), None)
check("(Islands' own radius is what keeps them: at 8 km Tung Chung is refused)",
      "tung chung man tung road sports centre" in P8.refused, P8.refused)
check("a geocoded point far from its district's facilities is refused (Photon's Ocean Park for Hong Kong Park)",
      P.far_from_district((22.23477, 114.17082), "Central & Western") is True
      and P.far_from_district((22.27742, 114.16151), "Central & Western") is False)
asked.clear()
st = {}
kp = P.place("Kowloon Park (Sculpture Walk)", "Yau Tsim Mong", st)
kpsc = P.book["kowloon park sports centre"]["point"]
check("Kowloon Park is pinned at the sports centre inside it, never at Photon's Kowloon Bay Park",
      kp and (kp["lat"], kp["lon"]) == kpsc and kp["exact"] is False and not asked, (kp, asked))
st = {}
ssp = P.place("Sham Shui Po Sports Centre", "Sham Shui Po", st)
check("a refused facility point falls back to the geocoder, and is not coords_exact",
      ssp and ssp["exact"] is False and (ssp["lat"], ssp["lon"]) == GEO["Sham Shui Po Sports Centre"], ssp)
yck = P.place("Yuen Chau Kok Sports Centre", "Sha Tin", {})
check("a listed facility's point is LCSD's and coords_exact, its address free of HTML",
      yck["exact"] is True and "<" not in (yck["address"] or "") and yck["address"], yck)

# ------------------------------------------------------------- 4. SmartPlay
print("SmartPlay")
evs, st = sp_events(SP)
ballot = [r["ACTIVITY_NO"] for r in SP if r["ENROL_METHOD"] != "WALKIN"]
check("BALLOT and FCFS programmes are courses: no row, each counted by method",
      not any(e.source_id.split("#")[0] in ballot for e in evs)
      and st.get("refused: enrolment by BALLOT (a course you apply for)") == 1
      and st.get("refused: enrolment by FCFS (a course you apply for)") == 1, st)
yk = sorted(e.start_local[:10] for e in at(evs, "ST260015HES#"))
check("Mon,Wed from 10-05 to 10-28, and never the 19th its EN_DAY excludes",
      yk == ["2026-10-05", "2026-10-07", "2026-10-12", "2026-10-14", "2026-10-21", "2026-10-26", "2026-10-28"], yk)
th = [e for e in evs if e.venue_name == "Tai Hing Sports Centre"]
check("two back-to-back hours (07-08, 08-09) are one row a day, 07:00-09:00, naming both",
      len(th) == 4 and all(e.start_local[11:16] == "07:00" and e.end_local[11:16] == "09:00"
                           and "Sessions: 07:00-08:00, 08:00-09:00" in e.description for e in th)
      and all(e.source_id.startswith("TM260434HES#") for e in th), [(e.source_id, e.start_local, e.end_local) for e in th])
hy = sorted((e.start_local[11:16], e.end_local[11:16]) for e in evs if e.name == "Hydro Fitness Fun Day")
check("a pool's 11:00, 17:00, 20:00 and 21:00 hours: three rows, none spanning a gap",
      hy == [("11:00", "12:00"), ("17:00", "18:00"), ("20:00", "22:00")], hy)
tc = [e for e in evs if e.venue_name == "Tung Chau Street Park"]
check("the same session under two ACTIVITY_NOs is one row per date",
      len(tc) == len({e.start_local for e in tc}) == 4 and st.get("repeat sessions folded") == 4,
      ([(e.source_id, e.start_local) for e in tc], st.get("repeat sessions folded")))
kf = [e for e in evs if e.name == "Kung Fu Corner"]
check("Kung Fu Corner in Kowloon Park sits on the park's sports centre",
      kf and all((e.latitude, e.longitude) == kpsc for e in kf), [(e.latitude, e.longitude) for e in kf])
check("every walk-in row says 'Admission: free.' first (FEE 0 on the source's own field)",
      all(e.description.startswith("Admission: free. Walk-in session") for e in evs))
check("every row is tagged free by 0227 after the sync's cap, the attribution kept",
      all(stored(e)[0] and CFG["attribution"] in stored(e)[1]["description"] for e in evs))
# SYNTHETIC: LCSD's stock note for its ball-games courses (its real text; on 76
# walk-in programmes 2026-10-03) on a fixture row.
STOCK = ("Participants attending ball games training courses (except the mini-tennis course) should bring along "
         "their own rackets. Participants should wear proper sportswear and clean non-marking rubber-outsole sports shoes.")
sn, ss = sp_events([synth("ST260015HES", EN_NOTES=[STOCK, "Wear non-marking sports shoes"])])
check("LCSD's stock note for its COURSES is not on a walk-in row; the row's own note is",
      sn and not any("training course" in e.description for e in sn) and "Wear non-marking sports shoes." in sn[0].description
      and ss.get("notes dropped: LCSD's stock note for its courses"), ss)
check("every row is in Hong Kong's time zone: 07:00 local is 23:00Z the day before",
      th[0].start_utc.endswith("T23:00:00Z") and th[0].timezone == "Asia/Hong_Kong", th[0].start_utc)

# SYNTHETIC: the real Tai Po Water Safety Fun Day (2026-10-03) moved into the window.
pool = synth("TP261044CAR", PGM_START_DATE="2026-10-10", PGM_END_DATE="2026-10-10", EN_DAY="Sat")
fee = synth("ST260015HES", ACTIVITY_NO="SYN-FEE", FEE=20.0, EN_PGM_NAME="Synthetic Paid Walk-in")
e2, s2 = sp_events([pool, fee])
pe = [e for e in e2 if e.name.startswith("Tai Po District Water Safety")]
fe = [e for e in e2 if e.name == "Synthetic Paid Walk-in"]
check("a pool's admission fee in EN_NOTES makes the row '(not free)', and 0227 does not tag it",
      pe and "(not free)" in pe[0].description.split("\n\n")[0] and not tagged_free(pe[0])
      and not stored(pe[0])[0], pe and pe[0].description)
check("a FEE above 0 says its amount and '(not free)' in the first paragraph",
      fe and fe[0].description.startswith("Fee: HK$20 per session (not free).") and not any(stored(e)[0] for e in fe),
      fe and fe[0].description[:80])
longnote = synth("ST260015HES", ACTIVITY_NO="SYN-LONG", FEE=20.0, EN_NOTES=["Bring water. " * 120])
e3, s3 = sp_events([longnote])
check("a long note is cut to fit the sync's 800 characters; '(not free)' survives to_row",
      all(len(e.description) <= 800 and "(not free)" in stored(e)[1]["description"] and not stored(e)[0] for e in e3)
      and s3.get("source text shortened to fit the sync's cap"), s3)
bad = [synth("ST260015HES", ACTIVITY_NO="SYN-DAY", EN_DAY="Mon (Starting 5 Oct)"),
       synth("ST260015HES", ACTIVITY_NO="SYN-ONE", PGM_START_DATE="2026-10-06", PGM_END_DATE="2026-10-06",
             EN_DAY="Mon")]
e4, s4 = sp_events(bad)
check("an unread EN_DAY and a one-day programme whose weekday contradicts its date are refused, and counted",
      not e4 and s4.get("refused: a one-day programme whose weekday contradicts its date") == 1
      and any(k.startswith("refused: EN_DAY has an unread clause") for k in s4), s4)

ids = sorted((e.source_id, e.fingerprint, e.start_local, e.end_local) for e in evs)
shuffled = list(SP)
random.Random(7).shuffle(shuffled)
again = sorted((e.source_id, e.fingerprint, e.start_local, e.end_local) for e in sp_events(shuffled)[0])
check("identity is ACTIVITY_NO#date: a shuffled re-read gives the same rows", ids == again)
check("no two rows share a fingerprint", len({e.fingerprint for e in evs}) == len(evs))
with tempfile.TemporaryDirectory() as td:
    st1 = EventStore(os.path.join(td, "s.json"))
    for e in evs:
        st1.upsert(e)
    st1.save()
    st2 = EventStore(os.path.join(td, "s.json"))
    for e in sp_events(shuffled)[0]:
        st2.upsert(e)
    check("a second upsert of the same read adds 0 rows", st2.stats.get("added", 0) == 0
          and len(st2.records) == len(evs), st2.stats)
cats = {e.name: S.derive_categories(dict(e.__dict__))[0] for e in evs}
check("categories after derive_categories: gateball is sports, Kung Fu and hydro fitness are fitness",
      cats["Healthy Elderly Scheme-Indoor Gateball"] == "sports" and cats["Kung Fu Corner"] == "fitness"
      and cats["Hydro Fitness Fun Day"] == "fitness", cats)
dance = synth("ST260015HES", ACTIVITY_NO="SYN-DANCE", EN_PGM_NAME="Healthy Elderly Scheme-Social Dance")
de = sp_events([dance])[0]
check("a seniors' social dance is fitness, never the party door",
      de and all(S.derive_categories(dict(e.__dict__))[0] == "fitness" for e in de),
      de and S.derive_categories(dict(de[0].__dict__)))
sp_row = stored(evs[0])[1]
check("to_row writes no '🔎 More on this show' search and no violet big-venue pin on a walk-in session",
      "🔎" not in (sp_row.get("description") or "") and sp_row.get("color_hex") != "#7c3aed",
      (sp_row.get("color_hex"), (sp_row.get("description") or "")[-160:]))
nd = sp_events([synth("ST260015HES", ACTIVITY_NO="SYN-DIST", EN_DISTRICT="Yuen Chau Kok Sports Centre")])[0]
check("SYNTHETIC: an EN_DISTRICT that is the venue again is not written as '<venue> District'",
      nd and all("Sports Centre District" not in e.description and "At Yuen Chau Kok Sports Centre." in e.description
                 for e in nd), nd and nd[0].description[:300])
ng, ngs = sp_events([synth("SSP260544HES", ACTIVITY_NO="SYN-NODIST", EN_DISTRICT="Tung Chau Street Park")])
check("SYNTHETIC: a venue to geocode whose district has no facility to check the answer against is not geocoded",
      not ng and ngs.get("venues not geocoded: their district has no facility point to check an answer against") == 1
      and ngs.get("unplaceable sessions", 0) > 0, ngs)
bare, bst = T.Places([], CFG, geocode), {}
check("with no facility lists, Photon's Kowloon BAY Park and Ocean Park are not taken for Kowloon Park and Hong Kong Park",
      bare.place("Kowloon Park (Sculpture Walk)", "Yau Tsim Mong", bst, checked=True) is None
      and bare.place("Hong Kong Park (Tai Chi Garden)", "Central & Western", bst, checked=True) is None, bst)

# ------------------------------------------------------------- 5. culture
print("cultural programmes")
cv, cs = cul_events()
check("a weekly class season (eventDates lists every day of it) is refused as a course",
      not at(cv, "184454#") and cs.get("refused: a weekly series ('Every <day>'): a hirer's class season") == 1, cs)
cls = {"999002": dict(FX_CUL["events"]["184454"], titlee="Guzheng Classes", predateE="10/10/2026 (Sat) 14:00")}
FX_CUL["dates"]["999002"] = ["20261010"]
clv, cls_st = cul_events(cls)
check("SYNTHETIC: a hirer's class on a single date is still a course, by its title",
      not clv and cls_st.get("refused: a class or course (its title)") == 1, cls_st)
check("'Admission by enrollment' is refused", not at(cv, "188329#")
      and cs.get("refused: admission by enrolment (its price line)") == 1, cs)
check("'Venues in Eastern district' is a district, not a place: unplaced and counted", not at(cv, "188445#")
      and cs.get("unplaceable: the venue is a whole district ('Venues in ... district')") == 1, cs)
chr_ = at(cv, "186062#")
nov7 = sorted(e.start_local[11:16] for e in chr_ if e.start_local.startswith("2026-11-07"))
check("Charlie: one row per performance, two on a two-show day",
      len(chr_) == 31 and nov7 == ["14:30", "19:30"] and all(e.source_id.count("#") == 2 for e in chr_),
      (len(chr_), nov7))
tehs = sorted((e.start_local, e.end_local) for e in at(cv, "185783#"))
check("Teh Dar: the 14th at 19:30 and the 15th at 15:00, each ending at its stated hour",
      tehs == [("2026-11-14T19:30:00", "2026-11-14T20:30:00"), ("2026-11-15T15:00:00", "2026-11-15T16:00:00")], tehs)
ins = at(cv, "185886#")
check("an exhibition's run is ONE date-only row, from its first date (in the past) to its last",
      len(ins) == 1 and ins[0].start_local == "2026-09-19" and ins[0].end_local == "2026-10-18"
      and ins[0].start_utc is None and ins[0].source_id == "185886#2026-09-19..2026-10-18",
      [(e.source_id, e.start_local, e.end_local) for e in ins])
later = at(cul_events(today=date(2026, 10, 12))[0], "185886#")
check("... and its identity does not move as the days pass",
      [(e.source_id, e.fingerprint) for e in later] == [(e.source_id, e.fingerprint) for e in ins])
ahs = at(cv, "188319#")
check("hours that differ by day are one run, its hours in the text",
      len(ahs) == 1 and ahs[0].start_local == "2026-10-09" and ahs[0].end_local == "2026-10-15"
      and "10:00-16:30" in ahs[0].description and "10:00-20:00" in ahs[0].description,
      [(e.start_local, e.end_local, e.description[:200]) for e in ahs])
kyl = sorted(e.start_local for e in at(cv, "186878#"))
check("two nights of a show at 20:00 are two rows, never a run",
      kyl == ["2026-10-30T20:00:00", "2026-10-31T20:00:00"], kyl)
two = {"999001": dict(FX_CUL["events"]["186878"], predateE="30-31/10/2026 (Fri - Sat) 19:30-21:30")}
FX_CUL["dates"]["999001"] = FX_CUL["dates"]["186878"]
tv, _ = cul_events(two)
check("SYNTHETIC: a show with an END each night (19:30-21:30) is still two rows, not opening hours",
      sorted((e.start_local, e.end_local) for e in tv) == [("2026-10-30T19:30:00", "2026-10-30T21:30:00"),
                                                         ("2026-10-31T19:30:00", "2026-10-31T21:30:00")],
      [(e.start_local, e.end_local) for e in tv])
cp = at(cv, "188610#")
check("an exhibition that gives dates and no hours is a date-only run that says so",
      len(cp) == 1 and cp[0].start_local == "2026-10-04" and "Opening hours are not in the data." in cp[0].description,
      [(e.start_local, e.end_local) for e in cp])
andy = at(cv, "185801#")
check("a venue with no point (the Coliseum) is geocoded, inside Hong Kong, not coords_exact",
      andy and all(e.coords_exact is False and (e.latitude, e.longitude) == GEO["Hong Kong Coliseum"] for e in andy),
      [(e.latitude, e.coords_exact) for e in andy])
check("a ticket price says '(not free)' first, and 0227 does not tag it after to_row",
      andy and all(e.description.startswith("Tickets (HK$): $1280, $780, $580 (not free).") and not stored(e)[0]
                   for e in andy), andy and andy[0].description[:90])
fr = [e for e in cv if e.description.startswith("Admission: free")]
check("'Free Activities' and 'Free admission (first-come-first-served)' are free, and 0227 agrees after to_row",
      fr and all(stored(e)[0] for e in fr) and any("first come, first served" in e.description for e in fr),
      [e.description[:60] for e in fr])
check("no culture row is tagged free without saying so first",
      all(stored(e)[0] == e.description.startswith("Admission: free") for e in cv))
check("every culture row fits 800 characters and keeps the attribution after to_row",
      all(len(e.description) <= 800 and CFG["attribution"] in stored(e)[1]["description"] for e in cv))
GEOCODED = ("Hong Kong Coliseum", "Hong Kong Central Library")
check("venues.xml's points are LCSD's: coords_exact; a geocoded venue is not",
      all(e.coords_exact is (e.venue_name not in GEOCODED) for e in cv),
      [(e.venue_name, e.coords_exact) for e in cv if e.coords_exact is (e.venue_name in GEOCODED)])
check("a row's category is its cat2's (inc4sc1 Charlie and inc4sc9 Andy Hui music, inc4sc4 an exhibition arts)",
      chr_[0].category == "music" and andy[0].category == "music" and ins[0].category == "arts"
      and S.derive_categories(dict(andy[0].__dict__))[0] == "music"
      and S.derive_categories(dict(ins[0].__dict__))[0] == "arts",
      (chr_[0].category, andy[0].category, ins[0].category, S.derive_categories(dict(ins[0].__dict__))))
check("holiday.xml: an empty <holiday /> has no entries, one child is one",
      T.holiday_entries(b"<?xml version='1.0'?><holiday />") == 0
      and T.holiday_entries(b"<holiday><venue id='1'/></holiday>") == 1)


# Live 2026-10-05 (events.xml / eventDates.xml / venues.xml), descriptions cut.
# Each was written wrong by an earlier version of the adapter: eventDates lists
# every day from a lecture series' first Wednesday to its last (50 days for 8
# lectures); a list across two months and "8/11/ 2026" were not read, so every
# Border Town night got both 15:00 and 20:00; dotted dates were not read, so an
# installation's two exception days lent their hours to every day.
print("cultural programmes: what the text names, and nothing more")


def _span_days(a, b):
    a, b = date.fromisoformat(a), date.fromisoformat(b)
    return [(a + T.timedelta(days=i)).strftime("%Y%m%d") for i in range((b - a).days + 1)]


_EV2 = dict(cat2="inc4sc1", progtimee="Not Applicable", agelimite="", desce="", urle="", enquiry="", titlec="")
FX_CUL2 = {
    "events": {
        "187979": dict(_EV2, titlee='Classical Music Lecture Series: "Music at Heart"', venueid="73810020", pricee="$80",
                       predateE="07/10/2026 19:30\n14/10/2026 19:30\n21/10/2026 19:30\n28/10/2026 19:30\n04/11/2026 19:30"
                                "\n11/11/2026 19:30\n18/11/2026 19:30\n25/11/2026 19:30",
                       progtimee="Each lecture will run for about 1 hour and 30 minutes.",
                       presenterorge="Presented by Leisure and Cultural Services Department"),
        "187116": dict(_EV2, titlee='Kwai Tsing Theatre Venue Partnership Scheme: "Border Town the Musical"', cat2="inc4sc6",
                       venueid="87110023", pricee="$580, $480, $380, $280",
                       predateE="24, 29-31/10, 5-7/11/2026 (Thu-Sat) 20:00\n25/10, 1, 8/11/ 2026 (Sun) 15:00",
                       presenterorge="Presented by Chung Ying Theatre Company"),
        "185799": dict(_EV2, titlee="Asia+ Festival 2026: Sonic Odyssey of Curious Creatures", cat2="inc4sc10",
                       venueid="50117386", pricee="Free Activities",
                       predateE="10.9-14.10.2026* (Thu-Wed) \n*Opening hours of the following days:\n22.9.2026 (Tue) "
                                "19:00-23:00\n14.10.2026 (Wed) 09:00-15:00", presenterorge=""),
        "186936": dict(_EV2, titlee="(Calligraphy Exhibition)", cat2="inc4sc4", venueid="87510494", pricee="Free Activities",
                       predateE="16/10/2026 (Fri) 16:00-18:30\n17-18/10/2026 (Sat-Sun) 10:00-19:00\n19/10/2026 (Mon) 10:00-18:00",
                       presenterorge="Presented by Bean Zhai"),
        "188241": dict(_EV2, titlee="Sai Wan Ho Civic Centre Venue Partnership Scheme: Drip Music Series Masterclass: "
                                    "Espen Eriksen Trio", venueid="87710034", pricee="Free Activities",
                       predateE="07/10/2026 (Wed) 16:00", progtimee="Approx. 1 hour without intermission",
                       desce="An exclusive masterclass with the Espen Eriksen Trio before their concert in Hong Kong! "
                             "Free admission by registration: https://www.art-mate.net/doc/99829",
                       presenterorge="Presented by Drip Music"),
        "188367": dict(_EV2, titlee="Cantonese Opera Excerpts", cat2="inc4sc2", venueid="87610118", pricee="$50, $20, $10",
                       predateE="01/11/2026 (Sun) 14:15", progtimee="Approximately 3 hrs 15 mins",
                       presenterorge="Presented by Yummy Chinese Opera Troupe"),
        "188392": dict(_EV2, titlee="Cantonese Opera Excerpts", cat2="inc4sc2", venueid="87616551", pricee="Free Activities",
                       predateE="01/11/2026 (Sun) 14:15", progtimee="Approximately 3 hrs 15 mins",
                       presenterorge="Presented by 囍尚喜粵劇團"),
        "188313": dict(_EV2, titlee='Musicus Fest 2026 — Next Generation Virtuosi: "Unwind with Chamber Capriccio"',
                       venueid="87510010", pricee="$250, $160", predateE="01/11/2026 (Sun) 20:00",
                       presenterorge="Presented by Musicus Society"),
        "188338": dict(_EV2, titlee='Musicus Fest 2026 – Next Generation Virtuosi: "Unwind with Chamber Capriccio"',
                       cat2="inc4sc10", venueid="87510010", pricee="$250, $160", predateE="01/11/2026 (Sun) 20:00",
                       presenterorge="Presented by Musicus Society"),
    },
    "dates": {
        "187979": _span_days("2026-10-07", "2026-11-25"),
        # LCSD's own list leaves out 1/11, which the text names: not written.
        "187116": ["20261024", "20261025", "20261029", "20261030", "20261031", "20261105", "20261106", "20261107",
                   "20261108"],
        "185799": _span_days("2026-09-10", "2026-10-14"),
        "186936": _span_days("2026-10-16", "2026-10-19"),
        "188241": ["20261007"], "188367": ["20261101"], "188392": ["20261101"],
        "188313": ["20261101"], "188338": ["20261101"],
    },
    "venues": {
        "73810020": {"venuee": "Hong Kong Space Museum (Lecture Hall)", "latitude": "22.29441", "longitude": "114.17192"},
        "87110023": {"venuee": "Kwai Tsing Theatre (Auditorium)", "latitude": "22.35665", "longitude": "114.12623"},
        "50117386": {"venuee": "Hong Kong Cultural Centre (Foyer Exhibition Areas)", "latitude": "22.29386",
                     "longitude": "114.17053"},
        "87510494": {"venuee": "Hong Kong City Hall (Exhibition Gallery)", "latitude": "22.282279", "longitude": "114.161545"},
        "87710034": {"venuee": "Sai Wan Ho Civic Centre (Theatre)", "latitude": "22.2818", "longitude": "114.222501"},
        "87610118": {"venuee": "Ko Shan Theatre (Theatre)", "latitude": "22.31368", "longitude": "114.18556"},
        "87616551": {"venuee": "Ko Shan Theatre (New Wing Auditorium)", "latitude": "22.31368", "longitude": "114.18556"},
        "87510010": {"venuee": "Hong Kong City Hall (Theatre)", "latitude": "22.282279", "longitude": "114.161545"},
    },
}


def cul_from(fx, today=TODAY):
    ev = T.xml_records(to_xml("events", "event", fx["events"]), "event")
    dt = T.xml_records(to_xml("event_dates", "event", fx["dates"], many="indate"), "event")
    vn = T.xml_records(to_xml("venues", "venue", fx["venues"]), "venue")
    stats = {}
    return T.culture_events(ev, dt, vn, CUL_SRC, CFG, places(), TZ, today, stats), stats


c2, c2s = cul_from(FX_CUL2)
lec = at(c2, "187979#")
check("eventDates' 50 days for 8 Wednesday lectures: 8 rows at 19:30, none on a day the text does not name",
      len(lec) == 8 and all(date.fromisoformat(e.start_local[:10]).weekday() == 2 and e.start_local[11:16] == "19:30"
                            and e.end_local[11:16] == "21:00" for e in lec)
      and c2s.get("dates not written: in eventDates.xml, not in the event's own text") == 42,
      ([e.start_local for e in lec][:10], c2s))
bt = sorted(e.start_local for e in at(c2, "187116#"))
check("'24, 29-31/10, 5-7/11/2026 ... 20:00 / 25/10, 1, 8/11/ 2026 (Sun) 15:00': 9 shows, one clock each",
      bt == ["2026-10-24T20:00:00", "2026-10-25T15:00:00", "2026-10-29T20:00:00", "2026-10-30T20:00:00",
             "2026-10-31T20:00:00", "2026-11-05T20:00:00", "2026-11-06T20:00:00", "2026-11-07T20:00:00",
             "2026-11-08T15:00:00"], bt)
so = at(c2, "185799#")
check("dotted dates: '10.9-14.10.2026* ... 22.9.2026 19:00-23:00 / 14.10.2026 09:00-15:00' is ONE date-only run",
      len(so) == 1 and (so[0].start_local, so[0].end_local) == ("2026-09-10", "2026-10-14") and so[0].start_utc is None
      and "Open " not in so[0].description, [(e.start_local, e.end_local) for e in so])
g, _x, _e = T.parse_predate("10.9-14.10.2026* (Thu-Wed) \n*Opening hours of the following days:\n22.9.2026 (Tue) 19:00-23:00",
                            2026)
check("... and the exception day's hours are not lent across the prose to the whole run",
      T.times_for(date(2026, 10, 5), g, _e) == [] and T.times_for(date(2026, 9, 22), g, _e) == [(1140, 1380)], g)
g, _x, _e = T.parse_predate("14/11/2026 (Sat), 15/11/2026 (Sun) 19:30", 2026)
check("... while a list of dates still shares the clock after it",
      T.times_for(date(2026, 11, 14), g, _e) == [(1170, None)] == T.times_for(date(2026, 11, 15), g, _e), g)
check("'28-31/12, 2/1/2027': a month after the list's last is the year before",
      T.times_for(date(2026, 12, 29), *(T.parse_predate("28-31/12, 2/1/2027 (Sat) 20:00", 2026)[0::2])) == [(1200, None)])
cal = at(c2, "186936#")
check("an exhibition whose opening day is short (16:00-18:30) is still ONE date-only run",
      len(cal) == 1 and (cal[0].start_local, cal[0].end_local) == ("2026-10-16", "2026-10-19"),
      [(e.start_local, e.end_local) for e in cal])
gap = {"events": {"999003": FX_CUL["events"]["188610"]}, "venues": FX_CUL["venues"],
       "dates": {"999003": _span_days("2026-10-01", "2026-10-08")}}
gp = cul_from(gap)[0]
check("SYNTHETIC: eventDates adding 3/10 to '01-02/10/2026 / 04-08/10/2026' does not join the runs across it",
      [(e.start_local, e.end_local) for e in gp] == [("2026-10-04", "2026-10-08")],
      [(e.start_local, e.end_local) for e in gp])
check("'Free admission by registration' in the description is refused, as pricee's enrolment is, and counted",
      not at(c2, "188241#") and c2s.get("refused: admission by advance registration (its description)") == 1, c2s)
check("... but a description that says no registration is needed is not",
      T.culture_verdict({"titlee": "Concert", "pricee": "Free Activities", "predateE": "01/11/2026 19:30",
                         "desce": "Free admission, no registration required."}) is None)
ko = at(c2, "188367#") + at(c2, "188392#")
mu = at(c2, "188313#") + at(c2, "188338#")
with tempfile.TemporaryDirectory() as td:
    st = EventStore(os.path.join(td, "c.json"))
    for e in ko + mu:
        st.upsert(e)
    held = {r["sources"][0]["source_id"].split("#")[0] for r in st.records.values()}
check("two 'Cantonese Opera Excerpts' at 14:15 in two rooms of Ko Shan Theatre stay two shows, the free one kept",
      len(ko) == 2 and ko[0].fingerprint != ko[1].fingerprint and {"188367", "188392"} <= held
      and any(e.description.startswith("Admission: free") for e in ko), held)
check("... while one show listed twice in one room (two cat2 codes) still folds into one record",
      len(mu) == 2 and mu[0].fingerprint == mu[1].fingerprint and len(held & {"188313", "188338"}) == 1, held)

# ------------------------------------------------------------- 6. HTTP and the run
print("HTTP and the run")


class Resp:
    def __init__(self, status, body=b"", headers=None):
        self.status_code, self.content, self.headers = status, body, headers or {}


SERVED = {
    "facility-sc.json": json.dumps(FAC).encode(),
    "facility-sg.json": b"[]",
    "activity-prog/file": json.dumps(SP).encode("utf-8"),
    "events.xml": to_xml("events", "event", FX_CUL["events"]),
    "eventDates.xml": to_xml("event_dates", "event", FX_CUL["dates"], many="indate"),
    "venues.xml": to_xml("venues", "venue", FX_CUL["venues"]),
    "holiday.xml": b"<?xml version='1.0' encoding='UTF-8'?>\n<holiday />",
}


class FakeSession:
    """The two hosts, served from the fixtures. `answer(url, n)` may return a
    Resp for the n-th call, to play a refusal or a redirect."""

    def __init__(self, answer=None):
        self.headers, self.calls, self.answer = {}, [], answer or (lambda u, n: None)

    def get(self, url, timeout=None, allow_redirects=True, params=None, stream=False):
        self.calls.append(url)
        got = self.answer(url, len(self.calls))
        if got is not None:
            return got
        for k, v in SERVED.items():
            if url.endswith(k):
                return Resp(200, v)
        return Resp(404)


class FakeRobots:
    def __init__(self, deny=()):
        self._files, self.deny, self.asked = {}, deny, []

    def check(self, url):
        origin = T.robots_txt.origin_of(url)
        self.asked.append(origin)
        self._files[origin] = {"status": "ok"}
        ok = not any(d in url for d in self.deny)
        return {"allowed": ok, "status": "ok", "rule": None if ok else "Disallow: /datagovhk/", "crawl_delay": None}


slept = []
fs = FakeSession()
rd = T.Reader(fs, robots=FakeRobots(), sleep=slept.append, clock=lambda: 0.0)
rd.get("https://www.lcsd.gov.hk/datagovhk/event/events.xml")
rd.get("https://www.lcsd.gov.hk/datagovhk/event/venues.xml")
check("requests to one host are paced (>= 1.1 s)", slept == [T.MIN_INTERVAL_S], slept)
fs = FakeSession(lambda u, n: Resp(302, headers={"location": "https://cdn.example.org/events.xml"}) if n == 1 else None)
rb = FakeRobots()
T.Reader(fs, robots=rb, sleep=lambda s: None, clock=lambda: 0.0).get("https://www.lcsd.gov.hk/datagovhk/event/events.xml")
check("a redirect is followed only after the new host's robots.txt is asked",
      "https://cdn.example.org" in rb.asked and fs.calls[-1] == "https://cdn.example.org/events.xml", (rb.asked, fs.calls))
fs = FakeSession(lambda u, n: Resp(200, b"<html><title>Just a moment...</title></html>"))
try:
    T.Reader(fs, robots=FakeRobots(), sleep=lambda s: None, clock=lambda: 0.0).get("https://www.lcsd.gov.hk/x.xml")
    got = None
except T.Refused as exc:
    got = str(exc)
check("a bot challenge is a refusal, asked once", got and "challenge" in got and len(fs.calls) == 1, (got, fs.calls))


class Trickle(Resp):
    """A 200 whose body arrives in chunks, each `step` seconds of `clock` (or
    of real time when `real`) after the last."""

    def __init__(self, body, n=6, step=40.0, clock=None, real=False):
        super().__init__(200, body)
        self.n, self.step, self.clock, self.real, self.closed = n, step, clock, real, False

    def iter_content(self, size):
        k = max(1, len(self.content) // self.n + 1)
        for i in range(0, len(self.content), k):
            if self.real:
                time.sleep(self.step)
            else:
                self.clock[0] += self.step
            yield self.content[i:i + k]

    def close(self):
        self.closed = True


now = [0.0]
slow = Trickle(b"x" * 600, n=6, step=40.0, clock=now)
fs = FakeSession(lambda u, n: slow)
rd = T.Reader(fs, robots=FakeRobots(), sleep=lambda s: None, clock=lambda: now[0], deadline=100.0)
try:
    rd.get("https://data.smartplay.lcsd.gov.hk/file")
    got = None
except T.OutOfTime as exc:
    got = str(exc)
check("a body still trickling in at the deadline is abandoned there, not read to its end, and not retried",
      got and "while reading" in got and now[0] < 200 and slow.closed and len(fs.calls) == 1, (got, now, fs.calls))
now[0] = 95.0
check("... and no read waits longer than the time left (never under 10 s)",
      T.Reader(FakeSession(), robots=FakeRobots(), clock=lambda: now[0], deadline=100.0)._timeout()[1] == 10.0
      and T.Reader(FakeSession(), robots=FakeRobots(), clock=lambda: 0.0, deadline=1000.0)._timeout()[1]
      == T.REQUEST_TIMEOUT_S)


def run(fake, robots=None, *extra):
    real_session, real_reader, real_geo, real_save = T.requests.Session, T.Reader, T.make_geocoder, T.save_geocoder
    T.requests.Session = lambda: fake
    T.Reader = lambda *a, **k: real_reader(*a, **dict(k, robots=robots or FakeRobots(), sleep=lambda s: None))
    T.make_geocoder = lambda session, cfg: geocode
    T.save_geocoder = lambda: None
    out = io.StringIO()
    path = os.path.join(tmpdir, f"store{len(os.listdir(tmpdir))}.json")
    try:
        with contextlib.redirect_stdout(out):
            got = T.main(["--config", CONFIG, "--store", path, *extra])
    except BaseException as exc:                                  # noqa: BLE001
        got = exc
    finally:
        T.requests.Session, T.Reader, T.make_geocoder, T.save_geocoder = real_session, real_reader, real_geo, real_save
    return got, out.getvalue(), path


def stored_rows(path):
    return EventStore(path).records if os.path.exists(path) else {}


with tempfile.TemporaryDirectory() as tmpdir:
    code, out, path = run(FakeSession())
    n_all = len(stored_rows(path))
    check("a whole run writes both sources and exits 0, printing every refusal with its count",
          code == 0 and n_all > len(evs) and "refused: enrolment by BALLOT (a course you apply for): 1" in out
          and "refused: a weekly series ('Every <day>'): a hirer's class season: 1" in out, (code, out[-600:]))
    fake = FakeSession(lambda u, n: Resp(403) if "smartplay" in u else None)
    code, out, path = run(fake)
    check("a 403 from SmartPlay is a refusal: asked once, never retried; the cultural programme still lands",
          code == 0 and sum("smartplay" in c for c in fake.calls) == 1 and "REFUSED" in out
          and stored_rows(path) and all(r["sources"][0]["source"] == "lcsd-culture" for r in stored_rows(path).values()),
          out[-400:])
    fake = FakeSession(lambda u, n: Resp(503) if ("smartplay" in u and n <= 4) else None)
    code, out, path = run(fake)
    check("a 503 is retried, and the run then completes", code == 0 and len(stored_rows(path)) == n_all, out[-300:])
    code, out, path = run(FakeSession(), FakeRobots(deny=("/datagovhk/event/",)))
    check("robots.txt refusing the cultural files stops that source only", code == 0 and "REFUSED" in out
          and stored_rows(path) and all(r["sources"][0]["source"] == "lcsd-smartplay" for r in stored_rows(path).values()),
          out[-400:])
    held = SERVED["holiday.xml"]
    SERVED["holiday.xml"] = b"<holiday><venue id='1'><date>20261020</date></venue></holiday>"
    code, out, path = run(FakeSession())
    SERVED["holiday.xml"] = held
    check("a holiday.xml with entries is reported loudly, not silently ignored",
          code == 0 and "WARNING: holiday.xml lists 1 venue closure" in out, out[-300:])
    fake = FakeSession()
    code, out, path = run(fake, None, "--max-minutes", "0.0000001")
    check("--max-minutes: past the deadline no request starts, the run exits 0 and still saves",
          code == 0 and not fake.calls and "STOPPED" in out and os.path.exists(path), (fake.calls, out[-300:]))
    fake = FakeSession(lambda u, n: Resp(404) if u.endswith("facility-sc.json") else None)
    code, out, path = run(fake)
    check("facility lists that cannot be read: SmartPlay is not read at all (no district could vouch for a "
          "geocode), the cultural programme still lands",
          code == 0 and "facility lists NOT read" in out and not any("smartplay" in c for c in fake.calls)
          and stored_rows(path) and all(r["sources"][0]["source"] == "lcsd-culture" for r in stored_rows(path).values()),
          out[-500:])
    fake = FakeSession(lambda u, n: Trickle(SERVED["activity-prog/file"], n=8, step=0.25, real=True)
                       if "smartplay" in u else None)
    code, out, path = run(fake, None, "--max-minutes", "0.02")
    rows = stored_rows(path)
    check("a SmartPlay transfer cut at --max-minutes stops SmartPlay only: the cultural programme, read first, is saved",
          code == 0 and "STOPPED" in out and "while reading" in out and rows
          and all(r["sources"][0]["source"] == "lcsd-culture" for r in rows.values()), out[-500:])

    # COMPLETE READS (2026-10-05, the owner: a session taken off must not stay
    # on the map). Neither file says a session is cancelled, so a WHOLE read is
    # the evidence (mapsee_supabase_sync --retire-absent); a partial one never.
    def reads(path):
        return json.load(open(path, encoding="utf-8")).get("complete_reads") or {}
    want = (TODAY.isoformat(), (TODAY + timedelta(days=90)).isoformat())
    code, out, path = run(FakeSession())
    got = {k: (v["from"][:10], v["to"][:10]) for k, v in reads(path).items()}
    check("COMPLETE: a whole run marks both sources, each over [today, today + 90 days]",
          got == {"lcsd-culture": want, "lcsd-smartplay": want}, got)
    code, out, path = run(FakeSession(lambda u, n: Resp(403) if "smartplay" in u else None))
    check("a refused SmartPlay is NOT complete; the cultural programme still is", set(reads(path)) == {"lcsd-culture"},
          reads(path))
    code, out, path = run(FakeSession(lambda u, n: Resp(404) if u.endswith("facility-sc.json") else None))
    check("facility lists not read: NEITHER is complete (a venue they placed would read as gone)",
          reads(path) == {} and "read NOT complete: the facility lists were not read" in out, out[-400:])
    code, out, path = run(FakeSession(lambda u, n: Trickle(SERVED["activity-prog/file"], n=8, step=0.25, real=True)
                                      if "smartplay" in u else None), None, "--max-minutes", "0.02")
    check("SmartPlay cut at the deadline is NOT complete; the cultural programme read before it is",
          set(reads(path)) == {"lcsd-culture"}, reads(path))
    import mapsee_geo_budget as _gb  # noqa: E402
    budget = os.path.join(tmpdir, "budget.json")
    json.dump({"n": 3}, open(budget, "w"))
    held_geo, held_budget = dict(GEO), (_gb._MAX, _gb._FILE)
    GEO.clear()
    _gb._MAX, _gb._FILE = 3, budget
    asked.clear()
    try:
        code, out, path = run(FakeSession())
    finally:
        GEO.update(held_geo)
        _gb._MAX, _gb._FILE = held_budget
    check("the geocoding budget spent with a venue unasked: NOT complete (it is not a venue nobody can find)",
          asked and reads(path) == {} and "the budget was spent" in out, (len(asked), reads(path)))

print()
print(f"{'FAILURES: ' + ', '.join(fails) if fails else 'all checks passed'}")
sys.exit(1 if fails else 0)
