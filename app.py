from collections import defaultdict
import re
import time

import pandas as pd
import requests
import streamlit as st

from google import genai
from google.genai import types

from youtube_transcript_api import YouTubeTranscriptApi


# ============================================================
# FPL ASSISTANT MANAGER
# ULTIMATE DECISION ENGINE
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)


# ============================================================
# CONFIGURATION
# ============================================================

API = "https://fantasy.premierleague.com/api"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/140 Safari/537.36"
    ),
    "Accept": "application/json",
}

FIXTURE_HORIZON = 5
HIT_PROJECTION_WEEKS = 4

SQUAD_BUDGET = 1000
MAX_PER_CLUB = 3
TRANSFER_HIT = 4

# Gemini model priority.
# The first one matches the model suggested by the error
# you received. If unavailable, the app automatically tries
# the next model.
GEMINI_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash",
]

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

CREATOR_CHANNELS = {
    "FPL Harry": "https://www.youtube.com/@FPLHarry",
    "Let's Talk FPL (Andy)": "https://www.youtube.com/@LetsTalkFPL",
    "FPL Focal (Oscar)": "https://www.youtube.com/@FPLFocal",
    "FPL Mate (Dan)": "https://www.youtube.com/@FPLMate",
    "Planet FPL (James & Suj)": "https://www.youtube.com/@PlanetFPL",
}


