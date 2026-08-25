from collections import defaultdict
import re
from urllib.parse import urlparse, parse_qs

import pandas as pd
import requests
import streamlit as st


# ============================================================
# OPTIONAL DEPENDENCIES
# ============================================================

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except Exception:
    YouTubeTranscriptApi = None


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide",
)


# ============================================================
# SETTINGS
# ============================================================

API = "https://fantasy.premierleague.com/api"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/139 Safari/537.36"
    )
}

FIXTURE_HORIZON = 5
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

GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]


# ============================================================
# 7 ELITE MANAGERS
# ============================================================
#
# These are current publicly listed 2026/27 FPL Team IDs.
#
# IMPORTANT:
# The app still verifies every ID against the live FPL API.
# If an ID stops working, the app will show FAILED rather
# than silently following the wrong manager.
#
# ============================================================

ELITE_MANAGERS = {
    "Ben Crellin": {
        "entry_id": 53517,
        "description": "All-time elite / fixture specialist",
    },

    "FPL Harry": {
        "entry_id": 3054,
        "description": "Harry Daniels / FPL Harry",
    },

    "Andy LTFPL": {
        "entry_id": 41,
        "description": "Let's Talk FPL",
    },

    "Tom Dollimore": {
        "entry_id": 179777,
        "description": "FPL Barbossa",
    },

    "Pras United": {
        "entry_id": 3315,
        "description": "Long-term elite manager",
    },

    "Sam Bonfield": {
        "entry_id": 2977,
        "description": "FPL creator / manager",
    },

    "BigMan Bakar": {
        "entry_id": 5133,
        "description": "Data-led FPL manager",
    },
}


# ============================================================
# YOUTUBE CHANNELS
# ============================================================

CREATOR_CHANNELS = {
    "FPL Harry": "https://www.youtube.com/@FPLHarry",
    "Let's Talk FPL": "https://www.youtube.com/@LetsTalkFPL",
    "FPL Focal": "https://www.youtube.com/@FPLFocal",
    "FPL Mate": "https://www.youtube.com/@FPLMate",
    "Planet FPL": "https://www.youtube.com/@PlanetFPL",
}


# ============================================================
# BASIC API
# ============================================================

@st.cache_data(
    ttl=300,
    show_spinner=False,
)
def api_get(url):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=20,
    )

    response.raise_for_status()

    return response.json()


