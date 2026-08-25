import streamlit as st
import requests
import pandas as pd
import sqlite3
import json
import re
from pathlib import Path
from collections import Counter

# ============================================================
# FPL ASSISTANT MANAGER — COMPLETE VERSION
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide",
)

FPL_API = "https://fantasy.premierleague.com/api"
DB_PATH = Path("fpl_decision_log.db")

# ============================================================
# FIVE HARD-CODED ELITE MANAGERS
# ============================================================

ELITE_MANAGERS = [
    {"name": "FPL Focal", "id": 200},
    {"name": "FPL Harry", "id": 1320},
    {"name": "FPL Raptor", "id": 1587},
    {"name": "FPL Pickle", "id": 14501},
    {"name": "BigMan Bakar", "id": 963},
]

# ============================================================
# SESSION DEFAULTS
# ============================================================

if "creator_signals" not in st.session_state:
    st.session_state.creator_signals = []

if "creator_analysis" not in st.session_state:
    st.session_state.creator_analysis = ""

if "elite_data" not in st.session_state:
    st.session_state.elite_data = {}

if "team_id" not in st.session_state:
    st.session_state.team_id = ""

if "bootstrap" not in st.session_state:
    st.session_state.bootstrap = None

# ============================================================
# API HELPERS
# ============================================================

@st.cache_data(ttl=300)
def get_json(url):
    try:
        r = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 FPL Assistant Manager"},
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return None


@st.cache_data(ttl=300)
def get_bootstrap():
    return get_json(f"{FPL_API}/bootstrap-static/")


def get_current_gw(data):
    if not data:
        return 1

    events = data.get("events", [])

    for e in events:
        if e.get("is_current"):
            return e["id"]

    for e in events:
        if e.get("is_next"):
            return e["id"]

    return 1


def get_team_name(team_id, teams):
    for t in teams:
        if t["id"] == team_id:
            return t["name"]
    return "Unknown"


# ============================================================
# PLAYER DATA
# ============================================================

def prepare_players(data):
    if not data:
        return []

    teams = {
        t["id"]: t["name"]
        for t in data.get("teams", [])
    }

    positions = {
        1: "GK",
        2: "DEF",
        3: "MID",
        4: "FWD",
    }

    players = []

    for p in data.get("elements", []):
        players.append({
            "id": p["id"],
            "name": (
                f"{p.get('first_name', '')} "
                f"{p.get('second_name', '')}"
            ).strip(),
            "short_name": p.get("web_name", ""),
            "team": teams.get(p["team"], "?"),
            "team_id": p["team"],
            "position": positions.get(p["element_type"], "?"),
            "price": p.get("now_cost", 0) / 10,
            "points": p.get("total_points", 0),
            "form": float(p.get("form", 0) or 0),
            "ppg": float(p.get("points_per_game", 0) or 0),
            "minutes": p.get("minutes", 0),
            "goals": p.get("goals_scored", 0),
            "assists": p.get("assists", 0),
            "bonus": p.get("bonus", 0),
            "bps": p.get("bps", 0),
            "selected": p.get("selected_by_percent", 0),
            "ownership": p.get("selected_by_percent", 0),
            "status": p.get("status", ""),
            "news": p.get("news", ""),
            "chance": p.get("chance_of_playing_next_round"),
            "fixture_difficulty": 3,
        })

    return players


def calculate_scores(players):
    if not players:
        return []

    for p in players:
        score = 0

        score += min(p["form"] * 7, 35)
        score += min(p["ppg"] * 5, 25)
        score += min(p["bps"] / 10, 10)
        score += min(p["bonus"] / 3, 5)

        if p["minutes"] >= 500:
            score += 5

        if p["chance"] is not None:
            if p["chance"] >= 90:
                score += 5
            elif p["chance"] >= 75:
                score += 2
            elif p["chance"] < 50:
                score -= 15

        if p["status"] != "a":
            score -= 20

        score -= (p["fixture_difficulty"] - 1) * 4

        p["blended"] = round(max(0, min(100, score)), 1)

    return players


# ============================================================
# MANAGER DATA
# ============================================================

def get_manager(manager_id):
    return get_json(
        f"{FPL_API}/entry/{manager_id}/"
    )


def get_manager_picks(manager_id, gw):
    return get_json(
        f"{FPL_API}/entry/{manager_id}/event/{gw}/picks/"
    )


def get_manager_history(manager_id):
    return get_json(
        f"{FPL_API}/entry/{manager_id}/history/"
    )


def validate_manager(manager_id):
    data = get_manager(manager_id)

    if not data:
        return None, "Manager ID could not be found."

    return data, None


# ============================================================
# USER TEAM
# ============================================================

def load_my_team(team_id, gw, players):
    if not team_id:
        return None, "Enter your FPL Manager ID."

    picks = get_manager_picks(team_id, gw)

    if not picks:
        return None, "Couldn't load that FPL team."

    player_by_id = {p["id"]: p for p in players}

    squad = []

    for item in picks.get("picks", []):
        pid = item["element"]

        if pid in player_by_id:
            p = player_by_id[pid].copy()
            p["multiplier"] = item.get("multiplier", 1)
            p["position_order"] = item.get("position", 99)
            squad.append(p)

    return squad, None