# ============================================================
# API HELPERS
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def api_get(url):
    """
    Generic FPL API GET request with retry handling.
    """

    last_error = None

    for attempt in range(3):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=20
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:
            last_error = exc

            if attempt < 2:
                time.sleep(1.2 * (attempt + 1))

    raise RuntimeError(
        f"FPL API request failed: {last_error}"
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_info(entry_id):
    return api_get(
        f"{API}/entry/{entry_id}/"
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_picks(entry_id, gameweek):
    return api_get(
        f"{API}/entry/{entry_id}/event/{gameweek}/picks/"
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_league(league_id):
    return api_get(
        f"{API}/leagues-classic/{league_id}/standings/"
    )


@st.cache_data(ttl=600, show_spinner=False)
def get_team_history(entry_id):
    return api_get(
        f"{API}/entry/{entry_id}/history/"
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_live_gw(gameweek):

    data = api_get(
        f"{API}/event/{gameweek}/live/"
    )

    return {
        element["id"]: element.get("stats", {}).get("total_points", 0)
        for element in data.get("elements", [])
    }


# ============================================================
# GEMINI ENGINE
# ============================================================

def get_gemini_client():

    if "GEMINI_API_KEY" not in st.secrets:
        raise RuntimeError(
            "GEMINI_API_KEY is missing from Streamlit Secrets."
        )

    api_key = st.secrets["GEMINI_API_KEY"]

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is empty."
        )

    return genai.Client(
        api_key=api_key
    )


def gemini_generate(
    prompt,
    system_instruction,
    temperature=0.3
):
    """
    Central Gemini function.

    Automatically tries the configured Gemini models
    in order, so a model retirement/change doesn't
    immediately break the application.
    """

    client = get_gemini_client()

    errors = []

    for model_name in GEMINI_MODELS:

        try:

            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=temperature,
                ),
            )

            if response and response.text:
                return response.text, model_name, None

        except Exception as exc:

            errors.append(
                f"{model_name}: {exc}"
            )

    return (
        None,
        None,
        "All Gemini models failed:\n\n"
        + "\n\n".join(errors)
    )


# ============================================================
# PURE DATA HELPERS
# ============================================================

def num(value, default=0.0):

    try:

        if value is None:
            return default

        return float(value)

    except Exception:

        return default


def availability_factor(player):

    chance = player.get("chance", 100)

    if chance >= 90:
        return 1.0

    if chance >= 75:
        return 0.85

    if chance >= 50:
        return 0.60

    if chance > 0:
        return 0.30

    return 0.0


def fixture_count(
    fixture_map,
    team_id,
    gw
):

    return len(
        [
            f
            for f in fixture_map.get(team_id, [])
            if f["gw"] == gw
        ]
    )


def average_fdr(
    fixture_map,
    team_id,
    weeks=None
):

    games = fixture_map.get(team_id, [])

    if weeks:
        games = games[:weeks]

    if not games:
        return 3.0

    return sum(
        f["difficulty"]
        for f in games
    ) / len(games)


def fixture_text(
    fixture_map,
    team_names,
    team_id,
    number=5
):

    games = sorted(
        fixture_map.get(team_id, []),
        key=lambda x: (
            x["gw"],
            not x["home"]
        )
    )[:number]

    output = []

    for f in games:

        opponent = team_names.get(
            f["opponent"],
            "?"
        )

        location = (
            "H"
            if f["home"]
            else "A"
        )

        output.append(
            f"GW{f['gw']} "
            f"{opponent} "
            f"({location}) "
            f"[{f['difficulty']}]"
        )

    return (
        " | ".join(output)
        if output
        else "No fixtures"
    )


def price_momentum_flag(player):

    net = player.get(
        "net_transfers",
        0
    )

    ownership = max(
        player.get("ownership", 0.1),
        0.1
    )

    ratio = net / (
        ownership * 1000
    )

    if ratio > 0.4:
        return "📈 Likely rise"

    if ratio < -0.4:
        return "📉 Likely fall"

    return "— Stable"


# ============================================================
# DECISION ENGINE
# ============================================================

def calc_blended_score(player):

    ppg = min(
        player["ppg"] * 1.5,
        10
    )

    form = min(
        player["form"] * 1.2,
        9
    )

    expected = min(
        player["ep_next"] * 2.5,
        16
    )

    fixture = max(
        0,
        (3.2 - player["fdr"]) * 3
    )

    availability = (
        availability_factor(player) * 5
    )

    attacking = min(
        player["xgi90"] * 8,
        12
    )

    defensive = 0

    if player["position"] in (
        "GK",
        "DEF"
    ):

        defensive = max(
            0,
            (1.4 - player["xgc90"]) * 4
        )

    dgw_bonus = (
        7
        if player["next_gw_fixtures"] >= 2
        else 0
    )

    bgw_penalty = (
        8
        if player["next_gw_fixtures"] == 0
        else 0
    )

    ownership_bonus = (
        2
        if (
            player["ownership"] < 5
            and player["xgi90"] >= 0.25
        )
        else 0
    )

    score = (
        ppg
        + form
        + expected
        + fixture
        + availability
        + attacking
        + defensive
        + dgw_bonus
        + ownership_bonus
        - bgw_penalty
    )

    return round(
        score,
        2
    )


def calc_multi_gw_projection(
    player,
    fixture_map,
    weeks=HIT_PROJECTION_WEEKS
):

    games = sorted(
        fixture_map.get(
            player["team_id"],
            []
        ),
        key=lambda x: x["gw"]
    )[:weeks]

    if not games:
        return round(
            player["ep_next"],
            1
        )

    availability = (
        availability_factor(player)
    )

    base = (
        player["ep_next"] * 0.55
        + player["ppg"] * 0.20
        + player["xgi90"] * 2.0
    )

    total = 0.0

    for fixture in games:

        difficulty_multiplier = (
            1.0
            + (
                (3 - fixture["difficulty"])
                * 0.08
            )
        )

        total += (
            base
            * difficulty_multiplier
            * availability
        )

    return round(
        total,
        1
    )


# ============================================================
# FPL DATA LOADER
# ============================================================

@st.cache_data(
    ttl=900,
    show_spinner="Loading FPL data..."
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
        t["id"]:
        t.get(
            "short_name",
            "?"
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
        None
    )

    next_event = next(
        (
            e
            for e in events
            if e.get("is_next")
        ),
        None
    )

    if current_event:

        current_gw = current_event["id"]

    elif next_event:

        current_gw = max(
            1,
            next_event["id"] - 1
        )

    else:

        current_gw = 1

    if next_event:

        next_gw = next_event["id"]

    else:

        next_gw = current_gw + 1

    # --------------------------------------------------------
    # FIXTURE MAP
    # --------------------------------------------------------

    fixture_map = defaultdict(list)

    max_gw = (
        next_gw
        + FIXTURE_HORIZON
        - 1
    )

    for fixture in fixtures_raw:

        gw = fixture.get(
            "event"
        )

        if gw is None:
            continue

        if gw < next_gw:
            continue

        if gw > max_gw:
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
                    "gw": gw,
                    "home": True,
                    "opponent": away,
                    "difficulty": fixture.get(
                        "team_h_difficulty",
                        3
                    ),
                }
            )

        if away:

            fixture_map[away].append(
                {
                    "gw": gw,
                    "home": False,
                    "opponent": home,
                    "difficulty": fixture.get(
                        "team_a_difficulty",
                        3
                    ),
                }
            )

    # --------------------------------------------------------
    # PLAYER DATA
    # --------------------------------------------------------

    players = []

    for p in raw_players:

        team_id = p["team"]

        chance = p.get(
            "chance_of_playing_next_round"
        )

        if chance is None:
            chance = 100

        transfers_in = p.get(
            "transfers_in_event",
            0
        )

        transfers_out = p.get(
            "transfers_out_event",
            0
        )

        player = {

            "id": p["id"],

            "name": p.get(
                "web_name",
                "?"
            ),

            "full_name": (
                f"{p.get('first_name', '')} "
                f"{p.get('second_name', '')}"
            ).strip(),

            "position": positions.get(
                p.get("element_type"),
                "?"
            ),

            "team_id": team_id,

            "team": team_names.get(
                team_id,
                "?"
            ),

            "price": (
                p.get("now_cost", 0)
                / 10
            ),

            "points": p.get(
                "total_points",
                0
            ),

            "ppg": num(
                p.get("points_per_game")
            ),

            "form": num(
                p.get("form")
            ),

            "minutes": p.get(
                "minutes",
                0
            ),

            "goals": p.get(
                "goals_scored",
                0
            ),

            "assists": p.get(
                "assists",
                0
            ),

            "clean_sheets": p.get(
                "clean_sheets",
                0
            ),

            "bonus": p.get(
                "bonus",
                0
            ),

            "bps": p.get(
                "bps",
                0
            ),

            "ep_next": num(
                p.get("ep_next")
            ),

            "ownership": num(
                p.get(
                    "selected_by_percent"
                )
            ),

            "chance": chance,

            "status": p.get(
                "status",
                "a"
            ),

            "news": p.get(
                "news",
                ""
            ),

            "xgi90": num(
                p.get(
                    "expected_goal_involvements_per_90"
                )
            ),

            "xgc90": num(
                p.get(
                    "expected_goals_conceded_per_90"
                )
            ),

            "ict": num(
                p.get("ict_index")
            ),

            "transfers_in": transfers_in,

            "transfers_out": transfers_out,

            "net_transfers": (
                transfers_in
                - transfers_out
            ),

            "price_change": p.get(
                "cost_change_event",
                0
            ),
        }

        player["fdr"] = average_fdr(
            fixture_map,
            team_id
        )

        player["next_gw_fixtures"] = (
            fixture_count(
                fixture_map,
                team_id,
                next_gw
            )
        )

        player["fixtures"] = (
            fixture_text(
                fixture_map,
                team_names,
                team_id,
                FIXTURE_HORIZON
            )
        )

        player["blended"] = (
            calc_blended_score(player)
        )

        player["projection_4gw"] = (
            calc_multi_gw_projection(
                player,
                fixture_map
            )
        )

        players.append(player)

    player_by_id = {
        p["id"]: p
        for p in players
    }

    return {
        "bootstrap": bootstrap,
        "teams": teams,
        "team_names": team_names,
        "current_gw": current_gw,
        "next_gw": next_gw,
        "fixture_map": dict(
            fixture_map
        ),
        "players": players,
        "player_by_id": player_by_id,
    }


