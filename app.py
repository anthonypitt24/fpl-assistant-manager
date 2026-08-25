from collections import defaultdict
import re
from urllib.parse import urlparse, parse_qs

import pandas as pd
import requests
import streamlit as st

# Optional Gemini
try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

# Optional YouTube transcripts
try:
    from youtube_transcript_api import YouTubeTranscriptApi
except Exception:
    YouTubeTranscriptApi = None


# ============================================================
# FPL ASSISTANT MANAGER
# ============================================================
# Designed to be:
# - Fast
# - Lightweight
# - Personal-use friendly
# - Resistant to API overload
#
# Main data:
# - Official FPL API
# - Team recent form calculated from FPL fixtures
# - Optional betting-market signal
# - Elite manager tracking
# - YouTube creator analysis
# - Gemini AI
#
# IMPORTANT:
# Elite managers are loaded ONLY when their tab is opened.
# This prevents the whole app from waiting for multiple
# external requests on every page load.
# ============================================================


st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide",
)

API = "https://fantasy.premierleague.com/api"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/139 Safari/537.36"
    )
}

FIXTURE_HORIZON = 5
FORM_MATCHES = 5
PROJECTION_WEEKS = 4
MAX_PER_CLUB = 3
TRANSFER_HIT = 4


VALID_FORMATIONS = [
    (3, 4, 3),
    (3, 5, 2),
    (4, 4, 2),
    (4, 3, 3),
    (4, 5, 1),
    (5, 4, 1),
    (5, 3, 2),
    (5, 2, 3),
]


# ============================================================
# GEMINI MODELS
# ============================================================

GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]


# ============================================================
# ELITE MANAGERS
# ============================================================

ELITE_MANAGERS = {
    "Abinav C": {
        "entry_id": 175376,
        "hall_of_fame": 3,
    },
    "John Walsh": {
        "entry_id": 1519295,
        "hall_of_fame": 5,
    },
    "FPL Harry": {
        "entry_id": 1320,
        "hall_of_fame": 10,
    },
    "Keilan Kenny": {
        "entry_id": None,
        "hall_of_fame": 38,
    },
    "Nick (FPL Spartan)": {
        "entry_id": None,
        "hall_of_fame": 63,
    },
}


# ============================================================
# CREATOR CHANNELS
# ============================================================

CREATOR_CHANNELS = {
    "FPL Harry": "https://www.youtube.com/@FPLHarry",
    "Let's Talk FPL": "https://www.youtube.com/@LetsTalkFPL",
    "FPL Focal": "https://www.youtube.com/@FPLFocal",
    "FPL Mate": "https://www.youtube.com/@FPLMate",
    "Planet FPL": "https://www.youtube.com/@PlanetFPL",
}


# ============================================================
# BASIC HELPERS
# ============================================================

