from collections import defaultdict
import re

import pandas as pd
import requests
import streamlit as st

# ============================================================
# OPTIONAL PACKAGES
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


# ============================================================
# GEMINI
# ============================================================

DEFAULT_GEMINI_MODEL = "gemini-3.7-flash"

GEMINI_FALLBACK_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]


# ============================================================
# FORMATIONS
# ============================================================

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
# ELITE MANAGERS
# ============================================================
# These are the five managers you wanted to follow.
#
# IMPORTANT:
# Do NOT guess FPL entry IDs.
# The user can enter them in the Elite Managers tab.
# ============================================================

ELITE_MANAGERS = {
    "Abinav C": {
        "hall_of_fame": 3,
        "entry_id": "",
    },
    "John Walsh": {
        "hall_of_fame": 5,
        "entry_id": "",
    },
    "FPL Harry": {
        "hall_of_fame": 10,
        "entry_id": "",
    },
    "Keilan Kenny": {
        "hall_of_fame": 38,
        "entry_id": "",
    },
    "Nick (FPL Spartan)": {
        "hall_of_fame": 63,
        "entry_id": "",
    },
}


# ============================================================
# API
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def api_get(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


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
def get_entry_transfers(entry_id):
    return api_get(
        f"{API}/entry/{entry_id}/transfers/"
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
        element["id"]: element.get(
            "stats", {}
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


def fixture_count(
    fixture_map,
    team_id,
    gw,
):
    return sum(
        1
        for fixture in fixture_map.get(
            team_id,
            [],
        )
        if fixture["gw"] == gw
    )


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

    return (
        sum(
            num(
                fixture["difficulty"],
                3,
            )
            for fixture in games
        )
        / len(games)
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
        num(player.get("ownership")),
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
# MODEL SCORE
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
        )
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


# ============================================================
# MULTI-GW PROJECTION
# ============================================================

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
            num(player["ep_next"]),
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

    total = 0.0

    for fixture in games:

        difficulty = num(
            fixture["difficulty"],
            3,
        )

        difficulty_multiplier = (
            1.0
            + (
                (3 - difficulty)
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
        1,
    )


# ============================================================
# LOAD FPL DATA
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
        team["id"]: team
        for team in raw_teams
    }

    team_names = {
        team["id"]: team.get(
            "short_name",
            "?",
        )
        for team in raw_teams
    }

    positions = {
        1: "GK",
        2: "DEF",
        3: "MID",
        4: "FWD",
    }

    current_event = next(
        (
            event
            for event in events
            if event.get(
                "is_current"
            )
        ),
        None,
    )

    next_event = next(
        (
            event
            for event in events
            if event.get(
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
            )
            - 1,
            1,
        )

    else:
        current_gw = 1

    if next_event:

        next_gw = safe_int(
            next_event.get(
                "id"
            ),
            current_gw + 1,
        )

    else:

        next_gw = (
            current_gw + 1
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
                )
                / 10
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

    player_by_id = {
        player["id"]: player
        for player in players
        if player.get("id")
        is not None
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
# INITIAL DATA
# ============================================================

try:

    data = load_fpl_data()

except Exception as exc:

    st.error(
        "⚠️ The official FPL API could not be loaded."
    )

    st.caption(
        f"Technical detail: {exc}"
    )

    st.stop()


teams = data["teams"]
team_names = data["team_names"]
current_gw = data["current_gw"]
next_gw = data["next_gw"]
fixture_map = data["fixture_map"]
players = data["players"]
player_by_id = data["player_by_id"]


# ============================================================
# PLAYER HELPERS
# ============================================================

def blended_score(player):
    return player["blended"]


def multi_gw_projection(
    player,
    weeks=PROJECTION_WEEKS,
):
    return calc_multi_gw_projection(
        player,
        fixture_map,
        weeks,
    )


def player_status(player):

    if player["status"] != "a":
        return "🔴 Unavailable"

    if player["chance"] < 50:
        return "🔴 Major doubt"

    if player["chance"] < 75:
        return "🟠 Rotation risk"

    if player[
        "next_gw_fixtures"
    ] == 0:
        return "⚠️ Blank GW"

    if player[
        "next_gw_fixtures"
    ] >= 2:
        return "⚡ Double GW"

    if player["form"] >= 5:
        return "🟢 In Form"

    return "🟡 Normal"


def hold_sell(player):

    if player["status"] != "a":
        return "🔴 SELL / REPLACE"

    if player["chance"] < 50:
        return "🔴 SELL / REPLACE"

    if player["chance"] < 75:
        return "🟠 CONSIDER SELLING"

    if player[
        "next_gw_fixtures"
    ] == 0:
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

        copy_player = player.copy()

        copy_player[
            "is_captain"
        ] = bool(
            pick.get(
                "is_captain",
                False,
            )
        )

        copy_player[
            "is_vice"
        ] = bool(
            pick.get(
                "is_vice_captain",
                False,
            )
        )

        copy_player[
            "multiplier"
        ] = safe_int(
            pick.get(
                "multiplier"
            ),
            1,
        )

        copy_player[
            "position_slot"
        ] = safe_int(
            pick.get(
                "position"
            ),
            0,
        )

        squad.append(
            copy_player
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

        if (
            player["id"]
            == exclude_id
        ):
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
        player["id"]
        for player in squad
    }

    club_counts = squad_club_counts(
        squad
    )

    suggestions = []

    for outgoing in squad:

        candidates = [
            player
            for player in players
            if (
                player["position"]
                == outgoing["position"]
            )
            and (
                player["id"]
                not in owned_ids
            )
            and (
                player["status"]
                == "a"
            )
            and (
                player["chance"]
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
        key=lambda item:
        item["net_gain"],
        reverse=True,
    )

    return suggestions[:10]


def transfer_decision(
    squad,
    bank,
    free_transfers,
):

    suggestions = (
        transfer_suggestions(
            squad,
            bank,
            free_transfers,
        )
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
                f"compared with "
                f"{best['out']['name']}."
            )

        else:

            decision = "ROLL"

            reason = (
                "A minor upgrade exists, "
                "but rolling the transfer "
                "provides greater flexibility."
            )

    else:

        if (
            best["net_gain"] >= 2
        ):

            decision = "TAKE HIT"

            reason = (
                f"Projected improvement: "
                f"+{best['projected_gain']:.1f} "
                f"points "
                f"(+{best['net_gain']:.1f} "
                f"net after -4)."
            )

        else:

            decision = "ROLL"

            reason = (
                "The best available move "
                "does not justify paying "
                "the -4 hit."
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
        player
        for player in squad
        if (
            player["chance"]
            >= 75
        )
        and (
            player["status"]
            == "a"
        )
        and (
            player[
                "next_gw_fixtures"
            ]
            > 0
        )
    ]

    def captain_score(player):

        return (
            blended_score(player)
            + num(
                player["ep_next"]
            ) * 1.5
            + max(
                0,
                3
                - num(
                    player["fdr"],
                    3,
                ),
            )
            * 1.5
            + (
                4
                if player[
                    "next_gw_fixtures"
                ]
                >= 2
                else 0
            )
        )

    available.sort(
        key=captain_score,
        reverse=True,
    )

    return available[:5]


# ============================================================
# BEST XI
# ============================================================

def best_xi(squad):

    if len(squad) < 11:
        return None, 0

    by_pos = defaultdict(
        list
    )

    for player in squad:
        by_pos[
            player["position"]
        ].append(player)

    for position in by_pos:
        by_pos[
            position
        ].sort(
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
        return None, 0

    best_formation = None
    best_score = float(
        "-inf"
    )

    best_lineup = None

    for (
        def_count,
        mid_count,
        fwd_count,
    ) in VALID_FORMATIONS:

        if len(defs) < def_count:
            continue

        if len(mids) < mid_count:
            continue

        if len(fwds) < fwd_count:
            continue

        lineup = (
            [gks[0]]
            + defs[:def_count]
            + mids[:mid_count]
            + fwds[:fwd_count]
        )

        score = sum(
            blended_score(
                player
            )
            for player in lineup
        )

        if score > best_score:

            best_score = score

            best_formation = (
                f"{def_count}-"
                f"{mid_count}-"
                f"{fwd_count}"
            )

            best_lineup = lineup

    if not best_lineup:
        return None, 0

    lineup_ids = {
        player["id"]
        for player in best_lineup
    }

    bench = [
        player
        for player in squad
        if player["id"]
        not in lineup_ids
    ]

    return (
        {
            "formation": best_formation,
            "lineup": best_lineup,
            "bench": bench,
        },
        best_score,
    )


# ============================================================
# STRATEGY BRIEFING
# ============================================================

def generate_manager_briefing(
    squad,
    bank,
    free_transfers,
):

    if not squad:
        return None

    injuries = [
        player
        for player in squad
        if (
            player["status"]
            != "a"
            or player["chance"]
            < 75
        )
    ]

    blanks = [
        player
        for player in squad
        if player[
            "next_gw_fixtures"
        ] == 0
    ]

    doubles = [
        player
        for player in squad
        if player[
            "next_gw_fixtures"
        ] >= 2
    ]

    captains = captain_recommendations(
        squad
    )

    top_cap = (
        captains[0]
        if captains
        else None
    )

    vice_cap = (
        captains[1]
        if len(captains) > 1
        else None
    )

    transfer_eval = (
        transfer_decision(
            squad,
            bank,
            free_transfers,
        )
    )

    squad_avg_fdr = (
        sum(
            player["fdr"]
            for player in squad
        )
        / len(squad)
    )

    hardest_run = sorted(
        squad,
        key=lambda player:
        player["fdr"],
        reverse=True,
    )[:2]

    easiest_run = sorted(
        squad,
        key=lambda player:
        player["fdr"],
    )[:2]

    if len(blanks) >= 4:

        chip_advice = (
            "⚠️ CHIP ALERT — "
            "4+ squad players blank next GW."
        )

    elif len(doubles) >= 4:

        chip_advice = (
            "⚡ DOUBLE GW ALERT — "
            "4+ squad players have "
            "multiple fixtures."
        )

    else:

        chip_advice = (
            "Hold chips unless your "
            "wider squad structure "
            "or future fixture swings "
            "create a stronger opportunity."
        )

    return {
        "injuries": injuries,
        "blanks": blanks,
        "doubles": doubles,
        "top_cap": top_cap,
        "vice_cap": vice_cap,
        "t_dec": transfer_eval,
        "squad_avg_fdr": squad_avg_fdr,
        "hardest_run": hardest_run,
        "easiest_run": easiest_run,
        "chip_advice": chip_advice,
    }


# ============================================================
# YOUTUBE
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

    patterns = [
        r"(?:v=)([0-9A-Za-z_-]{11})",
        r"(?:youtu\.be/)([0-9A-Za-z_-]{11})",
        r"(?:youtube\.com/embed/)([0-9A-Za-z_-]{11})",
        r"(?:youtube\.com/shorts/)([0-9A-Za-z_-]{11})",
        r"(?:youtube\.com/live/)([0-9A-Za-z_-]{11})",
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

    if (
        YouTubeTranscriptApi
        is None
    ):

        return (
            None,
            "youtube-transcript-api is not installed."
        )

    video_id = extract_video_id(
        video_identifier
    )

    if not video_id:

        return (
            None,
            "Invalid YouTube URL or Video ID."
        )

    try:

        api = (
            YouTubeTranscriptApi()
        )

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

                    lines.append(
                        str(
                            snippet.get(
                                "text",
                                "",
                            )
                        )
                    )

                else:

                    lines.append(
                        str(
                            getattr(
                                snippet,
                                "text",
                                snippet,
                            )
                        )
                    )

            text = " ".join(
                line.strip()
                for line in lines
                if line
                and line.strip()
            )

            if text:
                return text, None

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

            text = " ".join(
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

            if text:
                return text, None

        return (
            None,
            "No transcript was available."
        )

    except Exception as exc:

        return (
            None,
            f"Could not retrieve transcript: {exc}"
        )


# ============================================================
# GEMINI
# ============================================================

def get_gemini_key():

    try:
        return st.secrets.get(
            "GEMINI_API_KEY"
        )
    except Exception:
        return None


def get_gemini_models():

    try:

        configured = st.secrets.get(
            "GEMINI_MODEL",
            DEFAULT_GEMINI_MODEL,
        )

    except Exception:

        configured = (
            DEFAULT_GEMINI_MODEL
        )

    models = [
        configured
    ] + GEMINI_FALLBACK_MODELS

    return list(
        dict.fromkeys(
            models
        )
    )


def gemini_generate(
    prompt,
    system_instruction,
):

    api_key = (
        get_gemini_key()
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

    for model_name in (
        get_gemini_models()
    ):

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
                            max_output_tokens=3000,
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
        "Gemini failed:\n"
        + "\n".join(
            errors
        )
    )


# ============================================================
# ELITE MANAGER TRACKER
# ============================================================

def load_elite_manager(
    name,
    entry_id,
):

    entry_id = safe_int(
        entry_id,
        0,
    )

    if not entry_id:

        return {
            "name": name,
            "status": "MISSING",
            "error": (
                "FPL Team ID required."
            ),
            "squad": [],
            "transfers": [],
        }

    try:

        info = get_entry_info(
            entry_id
        )

        picks_data = (
            get_entry_picks(
                entry_id,
                current_gw,
            )
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

            squad.append(
                player.copy()
            )

            if pick.get(
                "is_captain"
            ):
                captain = player[
                    "name"
                ]

            if pick.get(
                "is_vice_captain"
            ):
                vice = player[
                    "name"
                ]

        transfers = []

        try:

            raw_transfers = (
                get_entry_transfers(
                    entry_id
                )
            )

            for transfer in (
                raw_transfers
            ):

                if (
                    transfer.get(
                        "event"
                    )
                    != current_gw
                ):
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
                        "cost": transfer.get(
                            "event_cost",
                            0,
                        ),
                    }
                )

        except Exception:
            pass

        history = (
            get_team_history(
                entry_id
            )
        )

        current_history = (
            history.get(
                "current",
                []
            )
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
            "status": "ERROR",
            "error": str(exc),
            "squad": [],
            "transfers": [],
        }


def elite_consensus(
    elite_rows
):

    valid = [
        row
        for row in elite_rows
        if (
            row.get(
                "status"
            )
            == "OK"
            and row.get(
                "squad"
            )
        )
    ]

    total = len(valid)

    if not total:

        return (
            [],
            [],
            [],
            valid,
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
            player["id"]: player
            for player in row["squad"]
        }

        for player in (
            unique_players.values()
        ):

            player_counts[
                player["id"]
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

    for player_id, count in sorted(
        player_counts.items(),
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

        player = player_by_id.get(
            player_id
        )

        if not player:
            continue

        player_rows.append(
            {
                "Player": player[
                    "name"
                ],
                "Club": player[
                    "team"
                ],
                "Pos": player[
                    "position"
                ],
                "Elite": (
                    f"{count}/{total}"
                ),
                "Elite %": round(
                    100
                    * count
                    / total
                ),
                "Model Score": round(
                    blended_score(
                        player
                    ),
                    1,
                ),
                "FDR": round(
                    player["fdr"],
                    1,
                ),
            }
        )

    captain_rows = [
        {
            "Captain": name,
            "Managers": f"{count}/{total}",
            "%": round(
                100
                * count
                / total
            ),
        }
        for name, count
        in sorted(
            captain_counts.items(),
            key=lambda x:
            -x[1],
        )
    ]

    transfer_rows = [
        {
            "Out": pair[0],
            "In": pair[1],
            "Managers": count,
        }
        for pair, count
        in sorted(
            transfer_counts.items(),
            key=lambda x:
            -x[1],
        )
    ]

    return (
        player_rows,
        captain_rows,
        transfer_rows,
        valid,
    )


def render_elite_tracker():

    st.header(
        "🏆 Elite Manager Tracker"
    )

    st.caption(
        "Follow five high-performing "
        "FPL Hall-of-Fame managers and "
        "compare their decisions with yours."
    )

    st.info(
        "The tracked group is Abinav C, "
        "John Walsh, FPL Harry, Keilan Kenny "
        "and Nick (FPL Spartan)."
    )

    st.subheader(
        "⚙️ Connect Elite Managers"
    )

    st.caption(
        "Enter each manager's FPL Team ID. "
        "The app deliberately does not guess IDs."
    )

    elite_ids = {}

    cols = st.columns(2)

    for index, (
        name,
        meta,
    ) in enumerate(
        ELITE_MANAGERS.items()
    ):

        with cols[
            index % 2
        ]:

            elite_ids[
                name
            ] = st.text_input(
                f"{name} — HOF #{meta['hall_of_fame']}",
                value=meta.get(
                    "entry_id",
                    "",
                ),
                key=f"elite_id_{name}",
            )

    if st.button(
        "🔄 Update Elite Managers",
        key="elite_refresh",
    ):

        st.cache_data.clear()
        st.rerun()

    with st.spinner(
        "Loading elite managers..."
    ):

        rows = [
            load_elite_manager(
                name,
                elite_ids[name],
            )
            for name
            in ELITE_MANAGERS
        ]

    overview = []

    for row in rows:

        overview.append(
            {
                "Manager": row[
                    "name"
                ],
                "HOF Rank": ELITE_MANAGERS[
                    row["name"]
                ][
                    "hall_of_fame"
                ],
                "Status": (
                    "🟢 Connected"
                    if row.get(
                        "status"
                    )
                    == "OK"
                    else "🟠 ID required"
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
        rows
    )

    if not valid:

        st.warning(
            "No elite teams are connected yet."
        )

        return

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

    st.divider()

    st.subheader(
        "🔥 Elite Consensus"
    )

    st.caption(
        "How often each player appears "
        "in the connected elite squads."
    )

    if consensus:

        st.dataframe(
            pd.DataFrame(
                consensus[:30]
            ),
            use_container_width=True,
            hide_index=True,
        )

    c1, c2 = st.columns(2)

    with c1:

        st.markdown(
            "### 🧢 Captain Consensus"
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

    with c2:

        st.markdown(
            "### 🔄 Elite Transfers"
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
                "No current transfer data."
            )

    st.divider()

    st.subheader(
        "🆚 Elite Managers vs You"
    )

    if not my_squad:

        st.info(
            "Load your FPL Team ID in "
            "the sidebar to compare."
        )

    else:

        owned_ids = {
            player["id"]
            for player in my_squad
        }

        comparison = []

        threshold = max(
            2,
            (
                len(valid) + 1
            )
            // 2,
        )

        for row in consensus:

            count = safe_int(
                row["Elite"].split(
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

            owned = (
                player["id"]
                in owned_ids
            )

            if owned:

                verdict = (
                    "✅ Already owned"
                )

            elif (
                row["Model Score"]
                >= 60
            ):

                verdict = (
                    "🔥 Elite + Model target"
                )

            else:

                verdict = (
                    "🟡 Elite pick — review"
                )

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
                        if owned
                        else "❌ No"
                    ),
                    "Model Score": row[
                        "Model Score"
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

    st.divider()

    st.subheader(
        "👤 Individual Elite Squads"
    )

    for row in valid:

        with st.expander(
            f"{row['name']} — "
            f"{row.get('entry_name', '')}"
        ):

            squad_rows = []

            for player in row[
                "squad"
            ]:

                squad_rows.append(
                    {
                        "Player": player[
                            "name"
                        ],
                        "Club": player[
                            "team"
                        ],
                        "Pos": player[
                            "position"
                        ],
                        "Price": (
                            f"£{player['price']:.1f}m"
                        ),
                        "Captain": (
                            "👑"
                            if player[
                                "name"
                            ]
                            == row.get(
                                "captain"
                            )
                            else ""
                        ),
                        "Model Score": round(
                            blended_score(
                                player
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
                        f"{transfer['out']} → "
                        f"{transfer['in']}"
                        for transfer
                        in row[
                            "transfers"
                        ]
                    )
                )


# ============================================================
# HEADER
# ============================================================

st.title(
    "⚽ FPL Assistant Manager"
)

st.caption(
    f"GW{current_gw} → GW{next_gw} | "
    "FPL API + underlying metrics + "
    "decision engine + elite manager tracking"
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Manager Settings"
    )

    entry_id = st.text_input(
        "FPL Team ID",
        value="",
        help=(
            "Found in the URL of your "
            "FPL team page."
        ),
    )

    league_id = st.text_input(
        "Mini-League ID",
        value="",
    )

    free_transfers = st.number_input(
        "Free Transfers Available",
        min_value=0,
        max_value=5,
        value=1,
        step=1,
    )

    st.divider()

    st.caption(
        "FPL data is cached briefly "
        "to reduce API requests."
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
# LOAD USER SQUAD
# ============================================================

team_data = None
my_squad = []

if entry_id.strip():

    try:

        team_data, my_squad = (
            load_my_team(
                safe_int(
                    entry_id.strip()
                )
            )
        )

    except Exception as exc:

        st.error(
            "Couldn't load your squad. "
            "Check the Team ID."
        )

        st.caption(
            f"Technical detail: {exc}"
        )


# ============================================================
# TAB 1 — STRATEGY
# ============================================================

with tabs[0]:

    st.header(
        f"📋 GW{next_gw} Strategy"
    )

    if not my_squad:

        st.info(
            "Enter your FPL Team ID."
        )

    else:

        entry_history = team_data.get(
            "entry_history",
            {},
        )

        bank = (
            num(
                entry_history.get(
                    "bank"
                )
            )
            / 10
        )

        briefing = (
            generate_manager_briefing(
                my_squad,
                bank,
                free_transfers,
            )
        )

        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Transfer",
            briefing[
                "t_dec"
            ][
                "decision"
            ],
        )

        c2.metric(
            "Captain",
            (
                briefing[
                    "top_cap"
                ][
                    "name"
                ]
                if briefing[
                    "top_cap"
                ]
                else "N/A"
            ),
        )

        c3.metric(
            "Vice",
            (
                briefing[
                    "vice_cap"
                ][
                    "name"
                ]
                if briefing[
                    "vice_cap"
                ]
                else "N/A"
            ),
        )

        st.markdown(
            "**Transfer Assessment:** "
            + briefing[
                "t_dec"
            ][
                "reason"
            ]
        )

        if briefing[
            "injuries"
        ]:

            st.warning(
                "🚨 "
                + ", ".join(
                    f"{p['name']} "
                    f"({p['news'] or 'Doubt'})"
                    for p
                    in briefing[
                        "injuries"
                    ]
                )
            )

        if briefing[
            "blanks"
        ]:

            st.error(
                "⚠️ Blank GW: "
                + ", ".join(
                    p["name"]
                    for p
                    in briefing[
                        "blanks"
                    ]
                )
            )

        if briefing[
            "doubles"
        ]:

            st.success(
                "⚡ Double GW: "
                + ", ".join(
                    p["name"]
                    for p
                    in briefing[
                        "doubles"
                    ]
                )
            )

        st.divider()

        st.subheader(
            "🔭 5-GW Squad Horizon"
        )

        avg_fdr = briefing[
            "squad_avg_fdr"
        ]

        if avg_fdr < 2.9:
            fdr_eval = "🟢 Favourable"

        elif avg_fdr > 3.2:
            fdr_eval = "🔴 Difficult"

        else:
            fdr_eval = "🟡 Balanced"

        st.write(
            f"**Schedule:** "
            f"{fdr_eval} "
            f"(FDR {avg_fdr:.2f})"
        )

        c1, c2 = st.columns(2)

        with c1:

            st.markdown(
                "### 🟢 Best Runs"
            )

            for player in briefing[
                "easiest_run"
            ]:

                st.write(
                    f"**{player['name']}** "
                    f"({player['team']}) — "
                    f"FDR {player['fdr']:.1f}"
                )

                st.caption(
                    player["fixtures"]
                )

        with c2:

            st.markdown(
                "### 🔴 Toughest Runs"
            )

            for player in briefing[
                "hardest_run"
            ]:

                st.write(
                    f"**{player['name']}** "
                    f"({player['team']}) — "
                    f"FDR {player['fdr']:.1f}"
                )

                st.caption(
                    player["fixtures"]
                )

        st.divider()

        st.subheader(
            "💊 Chip View"
        )

        st.write(
            briefing[
                "chip_advice"
            ]
        )


# ============================================================
# TAB 2 — MY TEAM
# ============================================================

with tabs[1]:

    st.header(
        "👤 My FPL Team"
    )

    if not my_squad:

        st.info(
            "Enter your Team ID."
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

        rows = []

        for player in my_squad:

            rows.append(
                {
                    "Player": player[
                        "name"
                    ],
                    "Club": player[
                        "team"
                    ],
                    "Pos": player[
                        "position"
                    ],
                    "Role": (
                        "👑 Captain"
                        if player.get(
                            "is_captain"
                        )
                        else (
                            "VC"
                            if player.get(
                                "is_vice"
                            )
                            else ""
                        )
                    ),
                    "Price": (
                        f"£{player['price']:.1f}m"
                    ),
                    "Points": player[
                        "points"
                    ],
                    "PPG": round(
                        player["ppg"],
                        1,
                    ),
                    "Form": round(
                        player["form"],
                        1,
                    ),
                    "xGI/90": round(
                        player["xgi90"],
                        2,
                    ),
                    "FDR": round(
                        player["fdr"],
                        1,
                    ),
                    "Status": player_status(
                        player
                    ),
                }
            )

        st.dataframe(
            pd.DataFrame(
                rows
            ),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# TAB 3 — TRANSFERS
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

        for index, suggestion in enumerate(
            evaluation[
                "suggestions"
            ][:5],
            1,
        ):

            outgoing = suggestion[
                "out"
            ]

            incoming = suggestion[
                "in"
            ]

            difference = suggestion[
                "cost_difference"
            ]

            if difference > 0:

                cost_text = (
                    f"+£{difference:.1f}m"
                )

            elif difference < 0:

                cost_text = (
                    f"-£{abs(difference):.1f}m"
                )

            else:

                cost_text = "Same price"

            st.subheader(
                f"{index}. "
                f"{outgoing['name']} ➡️ "
                f"{incoming['name']}"
            )

            c1, c2, c3, c4 = st.columns(4)

            c1.metric(
                "Out xGI/90",
                f"{outgoing['xgi90']:.2f}",
            )

            c2.metric(
                "In xGI/90",
                f"{incoming['xgi90']:.2f}",
            )

            c3.metric(
                "4-GW Gain",
                f"+{suggestion['projected_gain']:.1f}",
            )

            c4.metric(
                "Net",
                f"{suggestion['net_gain']:+.1f}",
            )

            st.write(
                f"**Price:** {cost_text}"
            )

            st.caption(
                f"Incoming fixtures: "
                f"{incoming['fixtures']}"
            )

            st.caption(
                f"Price trend: "
                f"{price_momentum_flag(incoming)}"
            )

            st.divider()


# ============================================================
# TAB 4 — HOLD / SELL
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

        rows = []

        for player in my_squad:

            rows.append(
                {
                    "Player": player[
                        "name"
                    ],
                    "Club": player[
                        "team"
                    ],
                    "Pos": player[
                        "position"
                    ],
                    "Points": player[
                        "points"
                    ],
                    "Form": round(
                        player["form"],
                        1,
                    ),
                    "xGI/90": round(
                        player["xgi90"],
                        2,
                    ),
                    "FDR": round(
                        player["fdr"],
                        1,
                    ),
                    "Trend": price_momentum_flag(
                        player
                    ),
                    "Action": hold_sell(
                        player
                    ),
                }
            )

        st.dataframe(
            pd.DataFrame(
                rows
            ),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# TAB 5 — CAPTAIN
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

        captains = (
            captain_recommendations(
                my_squad
            )
        )

        if captains:

            captain = captains[0]

            vice = (
                captains[1]
                if len(captains) > 1
                else None
            )

            c1, c2 = st.columns(2)

            with c1:

                st.success(
                    f"👑 CAPTAIN — "
                    f"{captain['name']}"
                )

                st.write(
                    f"**{captain['team']}**"
                )

                st.write(
                    f"xGI/90: "
                    f"{captain['xgi90']:.2f}"
                )

                st.write(
                    f"Form: "
                    f"{captain['form']:.1f}"
                )

                st.write(
                    f"PPG: "
                    f"{captain['ppg']:.1f}"
                )

                st.write(
                    f"FDR: "
                    f"{captain['fdr']:.1f}"
                )

                st.caption(
                    captain["fixtures"]
                )

            with c2:

                if vice:

                    st.info(
                        f"🥈 VICE — "
                        f"{vice['name']}"
                    )

                    st.write(
                        f"**{vice['team']}**"
                    )

                    st.write(
                        f"xGI/90: "
                        f"{vice['xgi90']:.2f}"
                    )

                    st.write(
                        f"Form: "
                        f"{vice['form']:.1f}"
                    )

                    st.write(
                        f"PPG: "
                        f"{vice['ppg']:.1f}"
                    )

            st.divider()

            st.subheader(
                "Top Captain Candidates"
            )

            rows = []

            for index, player in enumerate(
                captains,
                1,
            ):

                rows.append(
                    {
                        "Rank": index,
                        "Player": player[
                            "name"
                        ],
                        "Club": player[
                            "team"
                        ],
                        "xGI/90": round(
                            player[
                                "xgi90"
                            ],
                            2,
                        ),
                        "Form": round(
                            player[
                                "form"
                            ],
                            1,
                        ),
                        "PPG": round(
                            player[
                                "ppg"
                            ],
                            1,
                        ),
                        "FDR": round(
                            player[
                                "fdr"
                            ],
                            1,
                        ),
                    }
                )

            st.dataframe(
                pd.DataFrame(
                    rows
                ),
                use_container_width=True,
                hide_index=True,
            )


# ============================================================
# TAB 6 — RANKINGS
# ============================================================

with tabs[5]:

    st.header(
        "📊 Player Rankings"
    )

    sort_by = st.radio(
        "Rank by",
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
        player
        for player in players
        if (
            position == "ALL"
            or player["position"]
            == position
        )
    ]

    if sort_by == "Model Score":

        pool.sort(
            key=blended_score,
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

    else:

        pool.sort(
            key=lambda p:
            p["projection_4gw"],
            reverse=True,
        )

    rows = []

    for player in pool[:75]:

        rows.append(
            {
                "Player": player[
                    "name"
                ],
                "Club": player[
                    "team"
                ],
                "Pos": player[
                    "position"
                ],
                "Price": (
                    f"£{player['price']:.1f}m"
                ),
                "Points": player[
                    "points"
                ],
                "xGI/90": round(
                    player[
                        "xgi90"
                    ],
                    2,
                ),
                "Form": round(
                    player[
                        "form"
                    ],
                    1,
                ),
                "PPG": round(
                    player[
                        "ppg"
                    ],
                    1,
                ),
                "FDR": round(
                    player[
                        "fdr"
                    ],
                    1,
                ),
                "4GW Projection": round(
                    player[
                        "projection_4gw"
                    ],
                    1,
                ),
                "Model Score": round(
                    blended_score(
                        player
                    ),
                    1,
                ),
            }
        )

    st.dataframe(
        pd.DataFrame(
            rows
        ),
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# TAB 7 — FIXTURES
# ============================================================

with tabs[6]:

    st.header(
        "📅 Fixture Swings"
    )

    if my_squad:

        st.subheader(
            "Your Squad"
        )

        rows = []

        for player in my_squad:

            rows.append(
                {
                    "Player": player[
                        "name"
                    ],
                    "Club": player[
                        "team"
                    ],
                    "Pos": player[
                        "position"
                    ],
                    "FDR": round(
                        player[
                            "fdr"
                        ],
                        2,
                    ),
                    "Fixtures": player[
                        "fixtures"
                    ],
                }
            )

        st.dataframe(
            pd.DataFrame(
                rows
            ).sort_values(
                "FDR"
            ),
            use_container_width=True,
            hide_index=True,
        )

    st.divider()

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

        st.subheader(
            "🟢 Getting Easier"
        )

        for (
            name,
            near,
            later,
        ) in improving:

            st.write(
                f"**{name}** "
                f"{near:.1f} ➜ "
                f"{later:.1f}"
            )

    with c2:

        st.subheader(
            "🔴 Getting Tougher"
        )

        for (
            name,
            near,
            later,
        ) in worsening:

            st.write(
                f"**{name}** "
                f"{near:.1f} ➜ "
                f"{later:.1f}"
            )


# ============================================================
# TAB 8 — CHIPS
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

        briefing = (
            generate_manager_briefing(
                my_squad,
                bank,
                free_transfers,
            )
        )

        st.info(
            briefing[
                "chip_advice"
            ]
        )

        try:

            live_points = (
                get_live_gw(
                    current_gw
                )
            )

            bench = [
                player
                for player
                in my_squad
                if player.get(
                    "multiplier",
                    1,
                ) == 0
            ]

            rows = []

            total = 0

            for player in bench:

                points = live_points.get(
                    player["id"],
                    0,
                )

                total += points

                rows.append(
                    {
                        "Player": player[
                            "name"
                        ],
                        "GW Points": points,
                    }
                )

            st.subheader(
                "🪑 Bench Points"
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
                f"{total} pts",
            )

        except Exception:

            st.info(
                "Live bench points unavailable."
            )


# ============================================================
# TAB 9 — MINI LEAGUE
# ============================================================

with tabs[8]:

    st.header(
        "🕵️ Mini-League"
    )

    if not league_id.strip():

        st.info(
            "Enter your Mini-League ID."
        )

    else:

        try:

            league = get_league(
                safe_int(
                    league_id.strip()
                )
            )

            standings = (
                league
                .get(
                    "standings",
                    {}
                )
                .get(
                    "results",
                    []
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
                    "Total Points": row.get(
                        "total"
                    ),
                    "GW Points": row.get(
                        "event_total"
                    ),
                }
                for row
                in standings
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
                "Couldn't retrieve "
                "mini-league."
            )

            st.caption(
                str(exc)
            )


# ============================================================
# TAB 10 — BEST XI
# ============================================================

with tabs[9]:

    st.header(
        "🏆 Best Starting XI"
    )

    st.caption(
        "IMPORTANT: This ONLY selects "
        "players from your current "
        "15-man FPL squad."
    )

    if not my_squad:

        st.info(
            "Load your squad first."
        )

    else:

        if st.button(
            "⚽ Compute Best XI",
            key="best_xi",
        ):

            result, score = best_xi(
                my_squad
            )

            if result:

                st.success(
                    f"Formation: "
                    f"**{result['formation']}**"
                )

                rows = []

                for player in result[
                    "lineup"
                ]:

                    rows.append(
                        {
                            "Player": player[
                                "name"
                            ],
                            "Club": player[
                                "team"
                            ],
                            "Pos": player[
                                "position"
                            ],
                            "xGI/90": round(
                                player[
                                    "xgi90"
                                ],
                                2,
                            ),
                            "Model Score": round(
                                blended_score(
                                    player
                                ),
                                1,
                            ),
                        }
                    )

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

                bench_rows = []

                for player in result[
                    "bench"
                ]:

                    bench_rows.append(
                        {
                            "Player": player[
                                "name"
                            ],
                            "Club": player[
                                "team"
                            ],
                            "Pos": player[
                                "position"
                            ],
                            "Model Score": round(
                                blended_score(
                                    player
                                ),
                                1,
                            ),
                        }
                    )

                st.dataframe(
                    pd.DataFrame(
                        bench_rows
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

            else:

                st.error(
                    "Unable to calculate "
                    "a valid formation."
                )


# ============================================================
# TAB 11 — ELITE MANAGERS
# ============================================================

with tabs[10]:

    render_elite_tracker()


# ============================================================
# TAB 12 — CREATOR AI
# ============================================================

with tabs[11]:

    st.header(
        "📺 Creator Intelligence"
    )

    st.caption(
        "Paste a YouTube video and the "
        "AI will stress-test the creator's "
        "recommendations against the data."
    )

    st.subheader(
        "🎙️ Monitored Creators"
    )

    cols = st.columns(5)

    for index, (
        name,
        url,
    ) in enumerate(
        CREATOR_CHANNELS.items()
    ):

        cols[
            index
        ].markdown(
            f"**[{name}]({url})**"
        )

    st.divider()

    video = st.text_input(
        "YouTube Video URL",
        placeholder=(
            "https://youtube.com/watch?v=..."
        ),
        key="creator_video",
    )

    if st.button(
        "🔎 Analyse Creator",
        key="creator_analyse",
    ):

        if not video:

            st.warning(
                "Paste a YouTube video first."
            )

        elif not get_gemini_key():

            st.error(
                "GEMINI_API_KEY is missing."
            )

        else:

            with st.spinner(
                "Reading and analysing video..."
            ):

                transcript, error = (
                    fetch_youtube_transcript(
                        video
                    )
                )

                if error:

                    st.error(
                        error
                    )

                else:

                    squad_context = (
                        "\n".join(
                            f"- {p['name']} "
                            f"({p['team']}, "
                            f"{p['position']}) "
                            f"xGI/90={p['xgi90']:.2f}, "
                            f"FDR={p['fdr']:.1f}, "
                            f"Form={p['form']:.1f}"
                            for p in my_squad
                        )
                        if my_squad
                        else
                        "No squad loaded."
                    )

                    prompt = f"""
You are an elite FPL analyst.

CURRENT GAMEWEEK:
GW{current_gw}

NEXT GAMEWEEK:
GW{next_gw}

MANAGER SQUAD:
{squad_context}

CREATOR TRANSCRIPT:
{transcript[:14000]}

Analyse the video.

Give:

1. Creator's main recommendations.
2. Players they recommend buying.
3. Players they recommend selling.
4. Captaincy recommendations.
5. Key fixture arguments.
6. Where the creator's reasoning is strong.
7. Where the creator's reasoning conflicts
   with the supplied FPL data.
8. What matters specifically for this manager.
9. Final verdict:
   FOLLOW / PARTLY FOLLOW / IGNORE.

Do not invent statistics.
Do not pretend creator opinions are facts.
Clearly separate:
CREATOR VIEW
DATA VIEW
YOUR VERDICT.
"""

                    try:

                        answer, model = (
                            gemini_generate(
                                prompt,
                                (
                                    "You are an objective "
                                    "elite FPL strategist. "
                                    "Stress-test opinions "
                                    "against the supplied "
                                    "data and never invent "
                                    "statistics."
                                ),
                            )
                        )

                        st.success(
                            f"Analysis completed using "
                            f"{model}"
                        )

                        st.markdown(
                            answer
                        )

                    except Exception as exc:

                        st.error(
                            "Creator AI failed."
                        )

                        st.code(
                            str(exc)
                        )


# ============================================================
# TAB 13 — AI ASSISTANT
# ============================================================

with tabs[12]:

    st.header(
        "💬 FPL AI Assistant"
    )

    if not get_gemini_key():

        st.warning(
            "GEMINI_API_KEY not found."
        )

    else:

        try:

            assistant_pin = (
                st.secrets.get(
                    "AI_ASSISTANT_PIN",
                    "2325",
                )
            )

        except Exception:

            assistant_pin = "2325"

        pin = st.text_input(
            "Manager PIN",
            type="password",
            key="assistant_pin",
        )

        if pin != str(
            assistant_pin
        ):

            st.info(
                "🔒 Enter the Manager PIN."
            )

        else:

            st.success(
                "🔓 Assistant unlocked."
            )

            if (
                "messages"
                not in st.session_state
            ):

                st.session_state.messages = []

            for message in (
                st.session_state.messages
            ):

                with st.chat_message(
                    message["role"]
                ):

                    st.markdown(
                        message[
                            "content"
                        ]
                    )

            user_prompt = st.chat_input(
                "Ask about transfers, captaincy, fixtures..."
            )

            if user_prompt:

                st.session_state.messages.append(
                    {
                        "role": "user",
                        "content": user_prompt,
                    }
                )

                with st.chat_message(
                    "user"
                ):

                    st.markdown(
                        user_prompt
                    )

                with st.chat_message(
                    "assistant"
                ):

                    with st.spinner(
                        "Analysing..."
                    ):

                        if my_squad:

                            squad_context = (
                                "\n".join(
                                    f"- {p['name']} "
                                    f"({p['team']}, "
                                    f"{p['position']}): "
                                    f"£{p['price']:.1f}m | "
                                    f"Form {p['form']:.1f} | "
                                    f"PPG {p['ppg']:.1f} | "
                                    f"xGI/90 {p['xgi90']:.2f} | "
                                    f"xGC/90 {p['xgc90']:.2f} | "
                                    f"FDR {p['fdr']:.1f}"
                                    for p in my_squad
                                )
                            )

                        else:

                            squad_context = (
                                "No squad loaded."
                            )

                        bank = 0

                        if team_data:

                            bank = (
                                num(
                                    team_data
                                    .get(
                                        "entry_history",
                                        {},
                                    )
                                    .get(
                                        "bank"
                                    )
                                )
                                / 10
                            )

                        transfer_summary = (
                            "No transfer analysis."
                        )

                        if my_squad:

                            evaluation = (
                                transfer_decision(
                                    my_squad,
                                    bank,
                                    free_transfers,
                                )
                            )

                            transfer_summary = (
                                evaluation[
                                    "reason"
                                ]
                            )

                        prompt = f"""
You are an elite FPL strategist.

GW:
{current_gw}

NEXT GW:
{next_gw}

FREE TRANSFERS:
{free_transfers}

BANK:
£{bank:.1f}m

SQUAD:
{squad_context}

TRANSFER MODEL:
{transfer_summary}

USER QUESTION:
{user_prompt}

Rules:

- Prioritise the user's actual squad.
- Use supplied FPL data.
- Consider fixtures.
- Consider form.
- Consider xGI/90.
- Consider xGC/90.
- Consider availability.
- Consider projected output.
- Do not invent statistics.
- Give a clear recommendation.
- Explain the reasoning.
"""

                        try:

                            answer, model = (
                                gemini_generate(
                                    prompt,
                                    (
                                        "You are an "
                                        "elite FPL "
                                        "strategist. "
                                        "Give concise, "
                                        "data-led advice "
                                        "based on the "
                                        "manager's actual "
                                        "squad."
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
    "Official FPL API + underlying metrics + "
    "decision engine + elite manager tracking."
)