# ============================================================
# LOAD DATA
# ============================================================

try:

    _data = load_fpl_data()

except Exception as exc:

    st.error(
        "⚠️ FPL API temporarily unavailable."
    )

    st.caption(
        f"Technical detail: {exc}"
    )

    st.stop()


teams = _data["teams"]
team_names = _data["team_names"]
current_gw = _data["current_gw"]
next_gw = _data["next_gw"]
fixture_map = _data["fixture_map"]
players = _data["players"]
player_by_id = _data["player_by_id"]


# ============================================================
# SIMPLE WRAPPERS
# ============================================================

def blended_score(player):
    return player["blended"]


def multi_gw_projection(
    player,
    weeks=HIT_PROJECTION_WEEKS
):

    return calc_multi_gw_projection(
        player,
        fixture_map,
        weeks
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

    if player["status"] != "a":
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


# ============================================================
# MY TEAM
# ============================================================

def load_my_team(entry_id):

    data = get_entry_picks(
        entry_id,
        current_gw
    )

    squad = []

    for pick in data.get(
        "picks",
        []
    ):

        p = player_by_id.get(
            pick["element"]
        )

        if not p:
            continue

        cp = p.copy()

        cp["is_captain"] = pick.get(
            "is_captain",
            False
        )

        cp["is_vice"] = pick.get(
            "is_vice_captain",
            False
        )

        cp["multiplier"] = pick.get(
            "multiplier",
            1
        )

        cp["position_slot"] = pick.get(
            "position",
            0
        )

        squad.append(cp)

    return data, squad


def squad_club_counts(
    squad,
    exclude_id=None
):

    counts = defaultdict(int)

    for p in squad:

        if (
            exclude_id is not None
            and p["id"] == exclude_id
        ):
            continue

        counts[p["team_id"]] += 1

    return counts


# ============================================================
# TRANSFER ENGINE
# ============================================================

def transfer_suggestions(
    squad,
    bank,
    free_transfers
):

    owned_ids = {
        p["id"]
        for p in squad
    }

    suggestions = []

    for outgoing in squad:

        club_counts = squad_club_counts(
            squad,
            exclude_id=outgoing["id"]
        )

        candidates = [

            p

            for p in players

            if (
                p["position"]
                == outgoing["position"]
            )

            and p["id"] not in owned_ids

            and p["status"] == "a"

            and p["chance"] > 0

        ]

        candidates.sort(
            key=blended_score,
            reverse=True
        )

        for incoming in candidates[:30]:

            available = (
                bank
                + outgoing["price"]
            )

            if incoming["price"] > available:
                continue

            projected_count = (
                club_counts[
                    incoming["team_id"]
                ]
                + 1
            )

            if projected_count > MAX_PER_CLUB:
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
        reverse=True
    )

    return suggestions[:10]