def num(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_secret(name, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def availability_factor(player):
    chance = num(player.get("chance"), 100)

    if chance >= 90:
        return 1.0
    if chance >= 75:
        return 0.85
    if chance >= 50:
        return 0.60
    if chance > 0:
        return 0.30

    return 0.0


# ============================================================
# API
# ============================================================

@st.cache_data(
    ttl=300,
    show_spinner=False,
)
def api_get(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=15,
    )

    response.raise_for_status()

    return response.json()


@st.cache_data(
    ttl=300,
    show_spinner=False,
)
def get_entry_info(entry_id):
    return api_get(
        f"{API}/entry/{entry_id}/"
    )


@st.cache_data(
    ttl=300,
    show_spinner=False,
)
def get_entry_picks(entry_id, gameweek):
    return api_get(
        f"{API}/entry/{entry_id}/event/{gameweek}/picks/"
    )


@st.cache_data(
    ttl=300,
    show_spinner=False,
)
def get_entry_transfers(entry_id):
    return api_get(
        f"{API}/entry/{entry_id}/transfers/"
    )


@st.cache_data(
    ttl=600,
    show_spinner=False,
)
def get_league(league_id):
    return api_get(
        f"{API}/leagues-classic/{league_id}/standings/"
    )


@st.cache_data(
    ttl=900,
    show_spinner=False,
)
def get_team_history(entry_id):
    return api_get(
        f"{API}/entry/{entry_id}/history/"
    )


@st.cache_data(
    ttl=300,
    show_spinner=False,
)
def get_live_gw(gameweek):
    data = api_get(
        f"{API}/event/{gameweek}/live/"
    )

    return {
        element["id"]: element.get(
            "stats",
            {},
        ).get(
            "total_points",
            0,
        )
        for element in data.get(
            "elements",
            [],
        )
    }


# ============================================================
# FIXTURE FUNCTIONS
# ============================================================

def average_fdr(
    fixture_map,
    team_id,
    weeks=None,
):
    games = sorted(
        fixture_map.get(team_id, []),
        key=lambda x: (
            x["gw"],
            not x["home"],
        ),
    )

    if weeks is not None:
        games = games[:weeks]

    if not games:
        return 3.0

    return (
        sum(
            num(
                f["difficulty"],
                3,
            )
            for f in games
        )
        / len(games)
    )


def fixture_count(
    fixture_map,
    team_id,
    gw,
):
    return sum(
        1
        for f in fixture_map.get(
            team_id,
            [],
        )
        if f["gw"] == gw
    )


def fixture_text(
    fixture_map,
    team_names,
    team_id,
    number=5,
):
    games = sorted(
        fixture_map.get(
            team_id,
            [],
        ),
        key=lambda x: (
            x["gw"],
            not x["home"],
        ),
    )[:number]

    if not games:
        return "No fixtures"

    output = []

    for fixture in games:
        opponent = team_names.get(
            fixture["opponent"],
            "?",
        )

        location = (
            "H"
            if fixture["home"]
            else "A"
        )

        output.append(
            f"GW{fixture['gw']} "
            f"{opponent} "
            f"({location}) "
            f"[{fixture['difficulty']}]"
        )

    return " | ".join(output)


# ============================================================
# TEAM FORM
# ============================================================

def calculate_team_form(
    fixtures_raw,
    teams,
    current_gw,
):
    """
    Calculates recent form from completed FPL fixtures.

    No additional API is required.
    """

    form = {}

    for team_id in teams:
        form[team_id] = {
            "played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": 0,
            "goals_against": 0,
            "clean_sheets": 0,
            "recent_results": [],
        }

    completed = [
        f
        for f in fixtures_raw
        if (
            f.get("event") is not None
            and safe_int(
                f.get("event"),
                0,
            ) < current_gw
            and f.get("team_h") is not None
            and f.get("team_a") is not None
            and f.get("team_h_score") is not None
            and f.get("team_a_score") is not None
        )
    ]

    completed.sort(
        key=lambda f: safe_int(
            f.get("event"),
            0,
        ),
        reverse=True,
    )

    for fixture in completed:
        home = fixture.get("team_h")
        away = fixture.get("team_a")

        home_score = safe_int(
            fixture.get("team_h_score"),
            0,
        )

        away_score = safe_int(
            fixture.get("team_a_score"),
            0,
        )

        if home not in form or away not in form:
            continue

        # Home
        h = form[home]

        h["played"] += 1
        h["goals_for"] += home_score
        h["goals_against"] += away_score

        if away_score == 0:
            h["clean_sheets"] += 1

        if home_score > away_score:
            h["wins"] += 1
            result = "W"
        elif home_score == away_score:
            h["draws"] += 1
            result = "D"
        else:
            h["losses"] += 1
            result = "L"

        h["recent_results"].append(result)

        # Away
        a = form[away]

        a["played"] += 1
        a["goals_for"] += away_score
        a["goals_against"] += home_score

        if home_score == 0:
            a["clean_sheets"] += 1

        if away_score > home_score:
            a["wins"] += 1
            result = "W"
        elif away_score == home_score:
            a["draws"] += 1
            result = "D"
        else:
            a["losses"] += 1
            result = "L"

        a["recent_results"].append(result)

    # Keep only most recent five results
    for team_id, data in form.items():

        data["recent_results"] = (
            data["recent_results"][:FORM_MATCHES]
        )

        played = len(
            data["recent_results"]
        )

        if played:
            points = (
                data["wins"] * 3
                + data["draws"]
            )

            data["points_per_game"] = (
                points / played
            )

            data["goals_per_game"] = (
                data["goals_for"]
                / played
            )

            data["conceded_per_game"] = (
                data["goals_against"]
                / played
            )

            data["clean_sheet_rate"] = (
                data["clean_sheets"]
                / played
            )

            # 0-10 form score
            score = (
                data["points_per_game"] / 3 * 5
                + min(
                    data["goals_per_game"],
                    3,
                ) / 3 * 2
                + data["clean_sheet_rate"] * 2
                + max(
                    0,
                    1
                    - data["conceded_per_game"]
                    / 3,
                )
            )

            data["form_score"] = round(
                min(
                    max(score, 0),
                    10,
                ),
                1,
            )

        else:
            data["points_per_game"] = 0
            data["goals_per_game"] = 0
            data["conceded_per_game"] = 0
            data["clean_sheet_rate"] = 0
            data["form_score"] = 5.0

    return form


def team_form_label(form_score):
    if form_score >= 8:
        return "🔥 Excellent"
    if form_score >= 6.5:
        return "🟢 Strong"
    if form_score >= 5:
        return "🟡 Average"
    if form_score >= 3.5:
        return "🟠 Poor"

    return "🔴 Very Poor"


# ============================================================
# PLAYER MODEL
# ============================================================

def calc_blended_score(player):
    ppg = min(
        num(player["ppg"]) * 1.5,
        10,
    )

    player_form = min(
        num(player["form"]) * 1.2,
        9,
    )

    expected = min(
        num(player["ep_next"]) * 2.5,
        16,
    )

    fixture = max(
        0,
        (3.2 - num(player["fdr"], 3))
        * 3,
    )

    availability = (
        availability_factor(player)
        * 5
    )

    attacking = min(
        num(player["xgi90"]) * 8,
        12,
    )

    defensive = 0

    if player["position"] in (
        "GK",
        "DEF",
    ):
        defensive = max(
            0,
            (
                1.4
                - num(
                    player["xgc90"]
                )
            )
            * 4,
        )

    team_form_score = num(
        player.get(
            "team_form_score",
            5,
        ),
        5,
    )

    # Team form contributes a modest amount.
    team_form_bonus = (
        team_form_score - 5
    ) * 1.2

    dgw_bonus = (
        7
        if safe_int(
            player["next_gw_fixtures"]
        ) >= 2
        else 0
    )

    bgw_penalty = (
        8
        if safe_int(
            player["next_gw_fixtures"]
        ) == 0
        else 0
    )

    ownership_bonus = (
        2
        if (
            num(player["ownership"]) < 5
            and num(player["xgi90"]) >= 0.25
        )
        else 0
    )

    score = (
        ppg
        + player_form
        + expected
        + fixture
        + availability
        + attacking
        + defensive
        + team_form_bonus
        + dgw_bonus
        + ownership_bonus
        - bgw_penalty
    )

    return round(
        score,
        2,
    )


def calc_multi_gw_projection(
    player,
    fixture_map,
    weeks=PROJECTION_WEEKS,
):
    games = sorted(
        fixture_map.get(
            player["team_id"],
            [],
        ),
        key=lambda x: x["gw"],
    )[:weeks]

    if not games:
        return round(
            num(
                player["ep_next"]
            ),
            1,
        )

    availability = availability_factor(
        player
    )

    base = (
        num(player["ep_next"]) * 0.55
        + num(player["ppg"]) * 0.20
        + num(player["xgi90"]) * 2.0
    )

    # Small adjustment for recent team form.
    team_form = num(
        player.get(
            "team_form_score",
            5,
        ),
        5,
    )

    form_multiplier = (
        0.9
        + (
            team_form / 10
        ) * 0.2
    )

    total = 0.0

    for fixture in games:
        difficulty = num(
            fixture["difficulty"],
            3,
        )

        multiplier = (
            1.0
            + (
                (3 - difficulty)
                * 0.08
            )
        )

        total += (
            base
            * multiplier
            * availability
            * form_multiplier
        )

    return round(
        total,
        1,
    )


# ============================================================
# LOAD FPL DATA
# ============================================================

@st.cache_data(
    ttl=900,
    show_spinner="Loading FPL data...",
)
def load_fpl_data():

    bootstrap = api_get(
        f"{API}/bootstrap-static/"
    )

    fixtures_raw = api_get(
        f"{API}/fixtures/"
    )

    events = bootstrap.get(
        "events",
        []
    )

    raw_players = bootstrap.get(
        "elements",
        []
    )

    raw_teams = bootstrap.get(
        "teams",
        []
    )

    teams = {
        t["id"]: t
        for t in raw_teams
    }

    team_names = {
        t["id"]: t.get(
            "short_name",
            "?",
        )
        for t in raw_teams
    }

    positions = {
        1: "GK",
        2: "DEF",
        3: "MID",
        4: "FWD",
    }

    current_event = next(
        (
            e
            for e in events
            if e.get("is_current")
        ),
        None,
    )

    next_event = next(
        (
            e
            for e in events
            if e.get("is_next")
        ),
        None,
    )

    if current_event:
        current_gw = safe_int(
            current_event.get("id"),
            1,
        )
    elif next_event:
        current_gw = max(
            safe_int(
                next_event.get("id"),
                1,
            )
            - 1,
            1,
        )
    else:
        current_gw = 1

    next_gw = (
        safe_int(
            next_event.get("id"),
            current_gw + 1,
        )
        if next_event
        else current_gw + 1
    )

    # --------------------------------------------------------
    # Future fixture map
    # --------------------------------------------------------

    fixture_map = defaultdict(list)

    for fixture in fixtures_raw:

        gw = fixture.get(
            "event"
        )

        if gw is None:
            continue

        if (
            gw < next_gw
            or gw
            > next_gw
            + FIXTURE_HORIZON
            - 1
        ):
            continue

        home = fixture.get(
            "team_h"
        )

        away = fixture.get(
            "team_a"
        )

        if home:

            fixture_map[home].append(
                {
                    "gw": safe_int(gw),
                    "home": True,
                    "opponent": away,
                    "difficulty": safe_int(
                        fixture.get(
                            "team_h_difficulty"
                        ),
                        3,
                    ),
                }
            )

        if away:

            fixture_map[away].append(
                {
                    "gw": safe_int(gw),
                    "home": False,
                    "opponent": home,
                    "difficulty": safe_int(
                        fixture.get(
                            "team_a_difficulty"
                        ),
                        3,
                    ),
                }
            )

    # --------------------------------------------------------
    # Team form
    # --------------------------------------------------------

    team_form = calculate_team_form(
        fixtures_raw,
        teams,
        current_gw,
    )

    # --------------------------------------------------------
    # Players
    # --------------------------------------------------------

    players = []

    for raw in raw_players:

        team_id = raw.get(
            "team"
        )

        chance = raw.get(
            "chance_of_playing_next_round"
        )

        if chance is None:
            chance = 100

        transfers_in = safe_int(
            raw.get(
                "transfers_in_event"
            )
        )

        transfers_out = safe_int(
            raw.get(
                "transfers_out_event"
            )
        )

        player_team_form = team_form.get(
            team_id,
            {},
        )

        player = {
            "id": raw.get("id"),

            "name": raw.get(
                "web_name",
                "?",
            ),

            "full_name": (
                f"{raw.get('first_name', '')} "
                f"{raw.get('second_name', '')}"
            ).strip(),

            "position": positions.get(
                raw.get("element_type"),
                "?",
            ),

            "team_id": team_id,

            "team": team_names.get(
                team_id,
                "?",
            ),

            "price": num(
                raw.get("now_cost")
            ) / 10,

            "points": safe_int(
                raw.get("total_points")
            ),

            "ppg": num(
                raw.get(
                    "points_per_game"
                )
            ),

            "form": num(
                raw.get("form")
            ),

            "minutes": safe_int(
                raw.get("minutes")
            ),

            "goals": safe_int(
                raw.get("goals_scored")
            ),

            "assists": safe_int(
                raw.get("assists")
            ),

            "clean_sheets": safe_int(
                raw.get("clean_sheets")
            ),

            "bonus": safe_int(
                raw.get("bonus")
            ),

            "bps": safe_int(
                raw.get("bps")
            ),

            "ep_next": num(
                raw.get("ep_next")
            ),

            "ownership": num(
                raw.get(
                    "selected_by_percent"
                )
            ),

            "chance": num(
                chance,
                100,
            ),

            "status": raw.get(
                "status",
                "a",
            ),

            "news": raw.get(
                "news",
                "",
            ),

            "xgi90": num(
                raw.get(
                    "expected_goal_involvements_per_90"
                )
            ),

            "xgc90": num(
                raw.get(
                    "expected_goals_conceded_per_90"
                )
            ),

            "ict": num(
                raw.get("ict_index")
            ),

            "transfers_in": transfers_in,

            "transfers_out": transfers_out,

            "net_transfers": (
                transfers_in
                - transfers_out
            ),

            "price_change": safe_int(
                raw.get(
                    "cost_change_event"
                )
            ),

            # Team form
            "team_form_score": player_team_form.get(
                "form_score",
                5,
            ),

            "team_form_results": (
                player_team_form.get(
                    "recent_results",
                    [],
                )
            ),

            "team_goals_for": player_team_form.get(
                "goals_for",
                0,
            ),

            "team_goals_against": player_team_form.get(
                "goals_against",
                0,
            ),

            "team_clean_sheets": player_team_form.get(
                "clean_sheets",
                0,
            ),
        }

        player["fdr"] = average_fdr(
            fixture_map,
            team_id,
        )

        player["next_gw_fixtures"] = fixture_count(
            fixture_map,
            team_id,
            next_gw,
        )

        player["fixtures"] = fixture_text(
            fixture_map,
            team_names,
            team_id,
            FIXTURE_HORIZON,
        )

        # Score calculated after all inputs exist
        player["blended"] = calc_blended_score(
            player
        )

        player["projection_4gw"] = calc_multi_gw_projection(
            player,
            fixture_map,
        )

        players.append(player)

    return {
        "bootstrap": bootstrap,
        "fixtures_raw": fixtures_raw,
        "teams": teams,
        "team_names": team_names,
        "team_form": team_form,
        "current_gw": current_gw,
        "next_gw": next_gw,
        "fixture_map": dict(
            fixture_map
        ),
        "players": players,
        "player_by_id": {
            p["id"]: p
            for p in players
            if p.get("id") is not None
        },
    }


# ============================================================
# LOAD DATA
# ============================================================

try:

    DATA = load_fpl_data()

except Exception as exc:

    st.error(
        "⚠️ The official FPL API could not be loaded."
    )

    st.caption(
        f"Technical detail: {exc}"
    )

    st.stop()


teams = DATA["teams"]
team_names = DATA["team_names"]
team_form = DATA["team_form"]
current_gw = DATA["current_gw"]
next_gw = DATA["next_gw"]
fixture_map = DATA["fixture_map"]
players = DATA["players"]
player_by_id = DATA["player_by_id"]


# ============================================================
# PLAYER HELPERS
# ============================================================

def blended_score(player):
    return player["blended"]


def multi_gw_projection(player):
    return calc_multi_gw_projection(
        player,
        fixture_map,
    )


def player_status(player):

    if player["status"] != "a":
        return "🔴 Unavailable"

    if player["chance"] < 50:
        return "🔴 Major doubt"

    if player["chance"] < 75:
        return "🟠 Rotation risk"

    if player["next_gw_fixtures"] == 0:
        return "⚠️ Blank GW"

    if player["next_gw_fixtures"] >= 2:
        return "⚡ Double GW"

    if player["form"] >= 5:
        return "🟢 In Form"

    return "🟡 Normal"


def hold_sell(player):

    if (
        player["status"] != "a"
        or player["chance"] < 50
    ):
        return "🔴 SELL / REPLACE"

    if player["chance"] < 75:
        return "🟠 CONSIDER SELLING"

    if player["next_gw_fixtures"] == 0:
        return "🟡 MONITOR — BLANK"

    if (
        player["form"] < 2.5
        and player["ppg"] < 3
        and player["minutes"] > 300
    ):
        return "🔴 SELL"

    if (
        player["form"] >= 5
        or player["ppg"] >= 5
    ):
        return "🟢 STRONG HOLD"

    return "🟡 MONITOR"


def price_momentum_flag(player):

    net = num(
        player.get(
            "net_transfers"
        )
    )

    ownership = max(
        num(
            player.get(
                "ownership"
            )
        ),
        0.1,
    )

    ratio = (
        net
        / (
            ownership
            * 1000
        )
    )

    if ratio > 0.4:
        return "📈 Likely rise"

    if ratio < -0.4:
        return "📉 Likely fall"

    return "— Stable"


# ============================================================
# USER TEAM
# ============================================================

def load_my_team(entry_id):

    data = get_entry_picks(
        entry_id,
        current_gw,
    )

    squad = []

    for pick in data.get(
        "picks",
        [],
    ):

        player = player_by_id.get(
            pick.get("element")
        )

        if not player:
            continue

        p = player.copy()

        p["is_captain"] = bool(
            pick.get(
                "is_captain"
            )
        )

        p["is_vice"] = bool(
            pick.get(
                "is_vice_captain"
            )
        )

        p["multiplier"] = safe_int(
            pick.get(
                "multiplier"
            ),
            1,
        )

        p["position_slot"] = safe_int(
            pick.get(
                "position"
            ),
            0,
        )

        squad.append(p)

    return data, squad


def squad_club_counts(
    squad,
    exclude_id=None,
):

    counts = defaultdict(int)

    for player in squad:

        if player["id"] == exclude_id:
            continue

        counts[
            player["team_id"]
        ] += 1

    return counts


# ============================================================
# TRANSFER ENGINE
# ============================================================

def transfer_suggestions(
    squad,
    bank,
    free_transfers,
):

    owned_ids = {
        p["id"]
        for p in squad
    }

    club_counts = squad_club_counts(
        squad
    )

    suggestions = []

    for outgoing in squad:

        candidates = [
            p
            for p in players
            if (
                p["position"]
                == outgoing["position"]

                and p["id"]
                not in owned_ids

                and p["status"]
                == "a"

                and p["chance"]
                > 0
            )
        ]

        candidates.sort(
            key=blended_score,
            reverse=True,
        )

        for incoming in candidates[:40]:

            available = (
                bank
                + outgoing["price"]
            )

            if (
                incoming["price"]
                > available
            ):
                continue

            projected_count = club_counts[
                incoming["team_id"]
            ]

            if (
                incoming["team_id"]
                == outgoing["team_id"]
            ):
                projected_count -= 1

            if (
                projected_count + 1
                > MAX_PER_CLUB
            ):
                continue

            projected_gain = (
                multi_gw_projection(
                    incoming
                )
                - multi_gw_projection(
                    outgoing
                )
            )

            hit = (
                0
                if free_transfers > 0
                else TRANSFER_HIT
            )

            net_gain = (
                projected_gain
                - hit
            )

            if (
                free_transfers > 0
                and projected_gain < 2
            ):
                continue

            if (
                free_transfers == 0
                and projected_gain < 4
            ):
                continue

            suggestions.append(
                {
                    "out": outgoing,
                    "in": incoming,
                    "projected_gain": projected_gain,
                    "hit": hit,
                    "net_gain": net_gain,
                    "cost_difference": (
                        incoming["price"]
                        - outgoing["price"]
                    ),
                }
            )

    suggestions.sort(
        key=lambda x: x["net_gain"],
        reverse=True,
    )

    return suggestions[:10]


def transfer_decision(
    squad,
    bank,
    free_transfers,
):

    suggestions = transfer_suggestions(
        squad,
        bank,
        free_transfers,
    )

    if not suggestions:

        return {
            "decision": "ROLL",
            "reason": (
                "No available transfer "
                "clears the model's "
                "minimum improvement "
                "threshold."
            ),
            "suggestions": [],
        }

    best = suggestions[0]

    if free_transfers > 0:

        if (
            best["projected_gain"]
            >= 4.5
        ):

            decision = "TRANSFER"

            reason = (
                f"{best['in']['name']} "
                f"projects "
                f"+{best['projected_gain']:.1f} "
                f"points over "
                f"{PROJECTION_WEEKS} GWs "
                f"versus "
                f"{best['out']['name']}."
            )

        else:

            decision = "ROLL"

            reason = (
                "An upgrade exists, "
                "but it is not large "
                "enough to justify "
                "using the transfer."
            )

    else:

        if (
            best["net_gain"]
            >= 2
        ):

            decision = "TAKE HIT"

            reason = (
                f"Projected improvement "
                f"+{best['projected_gain']:.1f}; "
                f"net "
                f"+{best['net_gain']:.1f} "
                f"after the -4."
            )

        else:

            decision = "ROLL"

            reason = (
                "The best move does "
                "not justify the -4."
            )

    return {
        "decision": decision,
        "reason": reason,
        "suggestions": suggestions,
    }


# ============================================================
# CAPTAIN
# ============================================================

def captain_recommendations(squad):

    available = [
        p
        for p in squad
        if (
            p["chance"] >= 75
            and p["status"] == "a"
            and p["next_gw_fixtures"] > 0
        )
    ]

    def score(p):

        return (
            blended_score(p)
            + num(
                p["ep_next"]
            ) * 1.5
            + max(
                0,
                3
                - num(
                    p["fdr"],
                    3,
                ),
            ) * 1.5
            + (
                4
                if p["next_gw_fixtures"]
                >= 2
                else 0
            )
        )

    available.sort(
        key=score,
        reverse=True,
    )

    return available[:5]


# ============================================================
# BEST XI
# ============================================================

def best_xi(squad):

    if len(squad) < 11:
        return None

    by_pos = defaultdict(list)

    for p in squad:
        by_pos[
            p["position"]
        ].append(p)

    for pos in by_pos:
        by_pos[pos].sort(
            key=blended_score,
            reverse=True,
        )

    gks = by_pos.get(
        "GK",
        [],
    )

    defs = by_pos.get(
        "DEF",
        [],
    )

    mids = by_pos.get(
        "MID",
        [],
    )

    fwds = by_pos.get(
        "FWD",
        [],
    )

    if (
        not gks
        or not defs
        or not mids
        or not fwds
    ):
        return None

    best = None
    best_score = float(
        "-inf"
    )

    for d, m, f in VALID_FORMATIONS:

        if (
            len(defs) < d
            or len(mids) < m
            or len(fwds) < f
        ):
            continue

        lineup = (
            [gks[0]]
            + defs[:d]
            + mids[:m]
            + fwds[:f]
        )

        score = sum(
            blended_score(p)
            for p in lineup
        )

        if score > best_score:

            best_score = score

            lineup_ids = {
                p["id"]
                for p in lineup
            }

            best = {
                "formation": (
                    f"{d}-{m}-{f}"
                ),
                "lineup": lineup,
                "bench": [
                    p
                    for p in squad
                    if p["id"]
                    not in lineup_ids
                ],
                "score": score,
            }

    return best


# ============================================================
# BENCH BOOST
# ============================================================

def bench_boost_value(squad):

    try:
        live = get_live_gw(
            current_gw
        )
    except Exception:
        return None

    bench = [
        p
        for p in squad
        if p.get(
            "multiplier",
            1,
        ) == 0
    ]

    rows = []
    total = 0

    for p in bench:

        pts = live.get(
            p["id"],
            0,
        )

        total += pts

        rows.append(
            {
                "Player": p["name"],
                "GW Points": pts,
            }
        )

    return rows, total


# ============================================================
# ELITE MANAGERS
# ============================================================

def verify_team_id(entry_id):

    if not entry_id:
        return None, "No ID configured"

    try:

        info = get_entry_info(
            int(entry_id)
        )

        if (
            not isinstance(
                info,
                dict,
            )
            or "id" not in info
        ):
            return (
                None,
                "FPL API returned no team",
            )

        return info, "Verified"

    except Exception as exc:

        return (
            None,
            f"Not verified: {exc}",
        )


def load_elite_manager(
    name,
    entry_id,
):

    if not entry_id:

        return {
            "name": name,
            "status": "UNVERIFIED",
            "error": (
                "No verified Team ID "
                "configured."
            ),
            "squad": [],
            "transfers": [],
        }

    info, verification = verify_team_id(
        entry_id
    )

    if not info:

        return {
            "name": name,
            "entry_id": entry_id,
            "status": "FAILED",
            "error": verification,
            "squad": [],
            "transfers": [],
        }

    try:

        picks_data = get_entry_picks(
            entry_id,
            current_gw,
        )

        picks = picks_data.get(
            "picks",
            [],
        )

        squad = []
        captain = None
        vice = None

        for pick in picks:

            p = player_by_id.get(
                pick.get("element")
            )

            if not p:
                continue

            squad.append(
                p.copy()
            )

            if pick.get(
                "is_captain"
            ):
                captain = p["name"]

            if pick.get(
                "is_vice_captain"
            ):
                vice = p["name"]

        transfers = []

        try:

            for transfer in get_entry_transfers(
                entry_id
            ):

                if (
                    transfer.get(
                        "event"
                    )
                    != current_gw
                ):
                    continue

                out_p = player_by_id.get(
                    transfer.get(
                        "element_out"
                    )
                )

                in_p = player_by_id.get(
                    transfer.get(
                        "element_in"
                    )
                )

                transfers.append(
                    {
                        "out": (
                            out_p["name"]
                            if out_p
                            else str(
                                transfer.get(
                                    "element_out"
                                )
                            )
                        ),
                        "in": (
                            in_p["name"]
                            if in_p
                            else str(
                                transfer.get(
                                    "element_in"
                                )
                            )
                        ),
                        "cost": transfer.get(
                            "event_cost",
                            0,
                        ),
                    }
                )

        except Exception:
            pass

        history = get_team_history(
            entry_id
        )

        current_history = (
            history.get(
                "current"
            )
            or []
        )

        latest = (
            current_history[-1]
            if current_history
            else {}
        )

        return {
            "name": name,
            "entry_id": entry_id,
            "status": "OK",
            "entry_name": info.get(
                "name",
                "",
            ),
            "manager_name": (
                f"{info.get('player_first_name', '')} "
                f"{info.get('player_last_name', '')}"
            ).strip(),
            "overall_rank": info.get(
                "summary_overall_rank",
                "—",
            ),
            "total_points": info.get(
                "summary_overall_points",
                "—",
            ),
            "gw_points": latest.get(
                "points",
                "—",
            ),
            "squad": squad,
            "captain": captain,
            "vice": vice,
            "transfers": transfers,
        }

    except Exception as exc:

        return {
            "name": name,
            "entry_id": entry_id,
            "status": "FAILED",
            "error": str(exc),
            "squad": [],
            "transfers": [],
        }


def elite_consensus(elite_rows):

    valid = [
        row
        for row in elite_rows
        if (
            row.get("status")
            == "OK"
            and row.get("squad")
        )
    ]

    total = len(valid)

    if not total:
        return [], [], [], []

    counts = defaultdict(int)
    captain_counts = defaultdict(int)
    transfer_counts = defaultdict(int)

    for row in valid:

        unique = {
            p["id"]: p
            for p in row["squad"]
        }

        for p in unique.values():
            counts[
                p["id"]
            ] += 1

        if row.get("captain"):
            captain_counts[
                row["captain"]
            ] += 1

        for transfer in row.get(
            "transfers",
            [],
        ):

            transfer_counts[
                (
                    transfer["out"],
                    transfer["in"],
                )
            ] += 1

    player_rows = []

    for pid, count in sorted(
        counts.items(),
        key=lambda item: (
            -item[1],
            player_by_id.get(
                item[0],
                {},
            ).get(
                "name",
                "",
            ),
        ),
    ):

        p = player_by_id.get(
            pid
        )

        if not p:
            continue

        player_rows.append(
            {
                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Elite": (
                    f"{count}/{total}"
                ),
                "Elite %": round(
                    100
                    * count
                    / total
                ),
                "Model Score": round(
                    blended_score(p),
                    1,
                ),
                "Team Form": round(
                    p["team_form_score"],
                    1,
                ),
                "FDR": round(
                    p["fdr"],
                    1,
                ),
            }
        )

    captain_rows = [
        {
            "Captain": name,
            "Managers": (
                f"{count}/{total}"
            ),
            "%": round(
                100
                * count
                / total
            ),
        }
        for name, count in sorted(
            captain_counts.items(),
            key=lambda item: -item[1],
        )
    ]

    transfer_rows = [
        {
            "Out": out_name,
            "In": in_name,
            "Managers": count,
        }
        for (
            out_name,
            in_name,
        ), count in sorted(
            transfer_counts.items(),
            key=lambda item: -item[1],
        )
    ]

    return (
        player_rows,
        captain_rows,
        transfer_rows,
        valid,
    )


# ============================================================
# CREATOR FUNCTIONS
# ============================================================

def extract_video_id(value):

    value = (
        value or ""
    ).strip()

    if re.fullmatch(
        r"[0-9A-Za-z_-]{11}",
        value,
    ):
        return value

    parsed = urlparse(value)

    if parsed.netloc:

        query_id = parse_qs(
            parsed.query
        ).get(
            "v",
            [None],
        )[0]

        if (
            query_id
            and re.fullmatch(
                r"[0-9A-Za-z_-]{11}",
                query_id,
            )
        ):
            return query_id

    patterns = [
        r"youtu\.be/([0-9A-Za-z_-]{11})",
        r"youtube\.com/embed/([0-9A-Za-z_-]{11})",
        r"youtube\.com/shorts/([0-9A-Za-z_-]{11})",
        r"youtube\.com/live/([0-9A-Za-z_-]{11})",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            value,
        )

        if match:
            return match.group(1)

    return None


def fetch_youtube_transcript(
    video_identifier
):

    if YouTubeTranscriptApi is None:

        return (
            None,
            (
                "youtube-transcript-api "
                "is not installed."
            ),
        )

    video_id = extract_video_id(
        video_identifier
    )

    if not video_id:

        return (
            None,
            "Invalid YouTube URL or video ID.",
        )

    try:

        api = YouTubeTranscriptApi()

        if hasattr(
            api,
            "fetch",
        ):

            transcript = api.fetch(
                video_id
            )

            lines = []

            for snippet in transcript:

                if isinstance(
                    snippet,
                    dict,
                ):
                    text = snippet.get(
                        "text",
                        "",
                    )
                else:
                    text = getattr(
                        snippet,
                        "text",
                        str(snippet),
                    )

                if text:
                    lines.append(
                        str(text).strip()
                    )

            result = " ".join(
                x
                for x in lines
                if x
            )

            if result:
                return result, None

        if hasattr(
            YouTubeTranscriptApi,
            "get_transcript",
        ):

            transcript = (
                YouTubeTranscriptApi.get_transcript(
                    video_id
                )
            )

            result = " ".join(
                str(
                    item.get(
                        "text",
                        "",
                    )
                )
                for item in transcript
                if item.get(
                    "text"
                )
            )

            if result:
                return result, None

        return (
            None,
            "No transcript available.",
        )

    except Exception as exc:

        return (
            None,
            (
                "YouTube transcript could not "
                "be retrieved. "
                f"Technical detail: {exc}"
            ),
        )


# ============================================================
# GEMINI
# ============================================================

def gemini_generate(
    prompt,
    system_instruction,
):

    api_key = get_secret(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "GEMINI_API_KEY is missing."
        )

    if (
        genai is None
        or types is None
    ):

        raise RuntimeError(
            "google-genai is not installed."
        )

    client = genai.Client(
        api_key=api_key
    )

    errors = []

    for model_name in GEMINI_MODELS:

        try:

            response = (
                client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=(
                            system_instruction
                        ),
                        max_output_tokens=4000,
                    ),
                )
            )

            text = getattr(
                response,
                "text",
                None,
            )

            if text:
                return (
                    text,
                    model_name,
                )

            errors.append(
                f"{model_name}: empty response"
            )

        except Exception as exc:

            errors.append(
                f"{model_name}: {exc}"
            )

    raise RuntimeError(
        "Gemini failed on all models.\n"
        + "\n".join(errors)
    )