@st.cache_data(
    ttl=900,
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
def get_entry_picks(
    entry_id,
    gameweek,
):

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
    ttl=300,
    show_spinner=False,
)
def get_league(league_id):

    return api_get(
        f"{API}/leagues-classic/{league_id}/standings/"
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
# HELPERS
# ============================================================

def num(
    value,
    default=0.0,
):

    try:

        if value is None or value == "":
            return default

        return float(value)

    except (
        TypeError,
        ValueError,
    ):

        return default


def safe_int(
    value,
    default=0,
):

    try:

        return int(value)

    except (
        TypeError,
        ValueError,
    ):

        return default


def availability_factor(player):

    chance = num(
        player.get("chance"),
        100,
    )

    if chance >= 90:
        return 1.0

    if chance >= 75:
        return 0.85

    if chance >= 50:
        return 0.60

    if chance > 0:
        return 0.30

    return 0.0


def average_fdr(
    fixture_map,
    team_id,
    weeks=None,
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
    )

    if weeks is not None:

        games = games[:weeks]

    if not games:
        return 3.0

    return sum(
        num(
            f["difficulty"],
            3,
        )
        for f in games
    ) / len(games)


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


def price_momentum_flag(player):

    net = num(
        player.get("net_transfers")
    )

    ownership = max(
        num(
            player.get("ownership")
        ),
        0.1,
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
# PLAYER MODEL
# ============================================================

def calc_blended_score(player):

    ppg = min(
        num(player["ppg"]) * 1.5,
        10,
    )

    form = min(
        num(player["form"]) * 1.2,
        9,
    )

    expected = min(
        num(player["ep_next"]) * 2.5,
        16,
    )

    fixture = max(
        0,
        (
            3.2
            - num(
                player["fdr"],
                3,
            )
        ) * 3,
    )

    availability = (
        availability_factor(
            player
        ) * 5
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
            ) * 4,
        )

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
            num(
                player["ownership"]
            ) < 5
            and num(
                player["xgi90"]
            ) >= 0.25
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
        num(
            player["ep_next"]
        ) * 0.55

        + num(
            player["ppg"]
        ) * 0.20

        + num(
            player["xgi90"]
        ) * 2.0
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
            if e.get(
                "is_current"
            )
        ),
        None,
    )

    next_event = next(
        (
            e
            for e in events
            if e.get(
                "is_next"
            )
        ),
        None,
    )

    if current_event:

        current_gw = safe_int(
            current_event.get(
                "id"
            ),
            1,
        )

    elif next_event:

        current_gw = max(
            safe_int(
                next_event.get(
                    "id"
                ),
                1,
            ) - 1,
            1,
        )

    else:

        current_gw = 1

    next_gw = (

        safe_int(
            next_event.get(
                "id"
            ),
            current_gw + 1,
        )

        if next_event

        else current_gw + 1
    )

    fixture_map = defaultdict(
        list
    )

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

            fixture_map[
                home
            ].append(
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

            fixture_map[
                away
            ].append(
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

        player = {

            "id": raw.get(
                "id"
            ),

            "name": raw.get(
                "web_name",
                "?",
            ),

            "full_name": (
                f"{raw.get('first_name', '')} "
                f"{raw.get('second_name', '')}"
            ).strip(),

            "position": positions.get(
                raw.get(
                    "element_type"
                ),
                "?",
            ),

            "team_id": team_id,

            "team": team_names.get(
                team_id,
                "?",
            ),

            "price": (
                num(
                    raw.get(
                        "now_cost"
                    )
                ) / 10
            ),

            "points": safe_int(
                raw.get(
                    "total_points"
                )
            ),

            "ppg": num(
                raw.get(
                    "points_per_game"
                )
            ),

            "form": num(
                raw.get(
                    "form"
                )
            ),

            "minutes": safe_int(
                raw.get(
                    "minutes"
                )
            ),

            "goals": safe_int(
                raw.get(
                    "goals_scored"
                )
            ),

            "assists": safe_int(
                raw.get(
                    "assists"
                )
            ),

            "clean_sheets": safe_int(
                raw.get(
                    "clean_sheets"
                )
            ),

            "bonus": safe_int(
                raw.get(
                    "bonus"
                )
            ),

            "bps": safe_int(
                raw.get(
                    "bps"
                )
            ),

            "ep_next": num(
                raw.get(
                    "ep_next"
                )
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
                raw.get(
                    "ict_index"
                )
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
        }

        player["fdr"] = average_fdr(
            fixture_map,
            team_id,
        )

        player[
            "next_gw_fixtures"
        ] = fixture_count(
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

        player["blended"] = (
            calc_blended_score(
                player
            )
        )

        player[
            "projection_4gw"
        ] = calc_multi_gw_projection(
            player,
            fixture_map,
        )

        players.append(
            player
        )

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

        "player_by_id": {
            p["id"]: p
            for p in players
            if p.get("id")
            is not None
        },
    }


# ============================================================
# LOAD MAIN DATA SAFELY
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
            pick.get(
                "element"
            )
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

        squad.append(
            p
        )

    return data, squad


def squad_club_counts(
    squad,
    exclude_id=None,
):

    counts = defaultdict(
        int
    )

    for player in squad:

        if player["id"] == exclude_id:
            continue

        counts[
            player["team_id"]
        ] += 1

    return counts


# ============================================================
# TRANSFERS
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

        for incoming in candidates[:50]:

            available = (
                bank
                + outgoing["price"]
            )

            if (
                incoming["price"]
                > available
            ):
                continue

            projected_count = (
                club_counts[
                    incoming["team_id"]
                ]
            )

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
                -
                multi_gw_projection(
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
        key=lambda x: x[
            "net_gain"
        ],
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
                "No available transfer clears "
                "the model's minimum projected-"
                "improvement threshold."
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
                f"{PROJECTION_WEEKS} GWs versus "
                f"{best['out']['name']}."
            )

        else:

            decision = "ROLL"

            reason = (
                "An upgrade exists, but it is "
                "not large enough to justify "
                "using the transfer."
            )

    else:

        if (
            best["net_gain"] >= 2
        ):

            decision = "TAKE HIT"

            reason = (
                f"Projected improvement "
                f"+{best['projected_gain']:.1f}; "
                f"net +{best['net_gain']:.1f} "
                f"after the -4."
            )

        else:

            decision = "ROLL"

            reason = (
                "The best move does not "
                "justify the -4."
            )

    return {
        "decision": decision,
        "reason": reason,
        "suggestions": suggestions,
    }


# ============================================================
# CAPTAIN
# ============================================================

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

    def score(p):

        return (
            blended_score(p)

            + num(
                p["ep_next"]
            ) * 1.5

            + max(
                0,
                3 - num(
                    p["fdr"],
                    3,
                ),
            ) * 1.5

            + (
                4
                if p[
                    "next_gw_fixtures"
                ] >= 2
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

    by_pos = defaultdict(
        list
    )

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

            best = {

                "formation": (
                    f"{d}-{m}-{f}"
                ),

                "lineup": lineup,

                "bench": [
                    p
                    for p in squad
                    if p["id"]
                    not in {
                        x["id"]
                        for x in lineup
                    }
                ],

                "score": score,
            }

    return best


def bench_boost_value(
    squad
):

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
# ELITE MANAGER TRACKER
# ============================================================

@st.cache_data(
    ttl=900,
    show_spinner=False,
)
def load_elite_manager(
    name,
    entry_id,
    gameweek,
):

    if not entry_id:

        return {
            "name": name,
            "entry_id": entry_id,
            "status": "UNVERIFIED",
            "error": "No Team ID configured.",
            "squad": [],
            "transfers": [],
            "captain": None,
            "vice": None,
        }

    try:

        info = get_entry_info(
            entry_id
        )

        if (
            not isinstance(
                info,
                dict,
            )
            or "id" not in info
        ):

            return {
                "name": name,
                "entry_id": entry_id,
                "status": "FAILED",
                "error": (
                    "FPL API could not "
                    "verify this Team ID."
                ),
                "squad": [],
                "transfers": [],
                "captain": None,
                "vice": None,
            }

        picks_data = get_entry_picks(
            entry_id,
            gameweek,
        )

        picks = picks_data.get(
            "picks",
            [],
        )

        squad = []

        captain = None

        vice = None

        for pick in picks:

            player = player_by_id.get(
                pick.get(
                    "element"
                )
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

            squad.append(
                p
            )

            if pick.get(
                "is_captain"
            ):

                captain = p[
                    "name"
                ]

            if pick.get(
                "is_vice_captain"
            ):

                vice = p[
                    "name"
                ]

        transfers = []

        try:

            transfer_data = (
                get_entry_transfers(
                    entry_id
                )
            )

            if isinstance(
                transfer_data,
                list,
            ):

                for transfer in transfer_data:

                    if safe_int(
                        transfer.get(
                            "event"
                        )
                    ) != gameweek:

                        continue

                    out_player = (
                        player_by_id.get(
                            transfer.get(
                                "element_out"
                            )
                        )
                    )

                    in_player = (
                        player_by_id.get(
                            transfer.get(
                                "element_in"
                            )
                        )
                    )

                    transfers.append(
                        {
                            "out": (
                                out_player[
                                    "name"
                                ]
                                if out_player
                                else str(
                                    transfer.get(
                                        "element_out"
                                    )
                                )
                            ),

                            "in": (
                                in_player[
                                    "name"
                                ]
                                if in_player
                                else str(
                                    transfer.get(
                                        "element_in"
                                    )
                                )
                            ),

                            "cost": safe_int(
                                transfer.get(
                                    "event_cost"
                                ),
                                0,
                            ),
                        }
                    )

        except Exception:

            transfers = []

        manager_name = (
            f"{info.get('player_first_name', '')} "
            f"{info.get('player_last_name', '')}"
        ).strip()

        return {

            "name": name,

            "entry_id": entry_id,

            "status": "OK",

            "entry_name": info.get(
                "name",
                "—",
            ),

            "manager_name": (
                manager_name
                if manager_name
                else "—"
            ),

            "overall_rank": info.get(
                "summary_overall_rank",
                "—",
            ),

            "total_points": info.get(
                "summary_overall_points",
                "—",
            ),

            "gw_points": info.get(
                "summary_event_points",
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

            "captain": None,

            "vice": None,
        }


@st.cache_data(
    ttl=900,
    show_spinner=False,
)
def load_all_elite_managers(
    gameweek
):

    rows = []

    for name, meta in (
        ELITE_MANAGERS.items()
    ):

        rows.append(
            load_elite_manager(
                name,
                meta["entry_id"],
                gameweek,
            )
        )

    return rows


def elite_consensus(
    elite_rows
):

    valid = [
        row
        for row in elite_rows

        if (
            row.get(
                "status"
            ) == "OK"

            and row.get(
                "squad"
            )
        )
    ]

    total = len(valid)

    if total == 0:

        return (
            [],
            [],
            [],
            [],
        )

    player_counts = defaultdict(
        int
    )

    captain_counts = defaultdict(
        int
    )

    transfer_counts = defaultdict(
        int
    )

    for row in valid:

        unique_players = {
            p["id"]: p
            for p in row["squad"]
            if p.get("id")
            is not None
        }

        for pid in unique_players:

            player_counts[
                pid
            ] += 1

        if row.get(
            "captain"
        ):

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
        player_counts.items(),
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
                "Model": round(
                    blended_score(p),
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
            (
                out_name,
                in_name,
            ),
            count,
        ) in sorted(
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


def render_elite_tracker(
    my_squad
):

    st.header(
        "🏆 Elite Manager Tracker"
    )

    st.caption(
        "Seven tracked FPL managers — "
        "current squads, captaincy, transfers "
        "and elite consensus."
    )

    st.info(
        "Elite managers are only loaded when you "
        "press the button below. This keeps the "
        "main app fast."
    )

    if st.button(
        "🔄 Load / Refresh Elite Managers",
        type="primary",
        key="load_elites",
    ):

        with st.spinner(
            "Loading 7 elite managers..."
        ):

            st.session_state[
                "elite_rows"
            ] = load_all_elite_managers(
                current_gw
            )

    elite_rows = st.session_state.get(
        "elite_rows"
    )

    if not elite_rows:

        st.warning(
            "Elite managers have not been loaded yet."
        )

        st.write(
            "Press **Load / Refresh Elite Managers** "
            "to retrieve the seven teams."
        )

        return

    # --------------------------------------------------------
    # VERIFICATION
    # --------------------------------------------------------

    with st.expander(
        "🔐 Team ID Verification",
        expanded=False,
    ):

        verification_rows = []

        for name, meta in (
            ELITE_MANAGERS.items()
        ):

            row = next(
                (
                    r
                    for r in elite_rows
                    if r["name"] == name
                ),
                None,
            )

            if row and row.get(
                "status"
            ) == "OK":

                verification_rows.append(
                    {
                        "Manager": name,
                        "Team ID": meta[
                            "entry_id"
                        ],
                        "Status": "✅ VERIFIED",
                        "FPL Team": row.get(
                            "entry_name",
                            "—",
                        ),
                    }
                )

            else:

                verification_rows.append(
                    {
                        "Manager": name,
                        "Team ID": meta[
                            "entry_id"
                        ],
                        "Status": "❌ FAILED",
                        "FPL Team": (
                            row.get(
                                "error",
                                "—",
                            )
                            if row
                            else "—"
                        ),
                    }
                )

        st.dataframe(
            pd.DataFrame(
                verification_rows
            ),
            use_container_width=True,
            hide_index=True,
        )

    # --------------------------------------------------------
    # OVERVIEW
    # --------------------------------------------------------

    overview = []

    for row in elite_rows:

        overview.append(
            {
                "Manager": row[
                    "name"
                ],

                "Status": (
                    "🟢 Connected"
                    if row.get(
                        "status"
                    ) == "OK"
                    else "🔴 Failed"
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

    (
        consensus,
        captains,
        transfers,
        valid,
    ) = elite_consensus(
        elite_rows
    )

    if not valid:

        st.error(
            "None of the seven elite managers "
            "could currently be connected."
        )

        return

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Connected",
        f"{len(valid)}/7",
    )

    c2.metric(
        "Elite Captain",
        (
            captains[0][
                "Captain"
            ]
            if captains
            else "—"
        ),
    )

    c3.metric(
        "Captain Consensus",
        (
            captains[0][
                "Managers"
            ]
            if captains
            else "—"
        ),
    )

    c4.metric(
        "Most Owned",
        (
            consensus[0][
                "Player"
            ]
            if consensus
            else "—"
        ),
    )

    # --------------------------------------------------------
    # CONSENSUS
    # --------------------------------------------------------

    st.subheader(
        "🔥 Elite Player Consensus"
    )

    if consensus:

        st.dataframe(
            pd.DataFrame(
                consensus[:30]
            ),
            use_container_width=True,
            hide_index=True,
        )

    # --------------------------------------------------------
    # CAPTAIN / TRANSFERS
    # --------------------------------------------------------

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
                "No current-GW transfers "
                "recorded."
            )

    # --------------------------------------------------------
    # YOUR TEAM VS ELITE
    # --------------------------------------------------------

    st.divider()

    st.subheader(
        "🆚 Elite Consensus vs Your Team"
    )

    if not my_squad:

        st.info(
            "Enter your FPL Team ID in the "
            "sidebar to compare your squad."
        )

    else:

        owned = {
            p["id"]
            for p in my_squad
        }

        threshold = max(
            3,
            (
                len(valid)
                + 1
            ) // 2,
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

            player = next(
                (
                    p
                    for p in players
                    if p["name"]
                    == row["Player"]
                ),
                None,
            )

            if not player:
                continue

            is_owned = (
                player["id"]
                in owned
            )

            if is_owned:

                verdict = (
                    "✅ Already own"
                )

            elif row["Model"] >= 60:

                verdict = (
                    "🟢 Elite + model target"
                )

            elif row["Model"] >= 50:

                verdict = (
                    "🟡 Elite target — review"
                )

            else:

                verdict = (
                    "⚪ Elite target — data weaker"
                )

            comparison.append(
                {
                    "Player": player[
                        "name"
                    ],
                    "Club": player[
                        "team"
                    ],
                    "Elite": row[
                        "Elite"
                    ],
                    "Elite %": row[
                        "Elite %"
                    ],
                    "You Own": (
                        "✅ Yes"
                        if is_owned
                        else "❌ No"
                    ),
                    "Model": row[
                        "Model"
                    ],
                    "FDR": row[
                        "FDR"
                    ],
                    "Verdict": verdict,
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
                "No strong elite consensus "
                "differences found."
            )

    # --------------------------------------------------------
    # ELITE PLAYERS YOU DON'T OWN
    # --------------------------------------------------------

    if my_squad:

        owned = {
            p["id"]
            for p in my_squad
        }

        elite_targets = []

        for row in consensus:

            count = safe_int(
                str(
                    row["Elite"]
                ).split(
                    "/"
                )[0]
            )

            if count < 3:
                continue

            player = next(
                (
                    p
                    for p in players
                    if p["name"]
                    == row["Player"]
                ),
                None,
            )

            if not player:
                continue

            if player["id"] in owned:
                continue

            elite_targets.append(
                {
                    "Player": player[
                        "name"
                    ],
                    "Club": player[
                        "team"
                    ],
                    "Elite Ownership": row[
                        "Elite"
                    ],
                    "Elite %": row[
                        "Elite %"
                    ],
                    "Model": row[
                        "Model"
                    ],
                    "FDR": row[
                        "FDR"
                    ],
                }
            )

        if elite_targets:

            st.subheader(
                "🎯 Elite Players You Don't Own"
            )

            st.dataframe(
                pd.DataFrame(
                    elite_targets[:15]
                ),
                use_container_width=True,
                hide_index=True,
            )

    # --------------------------------------------------------
    # INDIVIDUAL SQUADS
    # --------------------------------------------------------

    st.divider()

    st.subheader(
        "👤 Individual Elite Squads"
    )

    for row in valid:

        with st.expander(
            (
                f"{row['name']} — "
                f"{row.get('entry_name', 'FPL Team')}"
            )
        ):

            st.caption(
                f"Team ID: {row['entry_id']} | "
                f"Overall rank: "
                f"{row.get('overall_rank', '—')}"
            )

            squad_rows = []

            for p in row["squad"]:

                squad_rows.append(
                    {
                        "Player": p[
                            "name"
                        ],
                        "Club": p[
                            "team"
                        ],
                        "Pos": p[
                            "position"
                        ],
                        "Price": (
                            f"£{p['price']:.1f}m"
                        ),
                        "Captain": (
                            "👑"
                            if p[
                                "name"
                            ]
                            == row.get(
                                "captain"
                            )
                            else ""
                        ),
                        "Model": round(
                            blended_score(
                                p
                            ),
                            1,
                        ),
                    }
                )

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
                    "**Current GW transfers:** "
                    + ", ".join(
                        (
                            f"{t['out']} → "
                            f"{t['in']}"
                        )
                        for t in row[
                            "transfers"
                        ]
                    )
                )


# ============================================================
# YOUTUBE
# ============================================================

def extract_video_id(
    value
):

    value = (
        value or ""
    ).strip()

    if re.fullmatch(
        r"[0-9A-Za-z_-]{11}",
        value,
    ):

        return value

    parsed = urlparse(
        value
    )

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

            return match.group(
                1
            )

    return None


def fetch_youtube_transcript(
    video_identifier
):

    if (
        YouTubeTranscriptApi
        is None
    ):

        return (
            None,
            (
                "youtube-transcript-api "
                "is not installed. "
                "Add it to requirements.txt."
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
                        str(
                            text
                        ).strip()
                    )

            result = " ".join(
                x
                for x in lines
                if x
            )

            if result:

                return (
                    result,
                    None,
                )

        if hasattr(
            YouTubeTranscriptApi,
            "get_transcript",
        ):

            transcript = (
                YouTubeTranscriptApi
                .get_transcript(
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

                return (
                    result,
                    None,
                )

        return (
            None,
            "No transcript was available.",
        )

    except Exception as exc:

        return (
            None,
            (
                "YouTube transcript could not "
                "be retrieved. The video may "
                "have captions disabled or "
                "YouTube may be blocking access. "
                f"Technical detail: {exc}"
            ),
        )


# ============================================================
# GEMINI
# ============================================================

def get_secret(
    name,
    default=None,
):

    try:

        return st.secrets.get(
            name,
            default,
        )

    except Exception:

        return default


def gemini_generate(
    prompt,
    system_instruction,
):

    api_key = get_secret(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "GEMINI_API_KEY is missing "
            "from Streamlit Secrets."
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
                client.models
                .generate_content(
                    model=model_name,
                    contents=prompt,
                    config=(
                        types
                        .GenerateContentConfig(
                            system_instruction=(
                                system_instruction
                            ),
                            max_output_tokens=4000,
                        )
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
        "Gemini failed on all configured models.\n"
        + "\n".join(errors)
    )


def creator_context(
    squad
):

    if not squad:

        return (
            "No manager squad loaded."
        )

    return "\n".join(
        (
            f"- {p['name']} "
            f"({p['team']}, "
            f"{p['position']}) | "
            f"£{p['price']:.1f}m | "
            f"Form {p['form']:.1f} | "
            f"PPG {p['ppg']:.1f} | "
            f"xGI/90 {p['xgi90']:.2f} | "
            f"FDR {p['fdr']:.1f}"
        )

        for p in squad
    )


def elite_context():

    elite_rows = st.session_state.get(
        "elite_rows",
        [],
    )

    valid = [
        r
        for r in elite_rows
        if r.get(
            "status"
        ) == "OK"
    ]

    if not valid:

        return (
            "Elite managers have not been loaded."
        )

    lines = []

    for row in valid:

        players_text = ", ".join(
            p["name"]
            for p in row[
                "squad"
            ]
        )

        lines.append(
            (
                f"{row['name']}: "
                f"{players_text}; "
                f"Captain="
                f"{row.get('captain', '—')}"
            )
        )

    return "\n".join(
        lines
    )


# ============================================================
# CREATOR AI
# ============================================================

def render_creator_ai(
    my_squad
):

    st.header(
        "📺 Creator Intelligence"
    )

    st.caption(
        "Paste a YouTube FPL video and the app "
        "will compare the creator's recommendations "
        "against your squad, FPL data and the "
        "elite-manager consensus."
    )

    st.subheader(
        "🎙️ Monitored Creator Channels"
    )

    cols = st.columns(
        5
    )

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
    )

    if st.button(
        "🧠 Analyse YouTube Video",
        type="primary",
        key="creator_analyse",
    ):

        if not video_url.strip():

            st.warning(
                "Paste a YouTube video URL or ID."
            )

            return

        if not get_secret(
            "GEMINI_API_KEY"
        ):

            st.error(
                "GEMINI_API_KEY is missing "
                "from Streamlit Secrets."
            )

            return

        with st.spinner(
            "Getting transcript and analysing..."
        ):

            transcript, error = (
                fetch_youtube_transcript(
                    video_url
                )
            )

            if error:

                st.error(
                    error
                )

                return

            transcript = (
                transcript[:25000]
            )

            top_players = sorted(
                players,
                key=lambda p: p[
                    "xgi90"
                ],
                reverse=True,
            )[:25]

            player_data = "\n".join(
                (
                    f"- {p['name']} | "
                    f"{p['team']} | "
                    f"{p['position']} | "
                    f"£{p['price']:.1f}m | "
                    f"xGI/90 {p['xgi90']:.2f} | "
                    f"Form {p['form']:.1f} | "
                    f"PPG {p['ppg']:.1f} | "
                    f"FDR {p['fdr']:.1f} | "
                    f"4GW projection "
                    f"{p['projection_4gw']:.1f}"
                )

                for p in top_players
            )

            prompt = f"""
You are the FPL Assistant Manager's Creator Intelligence engine.

CURRENT GAMEWEEK:
GW{current_gw} -> GW{next_gw}

MANAGER'S SQUAD:
{creator_context(my_squad)}

CONNECTED ELITE MANAGERS:
{elite_context()}

TOP CURRENT FPL DATA:
{player_data}

YOUTUBE TRANSCRIPT:
{transcript}

Do the following:

1. Identify the creator and summarise their main recommendations.

2. Extract named players they recommend buying,
selling, holding or captaining.

3. Compare important recommendations against
the supplied FPL data.

4. Compare them against the connected elite-manager
consensus.

5. Identify:
- CREATOR + ELITE AGREEMENT
- CREATOR ONLY
- ELITE ONLY
- DATA FAVOURS CREATOR
- DATA FAVOURS ELITE

6. Give a recommendation specifically for
the user's actual squad.

7. Highlight recommendations requiring:
- transfers
- captain changes
- transfer hits

8. Do not invent statistics, injuries or quotes.

9. Clearly distinguish what the creator said
from what the model concludes.

Use concise tables where useful.
"""

            try:

                result, model = (
                    gemini_generate(
                        prompt,
                        (
                            "You are an elite, "
                            "objective FPL analyst. "
                            "Never invent data. "
                            "Treat YouTube opinions "
                            "as opinions and stress-test "
                            "them against supplied data."
                        ),
                    )
                )

                st.success(
                    f"Analysis completed with {model}."
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
        if p[
            "next_gw_fixtures"
        ] == 0
    ]

    doubles = [
        p
        for p in squad
        if p[
            "next_gw_fixtures"
        ] >= 2
    ]

    captains = (
        captain_recommendations(
            squad
        )
    )

    transfer = (
        transfer_decision(
            squad,
            bank,
            free_transfers,
        )
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

    if len(blanks) >= 4:

        chip = (
            "⚠️ 4+ players blank next GW. "
            "Review Free Hit / restructuring options."
        )

    elif len(doubles) >= 4:

        chip = (
            "⚡ 4+ players have multiple fixtures. "
            "Potential Bench Boost / Triple Captain window."
        )

    else:

        chip = (
            "Hold chips unless a stronger "
            "fixture/blank window appears."
        )

    return {

        "injuries": injuries,

        "blanks": blanks,

        "doubles": doubles,

        "captains": captains,

        "transfer": transfer,

        "avg_fdr": avg_fdr,

        "chip": chip,
    }


# ============================================================
# SIDEBAR
# ============================================================

st.title(
    "⚽ FPL Assistant Manager"
)

st.caption(
    f"GW{current_gw} → GW{next_gw} | "
    "Official FPL API + data model + "
    "Elite Manager consensus"
)

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
        "FPL data is cached briefly for speed."
    )


# ============================================================
# LOAD USER TEAM
# ============================================================

team_data = None

my_squad = []

if entry_id_input.strip():

    try:

        team_data, my_squad = (
            load_my_team(
                safe_int(
                    entry_id_input.strip()
                )
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
            "Enter your FPL Team ID in the sidebar."
        )

    else:

        history = team_data.get(
            "entry_history",
            {},
        )

        bank = (
            num(
                history.get(
                    "bank"
                )
            )
            / 10
        )

        brief = strategy_briefing(
            my_squad,
            bank,
            free_transfers,
        )

        c1, c2, c3 = st.columns(
            3
        )

        c1.metric(
            "Transfer",
            brief[
                "transfer"
            ][
                "decision"
            ],
        )

        c2.metric(
            "Captain",
            (
                brief[
                    "captains"
                ][0]["name"]
                if brief[
                    "captains"
                ]
                else "—"
            ),
        )

        c3.metric(
            "Avg FDR",
            f"{brief['avg_fdr']:.2f}",
        )

        st.write(
            "**Transfer assessment:** "
            + brief[
                "transfer"
            ][
                "reason"
            ]
        )

        if brief[
            "injuries"
        ]:

            st.warning(
                "🚨 Flags: "
                + ", ".join(
                    p["name"]
                    for p in brief[
                        "injuries"
                    ]
                )
            )

        if brief[
            "blanks"
        ]:

            st.error(
                "⚠️ Blank GW: "
                + ", ".join(
                    p["name"]
                    for p in brief[
                        "blanks"
                    ]
                )
            )

        if brief[
            "doubles"
        ]:

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
                history.get(
                    "bank"
                )
            )
            / 10
        )

        value = (
            num(
                history.get(
                    "value"
                )
            )
            / 10
        )

        c1, c2, c3, c4 = st.columns(
            4
        )

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
                    if p[
                        "is_captain"
                    ]
                    else (
                        "VC"
                        if p[
                            "is_vice"
                        ]
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
            pd.DataFrame(
                rows
            ),
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
                history.get(
                    "bank"
                )
            )
            / 10
        )

        evaluation = (
            transfer_decision(
                my_squad,
                bank,
                free_transfers,
            )
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
                    f"frees £{abs(diff):.1f}m"
                )

            else:

                money = (
                    "same price"
                )

            st.markdown(
                f"### {i}. "
                f"{out_p['name']} ➡️ "
                f"{in_p['name']} "
                f"({money})"
            )

            c1, c2, c3 = st.columns(
                3
            )

            c1.metric(
                "Out xGI/90",
                f"{out_p['xgi90']:.2f}",
            )

            c2.metric(
                "In xGI/90",
                f"{in_p['xgi90']:.2f}",
            )

            c3.metric(
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
            pd.DataFrame(
                rows
            ),
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

        caps = (
            captain_recommendations(
                my_squad
            )
        )

        if caps:

            captain = caps[0]

            vice = (
                caps[1]
                if len(caps) > 1
                else None
            )

            c1, c2 = st.columns(
                2
            )

            with c1:

                st.success(
                    f"👑 CAPTAIN: "
                    f"**{captain['name']}**"
                )

                st.write(
                    f"{captain['team']} | "
                    f"xGI/90 "
                    f"{captain['xgi90']:.2f} | "
                    f"Form "
                    f"{captain['form']:.1f} | "
                    f"PPG "
                    f"{captain['ppg']:.1f}"
                )

                st.write(
                    f"Fixtures: "
                    f"{captain['fixtures']}"
                )

            with c2:

                if vice:

                    st.info(
                        f"🥈 VICE: "
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
                pd.DataFrame(
                    rows
                ),
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
            or p[
                "position"
            ] == position
        )
    ]

    if sort_by == "Model Score":

        pool.sort(
            key=lambda p: p[
                "blended"
            ],
            reverse=True,
        )

    elif sort_by == "xGI/90":

        pool.sort(
            key=lambda p: p[
                "xgi90"
            ],
            reverse=True,
        )

    elif sort_by == "FPL Points":

        pool.sort(
            key=lambda p: p[
                "points"
            ],
            reverse=True,
        )

    elif sort_by == "Form":

        pool.sort(
            key=lambda p: p[
                "form"
            ],
            reverse=True,
        )

    elif sort_by == "PPG":

        pool.sort(
            key=lambda p: p[
                "ppg"
            ],
            reverse=True,
        )

    else:

        pool.sort(
            key=lambda p: p[
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
        pd.DataFrame(
            rows
        ),
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# FIXTURES
# ============================================================

with tabs[6]:

    st.header(
        "📅 Fixtures"
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
            pd.DataFrame(
                rows
            ).sort_values(
                "Avg FDR"
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

    c1, c2 = st.columns(
        2
    )

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
                history.get(
                    "bank"
                )
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
                f"Bench Check"
            )

            st.dataframe(
                pd.DataFrame(
                    rows
                ),
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
            "Enter your Mini-League ID in the sidebar."
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
                )[:20]
            )

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
                pd.DataFrame(
                    rows
                ),
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
        "IMPORTANT: Best XI can ONLY select "
        "from your currently loaded 15-man squad."
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
                pd.DataFrame(
                    rows
                ),
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
                "No valid formation could "
                "be created from your squad."
            )


# ============================================================
# ELITE MANAGERS
# ============================================================

with tabs[10]:

    render_elite_tracker(
        my_squad
    )


# ============================================================
# CREATOR AI
# ============================================================

with tabs[11]:

    render_creator_ai(
        my_squad
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

            if (
                "messages"
                not in st.session_state
            ):

                st.session_state[
                    "messages"
                ] = []

            for message in (
                st.session_state[
                    "messages"
                ]
            ):

                with st.chat_message(
                    message["role"]
                ):

                    st.markdown(
                        message[
                            "content"
                        ]
                    )

            prompt = st.chat_input(
                "Ask about transfers, "
                "captaincy, fixtures..."
            )

            if prompt:

                st.session_state[
                    "messages"
                ].append(
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
                        {}
                    )
                    if team_data
                    else {}
                )

                bank = (
                    num(
                        history.get(
                            "bank"
                        )
                    ) / 10
                    if history
                    else 0
                )

                squad_text = (
                    creator_context(
                        my_squad
                    )
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

MANAGER SQUAD:
{squad_text}

ELITE MANAGERS:
{elite_context()}

TRANSFER MODEL:
{transfer_summary}

USER QUESTION:
{prompt}

Give practical FPL advice.

Rules:

- Prioritise the manager's actual squad.
- Use supplied data.
- Consider fixtures, form, xGI/90,
  xGC/90, availability and projected output.
- Consider elite consensus if available.
- Do not invent statistics.
- Be decisive when the evidence supports it.
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
                                        "You are an elite "
                                        "FPL strategist. "
                                        "Be practical, "
                                        "data-led and "
                                        "honest about "
                                        "uncertainty."
                                    ),
                                )
                            )

                            st.markdown(
                                answer
                            )

                            st.caption(
                                f"Model: {model}"
                            )

                            st.session_state[
                                "messages"
                            ].append(
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
    "Official FPL API + underlying metrics + "
    "Elite Manager consensus + "
    "YouTube Creator Intelligence."
)