def transfer_decision(
    squad,
    bank,
    free_transfers
):

    suggestions = transfer_suggestions(
        squad,
        bank,
        free_transfers
    )

    if not suggestions:

        return {
            "decision": "ROLL",
            "reason": (
                "No transfer clears the "
                "minimum projected improvement "
                "threshold."
            ),
            "suggestions": [],
        }

    best = suggestions[0]

    if free_transfers > 0:

        if best["projected_gain"] >= 4.5:

            decision = "TRANSFER"

            reason = (
                f"{best['in']['name']} "
                f"projects +"
                f"{best['projected_gain']:.1f} "
                f"points over "
                f"{HIT_PROJECTION_WEEKS} GWs "
                f"compared with "
                f"{best['out']['name']}."
            )

        else:

            decision = "ROLL"

            reason = (
                "Minor upgrade available, "
                "but rolling the transfer "
                "provides greater tactical "
                "flexibility."
            )

    else:

        if best["net_gain"] >= 2:

            decision = "TAKE HIT"

            reason = (
                f"Transfer projects +"
                f"{best['projected_gain']:.1f} "
                f"points "
                f"(+{best['net_gain']:.1f} "
                f"net after -4 hit)."
            )

        else:

            decision = "ROLL"

            reason = (
                "Best available move does "
                "not justify paying a -4 "
                "hit penalty."
            )

    return {
        "decision": decision,
        "reason": reason,
        "suggestions": suggestions,
    }


# ============================================================
# CAPTAIN ENGINE
# ============================================================

def captain_score(player):

    score = blended_score(player)

    # Extra emphasis on explosive attackers.
    score += (
        player["xgi90"] * 4
    )

    # Expected points matter heavily for captaincy.
    score += (
        player["ep_next"] * 1.5
    )

    # DGW captaincy bonus.
    if player["next_gw_fixtures"] >= 2:
        score += 8

    # Blank penalty.
    if player["next_gw_fixtures"] == 0:
        score -= 15

    return score