def creator_context(squad):

    if not squad:
        return "No squad loaded."

    return "\n".join(
        f"- {p['name']} "
        f"({p['team']}, {p['position']}) | "
        f"£{p['price']:.1f}m | "
        f"Form {p['form']:.1f} | "
        f"PPG {p['ppg']:.1f} | "
        f"xGI/90 {p['xgi90']:.2f} | "
        f"FDR {p['fdr']:.1f} | "
        f"Team form {p['team_form_score']:.1f}"
        for p in squad
    )


def elite_context(elite_rows):

    valid = [
        r
        for r in elite_rows
        if r.get("status") == "OK"
    ]

    if not valid:
        return "No elite teams connected."

    lines = []

    for row in valid:

        players_text = ", ".join(
            p["name"]
            for p in row["squad"]
        )

        lines.append(
            f"{row['name']}: "
            f"{players_text}; "
            f"Captain={row.get('captain', '—')}"
        )

    return "\n".join(lines)


# ============================================================
# STRATEGY
# ============================================================

def strategy_briefing(
    squad,
    bank,
    free_transfers,
):

    injuries = [
        p
        for p in squad
        if (
            p["status"] != "a"
            or p["chance"] < 75
        )
    ]

    blanks = [
        p
        for p in squad
        if p["next_gw_fixtures"] == 0
    ]

    doubles = [
        p
        for p in squad
        if p["next_gw_fixtures"] >= 2
    ]

    captains = captain_recommendations(
        squad
    )

    transfer = transfer_decision(
        squad,
        bank,
        free_transfers,
    )

    avg_fdr = (
        sum(
            p["fdr"]
            for p in squad
        )
        / len(squad)
        if squad
        else 3
    )

    avg_team_form = (
        sum(
            p["team_form_score"]
            for p in squad
        )
        / len(squad)
        if squad
        else 5
    )

    if len(blanks) >= 4:

        chip = (
            "⚠️ 4+ players blank next GW. "
            "Review Free Hit / restructuring."
        )

    elif len(doubles) >= 4:

        chip = (
            "⚡ 4+ players have multiple "
            "fixtures. Potential Bench "
            "Boost / Triple Captain window."
        )

    else:

        chip = (
            "Hold chips unless a stronger "
            "fixture or blank window appears."
        )

    return {
        "injuries": injuries,
        "blanks": blanks,
        "doubles": doubles,
        "captains": captains,
        "transfer": transfer,
        "avg_fdr": avg_fdr,
        "avg_team_form": avg_team_form,
        "chip": chip,
    }