# ============================================================
# ELITE MANAGER ANALYSIS
# ============================================================

def analyse_elite_managers(gw, players):
    player_by_id = {p["id"]: p for p in players}

    ownership = Counter()
    captaincy = Counter()
    appearances = Counter()

    manager_results = {}

    for manager in ELITE_MANAGERS:

        info, error = validate_manager(manager["id"])

        if error:
            manager_results[manager["name"]] = {
                "valid": False,
                "error": error,
            }
            continue

        picks = get_manager_picks(
            manager["id"],
            gw
        )

        if not picks:
            manager_results[manager["name"]] = {
                "valid": False,
                "error": "Could not load GW picks.",
            }
            continue

        selected = []

        for pick in picks.get("picks", []):
            pid = pick["element"]

            if pid in player_by_id:
                selected.append(
                    player_by_id[pid]["name"]
                )
                ownership[pid] += 1

                if pick.get("is_captain"):
                    captaincy[pid] += 1

                appearances[pid] += 1

        manager_results[manager["name"]] = {
            "valid": True,
            "manager": info,
            "players": selected,
            "overall_rank": info.get("summary_overall_rank"),
            "gw_points": info.get("summary_event_points"),
            "total_points": info.get("summary_overall_points"),
        }

    rows = []

    for p in players:

        own = ownership[p["id"]]
        cap = captaincy[p["id"]]

        if own >= 4:
            stance = "BUY"
        elif own >= 3:
            stance = "POSITIVE"
        elif own >= 2:
            stance = "HOLD"
        elif own == 1:
            stance = "WATCH"
        else:
            stance = "AVOID"

        rows.append({
            "Player": p["name"],
            "Elite Own": f"{own}/5",
            "Elite %": round(own / 5 * 100),
            "Captain": f"{cap}/5",
            "Elite Stance": stance,
        })

    return manager_results, pd.DataFrame(rows)


# ============================================================
# MODEL SIGNAL
# ============================================================

def model_signal(player, owned=False):

    if player["status"] != "a":
        return "SELL" if owned else "AVOID"

    if player["chance"] is not None and player["chance"] < 50:
        return "SELL" if owned else "AVOID"

    if owned:
        if player["blended"] >= 65:
            return "HOLD"
        if player["blended"] < 45:
            return "SELL"
        return "HOLD"

    if player["blended"] >= 70:
        return "BUY"

    if player["blended"] >= 55:
        return "WATCH"

    return "AVOID"


# ============================================================
# CREATOR SIGNAL EXTRACTION
# ============================================================

def extract_json(text):
    text = text.strip()

    text = re.sub(
        r"^```json",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"^```",
        "",
        text
    )

    text = re.sub(
        r"```$",
        "",
        text
    )

    start = text.find("[")
    end = text.rfind("]")

    if start >= 0 and end >= start:
        text = text[start:end + 1]

    try:
        return json.loads(text)
    except:
        return []


def gemini_extract_signals(transcript, players, api_key):

    if not api_key:
        return [], "No Gemini API key supplied."

    names = [
        p["name"]
        for p in players
    ]

    prompt = f"""
You are extracting FPL recommendations from a football analyst transcript.

Only use player names from this list:

{", ".join(names)}

Transcript:

{transcript[:18000]}

Return ONLY JSON.

Format:

[
  {{
    "player": "exact FPL player name",
    "stance": "BUY",
    "confidence": "HIGH"
  }}
]

Allowed stance:
BUY
SELL
HOLD
CAPTAIN

Allowed confidence:
HIGH
MEDIUM
LOW

Only include players where the analyst clearly gives an opinion.
Do not include players merely mentioned.
"""

    try:

        from google import genai

        client = genai.Client(
            api_key=api_key
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

        parsed = extract_json(
            getattr(response, "text", "")
        )

        valid = set(names)

        cleaned = []

        for item in parsed:

            if not isinstance(item, dict):
                continue

            if item.get("player") not in valid:
                continue

            if item.get("stance") not in [
                "BUY",
                "SELL",
                "HOLD",
                "CAPTAIN",
            ]:
                continue

            cleaned.append(item)

        return cleaned, None

    except Exception as e:
        return [], str(e)


# ============================================================
# SIGNAL CONFLICT ENGINE
# ============================================================

def build_signal_table(
    players,
    my_squad,
    elite_df,
    creator_signals,
):

    owned_ids = {
        p["id"]
        for p in my_squad
    }

    elite_lookup = {}

    if not elite_df.empty:
        for _, row in elite_df.iterrows():
            elite_lookup[row["Player"]] = row

    creator_lookup = {
        x["player"]: x
        for x in creator_signals
    }

    rows = []

    for p in players:

        if p["id"] not in owned_ids and p["blended"] < 55:
            continue

        owned = p["id"] in owned_ids

        model = model_signal(
            p,
            owned
        )

        elite = elite_lookup.get(
            p["name"]
        )

       