def captain_recommendations(
    squad
):

    available = [

        p

        for p in squad

        if (
            p["chance"] >= 75
            and p["status"] == "a"
            and p["next_gw_fixtures"] > 0
        )

    ]

    available.sort(
        key=captain_score,
        reverse=True
    )

    return available[:5]


# ============================================================
# BENCH BOOST
# ============================================================

def bench_boost_value(
    squad,
    entry_id
):

    try:

        live_points = get_live_gw(
            current_gw
        )

    except Exception:

        return None

    bench = [

        p

        for p in squad

        if p.get(
            "multiplier",
            1
        ) == 0

    ]

    rows = []
    total = 0

    for p in bench:

        pts = live_points.get(
            p["id"],
            0
        )

        total += pts

        rows.append(
            {
                "Player": p["name"],
                "GW Points": pts,
                "Status": player_status(p),
            }
        )

    return rows, total


# ============================================================
# MANAGER BRIEFING
# ============================================================

def generate_manager_briefing(
    squad,
    bank,
    free_transfers
):

    if not squad:
        return None

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

    caps = captain_recommendations(
        squad
    )

    top_cap = (
        caps[0]
        if caps
        else None
    )

    vice_cap = (
        caps[1]
        if len(caps) > 1
        else None
    )

    t_dec = transfer_decision(
        squad,
        bank,
        free_transfers
    )

    squad_avg_fdr = (
        sum(
            p["fdr"]
            for p in squad
        )
        / len(squad)
    )

    hardest_run = sorted(
        squad,
        key=lambda p: p["fdr"],
        reverse=True
    )[:2]

    easiest_run = sorted(
        squad,
        key=lambda p: p["fdr"]
    )[:2]

    chip_advice = (
        "Hold chips. Squad structure is "
        "balanced for standard play."
    )

    if len(blanks) >= 4:

        chip_advice = (
            "⚠️ **Chip Alert:** 4+ squad "
            "players are blanking. Consider "
            "Free Hit/Wildcard planning."
        )

    elif len(doubles) >= 4:

        chip_advice = (
            "⚡ **DGW Alert:** Multiple "
            "Double Gameweek assets detected. "
            "Consider Bench Boost or "
            "Triple Captain depending on "
            "player quality."
        )

    return {
        "injuries": injuries,
        "blanks": blanks,
        "doubles": doubles,
        "top_cap": top_cap,
        "vice_cap": vice_cap,
        "t_dec": t_dec,
        "squad_avg_fdr": squad_avg_fdr,
        "hardest_run": hardest_run,
        "easiest_run": easiest_run,
        "chip_advice": chip_advice,
    }


# ============================================================
# BEST XI
# ============================================================

def best_xi(squad):

    by_pos = defaultdict(list)

    for p in squad:

        by_pos[
            p["position"]
        ].append(p)

    for pos in by_pos:

        by_pos[pos].sort(
            key=blended_score,
            reverse=True
        )

    gks = by_pos.get(
        "GK",
        []
    )

    if not gks:
        return None, 0

    best_formation = None
    best_score = -1
    best_lineup = None

    for defs, mids, fwds in VALID_FORMATIONS:

        if len(
            by_pos.get(
                "DEF",
                []
            )
        ) < defs:

            continue

        if len(
            by_pos.get(
                "MID",
                []
            )
        ) < mids:

            continue

        if len(
            by_pos.get(
                "FWD",
                []
            )
        ) < fwds:

            continue

        lineup = (

            [gks[0]]

            + by_pos["DEF"][:defs]

            + by_pos["MID"][:mids]

            + by_pos["FWD"][:fwds]

        )

        score = sum(
            blended_score(p)
            for p in lineup
        )

        if score > best_score:

            best_score = score

            best_formation = (
                f"{defs}-{mids}-{fwds}"
            )

            best_lineup = lineup

    bench = [

        p

        for p in squad

        if p not in (
            best_lineup or []
        )

    ]

    return {
        "formation": best_formation,
        "