# ============================================================
# UI
# ============================================================

st.title(
    "⚽ FPL Assistant Manager"
)

st.caption(
    f"GW{current_gw} → GW{next_gw} | "
    "FPL data + team form + decision engine"
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Manager Settings"
    )

    entry_id_input = st.text_input(
        "FPL Team ID",
        value="",
        help=(
            "The number in your FPL team URL."
        ),
    )

    league_id_input = st.text_input(
        "Mini-League ID (optional)",
        value="",
    )

    free_transfers = st.number_input(
        "Free Transfers",
        min_value=0,
        max_value=5,
        value=1,
        step=1,
    )

    st.divider()

    st.caption(
        "FPL data is cached for speed."
    )

    st.caption(
        "Elite managers only load when "
        "you open that section."
    )


# ============================================================
# LOAD USER SQUAD
# ============================================================

team_data = None
my_squad = []

if entry_id_input.strip():

    try:

        team_data, my_squad = load_my_team(
            safe_int(
                entry_id_input.strip()
            )
        )

    except Exception as exc:

        st.error(
            "Couldn't load your squad. "
            "Check the Team ID."
        )

        st.caption(
            str(exc)
        )


# ============================================================
# TABS
# ============================================================

tabs = st.tabs(
    [
        "📋 Strategy",
        "👤 My Team",
        "🔄 Transfers",
        "🩺 Hold / Sell",
        "🧢 Captain",
        "📊 Rankings",
        "📅 Fixtures",
        "💊 Chips",
        "🕵️ Mini-League",
        "🏆 Best XI",
        "🏆 Elite Managers",
        "📺 Creator AI",
        "💬 AI Assistant",
    ]
)


