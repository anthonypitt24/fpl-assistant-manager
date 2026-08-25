from collections import defaultdict
import re
from urllib.parse import urlparse, parse_qs

import pandas as pd
import requests
import streamlit as st

# Optional dependencies
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
# CONFIGURATION
# ============================================================

API = "https://fantasy.premierleague.com/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 FPL Assistant Manager"
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
# HARD-CODED MANAGER
# ============================================================

MY_TEAMS = {
    "My FPL Team": 3240706,
}


# ============================================================
# HARD-CODED MINI-LEAGUES
# ============================================================

MINI_LEAGUES = {
    "Lads League": 70818,
    "IMW": 637276,
}


# ============================================================
# ELITE MANAGERS
# ============================================================

ELITE_MANAGERS = {
    "Ben Crellin": {
        "entry_id": 53517,
        "description": "Elite fixture specialist",
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
# API
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def api_get(url):
    r = requests.get(
        url,
        headers=HEADERS,
        timeout=20,
    )

    r.raise_for_status()

    return r.json()


@st.cache_data(ttl=900, show_spinner=False)
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
def get_entry_transfers(entry_id):
    return api_get(
        f"{API}/entry/{entry_id}/transfers/"
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_league(league_id):
    return api_get(
        f"{API}/leagues-classic/{league_id}/standings/"
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_live_gw(gameweek):
    data = api_get(
        f"{API}/event/{gameweek}/live/"
    )

    return {
        e["id"]: e.get("stats", {}).get(
            "total_points",
            0,
        )
        for e in data.get(
            "elements",
            [],
        )
    }


# ============================================================
# HELPERS
# ============================================================

def num(v, default=0.0):

    try:

        return (
            default
            if v is None or v == ""
            else float(v)
        )

    except (TypeError, ValueError):

        return default


def safe_int(v, default=0):

    try:

        return int(v)

    except (TypeError, ValueError):

        return default


def availability_factor(p):

    chance = num(
        p.get("chance"),
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

    return (
        sum(
            num(
                f["difficulty"],
                3,
            )
            for f in games
        )
        / len(games)
        if games
        else 3.0
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

    return " | ".join(
        f"GW{f['gw']} "
        f"{team_names.get(f['opponent'], '?')} "
        f"({'H' if f['home'] else 'A'}) "
        f"[{f['difficulty']}]"
        for f in games
    )


def price_momentum_flag(p):

    ratio = (
        num(
            p.get("net_transfers")
        )
        / max(
            num(
                p.get("ownership")
            ),
            0.1,
        )
        / 1000
    )

    if ratio > 0.4:
        return "📈 Likely rise"

    if ratio < -0.4:
        return "📉 Likely fall"

    return "— Stable"


# ============================================================
# PLAYER MODEL
# ============================================================

def calc_blended_score(p):

    ppg = min(
        num(p["ppg"]) * 1.5,
        10,
    )

    form = min(
        num(p["form"]) * 1.2,
        9,
    )

    expected = min(
        num(p["ep_next"]) * 2.5,
        16,
    )

    fixture = max(
        0,
        (
            3.2
            - num(
                p["fdr"],
                3,
            )
        )
        * 3,
    )

    availability = (
        availability_factor(p)
        * 5
    )

    attacking = min(
        num(p["xgi90"]) * 8,
        12,
    )

    defensive = (
        max(
            0,
            (
                1.4
                - num(
                    p["xgc90"]
                )
            )
            * 4,
        )
        if p["position"]
        in ("GK", "DEF")
        else 0
    )

    dgw_bonus = (
        7
        if safe_int(
            p["next_gw_fixtures"]
        ) >= 2
        else 0
    )

    bgw_penalty = (
        8
        if safe_int(
            p["next_gw_fixtures"]
        ) == 0
        else 0
    )

    ownership_bonus = (
        2
        if (
            num(p["ownership"]) < 5
            and num(p["xgi90"]) >= 0.25
        )
        else 0
    )

    return round(
        ppg
        + form
        + expected
        + fixture
        + availability
        + attacking
        + defensive
        + dgw_bonus
        + ownership_bonus
        - bgw_penalty,
        2,
    )


def calc_multi_gw_projection(
    p,
    fixture_map,
    weeks=PROJECTION_WEEKS,
):

    games = sorted(
        fixture_map.get(
            p["team_id"],
            [],
        ),
        key=lambda x: x["gw"],
    )[:weeks]

    if not games:

        return round(
            num(p["ep_next"]),
            1,
        )

    base = (
        num(p["ep_next"]) * 0.55
        + num(p["ppg"]) * 0.20
        + num(p["xgi90"]) * 2
    )

    availability = (
        availability_factor(p)
    )

    total = 0

    for f in games:

        total += (
            base
            * (
                1
                + (
                    (
                        3
                        - num(
                            f["difficulty"],
                            3,
                        )
                    )
                    * 0.08
                )
            )
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

    fixture_map = defaultdict(list)

    for f in fixtures_raw:

        gw = f.get("event")

        if (
            gw is None
            or gw < next_gw
            or gw
            > next_gw
            + FIXTURE_HORIZON
            - 1
        ):

            continue

        h = f.get("team_h")
        a = f.get("team_a")

        if h:

            fixture_map[h].append(
                {
                    "gw": safe_int(gw),
                    "home": True,
                    "opponent": a,
                    "difficulty": safe_int(
                        f.get(
                            "team_h_difficulty"
                        ),
                        3,
                    ),
                }
            )

        if a:

            fixture_map[a].append(
                {
                    "gw": safe_int(gw),
                    "home": False,
                    "opponent": h,
                    "difficulty": safe_int(
                        f.get(
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

        chance = (
            100
            if chance is None
            else chance
        )

        ti = safe_int(
            raw.get(
                "transfers_in_event"
            )
        )

        to = safe_int(
            raw.get(
                "transfers_out_event"
            )
        )

        p = {

            "id": raw.get("id"),

            "name": raw.get(
                "web_name",
                "?",
            ),

            "full_name":
                f"{raw.get('first_name','')} "
                f"{raw.get('second_name','')}"
                .strip(),

            "position":
                positions.get(
                    raw.get(
                        "element_type"
                    ),
                    "?",
                ),

            "team_id": team_id,

            "team":
                team_names.get(
                    team_id,
                    "?",
                ),

            "price":
                num(
                    raw.get(
                        "now_cost"
                    )
                ) / 10,

            "points":
                safe_int(
                    raw.get(
                        "total_points"
                    )
                ),

            "ppg":
                num(
                    raw.get(
                        "points_per_game"
                    )
                ),

            "form":
                num(
                    raw.get(
                        "form"
                    )
                ),

            "minutes":
                safe_int(
                    raw.get(
                        "minutes"
                    )
                ),

            "goals":
                safe_int(
                    raw.get(
                        "goals_scored"
                    )
                ),

            "assists":
                safe_int(
                    raw.get(
                        "assists"
                    )
                ),

            "clean_sheets":
                safe_int(
                    raw.get(
                        "clean_sheets"
                    )
                ),

            "bonus":
                safe_int(
                    raw.get(
                        "bonus"
                    )
                ),

            "bps":
                safe_int(
                    raw.get(
                        "bps"
                    )
                ),

            "ep_next":
                num(
                    raw.get(
                        "ep_next"
                    )
                ),

            "ownership":
                num(
                    raw.get(
                        "selected_by_percent"
                    )
                ),

            "chance":
                num(
                    chance,
                    100,
                ),

            "status":
                raw.get(
                    "status",
                    "a",
                ),

            "news":
                raw.get(
                    "news",
                    "",
                ),

            "xgi90":
                num(
                    raw.get(
                        "expected_goal_involvements_per_90"
                    )
                ),

            "xgc90":
                num(
                    raw.get(
                        "expected_goals_conceded_per_90"
                    )
                ),

            "ict":
                num(
                    raw.get(
                        "ict_index"
                    )
                ),

            "transfers_in": ti,

            "transfers_out": to,

            "net_transfers":
                ti - to,

            "price_change":
                safe_int(
                    raw.get(
                        "cost_change_event"
                    )
                ),
        }

        p["fdr"] = average_fdr(
            fixture_map,
            team_id,
        )

        p["next_gw_fixtures"] = fixture_count(
            fixture_map,
            team_id,
            next_gw,
        )

        p["fixtures"] = fixture_text(
            fixture_map,
            team_names,
            team_id,
            FIXTURE_HORIZON,
        )

        p["blended"] = calc_blended_score(
            p
        )

        p["projection_4gw"] = (
            calc_multi_gw_projection(
                p,
                fixture_map,
            )
        )

        players.append(p)

    return {
        "bootstrap": bootstrap,
        "teams": teams,
        "team_names": team_names,
        "current_gw": current_gw,
        "next_gw": next_gw,
        "fixture_map":
            dict(fixture_map),
        "players": players,
        "player_by_id": {
            p["id"]: p
            for p in players
            if p.get("id")
            is not None
        },
    }


# ============================================================
# INITIAL DATA LOAD
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

team_names = DATA[
    "team_names"
]

current_gw = DATA[
    "current_gw"
]

next_gw = DATA[
    "next_gw"
]

fixture_map = DATA[
    "fixture_map"
]

players = DATA[
    "players"
]

player_by_id = DATA[
    "player_by_id"
]


# ============================================================
# MODEL SHORTCUTS
# ============================================================

def blended_score(p):
    return p["blended"]


def multi_gw_projection(p):
    return calc_multi_gw_projection(
        p,
        fixture_map,
    )


def player_status(p):

    if p["status"] != "a":
        return "🔴 Unavailable"

    if p["chance"] < 50:
        return "🔴 Major doubt"

    if p["chance"] < 75:
        return "🟠 Rotation risk"

    if p["next_gw_fixtures"] == 0:
        return "⚠️ Blank GW"

    if p["next_gw_fixtures"] >= 2:
        return "⚡ Double GW"

    if p["form"] >= 5:
        return "🟢 In Form"

    return "🟡 Normal"


def hold_sell(p):

    if (
        p["status"] != "a"
        or p["chance"] < 50
    ):

        return "🔴 SELL / REPLACE"

    if p["chance"] < 75:
        return "🟠 CONSIDER SELLING"

    if p["next_gw_fixtures"] == 0:
        return "🟡 MONITOR — BLANK"

    if (
        p["form"] < 2.5
        and p["ppg"] < 3
        and p["minutes"] > 300
    ):

        return "🔴 SELL"

    if (
        p["form"] >= 5
        or p["ppg"] >= 5
    ):

        return "🟢 STRONG HOLD"

    return "🟡 MONITOR"


# ============================================================
# LOAD MY TEAM
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

        p = player_by_id.get(
            pick.get("element")
        )

        if not p:
            continue

        p = p.copy()

        p.update(
            is_captain=bool(
                pick.get(
                    "is_captain"
                )
            ),
            is_vice=bool(
                pick.get(
                    "is_vice_captain"
                )
            ),
            multiplier=safe_int(
                pick.get(
                    "multiplier"
                ),
                1,
            ),
            position_slot=safe_int(
                pick.get(
                    "position"
                ),
                0,
            ),
        )

        squad.append(p)

    return data, squad


# ============================================================
# TRANSFER MODEL
# ============================================================

def squad_club_counts(
    squad,
    exclude_id=None,
):

    counts = defaultdict(int)

    for p in squad:

        if p["id"] != exclude_id:

            counts[
                p["team_id"]
            ] += 1

    return counts


def transfer_suggestions(
    squad,
    bank,
    free_transfers,
    elite_rows=None,
):

    owned_ids = {
        p["id"]
        for p in squad
    }

    club_counts = squad_club_counts(
        squad
    )

    elite_counts = elite_player_counts(
        elite_rows or []
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
                and p["chance"] > 0
            )
        ]

        candidates.sort(
            key=lambda p:
                (
                    blended_score(p)
                    + elite_counts.get(
                        p["id"],
                        0,
                    )
                    * 1.5
                ),
            reverse=True,
        )

        for incoming in candidates[:60]:

            if (
                incoming["price"]
                > bank
                + outgoing["price"]
            ):

                continue

            count = club_counts[
                incoming["team_id"]
            ]

            if (
                incoming["team_id"]
                == outgoing["team_id"]
            ):

                count -= 1

            if (
                count + 1
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

            elite_bonus = (
                elite_counts.get(
                    incoming["id"],
                    0,
                )
                - elite_counts.get(
                    outgoing["id"],
                    0,
                )
            )

            hit = (
                0
                if free_transfers > 0
                else TRANSFER_HIT
            )

            net = (
                projected_gain
                - hit
                + elite_bonus
                * 0.25
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
                    "projected_gain":
                        projected_gain,
                    "hit": hit,
                    "net_gain": net,
                    "cost_difference":
                        incoming["price"]
                        - outgoing["price"],
                    "elite_gain":
                        elite_bonus,
                }
            )

    suggestions.sort(
        key=lambda x:
            x["net_gain"],
        reverse=True,
    )

    return suggestions[:10]


def transfer_decision(
    squad,
    bank,
    free_transfers,
    elite_rows=None,
):

    suggestions = transfer_suggestions(
        squad,
        bank,
        free_transfers,
        elite_rows,
    )

    if not suggestions:

        return {
            "decision": "ROLL",
            "reason":
                "No available transfer clears the model's minimum improvement threshold.",
            "suggestions": [],
        }

    b = suggestions[0]

    if free_transfers > 0:

        if b["projected_gain"] >= 4.5:

            return {
                "decision": "TRANSFER",
                "reason":
                    f"{b['in']['name']} projects "
                    f"+{b['projected_gain']:.1f} "
                    f"points over "
                    f"{PROJECTION_WEEKS} GWs "
                    f"versus "
                    f"{b['out']['name']}.",
                "suggestions":
                    suggestions,
            }

        return {
            "decision": "ROLL",
            "reason":
                "An upgrade exists, but it is not large enough to justify using the transfer.",
            "suggestions":
                suggestions,
        }

    if b["net_gain"] >= 2:

        return {
            "decision": "TAKE HIT",
            "reason":
                f"Projected improvement "
                f"+{b['projected_gain']:.1f}; "
                f"net model gain remains "
                f"positive after the -4.",
            "suggestions":
                suggestions,
        }

    return {
        "decision": "ROLL",
        "reason":
            "The best move does not justify the -4.",
        "suggestions":
            suggestions,
    }


# ============================================================
# CAPTAIN
# ============================================================

def captain_recommendations(
    squad,
    elite_rows=None,
):

    elite_caps = elite_captain_counts(
        elite_rows or []
    )

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
            + num(p["ep_next"])
            * 1.5
            + max(
                0,
                3 - num(
                    p["fdr"],
                    3,
                ),
            )
            * 1.5
            + (
                4
                if p["next_gw_fixtures"]
                >= 2
                else 0
            )
            + elite_caps.get(
                p["name"],
                0,
            )
            * 2
        )

    return sorted(
        available,
        key=score,
        reverse=True,
    )[:5]


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

            ids = {
                p["id"]
                for p in lineup
            }

            best = {
                "formation":
                    f"{d}-{m}-{f}",
                "lineup": lineup,
                "bench": [
                    p
                    for p in squad
                    if p["id"]
                    not in ids
                ],
                "score": score,
            }

            best_score = score

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

    rows = []
    total = 0

    for p in [
        x
        for x in squad
        if x.get(
            "multiplier",
            1,
        ) == 0
    ]:

        pts = live.get(
            p["id"],
            0,
        )

        total += pts

        rows.append(
            {
                "Player":
                    p["name"],
                "GW Points":
                    pts,
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

    try:

        info = get_entry_info(
            entry_id
        )

        if (
            not isinstance(info, dict)
            or "id" not in info
        ):

            return {
                "name": name,
                "entry_id": entry_id,
                "status": "FAILED",
                "error":
                    "FPL API could not verify this Team ID.",
                "squad": [],
                "transfers": [],
            }

        picks_data = get_entry_picks(
            entry_id,
            gameweek,
        )

        squad = []
        captain = None
        vice = None

        for pick in picks_data.get(
            "picks",
            [],
        ):

            p = player_by_id.get(
                pick.get("element")
            )

            if not p:
                continue

            p = p.copy()

            p.update(
                is_captain=bool(
                    pick.get(
                        "is_captain"
                    )
                ),
                is_vice=bool(
                    pick.get(
                        "is_vice_captain"
                    )
                ),
                multiplier=safe_int(
                    pick.get(
                        "multiplier"
                    ),
                    1,
                ),
            )

            squad.append(p)

            if p["is_captain"]:
                captain = p["id"]

            if p["is_vice"]:
                vice = p["id"]

        transfers = []

        try:

            td = get_entry_transfers(
                entry_id
            )

            if isinstance(
                td,
                list,
            ):

                for t in td:

                    if (
                        safe_int(
                            t.get("event")
                        )
                        != gameweek
                    ):
                        continue

                    outp = player_by_id.get(
                        t.get(
                            "element_out"
                        )
                    )

                    inp = player_by_id.get(
                        t.get(
                            "element_in"
                        )
                    )

                    transfers.append(
                        {
                            "out_id":
                                t.get(
                                    "element_out"
                                ),
                            "in_id":
                                t.get(
                                    "element_in"
                                ),
                            "out":
                                outp["name"]
                                if outp
                                else str(
                                    t.get(
                                        "element_out"
                                    )
                                ),
                            "in":
                                inp["name"]
                                if inp
                                else str(
                                    t.get(
                                        "element_in"
                                    )
                                ),
                            "cost":
                                safe_int(
                                    t.get(
                                        "event_cost"
                                    ),
                                    0,
                                ),
                        }
                    )

        except Exception:

            pass

        manager_name = (
            f"{info.get('player_first_name','')} "
            f"{info.get('player_last_name','')}"
            .strip()
            or "—"
        )

        return {
            "name": name,
            "entry_id": entry_id,
            "status": "OK",
            "entry_name":
                info.get(
                    "name",
                    "—",
                ),
            "manager_name":
                manager_name,
            "overall_rank":
                info.get(
                    "summary_overall_rank",
                    "—",
                ),
            "total_points":
                info.get(
                    "summary_overall_points",
                    "—",
                ),
            "gw_points":
                info.get(
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

    return [
        load_elite_manager(
            n,
            m["entry_id"],
            gameweek,
        )
        for n, m
        in ELITE_MANAGERS.items()
    ]


def elite_player_counts(rows):

    counts = defaultdict(int)

    for r in rows:

        if r.get(
            "status"
        ) != "OK":

            continue

        for p in {
            x["id"]
            for x in r.get(
                "squad",
                []
            )
            if x.get("id")
            is not None
        }:

            counts[p] += 1

    return counts


def elite_captain_counts(rows):

    counts = defaultdict(int)

    for r in rows:

        if (
            r.get("status")
            == "OK"
            and r.get("captain")
            in player_by_id
        ):

            counts[
                player_by_id[
                    r["captain"]
                ]["name"]
            ] += 1

    return counts


def elite_consensus(rows):

    valid = [
        r
        for r in rows
        if (
            r.get("status")
            == "OK"
            and r.get("squad")
        )
    ]

    total = len(valid)

    if not total:

        return (
            [],
            [],
            [],
            [],
            valid,
        )

    pc = elite_player_counts(
        valid
    )

    cc = elite_captain_counts(
        valid
    )

    tc = defaultdict(int)

    for r in valid:

        for t in r.get(
            "transfers",
            [],
        ):

            tc[
                (
                    t["out"],
                    t["in"],
                )
            ] += 1

    consensus = []

    for pid, count in sorted(
        pc.items(),
        key=lambda x: (
            -x[1],
            player_by_id.get(
                x[0],
                {}
            ).get(
                "name",
                "",
            ),
        ),
    ):

        p = player_by_id.get(
            pid
        )

        if p:

            consensus.append(
                {
                    "Player":
                        p["name"],
                    "Player ID":
                        pid,
                    "Club":
                        p["team"],
                    "Pos":
                        p["position"],
                    "Elite":
                        f"{count}/{total}",
                    "Elite %":
                        round(
                            100
                            * count
                            / total
                        ),
                    "Model":
                        round(
                            p["blended"],
                            1,
                        ),
                    "FDR":
                        round(
                            p["fdr"],
                            1,
                        ),
                }
            )

    captains = [

        {
            "Captain":
                player_by_id.get(
                    pid,
                    {},
                ).get(
                    "name",
                    "?",
                ),
            "Managers":
                f"{c}/{total}",
            "%":
                round(
                    100
                    * c
                    / total
                ),
        }

        for pid, c
        in sorted(
            (
                (
                    pid,
                    c,
                )
                for pid, c
                in cc.items()
            ),
            key=lambda x:
                -x[1],
        )
    ]

    transfers = [

        {
            "Out": o,
            "In": i,
            "Managers": c,
        }

        for (
            o,
            i
        ), c
        in sorted(
            tc.items(),
            key=lambda x:
                -x[1],
        )
    ]

    return (
        consensus,
        captains,
        transfers,
        valid,
        valid,
    )


def render_elite_tracker(
    my_squad
):

    st.header(
        "🏆 Elite Manager Tracker"
    )

    st.caption(
        "Seven hard-coded elite managers — "
        "verified IDs, squads, captaincy, transfers "
        "and consensus."
    )

    if st.button(
        "🔄 Load / Refresh Elite Managers",
        type="primary",
        key="load_elites",
    ):

        with st.spinner(
            "Loading seven elite managers..."
        ):

            st.session_state[
                "elite_rows"
            ] = load_all_elite_managers(
                current_gw
            )

    rows = st.session_state.get(
        "elite_rows"
    )

    if not rows:

        st.warning(
            "Elite managers have not been loaded yet."
        )

        return

    verification = []

    for name, meta in (
        ELITE_MANAGERS.items()
    ):

        r = next(
            (
                x
                for x in rows
                if x["name"] == name
            ),
            None,
        )

        verification.append(
            {
                "Manager":
                    name,
                "Team ID":
                    meta["entry_id"],
                "Status":
                    "✅ VERIFIED"
                    if (
                        r
                        and r.get(
                            "status"
                        )
                        == "OK"
                    )
                    else "❌ FAILED",
                "FPL Team":
                    r.get(
                        "entry_name",
                        r.get(
                            "error",
                            "—",
                        ),
                    )
                    if r
                    else "—",
                "Overall Rank":
                    r.get(
                        "overall_rank",
                        "—",
                    )
                    if r
                    else "—",
            }
        )

    with st.expander(
        "🔐 Team ID Verification"
    ):

        st.dataframe(
            pd.DataFrame(
                verification
            ),
            use_container_width=True,
            hide_index=True,
        )

    overview = [

        {
            "Manager":
                r["name"],
            "Status":
                "🟢 Connected"
                if r.get(
                    "status"
                )
                == "OK"
                else "🔴 Failed",
            "GW Points":
                r.get(
                    "gw_points",
                    "—",
                ),
            "Overall Rank":
                r.get(
                    "overall_rank",
                    "—",
                ),
            "Captain":
                player_by_id.get(
                    r.get(
                        "captain"
                    ),
                    {},
                ).get(
                    "name",
                    "—",
                ),
            "Transfers":
                len(
                    r.get(
                        "transfers",
                        [],
                    )
                ),
        }

        for r in rows
    ]

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
        _,
    ) = elite_consensus(rows)

    if not valid:

        st.error(
            "None of the seven elite managers could currently be connected."
        )

        return

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Connected",
        f"{len(valid)}/7",
    )

    c2.metric(
        "Elite Captain",
        captains[0]["Captain"]
        if captains
        else "—",
    )

    c3.metric(
        "Captain Consensus",
        captains[0]["Managers"]
        if captains
        else "—",
    )

    c4.metric(
        "Most Owned",
        consensus[0]["Player"]
        if consensus
        else "—",
    )

    st.subheader(
        "🔥 Elite Player Consensus"
    )

    st.dataframe(
        pd.DataFrame(
            consensus[:30]
        ),
        use_container_width=True,
        hide_index=True,
    )

    a, b = st.columns(2)

    with a:

        st.subheader(
            "🧢 Captain Consensus"
        )

        st.dataframe(
            pd.DataFrame(
                captains
            ),
            use_container_width=True,
            hide_index=True,
        )

    with b:

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
                "No current-GW transfers recorded."
            )

    st.divider()

    st.subheader(
        "🆚 Elite Consensus vs Your Team"
    )

    if not my_squad:

        st.info(
            "Your FPL team is not loaded."
        )

    else:

        owned = {
            p["id"]
            for p in my_squad
        }

        threshold = max(
            3,
            (len(valid) + 1) // 2,
        )

        comparison = []

        for r in consensus:

            count = safe_int(
                r["Elite"].split(
                    "/"
                )[0]
            )

            if count < threshold:
                continue

            pid = r["Player ID"]

            p = player_by_id.get(
                pid
            )

            if not p:
                continue

            is_owned = (
                pid in owned
            )

            if is_owned:

                verdict = (
                    "✅ Already own"
                )

            elif r["Model"] >= 60:

                verdict = (
                    "🟢 Elite + model target"
                )

            elif r["Model"] >= 50:

                verdict = (
                    "🟡 Elite target — review"
                )

            else:

                verdict = (
                    "⚪ Elite target — data weaker"
                )

            comparison.append(
                {
                    "Player":
                        p["name"],
                    "Club":
                        p["team"],
                    "Elite":
                        r["Elite"],
                    "Elite %":
                        r["Elite %"],
                    "You Own":
                        "✅ Yes"
                        if is_owned
                        else "❌ No",
                    "Model":
                        r["Model"],
                    "FDR":
                        r["FDR"],
                    "Verdict":
                        verdict,
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
                "No strong elite consensus differences found."
            )

        targets = []

        for r in consensus:

            count = safe_int(
                r["Elite"].split(
                    "/"
                )[0]
            )

            p = player_by_id.get(
                r["Player ID"]
            )

            if (
                p
                and count >= 3
                and p["id"]
                not in owned
            ):

                targets.append(
                    {
                        "Player":
                            p["name"],
                        "Club":
                            p["team"],
                        "Elite Ownership":
                            r["Elite"],
                        "Elite %":
                            r["Elite %"],
                        "Model":
                            r["Model"],
                        "FDR":
                            r["FDR"],
                    }
                )

        if targets:

            st.subheader(
                "🎯 Elite Players You Don't Own"
            )

            st.dataframe(
                pd.DataFrame(
                    targets[:15]
                ),
                use_container_width=True,
                hide_index=True,
            )

        elite_counts = elite_player_counts(
            valid
        )

        ignored = [

            p
            for p in my_squad

            if elite_counts.get(
                p["id"],
                0,
            ) <= 1
        ]

        if ignored:

            st.subheader(
                "⚠️ Your Players With Little Elite Support"
            )

            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Player":
                                p["name"],
                            "Club":
                                p["team"],
                            "Elite Owners":
                                f"{elite_counts.get(p['id'],0)}/{len(valid)}",
                            "Model":
                                round(
                                    p["blended"],
                                    1,
                                ),
                            "Action":
                                hold_sell(p),
                        }
                        for p in ignored
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

    st.divider()

    st.subheader(
        "👤 Individual Elite Squads"
    )

    for r in valid:

        with st.expander(
            f"{r['name']} — "
            f"{r.get('entry_name','FPL Team')}"
        ):

            st.caption(
                f"Team ID: {r['entry_id']} | "
                f"Overall rank: "
                f"{r.get('overall_rank','—')}"
            )

            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Player":
                                p["name"],
                            "Club":
                                p["team"],
                            "Pos":
                                p["position"],
                            "Price":
                                f"£{p['price']:.1f}m",
                            "Captain":
                                "👑"
                                if p["id"]
                                == r.get(
                                    "captain"
                                )
                                else "",
                            "Model":
                                round(
                                    p["blended"],
                                    1,
                                ),
                        }
                        for p in r["squad"]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

            if r.get(
                "transfers"
            ):

                st.write(
                    "**Current GW transfers:** "
                    + ", ".join(
                        f"{t['out']} → {t['in']}"
                        for t in r["transfers"]
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

        q = parse_qs(
            parsed.query
        ).get(
            "v",
            [None],
        )[0]

        if q and re.fullmatch(
            r"[0-9A-Za-z_-]{11}",
            q,
        ):

            return q

    for pattern in [

        r"youtu\.be/([0-9A-Za-z_-]{11})",

        r"youtube\.com/embed/([0-9A-Za-z_-]{11})",

        r"youtube\.com/shorts/([0-9A-Za-z_-]{11})",

        r"youtube\.com/live/([0-9A-Za-z_-]{11})",

    ]:

        m = re.search(
            pattern,
            value,
        )

        if m:

            return m.group(1)

    return None


def fetch_youtube_transcript(
    identifier
):

    if (
        YouTubeTranscriptApi
        is None
    ):

        return (
            None,
            "youtube-transcript-api is not installed.",
        )

    vid = extract_video_id(
        identifier
    )

    if not vid:

        return (
            None,
            "Invalid YouTube URL or video ID.",
        )

    try:

        api = (
            YouTubeTranscriptApi()
        )

        if hasattr(
            api,
            "fetch",
        ):

            tr = api.fetch(
                vid
            )

            lines = []

            for s in tr:

                text = (
                    s.get(
                        "text",
                        "",
                    )
                    if isinstance(
                        s,
                        dict,
                    )
                    else getattr(
                        s,
                        "text",
                        str(s),
                    )
                )

                if text:

                    lines.append(
                        str(
                            text
                        ).strip()
                    )

            if lines:

                return (
                    " ".join(lines),
                    None,
                )

        if hasattr(
            YouTubeTranscriptApi,
            "get_transcript",
        ):

            tr = (
                YouTubeTranscriptApi
                .get_transcript(
                    vid
                )
            )

            result = " ".join(
                str(
                    x.get(
                        "text",
                        "",
                    )
                )
                for x in tr
                if x.get(
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
            f"YouTube transcript could not be retrieved: {exc}",
        )


# ============================================================
# SECRETS / GEMINI
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

    key = get_secret(
        "GEMINI_API_KEY"
    )

    if not key:

        raise RuntimeError(
            "GEMINI_API_KEY is missing from Streamlit Secrets."
        )

    if (
        genai is None
        or types is None
    ):

        raise RuntimeError(
            "google-genai is not installed."
        )

    client = genai.Client(
        api_key=key
    )

    errors = []

    for model in GEMINI_MODELS:

        try:

            response = (
                client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=
                            system_instruction,
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
                    model,
                )

            errors.append(
                f"{model}: empty response"
            )

        except Exception as exc:

            errors.append(
                f"{model}: {exc}"
            )

    raise RuntimeError(
        "Gemini failed on all configured models.\n"
        + "\n".join(errors)
    )


# ============================================================
# CONTEXT
# ============================================================

def creator_context(
    squad
):

    if not squad:

        return (
            "No manager squad loaded."
        )

    return "\n".join(

        f"- {p['name']} "
        f"({p['team']}, {p['position']}) | "
        f"£{p['price']:.1f}m | "
        f"Form {p['form']:.1f} | "
        f"PPG {p['ppg']:.1f} | "
        f"xGI/90 {p['xgi90']:.2f} | "
        f"FDR {p['fdr']:.1f}"

        for p in squad
    )


def elite_context():

    rows = st.session_state.get(
        "elite_rows",
        [],
    )

    valid = [
        r
        for r in rows
        if r.get("status")
        == "OK"
    ]

    if not valid:

        return (
            "Elite managers have not been loaded."
        )

    return "\n".join(

        f"{r['name']}: "
        + ", ".join(
            p["name"]
            for p in r["squad"]
        )
        + "; Captain="
        + player_by_id.get(
            r.get("captain"),
            {},
        ).get(
            "name",
            "—",
        )

        for r in valid
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
        "Paste a YouTube FPL video and compare "
        "the creator with your squad, FPL data "
        "and elite consensus."
    )

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
        placeholder=
            "https://www.youtube.com/watch?v=XXXXXXXXXXX",
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
                "GEMINI_API_KEY is missing from Streamlit Secrets."
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

                st.error(error)

                return

            top = sorted(
                players,
                key=lambda p:
                    p["xgi90"],
                reverse=True,
            )[:25]

            pdata = "\n".join(

                f"- {p['name']} | "
                f"{p['team']} | "
                f"{p['position']} | "
                f"£{p['price']:.1f}m | "
                f"xGI/90 {p['xgi90']:.2f} | "
                f"Form {p['form']:.1f} | "
                f"PPG {p['ppg']:.1f} | "
                f"FDR {p['fdr']:.1f} | "
                f"4GW {p['projection_4gw']:.1f}"

                for p in top
            )

            prompt = f"""
You are the FPL Assistant Manager Creator Intelligence engine.

CURRENT GW:
{current_gw}

PLANNING GW:
{next_gw}

MANAGER SQUAD:
{creator_context(my_squad)}

ELITE MANAGERS:
{elite_context()}

TOP FPL DATA:
{pdata}

YOUTUBE TRANSCRIPT:
{transcript[:25000]}

Identify the creator, summarise recommendations,
extract buy/sell/hold/captain players,
compare with supplied data and elite consensus,
separate creator-only / elite-only / agreement,
and give advice for the user's actual squad.

Flag transfers, captain changes and hits.

Never invent data or quotes.
"""

            try:

                result, model = (
                    gemini_generate(
                        prompt,
                        "You are an objective elite FPL analyst. Never invent data.",
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
        if p["next_gw_fixtures"]
        == 0
    ]

    doubles = [
        p
        for p in squad
        if p["next_gw_fixtures"]
        >= 2
    ]

    elites = st.session_state.get(
        "elite_rows",
        [],
    )

    caps = captain_recommendations(
        squad,
        elites,
    )

    transfer = transfer_decision(
        squad,
        bank,
        free_transfers,
        elites,
    )

    avg = (
        sum(
            p["fdr"]
            for p in squad
        )
        / len(squad)
        if squad
        else 3
    )

    chip = (

        "⚠️ 4+ players blank next GW. "
        "Review Free Hit / restructuring options."

        if len(blanks) >= 4

        else

        "⚡ 4+ players have multiple fixtures. "
        "Potential Bench Boost / Triple Captain window."

        if len(doubles) >= 4

        else

        "Hold chips unless a stronger fixture/blank window appears."
    )

    return {
        "injuries": injuries,
        "blanks": blanks,
        "doubles": doubles,
        "captains": caps,
        "transfer": transfer,
        "avg_fdr": avg,
        "chip": chip,
    }


# ============================================================
# APP HEADER
# ============================================================

st.title(
    "⚽ FPL Assistant Manager"
)

st.caption(
    f"GW{current_gw} → GW{next_gw} | "
    "Official FPL API + data model + "
    "Elite Manager consensus"
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Manager Settings"
    )

    selected_team_name = st.selectbox(
        "Your FPL Team",
        list(
            MY_TEAMS.keys()
        ),
        index=0,
    )

    selected_entry_id = MY_TEAMS[
        selected_team_name
    ]

    st.caption(
        f"Team ID: {selected_entry_id}"
    )

    selected_league_name = st.selectbox(
        "Mini-League",
        list(
            MINI_LEAGUES.keys()
        ),
        index=0,
    )

    selected_league_id = MINI_LEAGUES[
        selected_league_name
    ]

    st.caption(
        f"League ID: {selected_league_id}"
    )

    free_transfers = st.number_input(
        "Free Transfers",
        min_value=0,
        max_value=5,
        value=1,
        step=1,
    )

    st.divider()

    if st.button(
        "🔄 Refresh FPL Data",
        use_container_width=True,
    ):

        st.cache_data.clear()

        st.session_state.pop(
            "elite_rows",
            None,
        )

        st.rerun()

    st.caption(
        "Your Team ID and Mini-League IDs are hard-coded."
    )


# ============================================================
# LOAD USER TEAM
# ============================================================

team_data = None
my_squad = []

try:

    team_data, my_squad = (
        load_my_team(
            selected_entry_id
        )
    )

except Exception as exc:

    st.error(
        "Couldn't load your FPL squad."
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
            "Your squad could not be loaded."
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

        b = strategy_briefing(
            my_squad,
            bank,
            free_transfers,
        )

        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Transfer",
            b["transfer"][
                "decision"
            ],
        )

        c2.metric(
            "Captain",
            b["captains"][0][
                "name"
            ]
            if b["captains"]
            else "—",
        )

        c3.metric(
            "Avg FDR",
            f"{b['avg_fdr']:.2f}",
        )

        st.write(
            "**Transfer assessment:** "
            + b["transfer"]["reason"]
        )

        if b["injuries"]:

            st.warning(
                "🚨 Flags: "
                + ", ".join(
                    p["name"]
                    for p in b["injuries"]
                )
            )

        if b["blanks"]:

            st.error(
                "⚠️ Blank GW: "
                + ", ".join(
                    p["name"]
                    for p in b["blanks"]
                )
            )

        if b["doubles"]:

            st.success(
                "⚡ Double GW: "
                + ", ".join(
                    p["name"]
                    for p in b["doubles"]
                )
            )

        st.subheader(
            "💊 Chip Outlook"
        )

        st.write(
            b["chip"]
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
            "Your FPL squad could not be loaded."
        )

    else:

        h = team_data.get(
            "entry_history",
            {},
        )

        bank = (
            num(
                h.get(
                    "bank"
                )
            )
            / 10
        )

        value = (
            num(
                h.get(
                    "value"
                )
            )
            / 10
        )

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "GW Points",
            h.get(
                "points",
                0,
            ),
        )

        c2.metric(
            "Total Points",
            h.get(
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

        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Player":
                            p["name"],
                        "Club":
                            p["team"],
                        "Pos":
                            p["position"],
                        "Role":
                            (
                                "👑 Captain"
                                if p["is_captain"]
                                else
                                "VC"
                                if p["is_vice"]
                                else ""
                            ),
                        "Price":
                            f"£{p['price']:.1f}m",
                        "Points":
                            p["points"],
                        "PPG":
                            round(
                                p["ppg"],
                                1,
                            ),
                        "Form":
                            round(
                                p["form"],
                                1,
                            ),
                        "xGI/90":
                            round(
                                p["xgi90"],
                                2,
                            ),
                        "FDR":
                            round(
                                p["fdr"],
                                1,
                            ),
                        "Status":
                            player_status(p),
                    }
                    for p in my_squad
                ]
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

        h = team_data.get(
            "entry_history",
            {},
        )

        bank = (
            num(
                h.get(
                    "bank"
                )
            )
            / 10
        )

        e = transfer_decision(
            my_squad,
            bank,
            free_transfers,
            st.session_state.get(
                "elite_rows",
                [],
            ),
        )

        st.info(
            f"**{e['decision']}** — "
            f"{e['reason']}"
        )

        for i, s in enumerate(
            e["suggestions"][:5],
            1,
        ):

            outp = s["out"]
            inp = s["in"]

            diff = s[
                "cost_difference"
            ]

            money = (

                f"+£{diff:.1f}m"
                if diff > 0

                else

                f"frees £{abs(diff):.1f}m"
                if diff < 0

                else

                "same price"
            )

            st.markdown(
                f"### {i}. "
                f"{outp['name']} ➡️ "
                f"{inp['name']} "
                f"({money})"
            )

            c1, c2, c3 = st.columns(3)

            c1.metric(
                "Out xGI/90",
                f"{outp['xgi90']:.2f}",
            )

            c2.metric(
                "In xGI/90",
                f"{inp['xgi90']:.2f}",
            )

            c3.metric(
                "Net Projection",
                f"{s['net_gain']:+.1f}",
            )

            st.write(
                f"**Fixtures:** "
                f"{inp['fixtures']}"
            )

            st.write(
                f"**Elite ownership change:** "
                f"{s['elite_gain']:+d}"
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

        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Player":
                            p["name"],
                        "Club":
                            p["team"],
                        "Pos":
                            p["position"],
                        "Points":
                            p["points"],
                        "Form":
                            round(
                                p["form"],
                                1,
                            ),
                        "xGI/90":
                            round(
                                p["xgi90"],
                                2,
                            ),
                        "FDR":
                            round(
                                p["fdr"],
                                1,
                            ),
                        "Trend":
                            price_momentum_flag(
                                p
                            ),
                        "Action":
                            hold_sell(p),
                    }
                    for p in my_squad
                ]
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

        caps = captain_recommendations(
            my_squad,
            st.session_state.get(
                "elite_rows",
                [],
            ),
        )

        if caps:

            cap = caps[0]

            vice = (
                caps[1]
                if len(caps) > 1
                else None
            )

            c1, c2 = st.columns(2)

            with c1:

                st.success(
                    f"👑 CAPTAIN: "
                    f"**{cap['name']}**"
                )

                st.write(
                    f"{cap['team']} | "
                    f"xGI/90 "
                    f"{cap['xgi90']:.2f} | "
                    f"Form "
                    f"{cap['form']:.1f} | "
                    f"PPG "
                    f"{cap['ppg']:.1f}"
                )

                st.write(
                    f"Fixtures: "
                    f"{cap['fixtures']}"
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

            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Rank":
                                i,
                            "Player":
                                p["name"],
                            "Club":
                                p["team"],
                            "xGI/90":
                                round(
                                    p["xgi90"],
                                    2,
                                ),
                            "Form":
                                round(
                                    p["form"],
                                    1,
                                ),
                            "PPG":
                                round(
                                    p["ppg"],
                                    1,
                                ),
                            "FDR":
                                round(
                                    p["fdr"],
                                    1,
                                ),
                        }
                        for i, p
                        in enumerate(
                            caps,
                            1,
                        )
                    ]
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
            or p["position"]
            == position
        )
    ]

    key = {
        "Model Score":
            "blended",
        "xGI/90":
            "xgi90",
        "FPL Points":
            "points",
        "Form":
            "form",
        "PPG":
            "ppg",
        "4-GW Projection":
            "projection_4gw",
    }[sort_by]

    pool.sort(
        key=lambda p:
            p[key],
        reverse=True,
    )

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Player":
                        p["name"],
                    "Club":
                        p["team"],
                    "Pos":
                        p["position"],
                    "Price":
                        f"£{p['price']:.1f}m",
                    "Points":
                        p["points"],
                    "xGI/90":
                        round(
                            p["xgi90"],
                            2,
                        ),
                    "Form":
                        round(
                            p["form"],
                            1,
                        ),
                    "PPG":
                        round(
                            p["ppg"],
                            1,
                        ),
                    "FDR":
                        round(
                            p["fdr"],
                            1,
                        ),
                    "4GW Projection":
                        round(
                            p["projection_4gw"],
                            1,
                        ),
                    "Model":
                        round(
                            p["blended"],
                            1,
                        ),
                }
                for p in pool[:75]
            ]
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

        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Player":
                            p["name"],
                        "Club":
                            p["team"],
                        "Pos":
                            p["position"],
                        "Avg FDR":
                            round(
                                p["fdr"],
                                2,
                            ),
                        "Upcoming":
                            p["fixtures"],
                    }
                    for p in my_squad
                ]
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

    for tid in teams:

        near = average_fdr(
            fixture_map,
            tid,
            2,
        )

        later = average_fdr(
            fixture_map,
            tid,
            5,
        )

        if later < near - 0.2:

            improving.append(
                (
                    team_names.get(
                        tid,
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
                        tid,
                        "?",
                    ),
                    near,
                    later,
                )
            )

    c1, c2 = st.columns(2)

    with c1:

        st.markdown(
            "### 🟢 Getting Easier"
        )

        for n, a, b in sorted(
            improving,
            key=lambda x: x[2],
        ):

            st.write(
                f"**{n}** — "
                f"{a:.1f} ➜ "
                f"{b:.1f}"
            )

    with c2:

        st.markdown(
            "### 🔴 Getting Tougher"
        )

        for n, a, b in sorted(
            worsening,
            key=lambda x: x[2],
            reverse=True,
        ):

            st.write(
                f"**{n}** — "
                f"{a:.1f} ➜ "
                f"{b:.1f}"
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

        h = team_data.get(
            "entry_history",
            {},
        )

        bank = (
            num(
                h.get(
                    "bank"
                )
            )
            / 10
        )

        b = strategy_briefing(
            my_squad,
            bank,
            free_transfers,
        )

        st.info(
            b["chip"]
        )

        bench = bench_boost_value(
            my_squad
        )

        if bench:

            rows, total = bench

            st.subheader(
                f"🪑 Current GW{current_gw} Bench Check"
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
# MINI-LEAGUE
# ============================================================

with tabs[8]:

    st.header(
        "🕵️ Mini-League"
    )

    st.caption(
        f"{selected_league_name} "
        f"— League ID {selected_league_id}"
    )

    try:

        league = get_league(
            selected_league_id
        )

        standings = (
            league
            .get(
                "standings",
                {},
            )
            .get(
                "results",
                [],
            )
        )

        # ----------------------------------------------------
        # FIND USER
        # ----------------------------------------------------

        my_entry_id = (
            selected_entry_id
        )

        my_row = next(
            (
                r
                for r in standings
                if safe_int(
                    r.get("entry")
                )
                == my_entry_id
            ),
            None,
        )

        # ----------------------------------------------------
        # LEAGUE SUMMARY
        # ----------------------------------------------------

        st.subheader(
            "📊 League Overview"
        )

        if my_row:

            my_rank = safe_int(
                my_row.get(
                    "rank"
                )
            )

            my_total = safe_int(
                my_row.get(
                    "total"
                )
            )

            my_gw = safe_int(
                my_row.get(
                    "event_total"
                )
            )

            first_total = (
                safe_int(
                    standings[0].get(
                        "total"
                    )
                )
                if standings
                else 0
            )

            gap_first = (
                first_total
                - my_total
            )

            above = next(
                (
                    r
                    for r in standings
                    if safe_int(
                        r.get("rank")
                    )
                    == my_rank - 1
                ),
                None,
            )

            below = next(
                (
                    r
                    for r in standings
                    if safe_int(
                        r.get("rank")
                    )
                    == my_rank + 1
                ),
                None,
            )

            gap_above = (
                safe_int(
                    above.get(
                        "total"
                    )
                )
                - my_total
                if above
                else 0
            )

            gap_below = (
                my_total
                - safe_int(
                    below.get(
                        "total"
                    )
                )
                if below
                else 0
            )

            c1, c2, c3, c4, c5 = (
                st.columns(5)
            )

            c1.metric(
                "Your Rank",
                f"#{my_rank}",
            )

            c2.metric(
                "Your Total",
                my_total,
            )

            c3.metric(
                "GW Points",
                my_gw,
            )

            c4.metric(
                "Gap to 1st",
                gap_first,
            )

            c5.metric(
                "Gap to Next",
                gap_above,
            )

            if above:

                st.info(
                    f"🎯 **Manager above:** "
                    f"{above.get('player_name','—')} "
                    f"({above.get('entry_name','—')}) "
                    f"— {gap_above} points ahead."
                )

            if below:

                st.success(
                    f"🛡️ **Manager below:** "
                    f"{below.get('player_name','—')} "
                    f"({below.get('entry_name','—')}) "
                    f"— you are {gap_below} points ahead."
                )

        else:

            st.warning(
                "Your FPL Team is not currently listed in this Mini-League."
            )

        # ----------------------------------------------------
        # FULL STANDINGS
        # ----------------------------------------------------

        st.subheader(
            "🏆 League Standings"
        )

        display_standings = standings[:20]

        league_rows = []

        for r in display_standings:

            entry = safe_int(
                r.get(
                    "entry"
                )
            )

            is_you = (
                entry
                == selected_entry_id
            )

            league_rows.append(
                {
                    "Rank":
                        (
                            "👉 "
                            + str(
                                r.get(
                                    "rank"
                                )
                            )
                            if is_you
                            else r.get(
                                "rank"
                            )
                        ),
                    "Manager":
                        r.get(
                            "player_name"
                        ),
                    "Team":
                        r.get(
                            "entry_name"
                        ),
                    "Total":
                        r.get(
                            "total"
                        ),
                    "GW":
                        r.get(
                            "event_total"
                        ),
                }
            )

        st.dataframe(
            pd.DataFrame(
                league_rows
            ),
            use_container_width=True,
            hide_index=True,
        )

        # ----------------------------------------------------
        # RIVALS
        # ----------------------------------------------------

        if my_row:

            st.subheader(
                "⚔️ Your Immediate Rivals"
            )

            my_rank = safe_int(
                my_row.get(
                    "rank"
                )
            )

            rivals = [

                r
                for r in standings

                if (
                    my_rank - 3
                    <= safe_int(
                        r.get(
                            "rank"
                        )
                    )
                    <= my_rank + 3
                )
            ]

            rival_rows = []

            for r in rivals:

                rank = safe_int(
                    r.get(
                        "rank"
                    )
                )

                total = safe_int(
                    r.get(
                        "total"
                    )
                )

                gap = (
                    total
                    - safe_int(
                        my_row.get(
                            "total"
                        )
                    )
                )

                if rank < my_rank:

                    status = (
                        f"🔴 +{abs(gap)} ahead"
                    )

                elif rank > my_rank:

                    status = (
                        f"🟢 {abs(gap)} behind"
                    )

                else:

                    status = (
                        "👉 YOU"
                    )

                rival_rows.append(
                    {
                        "Rank":
                            rank,
                        "Manager":
                            r.get(
                                "player_name"
                            ),
                        "Team":
                            r.get(
                                "entry_name"
                            ),
                        "Total":
                            total,
                        "GW":
                            r.get(
                                "event_total"
                            ),
                        "Gap vs You":
                            status,
                    }
                )

            st.dataframe(
                pd.DataFrame(
                    rival_rows
                ),
                use_container_width=True,
                hide_index=True,
            )

            # ------------------------------------------------
            # GW PERFORMANCE
            # ------------------------------------------------

            st.subheader(
                "📈 Gameweek Performance"
            )

            league_gw_scores = [
                safe_int(
                    r.get(
                        "event_total"
                    )
                )
                for r in standings
            ]

            if league_gw_scores:

                avg_gw = (
                    sum(
                        league_gw_scores
                    )
                    / len(
                        league_gw_scores
                    )
                )

                your_gw = safe_int(
                    my_row.get(
                        "event_total"
                    )
                )

                diff_avg = (
                    your_gw
                    - avg_gw
                )

                c1, c2, c3 = (
                    st.columns(3)
                )

                c1.metric(
                    "Your GW Score",
                    your_gw,
                )

                c2.metric(
                    "League Average",
                    f"{avg_gw:.1f}",
                )

                c3.metric(
                    "Vs League Average",
                    f"{diff_avg:+.1f}",
                )

                if diff_avg > 0:

                    st.success(
                        "📈 You outscored the league average this GW."
                    )

                elif diff_avg < 0:

                    st.warning(
                        "📉 You scored below the league average this GW."
                    )

                else:

                    st.info(
                        "Your GW score matched the league average."
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
        "players from your current 15-man squad."
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

            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Player":
                                p["name"],
                            "Club":
                                p["team"],
                            "Pos":
                                p["position"],
                            "xGI/90":
                                round(
                                    p["xgi90"],
                                    2,
                                ),
                            "Model":
                                round(
                                    p["blended"],
                                    1,
                                ),
                        }
                        for p in result[
                            "lineup"
                        ]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

            st.subheader(
                "🪑 Bench"
            )

            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Player":
                                p["name"],
                            "Club":
                                p["team"],
                            "Pos":
                                p["position"],
                            "Model":
                                round(
                                    p["blended"],
                                    1,
                                ),
                        }
                        for p in result[
                            "bench"
                        ]
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

        else:

            st.error(
                "No valid formation could be created from your squad."
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

    if not get_secret(
        "GEMINI_API_KEY"
    ):

        st.warning(
            "GEMINI_API_KEY is missing from Streamlit Secrets."
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
                "🔒 Enter the Manager PIN to unlock the assistant."
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

            for m in st.session_state[
                "messages"
            ]:

                with st.chat_message(
                    m["role"]
                ):

                    st.markdown(
                        m["content"]
                    )

            prompt = st.chat_input(
                "Ask about transfers, captaincy, fixtures..."
            )

            if prompt:

                st.session_state[
                    "messages"
                ].append(
                    {
                        "role":
                            "user",
                        "content":
                            prompt,
                    }
                )

                with st.chat_message(
                    "user"
                ):

                    st.markdown(
                        prompt
                    )

                h = (
                    team_data.get(
                        "entry_history",
                        {},
                    )
                    if team_data
                    else {}
                )

                bank = (
                    num(
                        h.get(
                            "bank"
                        )
                    )
                    / 10
                    if h
                    else 0
                )

                transfer_summary = (

                    transfer_decision(
                        my_squad,
                        bank,
                        free_transfers,
                        st.session_state.get(
                            "elite_rows",
                            [],
                        ),
                    )[
                        "reason"
                    ]

                    if my_squad

                    else

                    "No squad loaded."
                )

                assistant_prompt = f"""
FPL current GW:
{current_gw}

Planning GW:
{next_gw}

Free transfers:
{free_transfers}

Bank:
£{bank:.1f}m

MANAGER SQUAD:
{creator_context(my_squad)}

ELITE MANAGERS:
{elite_context()}

TRANSFER MODEL:
{transfer_summary}

USER QUESTION:
{prompt}

Give practical FPL advice.

Prioritise the actual squad.

Use supplied data.

Consider:
- fixtures
- form
- xGI/90
- xGC/90
- availability
- projected output
- elite consensus
- transfer value
- captaincy
- fixture swings

Do not invent statistics.
Be decisive when the evidence supports it.
Be honest about uncertainty.
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
                                    "You are an elite FPL strategist. Be practical, data-led and honest about uncertainty.",
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
                                    "role":
                                        "assistant",
                                    "content":
                                        answer,
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
    "YouTube Creator Intelligence + "
    "Mini-League Rival Analysis"
)