# ============================================================
# STRATEGY
# ============================================================

with tabs[0]:

    st.header(
        f"📋 GW{next_gw} Strategy"
    )

    if not my_squad:

        st.info(
            "Enter your FPL Team ID "
            "in the sidebar."
        )

    else:

        history = team_data.get(
            "entry_history",
            {},
        )

        bank = (
            num(
                history.get("bank")
            )
            / 10
        )

        brief = strategy_briefing(
            my_squad,
            bank,
            free_transfers,
        )

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Transfer",
            brief["transfer"][
                "decision"
            ],
        )

        c2.metric(
            "Captain",
            (
                brief["captains"][0]["name"]
                if brief["captains"]
                else "—"
            ),
        )

        c3.metric(
            "Avg FDR",
            f"{brief['avg_fdr']:.2f}",
        )

        c4.metric(
            "Team Form",
            f"{brief['avg_team_form']:.1f}/10",
        )

        st.write(
            f"**Transfer assessment:** "
            f"{brief['transfer']['reason']}"
        )

        if brief["injuries"]:

            st.warning(
                "🚨 Flags: "
                + ", ".join(
                    p["name"]
                    for p in brief[
                        "injuries"
                    ]
                )
            )

        if brief["blanks"]:

            st.error(
                "⚠️ Blank GW: "
                + ", ".join(
                    p["name"]
                    for p in brief[
                        "blanks"
                    ]
                )
            )

        if brief["doubles"]:

            st.success(
                "⚡ Double GW: "
                + ", ".join(
                    p["name"]
                    for p in brief[
                        "doubles"
                    ]
                )
            )

        st.subheader(
            "💊 Chip Outlook"
        )

        st.write(
            brief["chip"]
        )

        st.subheader(
            "📈 Team Form"
        )

        form_rows = []

        seen = set()

        for p in my_squad:

            if p["team_id"] in seen:
                continue

            seen.add(
                p["team_id"]
            )

            form_rows.append(
                {
                    "Club": p["team"],
                    "Recent": " ".join(
                        p[
                            "team_form_results"
                        ]
                    ),
                    "Form": round(
                        p[
                            "team_form_score"
                        ],
                        1,
                    ),
                    "Goals": p[
                        "team_goals_for"
                    ],
                    "Conceded": p[
                        "team_goals_against"
                    ],
                    "Clean Sheets": p[
                        "team_clean_sheets"
                    ],
                    "Verdict": team_form_label(
                        p[
                            "team_form_score"
                        ]
                    ),
                }
            )

        st.dataframe(
            pd.DataFrame(
                form_rows
            ),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# MY TEAM
# ============================================================

with tabs[1]:

    st.header(
        "👤 My FPL Team"
    )

    if not my_squad:

        st.info(
            "Enter your FPL Team ID."
        )

    else:

        history = team_data.get(
            "entry_history",
            {},
        )

        bank = (
            num(
                history.get("bank")
            )
            / 10
        )

        value = (
            num(
                history.get("value")
            )
            / 10
        )

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "GW Points",
            history.get(
                "points",
                0,
            ),
        )

        c2.metric(
            "Total Points",
            history.get(
                "total_points",
                0,
            ),
        )

        c3.metric(
            "Team Value",
            f"£{value:.1f}m",
        )

        c4.metric(
            "Bank",
            f"£{bank:.1f}m",
        )

        rows = [
            {
                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Role": (
                    "👑 Captain"
                    if p["is_captain"]
                    else (
                        "VC"
                        if p["is_vice"]
                        else ""
                    )
                ),
                "Price": (
                    f"£{p['price']:.1f}m"
                ),
                "Points": p["points"],
                "PPG": round(
                    p["ppg"],
                    1,
                ),
                "Form": round(
                    p["form"],
                    1,
                ),
                "Team Form": round(
                    p[
                        "team_form_score"
                    ],
                    1,
                ),
                "xGI/90": round(
                    p["xgi90"],
                    2,
                ),
                "FDR": round(
                    p["fdr"],
                    1,
                ),
                "Status": player_status(
                    p
                ),
            }
            for p in my_squad
        ]

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# TRANSFERS
# ============================================================

with tabs[2]:

    st.header(
        "🔄 Transfer Recommendations"
    )

    if not my_squad:

        st.info(
            "Load your squad first."
        )

    else:

        history = team_data.get(
            "entry_history",
            {},
        )

        bank = (
            num(
                history.get("bank")
            )
            / 10
        )

        evaluation = transfer_decision(
            my_squad,
            bank,
            free_transfers,
        )

        st.info(
            f"**{evaluation['decision']}** — "
            f"{evaluation['reason']}"
        )

        for i, suggestion in enumerate(
            evaluation[
                "suggestions"
            ][:5],
            1,
        ):

            out_p = suggestion[
                "out"
            ]

            in_p = suggestion[
                "in"
            ]

            diff = suggestion[
                "cost_difference"
            ]

            if diff > 0:
                money = (
                    f"+£{diff:.1f}m"
                )
            elif diff < 0:
                money = (
                    f"frees "
                    f"£{abs(diff):.1f}m"
                )
            else:
                money = "same price"

            st.markdown(
                f"### {i}. "
                f"{out_p['name']} ➡️ "
                f"{in_p['name']} "
                f"({money})"
            )

            c1, c2, c3, c4 = st.columns(4)

            c1.metric(
                "Out xGI/90",
                f"{out_p['xgi90']:.2f}",
            )

            c2.metric(
                "In xGI/90",
                f"{in_p['xgi90']:.2f}",
            )

            c3.metric(
                "Team Form",
                f"{in_p['team_form_score']:.1f}",
            )

            c4.metric(
                "Net Projection",
                f"{suggestion['net_gain']:+.1f}",
            )

            st.write(
                f"**Fixtures:** "
                f"{in_p['fixtures']}"
            )

            st.write(
                f"**Price trend:** "
                f"{price_momentum_flag(in_p)}"
            )


# ============================================================
# HOLD / SELL
# ============================================================

with tabs[3]:

    st.header(
        "🩺 Hold / Sell"
    )

    if not my_squad:

        st.info(
            "Load your squad first."
        )

    else:

        rows = [
            {
                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Points": p["points"],
                "Form": round(
                    p["form"],
                    1,
                ),
                "Team Form": round(
                    p[
                        "team_form_score"
                    ],
                    1,
                ),
                "xGI/90": round(
                    p["xgi90"],
                    2,
                ),
                "FDR": round(
                    p["fdr"],
                    1,
                ),
                "Trend": price_momentum_flag(
                    p
                ),
                "Action": hold_sell(
                    p
                ),
            }
            for p in my_squad
        ]

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# CAPTAIN
# ============================================================

with tabs[4]:

    st.header(
        "🧢 Captaincy"
    )

    if not my_squad:

        st.info(
            "Load your squad first."
        )

    else:

        caps = captain_recommendations(
            my_squad
        )

        if caps:

            captain = caps[0]

            vice = (
                caps[1]
                if len(caps) > 1
                else None
            )

            c1, c2 = st.columns(2)

            with c1:

                st.success(
                    "👑 CAPTAIN: "
                    f"**{captain['name']}**"
                )

                st.write(
                    f"{captain['team']} | "
                    f"xGI/90 "
                    f"{captain['xgi90']:.2f} | "
                    f"Form "
                    f"{captain['form']:.1f} | "
                    f"Team form "
                    f"{captain['team_form_score']:.1f}"
                )

                st.write(
                    f"Fixtures: "
                    f"{captain['fixtures']}"
                )

            with c2:

                if vice:

                    st.info(
                        "🥈 VICE: "
                        f"**{vice['name']}**"
                    )

                    st.write(
                        f"{vice['team']} | "
                        f"xGI/90 "
                        f"{vice['xgi90']:.2f} | "
                        f"Form "
                        f"{vice['form']:.1f}"
                    )

            st.subheader(
                "Top Captain Candidates"
            )

            rows = [
                {
                    "Rank": i,
                    "Player": p["name"],
                    "Club": p["team"],
                    "xGI/90": round(
                        p["xgi90"],
                        2,
                    ),
                    "Form": round(
                        p["form"],
                        1,
                    ),
                    "Team Form": round(
                        p[
                            "team_form_score"
                        ],
                        1,
                    ),
                    "PPG": round(
                        p["ppg"],
                        1,
                    ),
                    "FDR": round(
                        p["fdr"],
                        1,
                    ),
                }
                for i, p in enumerate(
                    caps,
                    1,
                )
            ]

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )


# ============================================================
# RANKINGS
# ============================================================

with tabs[5]:

    st.header(
        "📊 Player Rankings"
    )

    sort_by = st.radio(
        "Sort by",
        [
            "Model Score",
            "xGI/90",
            "FPL Points",
            "Form",
            "PPG",
            "4-GW Projection",
            "Team Form",
        ],
        horizontal=True,
    )

    position = st.selectbox(
        "Position",
        [
            "ALL",
            "GK",
            "DEF",
            "MID",
            "FWD",
        ],
    )

    pool = [
        p
        for p in players
        if (
            position == "ALL"
            or p["position"]
            == position
        )
    ]

    if sort_by == "Model Score":

        pool.sort(
            key=lambda p:
            p["blended"],
            reverse=True,
        )

    elif sort_by == "xGI/90":

        pool.sort(
            key=lambda p:
            p["xgi90"],
            reverse=True,
        )

    elif sort_by == "FPL Points":

        pool.sort(
            key=lambda p:
            p["points"],
            reverse=True,
        )

    elif sort_by == "Form":

        pool.sort(
            key=lambda p:
            p["form"],
            reverse=True,
        )

    elif sort_by == "PPG":

        pool.sort(
            key=lambda p:
            p["ppg"],
            reverse=True,
        )

    elif sort_by == "Team Form":

        pool.sort(
            key=lambda p:
            p[
                "team_form_score"
            ],
            reverse=True,
        )

    else:

        pool.sort(
            key=lambda p:
            p[
                "projection_4gw"
            ],
            reverse=True,
        )

    rows = [
        {
            "Player": p["name"],
            "Club": p["team"],
            "Pos": p["position"],
            "Price": (
                f"£{p['price']:.1f}m"
            ),
            "Points": p["points"],
            "xGI/90": round(
                p["xgi90"],
                2,
            ),
            "Form": round(
                p["form"],
                1,
            ),
            "Team Form": round(
                p[
                    "team_form_score"
                ],
                1,
            ),
            "PPG": round(
                p["ppg"],
                1,
            ),
            "FDR": round(
                p["fdr"],
                1,
            ),
            "4GW Projection": round(
                p[
                    "projection_4gw"
                ],
                1,
            ),
            "Model": round(
                p["blended"],
                1,
            ),
        }
        for p in pool[:75]
    ]

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# FIXTURES
# ============================================================

with tabs[6]:

    st.header(
        "📅 Fixtures & Team Form"
    )

    if my_squad:

        st.subheader(
            "Your Squad"
        )

        rows = [
            {
                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Team Form": round(
                    p[
                        "team_form_score"
                    ],
                    1,
                ),
                "Avg FDR": round(
                    p["fdr"],
                    2,
                ),
                "Upcoming": p[
                    "fixtures"
                ],
            }
            for p in my_squad
        ]

        st.dataframe(
            pd.DataFrame(rows).sort_values(
                "Avg FDR"
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.subheader(
        "Team Form Across the League"
    )

    team_rows = []

    for team_id, data in team_form.items():

        team_rows.append(
            {
                "Club": team_names.get(
                    team_id,
                    "?",
                ),
                "Recent": " ".join(
                    data.get(
                        "recent_results",
                        [],
                    )
                ),
                "Form": round(
                    data.get(
                        "form_score",
                        5,
                    ),
                    1,
                ),
                "Goals For": data.get(
                    "goals_for",
                    0,
                ),
                "Goals Against": data.get(
                    "goals_against",
                    0,
                ),
                "Clean Sheets": data.get(
                    "clean_sheets",
                    0,
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(
            team_rows
        ).sort_values(
            "Form",
            ascending=False,
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader(
        "Fixture Swings"
    )

    improving = []
    worsening = []

    for team_id in teams:

        near = average_fdr(
            fixture_map,
            team_id,
            2,
        )

        later = average_fdr(
            fixture_map,
            team_id,
            5,
        )

        if later < near - 0.2:

            improving.append(
                (
                    team_names.get(
                        team_id,
                        "?",
                    ),
                    near,
                    later,
                )
            )

        elif later > near + 0.2:

            worsening.append(
                (
                    team_names.get(
                        team_id,
                        "?",
                    ),
                    near,
                    later,
                )
            )

    improving.sort(
        key=lambda x: x[2]
    )

    worsening.sort(
        key=lambda x: x[2],
        reverse=True,
    )

    c1, c2 = st.columns(2)

    with c1:

        st.markdown(
            "### 🟢 Getting Easier"
        )

        for name, near, later in improving:

            st.write(
                f"**{name}** — "
                f"{near:.1f} ➜ "
                f"{later:.1f}"
            )

    with c2:

        st.markdown(
            "### 🔴 Getting Tougher"
        )

        for name, near, later in worsening:

            st.write(
                f"**{name}** — "
                f"{near:.1f} ➜ "
                f"{later:.1f}"
            )


# ============================================================
# CHIPS
# ============================================================

with tabs[7]:

    st.header(
        "💊 Chip Strategy"
    )

    if not my_squad:

        st.info(
            "Load your squad first."
        )

    else:

        history = team_data.get(
            "entry_history",
            {},
        )

        bank = (
            num(
                history.get("bank")
            )
            / 10
        )

        brief = strategy_briefing(
            my_squad,
            bank,
            free_transfers,
        )

        st.info(
            brief["chip"]
        )

        bench = bench_boost_value(
            my_squad
        )

        if bench:

            rows, total = bench

            st.subheader(
                f"🪑 Current GW{current_gw} "
                "Bench Check"
            )

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )

            st.metric(
                "Bench Points",
                total,
            )


# ============================================================
# MINI LEAGUE
# ============================================================

with tabs[8]:

    st.header(
        "🕵️ Mini-League"
    )

    if not league_id_input.strip():

        st.info(
            "Enter your Mini-League ID "
            "in the sidebar."
        )

    else:

        try:

            league = get_league(
                safe_int(
                    league_id_input.strip()
                )
            )

            standings = (
                league.get(
                    "standings",
                    {},
                ).get(
                    "results",
                    [],
                )
            )[:20]

            rows = [
                {
                    "Rank": row.get(
                        "rank"
                    ),
                    "Manager": row.get(
                        "player_name"
                    ),
                    "Team": row.get(
                        "entry_name"
                    ),
                    "Total": row.get(
                        "total"
                    ),
                    "GW": row.get(
                        "event_total"
                    ),
                }
                for row in standings
            ]

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )

        except Exception as exc:

            st.error(
                "Couldn't retrieve mini-league."
            )

            st.caption(
                str(exc)
            )


# ============================================================
# BEST XI
# ============================================================

with tabs[9]:

    st.header(
        "🏆 Best Starting XI"
    )

    st.caption(
        "This can ONLY select players "
        "from your current 15-man squad."
    )

    if not my_squad:

        st.info(
            "Load your squad first."
        )

    else:

        result = best_xi(
            my_squad
        )

        if result:

            st.success(
                f"Optimal formation: "
                f"**{result['formation']}** "
                f"| Model score: "
                f"{result['score']:.1f}"
            )

            rows = [
                {
                    "Player": p["name"],
                    "Club": p["team"],
                    "Pos": p["position"],
                    "Team Form": round(
                        p[
                            "team_form_score"
                        ],
                        1,
                    ),
                    "xGI/90": round(
                        p["xgi90"],
                        2,
                    ),
                    "Model": round(
                        p["blended"],
                        1,
                    ),
                }
                for p in result[
                    "lineup"
                ]
            ]

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )

            st.subheader(
                "🪑 Bench"
            )

            bench_rows = [
                {
                    "Player": p["name"],
                    "Club": p["team"],
                    "Pos": p["position"],
                    "Model": round(
                        p["blended"],
                        1,
                    ),
                }
                for p in result[
                    "bench"
                ]
            ]

            st.dataframe(
                pd.DataFrame(
                    bench_rows
                ),
                use_container_width=True,
                hide_index=True,
            )

        else:

            st.error(
                "No valid formation "
                "could be created."
            )


# ============================================================
# ELITE MANAGERS
# ============================================================

with tabs[10]:

    st.header(
        "🏆 Elite Manager Tracker"
    )

    st.caption(
        "Elite manager data is loaded only "
        "when this tab is opened."
    )

    st.info(
        "The app will never guess a Team ID. "
        "Unknown managers remain unverified."
    )

    # IMPORTANT:
    # This is intentionally inside the tab.
    # It prevents these requests happening
    # every time the application starts.

    with st.spinner(
        "Checking elite managers..."
    ):

        elite_rows = [
            load_elite_manager(
                name,
                meta["entry_id"],
            )
            for name, meta
            in ELITE_MANAGERS.items()
        ]

    verification_rows = []

    for name, meta in ELITE_MANAGERS.items():

        entry_id = meta["entry_id"]

        if entry_id:

            info, result = verify_team_id(
                entry_id
            )

            verification_rows.append(
                {
                    "Manager": name,
                    "HOF Rank": meta[
                        "hall_of_fame"
                    ],
                    "Team ID": entry_id,
                    "Verification": (
                        "✅ VERIFIED"
                        if info
                        else "❌ FAILED"
                    ),
                    "FPL Team": (
                        info.get(
                            "name",
                            "—",
                        )
                        if info
                        else "—"
                    ),
                }
            )

        else:

            verification_rows.append(
                {
                    "Manager": name,
                    "HOF Rank": meta[
                        "hall_of_fame"
                    ],
                    "Team ID": "Not known",
                    "Verification": (
                        "🟠 NOT VERIFIED"
                    ),
                    "FPL Team": "—",
                }
            )

    st.dataframe(
        pd.DataFrame(
            verification_rows
        ),
        use_container_width=True,
        hide_index=True,
    )

    overview = []

    for row in elite_rows:

        overview.append(
            {
                "Manager": row["name"],
                "HOF": ELITE_MANAGERS[
                    row["name"]
                ][
                    "hall_of_fame"
                ],
                "Status": (
                    "🟢 Connected"
                    if row.get(
                        "status"
                    ) == "OK"
                    else (
                        "🟠 ID not verified"
                        if row.get(
                            "status"
                        )
                        == "UNVERIFIED"
                        else "🔴 Failed"
                    )
                ),
                "GW Points": row.get(
                    "gw_points",
                    "—",
                ),
                "Overall Rank": row.get(
                    "overall_rank",
                    "—",
                ),
                "Captain": row.get(
                    "captain",
                    "—",
                ),
                "Transfers": len(
                    row.get(
                        "transfers",
                        [],
                    )
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(
            overview
        ),
        use_container_width=True,
        hide_index=True,
    )

    consensus, captains, transfers, valid = (
        elite_consensus(
            elite_rows
        )
    )

    if not valid:

        st.warning(
            "No elite teams are currently connected."
        )

    else:

        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Managers Connected",
            f"{len(valid)}/5",
        )

        c2.metric(
            "Captain Leader",
            (
                captains[0]["Captain"]
                if captains
                else "—"
            ),
        )

        c3.metric(
            "Captain Consensus",
            (
                captains[0]["Managers"]
                if captains
                else "—"
            ),
        )

        st.subheader(
            "🔥 Most-Owned Elite Players"
        )

        if consensus:

            st.dataframe(
                pd.DataFrame(
                    consensus[:30]
                ),
                use_container_width=True,
                hide_index=True,
            )

        left, right = st.columns(2)

        with left:

            st.subheader(
                "🧢 Captain Consensus"
            )

            if captains:

                st.dataframe(
                    pd.DataFrame(
                        captains
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

            else:

                st.info(
                    "No captain data."
                )

        with right:

            st.subheader(
                "🔄 Elite Transfers"
            )

            if transfers:

                st.dataframe(
                    pd.DataFrame(
                        transfers
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

            else:

                st.info(
                    "No current-GW "
                    "transfers recorded."
                )

        st.divider()

        st.subheader(
            "🆚 Elite Managers vs Your Team"
        )

        if not my_squad:

            st.info(
                "Load your team in the sidebar."
            )

        else:

            owned = {
                p["id"]
                for p in my_squad
            }

            threshold = max(
                2,
                (
                    len(valid)
                    + 1
                )
                // 2,
            )

            comparison = []

            for row in consensus:

                count = safe_int(
                    str(
                        row["Elite"]
                    ).split(
                        "/"
                    )[0]
                )

                if count < threshold:
                    continue

                player = player_by_id.get(
                    next(
                        (
                            pid
                            for pid, p
                            in player_by_id.items()
                            if p["name"]
                            == row["Player"]
                        ),
                        None,
                    )
                )

                if not player:
                    continue

                comparison.append(
                    {
                        "Player": player[
                            "name"
                        ],
                        "Elite": row[
                            "Elite"
                        ],
                        "You Own": (
                            "✅ Yes"
                            if player[
                                "id"
                            ]
                            in owned
                            else "❌ No"
                        ),
                        "Model": row[
                            "Model Score"
                        ],
                        "Team Form": round(
                            player[
                                "team_form_score"
                            ],
                            1,
                        ),
                        "FDR": row[
                            "FDR"
                        ],
                    }
                )

            if comparison:

                st.dataframe(
                    pd.DataFrame(
                        comparison
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

            else:

                st.info(
                    "No major consensus differences."
                )

        st.subheader(
            "👤 Individual Elite Squads"
        )

        for row in valid:

            with st.expander(
                f"{row['name']} — "
                f"{row.get('entry_name', '')}"
            ):

                squad_rows = [
                    {
                        "Player": p["name"],
                        "Club": p["team"],
                        "Pos": p["position"],
                        "Price": (
                            f"£{p['price']:.1f}m"
                        ),
                        "Captain": (
                            "👑"
                            if p["name"]
                            == row.get(
                                "captain"
                            )
                            else ""
                        ),
                        "Model": round(
                            blended_score(p),
                            1,
                        ),
                    }
                    for p in row[
                        "squad"
                    ]
                ]

                st.dataframe(
                    pd.DataFrame(
                        squad_rows
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

                if row.get(
                    "transfers"
                ):

                    st.write(
                        "**Current GW "
                        "transfers:** "
                        + ", ".join(
                            f"{t['out']} "
                            f"→ {t['in']}"
                            for t in row[
                                "transfers"
                            ]
                        )
                    )


# ============================================================
# CREATOR AI
# ============================================================

with tabs[11]:

    st.header(
        "📺 Creator Intelligence"
    )

    st.caption(
        "Analyse a YouTube FPL video against "
        "your squad and the available data."
    )

    # Elite data is loaded ONLY here if needed.
    elite_rows_for_creator = []

    st.subheader(
        "🎙️ Monitored Creator Channels"
    )

    cols = st.columns(5)

    for i, (
        name,
        url,
    ) in enumerate(
        CREATOR_CHANNELS.items()
    ):

        cols[i].markdown(
            f"**[{name}]({url})**"
        )

    st.divider()

    video_url = st.text_input(
        "YouTube Video URL or ID",
        placeholder=(
            "https://www.youtube.com/watch?v=XXXXXXXXXXX"
        ),
        key="creator_video_url",
    )

    if st.button(
        "🧠 Analyse YouTube Video",
        type="primary",
        key="creator_analyse",
    ):

        if not video_url.strip():

            st.warning(
                "Paste a YouTube video URL."
            )

        elif not get_secret(
            "GEMINI_API_KEY"
        ):

            st.error(
                "GEMINI_API_KEY is missing."
            )

        else:

            with st.spinner(
                "Getting transcript..."
            ):

                transcript, error = (
                    fetch_youtube_transcript(
                        video_url
                    )
                )

            if error:

                st.error(error)

            else:

                # Load elite data only when
                # Creator AI is actually used.
                with st.spinner(
                    "Checking elite consensus..."
                ):

                    elite_rows_for_creator = [
                        load_elite_manager(
                            name,
                            meta["entry_id"],
                        )
                        for name, meta
                        in ELITE_MANAGERS.items()
                    ]

                transcript = transcript[
                    :25000
                ]

                top_players = sorted(
                    players,
                    key=lambda p:
                    p["xgi90"],
                    reverse=True,
                )[:25]

                player_data = "\n".join(
                    f"- {p['name']} | "
                    f"{p['team']} | "
                    f"{p['position']} | "
                    f"£{p['price']:.1f}m | "
                    f"xGI/90 "
                    f"{p['xgi90']:.2f} | "
                    f"Form "
                    f"{p['form']:.1f} | "
                    f"Team form "
                    f"{p['team_form_score']:.1f} | "
                    f"PPG "
                    f"{p['ppg']:.1f} | "
                    f"FDR "
                    f"{p['fdr']:.1f} | "
                    f"4GW "
                    f"{p['projection_4gw']:.1f}"
                    for p in top_players
                )

                prompt = f"""
You are the FPL Assistant Manager's
Creator Intelligence engine.

CURRENT GAMEWEEK
GW{current_gw} -> GW{next_gw}

MANAGER'S SQUAD
{creator_context(my_squad)}

CONNECTED ELITE MANAGERS
{elite_context(elite_rows_for_creator)}

TOP CURRENT FPL DATA
{player_data}

YOUTUBE TRANSCRIPT
{transcript}

Do the following:

1. Identify the creator if possible.
2. Summarise their main recommendations.
3. Extract players they recommend buying,
   selling, holding or captaining.
4. Compare recommendations against
   the supplied FPL data.
5. Compare them against elite consensus.
6. Consider team recent form.
7. Identify agreements and disagreements.
8. Give advice specifically for the user's
   actual squad.
9. Do not invent statistics.
10. Clearly distinguish what the creator said
    from what the model concludes.

Be concise and practical.
"""

                try:

                    result, model = (
                        gemini_generate(
                            prompt,
                            (
                                "You are an elite "
                                "objective FPL analyst. "
                                "Never invent data."
                            ),
                        )
                    )

                    st.success(
                        f"Analysis completed "
                        f"with {model}."
                    )

                    st.markdown(
                        result
                    )

                except Exception as exc:

                    st.error(
                        "Creator analysis failed."
                    )

                    st.code(
                        str(exc)
                    )


# ============================================================
# AI ASSISTANT
# ============================================================

with tabs[12]:

    st.header(
        "💬 FPL AI Assistant"
    )

    api_key_present = bool(
        get_secret(
            "GEMINI_API_KEY"
        )
    )

    if not api_key_present:

        st.warning(
            "GEMINI_API_KEY is missing "
            "from Streamlit Secrets."
        )

    else:

        assistant_pin = str(
            get_secret(
                "AI_ASSISTANT_PIN",
                "2325",
            )
        )

        pin = st.text_input(
            "Manager PIN",
            type="password",
            key="assistant_pin",
        )

        if pin != assistant_pin:

            st.info(
                "🔒 Enter the Manager PIN "
                "to unlock the assistant."
            )

        else:

            st.success(
                "🔓 Assistant unlocked."
            )

            if "messages" not in st.session_state:

                st.session_state.messages = []

            for message in (
                st.session_state.messages
            ):

                with st.chat_message(
                    message["role"]
                ):

                    st.markdown(
                        message["content"]
                    )

            prompt = st.chat_input(
                "Ask about transfers, "
                "captaincy, fixtures..."
            )

            if prompt:

                st.session_state.messages.append(
                    {
                        "role": "user",
                        "content": prompt,
                    }
                )

                with st.chat_message(
                    "user"
                ):

                    st.markdown(
                        prompt
                    )

                history = (
                    team_data.get(
                        "entry_history",
                        {},
                    )
                    if team_data
                    else {}
                )

                bank = (
                    num(
                        history.get(
                            "bank"
                        )
                    )
                    / 10
                    if history
                    else 0
                )

                squad_text = creator_context(
                    my_squad
                )

                transfer_summary = (
                    "No squad loaded."
                )

                if my_squad:

                    transfer_summary = (
                        transfer_decision(
                            my_squad,
                            bank,
                            free_transfers,
                        )[
                            "reason"
                        ]
                    )

                assistant_prompt = f"""
FPL current GW: {current_gw}
Planning GW: {next_gw}
Free transfers: {free_transfers}
Bank: £{bank:.1f}m

MANAGER SQUAD
{squad_text}

TRANSFER MODEL
{transfer_summary}

USER QUESTION
{prompt}

Give practical FPL advice.

Rules:
- Prioritise the actual squad.
- Use supplied data.
- Consider fixtures.
- Consider player form.
- Consider team form.
- Consider xGI/90.
- Consider availability.
- Consider projected output.
- Do not invent statistics.
- Be decisive when evidence supports it.
"""

                with st.chat_message(
                    "assistant"
                ):

                    with st.spinner(
                        "Analysing your squad..."
                    ):

                        try:

                            answer, model = (
                                gemini_generate(
                                    assistant_prompt,
                                    (
                                        "You are an "
                                        "elite FPL "
                                        "strategist. "
                                        "Be practical, "
                                        "data-led and "
                                        "honest."
                                    ),
                                )
                            )

                            st.markdown(
                                answer
                            )

                            st.caption(
                                f"Model: {model}"
                            )

                            st.session_state.messages.append(
                                {
                                    "role": "assistant",
                                    "content": answer,
                                }
                            )

                        except Exception as exc:

                            st.error(
                                "AI Assistant failed."
                            )

                            st.code(
                                str(exc)
                            )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "⚽ FPL Assistant Manager — "
    "Official FPL data + team form + "
    "fixture analysis + elite manager "
    "consensus + Creator Intelligence."
)
