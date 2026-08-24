import streamlit as st
import requests
import pandas as pd
import math
from datetime import datetime

# ============================================================
# FPL ASSISTANT MANAGER
# REAL FPL-STYLE EXPECTED POINTS MODEL
# ============================================================
#
# SINGLE FILE VERSION
#
# This model attempts to estimate ACTUAL FPL POINTS rather
# than creating an arbitrary "player strength" score.
#
# It considers:
#
# - Appearance points
# - Goals
# - Assists
# - Clean sheets
# - Goalkeeper saves
# - Penalty saves
# - Defensive contributions
# - Bonus
# - Goals conceded
# - Yellow/red cards
# - Own goals / penalty misses
# - Expected minutes
# - xG / xA
# - Historical production
# - Fixture difficulty
# - Availability
# - Double Gameweeks
# - Captaincy
# - Transfer hits
# - Budget restructuring
#
# ============================================================


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)


# ============================================================
# API
# ============================================================

API = "https://fantasy.premierleague.com/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 FPL Assistant Manager"
}


# ============================================================
# CACHE / API HELPERS
# ============================================================

@st.cache_data(ttl=900)
def get_json(url):

    try:

        r = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        if r.status_code == 200:
            return r.json()

    except Exception:
        pass

    return None


@st.cache_data(ttl=900)
def get_bootstrap():

    return get_json(
        f"{API}/bootstrap-static/"
    )


@st.cache_data(ttl=900)
def get_fixtures():

    return get_json(
        f"{API}/fixtures/"
    )


@st.cache_data(ttl=600)
def get_manager(manager_id):

    return get_json(
        f"{API}/entry/{manager_id}/"
    )


@st.cache_data(ttl=600)
def get_manager_history(manager_id):

    return get_json(
        f"{API}/entry/{manager_id}/history/"
    )


@st.cache_data(ttl=600)
def get_manager_picks(
    manager_id,
    gw
):

    return get_json(
        f"{API}/entry/{manager_id}/event/{gw}/picks/"
    )


# ============================================================
# LOAD CORE DATA
# ============================================================

bootstrap = get_bootstrap()

if not bootstrap:

    st.error(
        "Unable to connect to the FPL API. "
        "Please refresh the page."
    )

    st.stop()


fixtures = get_fixtures() or []

players = bootstrap.get(
    "elements",
    []
)

teams = bootstrap.get(
    "teams",
    []
)

events = bootstrap.get(
    "events",
    []
)

element_types = bootstrap.get(
    "element_types",
    []
)


# ============================================================
# LOOKUP TABLES
# ============================================================

player_map = {
    p["id"]: p
    for p in players
}

team_map = {
    t["id"]: t
    for t in teams
}

position_map = {
    1: "GKP",
    2: "DEF",
    3: "MID",
    4: "FWD"
}


# ============================================================
# BASIC NUMBER HELPER
# ============================================================

def num(value, default=0.0):

    try:
        return float(value)
    except:
        return default


# ============================================================
# CURRENT / NEXT GAMEWEEK
# ============================================================

def find_next_gameweek():

    for event in events:

        if event.get("is_next"):
            return event["id"]

    for event in events:

        if event.get("is_current"):
            return event["id"]

    unfinished = [
        e["id"]
        for e in events
        if not e.get("finished")
    ]

    if unfinished:
        return min(unfinished)

    return 1


CURRENT_GW = find_next_gameweek()


# ============================================================
# FIXTURE MAP
# ============================================================

fixture_map = {}


for f in fixtures:

    event = f.get("event")

    home = f.get("team_h")
    away = f.get("team_a")

    if not event or not home or not away:
        continue

    fixture_map.setdefault(
        home,
        []
    ).append(
        {
            "gw": event,
            "opponent": away,
            "home": True,
            "difficulty": num(
                f.get(
                    "team_h_difficulty",
                    3
                )
            ),
            "finished": f.get(
                "finished",
                False
            )
        }
    )

    fixture_map.setdefault(
        away,
        []
    ).append(
        {
            "gw": event,
            "opponent": home,
            "home": False,
            "difficulty": num(
                f.get(
                    "team_a_difficulty",
                    3
                )
            ),
            "finished": f.get(
                "finished",
                False
            )
        }
    )


# ============================================================
# FPL SCORING RULES
# ============================================================

# These are the values the model uses when translating
# expected football events into expected FPL points.

GOAL_POINTS = {
    1: 10,   # GKP
    2: 6,    # DEF
    3: 5,    # MID
    4: 4     # FWD
}

CLEAN_SHEET_POINTS = {
    1: 4,
    2: 4,
    3: 1,
    4: 0
}

DEFCON_POINTS = 2

DEFCON_THRESHOLD = {
    1: None,
    2: 10,
    3: 12,
    4: 12
}

ASSIST_POINTS = 3

SAVE_POINTS = 1

PENALTY_SAVE_POINTS = 5

YELLOW_POINTS = -1
RED_POINTS = -3
OWN_GOAL_POINTS = -2
PENALTY_MISS_POINTS = -2


# ============================================================
# PLAYER AVAILABILITY
# ============================================================

def availability_probability(player):

    status = player.get(
        "status",
        "a"
    )

    chance = player.get(
        "chance_of_playing_next_round"
    )

    if status == "i":
        return 0.15

    if status == "s":
        return 0.15

    if status == "u":
        return 0.05

    if status == "d":

        if chance is not None:

            return max(
                0.05,
                min(
                    num(chance) / 100,
                    1
                )
            )

        return 0.60

    return 1.0


# ============================================================
# EXPECTED MINUTES
# ============================================================

def expected_minutes(player):

    minutes = num(
        player.get("minutes")
    )

    starts = num(
        player.get("starts")
    )

    appearances = num(
        player.get("appearances")
    )

    if appearances <= 0:

        # New players / players with no useful history
        base = 65

    else:

        minutes_per_appearance = (
            minutes /
            max(appearances, 1)
        )

        start_rate = (
            starts /
            max(appearances, 1)
        )

        base = (
            minutes_per_appearance * 0.70
            + 90 * start_rate * 0.30
        )

    # Cap at 90
    base = min(
        max(base, 20),
        90
    )

    # Availability adjustment
    base *= availability_probability(
        player
    )

    return min(
        max(base, 0),
        90
    )


# ============================================================
# MINUTES PROBABILITIES
# ============================================================

def minutes_probabilities(player):

    minutes = expected_minutes(
        player
    )

    # Approximate probability of:
    #
    # 0 minutes
    # 1-59 minutes
    # 60+ minutes
    #
    # This is deliberately conservative.

    if minutes >= 80:

        p60 = 0.85
        p1_59 = 0.08

    elif minutes >= 70:

        p60 = 0.75
        p1_59 = 0.14

    elif minutes >= 60:

        p60 = 0.62
        p1_59 = 0.20

    elif minutes >= 45:

        p60 = 0.45
        p1_59 = 0.30

    elif minutes >= 30:

        p60 = 0.30
        p1_59 = 0.35

    else:

        p60 = 0.18
        p1_59 = 0.35

    availability = availability_probability(
        player
    )

    p60 *= availability
    p1_59 *= availability

    p0 = max(
        0,
        1 - p60 - p1_59
    )

    return (
        p0,
        p1_59,
        p60
    )


# ============================================================
# FIXTURE DIFFICULTY
# ============================================================

def fixture_multiplier(difficulty):

    # FPL difficulty normally roughly 1-5.
    #
    # Easier fixture = higher attacking / CS expectation.

    difficulty = min(
        max(difficulty, 1),
        5
    )

    mapping = {
        1: 1.20,
        2: 1.10,
        3: 1.00,
        4: 0.88,
        5: 0.76
    }

    return mapping.get(
        int(round(difficulty)),
        1.0
    )


# ============================================================
# UPCOMING FIXTURES
# ============================================================

def upcoming_fixtures(
    team_id,
    start_gw,
    number_of_gws
):

    end_gw = (
        start_gw
        + number_of_gws
        - 1
    )

    return [
        f
        for f in fixture_map.get(
            team_id,
            []
        )
        if (
            start_gw
            <= f["gw"]
            <= end_gw
            and not f["finished"]
        )
    ]


# ============================================================
# GOAL / ASSIST RATES
# ============================================================

def per_90(
    total,
    minutes
):

    if minutes <= 0:
        return 0

    return (
        num(total)
        / minutes
        * 90
    )


def expected_goal_rate(player):

    minutes = num(
        player.get("minutes")
    )

    # Prefer xG when available.
    xg = num(
        player.get("expected_goals")
    )

    if xg > 0 and minutes > 0:

        return (
            xg /
            minutes
            * 90
        )

    # Fall back to actual goals.
    return per_90(
        player.get(
            "goals_scored",
            0
        ),
        minutes
    )


def expected_assist_rate(player):

    minutes = num(
        player.get("minutes")
    )

    xa = num(
        player.get("expected_assists")
    )

    if xa > 0 and minutes > 0:

        return (
            xa /
            minutes
            * 90
        )

    return per_90(
        player.get(
            "assists",
            0
        ),
        minutes
    )


# ============================================================
# CLEAN SHEET PROBABILITY
# ============================================================

def clean_sheet_probability(
    player,
    fixture
):

    team = team_map.get(
        player["team"],
        {}
    )

    strength_def = num(
        team.get(
            "strength_defence",
            1000
        )
    )

    opponent = team_map.get(
        fixture["opponent"],
        {}
    )

    opponent_attack = num(
        opponent.get(
            "strength_attack",
            1000
        )
    )

    difficulty = fixture.get(
        "difficulty",
        3
    )

    # Base clean-sheet probability.
    #
    # This is a model rather than the official FPL
    # fixture-difficulty algorithm.

    base = 0.32

    strength_ratio = (
        strength_def /
        max(
            opponent_attack,
            1
        )
    )

    strength_effect = (
        strength_ratio - 1
    ) * 0.20

    difficulty_effect = (
        3 - difficulty
    ) * 0.045

    home_bonus = (
        0.025
        if fixture["home"]
        else 0
    )

    probability = (
        base
        + strength_effect
        + difficulty_effect
        + home_bonus
    )

    return min(
        max(probability, 0.05),
        0.70
    )


# ============================================================
# EXPECTED GOALS CONCEDED
# ============================================================

def expected_goals_conceded(
    player,
    fixture
):

    opponent = team_map.get(
        fixture["opponent"],
        {}
    )

    opponent_attack = num(
        opponent.get(
            "strength_attack",
            1000
        )
    )

    team = team_map.get(
        player["team"],
        {}
    )

    team_defence = num(
        team.get(
            "strength_defence",
            1000
        )
    )

    ratio = (
        opponent_attack /
        max(team_defence, 1)
    )

    difficulty = fixture.get(
        "difficulty",
        3
    )

    base = 1.35

    expected = (
        base
        * ratio
        * (
            1
            + (difficulty - 3)
            * 0.08
        )
    )

    return min(
        max(expected, 0.25),
        3.5
    )


# ============================================================
# DEFENSIVE CONTRIBUTION PROBABILITY
# ============================================================

def defensive_contribution_probability(
    player,
    minutes
):

    threshold = DEFCON_THRESHOLD.get(
        player["element_type"]
    )

    if not threshold:
        return 0

    total_defcon = num(
        player.get(
            "defensive_contribution"
        )
    )

    season_minutes = num(
        player.get(
            "minutes"
        )
    )

    if season_minutes <= 0:
        return 0

    rate_per_90 = (
        total_defcon
        / season_minutes
        * 90
    )

    expected = (
        rate_per_90
        * minutes
        / 90
    )

    # Poisson approximation:
    #
    # P(X >= threshold)

    probability = 0

    for k in range(
        threshold,
        threshold + 60
    ):

        probability += (
            math.exp(-expected)
            * expected ** k
            / math.factorial(k)
        )

        if probability > 1:
            probability = 1
            break

    return min(
        max(probability, 0),
        1
    )


# ============================================================
# BONUS EXPECTATION
# ============================================================

def expected_bonus(
    player,
    minutes
):

    season_minutes = num(
        player.get(
            "minutes"
        )
    )

    bonus = num(
        player.get(
            "bonus"
        )
    )

    if season_minutes <= 0:
        return 0

    bonus_per_90 = (
        bonus
        / season_minutes
        * 90
    )

    expected = (
        bonus_per_90
        * minutes
        / 90
    )

    # Maximum theoretical bonus per match is 3.
    return min(
        max(expected, 0),
        2.5
    )


# ============================================================
# SAVE EXPECTATION
# ============================================================

def expected_saves(
    player,
    minutes
):

    if player["element_type"] != 1:
        return 0

    season_minutes = num(
        player.get(
            "minutes"
        )
    )

    saves = num(
        player.get(
            "saves"
        )
    )

    if season_minutes <= 0:
        return 0

    saves_per_90 = (
        saves
        / season_minutes
        * 90
    )

    return max(
        0,
        saves_per_90
        * minutes
        / 90
    )


# ============================================================
# PENALTY SAVE EXPECTATION
# ============================================================

def expected_penalty_saves(
    player,
    minutes
):

    if player["element_type"] != 1:
        return 0

    # Use historical penalty saves as a small signal.
    penalty_saves = num(
        player.get(
            "penalty_saves"
        )
    )

    season_minutes = num(
        player.get(
            "minutes"
        )
    )

    if season_minutes <= 0:
        return 0

    return (
        penalty_saves
        / season_minutes
        * minutes
    )


# ============================================================
# NEGATIVE EVENT EXPECTATION
# ============================================================

def expected_negatives(
    player,
    minutes
):

    season_minutes = num(
        player.get(
            "minutes"
        )
    )

    if season_minutes <= 0:
        return 0

    yellows = num(
        player.get(
            "yellow_cards"
        )
    )

    reds = num(
        player.get(
            "red_cards"
        )
    )

    own_goals = num(
        player.get(
            "own_goals"
        )
    )

    penalty_misses = num(
        player.get(
            "penalties_missed"
        )
    )

    scale = (
        minutes
        / season_minutes
    )

    negative = (
        yellows
        * scale
        * YELLOW_POINTS
        + reds
        * scale
        * RED_POINTS
        + own_goals
        * scale
        * OWN_GOAL_POINTS
        + penalty_misses
        * scale
        * PENALTY_MISS_POINTS
    )

    return negative


# ============================================================
# SINGLE FIXTURE EXPECTED POINTS
# ============================================================

def expected_points_for_fixture(
    player,
    fixture
):

    (
        p0,
        p1_59,
        p60
    ) = minutes_probabilities(
        player
    )

    expected_minutes_value = (
        p1_59 * 35
        + p60 * 75
    ) / 100 * 0
    # Dummy calculation deliberately replaced below.
    # We use probabilities directly.


    # --------------------------------------------------------
    # APPEARANCE
    # --------------------------------------------------------

    appearance_points = (
        p1_59 * 1
        + p60 * 2
    )


    # --------------------------------------------------------
    # EXPECTED MINUTES
    # --------------------------------------------------------

    minutes = expected_minutes(
        player
    )


    # --------------------------------------------------------
    # FIXTURE MULTIPLIER
    # --------------------------------------------------------

    fixture_mult = fixture_multiplier(
        fixture.get(
            "difficulty",
            3
        )
    )


    # --------------------------------------------------------
    # GOALS
    # --------------------------------------------------------

    goal_rate = expected_goal_rate(
        player
    )

    expected_goals = (
        goal_rate
        * minutes
        / 90
        * fixture_mult
    )

    goal_points = (
        expected_goals
        * GOAL_POINTS[
            player["element_type"]
        ]
    )


    # --------------------------------------------------------
    # ASSISTS
    # --------------------------------------------------------

    assist_rate = expected_assist_rate(
        player
    )

    expected_assists = (
        assist_rate
        * minutes
        / 90
        * fixture_mult
    )

    assist_points = (
        expected_assists
        * ASSIST_POINTS
    )


    # --------------------------------------------------------
    # CLEAN SHEET
    # --------------------------------------------------------

    cs_probability = clean_sheet_probability(
        player,
        fixture
    )

    # Need 60+ minutes for clean-sheet points.
    cs_points = (
        p60
        * cs_probability
        * CLEAN_SHEET_POINTS[
            player["element_type"]
        ]
    )


    # --------------------------------------------------------
    # GOALS CONCEDED
    # --------------------------------------------------------

    conceded = expected_goals_conceded(
        player,
        fixture
    )

    # GK and DEF lose points for every 2 goals conceded.
    #
    # Approximate probability / expected penalty.

    if player["element_type"] in [1, 2]:

        expected_conceded_penalty = (
            -0.5
            * conceded
            * p60
        )

    else:

        expected_conceded_penalty = 0


    # --------------------------------------------------------
    # GOALKEEPER SAVES
    # --------------------------------------------------------

    saves = expected_saves(
        player,
        minutes
    )

    save_points = (
        saves / 3
        * SAVE_POINTS
    )


    # --------------------------------------------------------
    # PENALTY SAVES
    # --------------------------------------------------------

    penalty_saves = expected_penalty_saves(
        player,
        minutes
    )

    penalty_save_points = (
        penalty_saves
        * PENALTY_SAVE_POINTS
    )


    # --------------------------------------------------------
    # DEFENSIVE CONTRIBUTIONS
    # --------------------------------------------------------

    defcon_probability = (
        defensive_contribution_probability(
            player,
            minutes
        )
    )

    defcon_points = (
        defcon_probability
        * DEFCON_POINTS
    )


    # --------------------------------------------------------
    # BONUS
    # --------------------------------------------------------

    bonus_points = expected_bonus(
        player,
        minutes
    )

    # Reduce bonus expectation slightly for difficult fixtures.
    bonus_points *= (
        0.85
        + fixture_mult * 0.15
    )


    # --------------------------------------------------------
    # NEGATIVES
    # --------------------------------------------------------

    negative_points = expected_negatives(
        player,
        minutes
    )


    # --------------------------------------------------------
    # TOTAL
    # --------------------------------------------------------

    total = (
        appearance_points
        + goal_points
        + assist_points
        + cs_points
        + expected_conceded_penalty
        + save_points
        + penalty_save_points
        + defcon_points
        + bonus_points
        + negative_points
    )


    return {
        "total": max(
            total,
            0
        ),
        "appearance": appearance_points,
        "goals": goal_points,
        "assists": assist_points,
        "clean_sheet": cs_points,
        "conceded": expected_conceded_penalty,
        "saves": save_points,
        "penalty_saves": penalty_save_points,
        "defcon": defcon_points,
        "bonus": bonus_points,
        "negatives": negative_points,
        "minutes": minutes,
        "fixture_multiplier": fixture_mult
    }


# ============================================================
# PLAYER PROJECTION ACROSS GAMEWEEKS
# ============================================================

def project_player(
    player,
    start_gw,
    weeks
):

    results = []

    for gw in range(
        start_gw,
        start_gw + weeks
    ):

        gw_fixtures = [
            f
            for f in fixture_map.get(
                player["team"],
                []
            )
            if (
                f["gw"] == gw
                and not f["finished"]
            )
        ]

        if not gw_fixtures:
            continue

        gw_total = 0

        for fixture in gw_fixtures:

            result = expected_points_for_fixture(
                player,
                fixture
            )

            gw_total += result["total"]

        results.append(
            {
                "gw": gw,
                "points": gw_total,
                "fixtures": len(
                    gw_fixtures
                )
            }
        )

    total = sum(
        x["points"]
        for x in results
    )

    return {
        "total": round(
            total,
            2
        ),
        "weeks": results
    }


# ============================================================
# PLAYER SHORT SUMMARY
# ============================================================

def player_projection(
    player,
    weeks=5
):

    return project_player(
        player,
        CURRENT_GW,
        weeks
    )["total"]


# ============================================================
# SQUAD RULE CHECK
# ============================================================

def valid_squad(
    squad
):

    if len(squad) != 15:
        return False

    counts = {
        1: 0,
        2: 0,
        3: 0,
        4: 0
    }

    clubs = {}

    for p in squad:

        position = p[
            "element_type"
        ]

        counts[position] += 1

        club = p["team"]

        clubs[club] = (
            clubs.get(club, 0)
            + 1
        )

    if counts[1] != 2:
        return False

    if counts[2] != 5:
        return False

    if counts[3] != 5:
        return False

    if counts[4] != 3:
        return False

    if any(
        value > 3
        for value in clubs.values()
    ):
        return False

    return True


# ============================================================
# TRANSFER VALIDATION
# ============================================================

def transfer_is_valid(
    player_out,
    player_in,
    squad,
    bank
):

    if (
        player_out["id"]
        == player_in["id"]
    ):
        return False

    if (
        player_out["element_type"]
        != player_in["element_type"]
    ):
        return False

    selling_price = num(
        player_out.get(
            "_selling_price",
            player_out["now_cost"]
        )
    ) / 10

    purchase_price = (
        player_in["now_cost"]
        / 10
    )

    if purchase_price > (
        bank
        + selling_price
        + 0.01
    ):
        return False

    new_squad = [
        p
        for p in squad
        if p["id"]
        != player_out["id"]
    ]

    new_squad.append(
        player_in
    )

    return valid_squad(
        new_squad
    )


# ============================================================
# FIND TRANSFERS
# ============================================================

def find_transfers(
    squad,
    bank,
    weeks=5
):

    options = []

    squad_ids = {
        p["id"]
        for p in squad
    }

    for player_out in squad:

        for player_in in players:

            if (
                player_in["id"]
                in squad_ids
            ):
                continue

            if (
                player_in["element_type"]
                != player_out[
                    "element_type"
                ]
            ):
                continue

            if player_in.get(
                "status"
            ) == "u":
                continue

            if not transfer_is_valid(
                player_out,
                player_in,
                squad,
                bank
            ):
                continue

            out_projection = player_projection(
                player_out,
                weeks
            )

            in_projection = player_projection(
                player_in,
                weeks
            )

            gain = (
                in_projection
                - out_projection
            )

            if gain <= 0:
                continue

            options.append(
                {
                    "out": player_out,
                    "in": player_in,
                    "gain": round(
                        gain,
                        2
                    )
                }
            )

    options.sort(
        key=lambda x: x["gain"],
        reverse=True
    )

    return options


# ============================================================
# -4 HIT ANALYSIS
# ============================================================

def hit_analysis(
    gain,
    weeks
):

    hit_cost = 4

    net = (
        gain
        - hit_cost
    )

    # The model becomes more willing to recommend
    # a hit when the gain occurs quickly.
    #
    # A 10-point gain over 5 GWs is much more
    # attractive than 10 points over 10 GWs.

    if weeks <= 2:

        required_gain = 5.0

    elif weeks <= 4:

        required_gain = 6.0

    else:

        required_gain = 7.0

    if gain >= required_gain:

        verdict = (
            "🟢 TAKE THE -4"
        )

    elif gain >= 4:

        verdict = (
            "🟡 CONSIDER THE -4"
        )

    else:

        verdict = (
            "🔴 DON'T TAKE THE -4"
        )

    return {
        "gross": gain,
        "hit": 4,
        "net": net,
        "verdict": verdict
    }


# ============================================================
# BUDGET RAISING
# ============================================================

def budget_moves(
    squad,
    weeks=5
):

    options = []

    for player_out in squad:

        current = player_projection(
            player_out,
            weeks
        )

        selling_price = num(
            player_out.get(
                "_selling_price",
                player_out["now_cost"]
            )
        ) / 10

        for player_in in players:

            if player_in["id"] == player_out["id"]:
                continue

            if (
                player_in["element_type"]
                != player_out[
                    "element_type"
                ]
            ):
                continue

            price = (
                player_in["now_cost"]
                / 10
            )

            if price >= selling_price:
                continue

            projection = player_projection(
                player_in,
                weeks
            )

            saving = (
                selling_price
                - price
            )

            loss = (
                current
                - projection
            )

            # Only consider reasonable downgrades.
            if loss > 8:
                continue

            if saving < 0.2:
                continue

            options.append(
                {
                    "out": player_out,
                    "in": player_in,
                    "saving": round(
                        saving,
                        1
                    ),
                    "loss": round(
                        loss,
                        2
                    )
                }
            )

    options.sort(
        key=lambda x:
            x["saving"]
            - x["loss"] * 0.5,
        reverse=True
    )

    return options[:15]


# ============================================================
# CAPTAIN ANALYSIS
# ============================================================

def captain_score(
    player
):

    projection = project_player(
        player,
        CURRENT_GW,
        1
    )["total"]

    minutes = expected_minutes(
        player
    )

    reliability = (
        min(
            minutes / 75,
            1
        )
    )

    return (
        projection
        * (
            0.85
            + reliability * 0.15
        )
    )


def captain_options(
    squad
):

    options = []

    for p in squad:

        projection = project_player(
            p,
            CURRENT_GW,
            1
        )["total"]

        score = captain_score(
            p
        )

        options.append(
            {
                "player": p,
                "projection": projection,
                "score": score
            }
        )

    options.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return options


# ============================================================
# CHIP ANALYSIS
# ============================================================

def future_gameweeks(
    number=10
):

    return [
        e["id"]
        for e in events
        if (
            e["id"] >= CURRENT_GW
            and not e.get("finished")
        )
    ][:number]


def analyse_chips():

    gws = future_gameweeks(
        12
    )

    results = {
        "Wildcard": [],
        "Free Hit": [],
        "Bench Boost": [],
        "Triple Captain": []
    }

    # --------------------------------------------------------
    # GAMEWEEK FIXTURE COUNTS
    # --------------------------------------------------------

    for gw in gws:

        team_counts = {}

        for f in fixtures:

            if f.get("event") != gw:
                continue

            if f.get("finished"):
                continue

            h = f.get("team_h")
            a = f.get("team_a")

            if h:
                team_counts[h] = (
                    team_counts.get(h, 0)
                    + 1
                )

            if a:
                team_counts[a] = (
                    team_counts.get(a, 0)
                    + 1
                )

        doubles = sum(
            1
            for value in team_counts.values()
            if value >= 2
        )

        blanks = sum(
            1
            for value in team_counts.values()
            if value == 0
        )

        # ----------------------------------------------------
        # WILDCARD
        # ----------------------------------------------------

        fixture_difficulties = []

        for team_id in team_counts:

            team_fixtures = [
                f
                for f in fixture_map.get(
                    team_id,
                    []
                )
                if (
                    f["gw"] == gw
                    and not f["finished"]
                )
            ]

            for f in team_fixtures:

                fixture_difficulties.append(
                    f["difficulty"]
                )

        if fixture_difficulties:

            average_difficulty = (
                sum(
                    fixture_difficulties
                )
                /
                len(
                    fixture_difficulties
                )
            )

        else:

            average_difficulty = 3

        wildcard_score = (
            (3 - average_difficulty)
            * 2
            + doubles * 1.5
            + blanks * 0.5
        )

        results[
            "Wildcard"
        ].append(
            {
                "gw": gw,
                "score": wildcard_score
            }
        )

        # ----------------------------------------------------
        # FREE HIT
        # ----------------------------------------------------

        free_hit_score = (
            doubles * 2
            + blanks * 1.5
        )

        results[
            "Free Hit"
        ].append(
            {
                "gw": gw,
                "score": free_hit_score
            }
        )

        # ----------------------------------------------------
        # BENCH BOOST
        # ----------------------------------------------------

        # Estimate how many players have a fixture
        # and how many get a double.

        players_with_fixture = 0
        double_players = 0

        for p in squad_for_chip_analysis:

            games = [
                f
                for f in fixture_map.get(
                    p["team"],
                    []
                )
                if (
                    f["gw"] == gw
                    and not f["finished"]
                )
            ]

            if games:

                players_with_fixture += 1

            if len(games) >= 2:

                double_players += 1

        bench_score = (
            players_with_fixture * 0.3
            + double_players * 2.5
        )

        results[
            "Bench Boost"
        ].append(
            {
                "gw": gw,
                "score": bench_score
            }
        )

        # ----------------------------------------------------
        # TRIPLE CAPTAIN
        # ----------------------------------------------------

        best_player_projection = 0

        for p in players:

            games = [
                f
                for f in fixture_map.get(
                    p["team"],
                    []
                )
                if (
                    f["gw"] == gw
                    and not f["finished"]
                )
            ]

            if not games:
                continue

            projection = project_player(
                p,
                gw,
                1
            )["total"]

            if len(games) >= 2:

                projection *= 1.35

            if projection > best_player_projection:

                best_player_projection = projection

        results[
            "Triple Captain"
        ].append(
            {
                "gw": gw,
                "score": best_player_projection
            }
        )

    return results


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title(
    "⚽ FPL Assistant Manager"
)

manager_id_text = st.sidebar.text_input(
    "Your FPL Team ID",
    placeholder="e.g. 3240706"
)

projection_weeks = st.sidebar.slider(
    "Projection window",
    min_value=1,
    max_value=8,
    value=5
)

allow_hits = st.sidebar.checkbox(
    "Allow -4 transfer recommendations",
    value=True
)

st.sidebar.divider()

st.sidebar.caption(
    "The projection model estimates actual FPL "
    "points rather than using a generic player score."
)


# ============================================================
# START SCREEN
# ============================================================

st.title(
    "⚽ FPL Assistant Manager"
)

st.caption(
    f"Gameweek {CURRENT_GW} | "
    "FPL scoring-based projection engine"
)

if not manager_id_text:

    st.info(
        "Enter your FPL Team ID in the sidebar."
    )

    st.markdown(
        """
### What this version does differently

The old model basically asked:

> "Is this player in good form?"

This version asks:

> **"How many FPL points is this player likely to score?"**

It estimates the actual FPL scoring categories:

**Appearance + Goals + Assists + Clean Sheets + Saves + Defensive Contributions + Bonus − expected negatives**

and then adjusts those estimates for fixtures, minutes and availability.
"""
    )

    st.stop()


# ============================================================
# VALIDATE MANAGER ID
# ============================================================

try:

    manager_id = int(
        manager_id_text
    )

except:

    st.error(
        "Please enter a valid numeric FPL Team ID."
    )

    st.stop()


# ============================================================
# LOAD MANAGER
# ============================================================

manager = get_manager(
    manager_id
)

if not manager:

    st.error(
        "Couldn't find that FPL team. "
        "Please check the Team ID."
    )

    st.stop()


# ============================================================
# LOAD PICKS
# ============================================================

picks_data = get_manager_picks(
    manager_id,
    CURRENT_GW
)

loaded_gw = CURRENT_GW

if not picks_data:

    # Try previous GW during transition periods.

    fallback = max(
        1,
        CURRENT_GW - 1
    )

    picks_data = get_manager_picks(
        manager_id,
        fallback
    )

    loaded_gw = fallback


if not picks_data:

    st.warning(
        "Your FPL picks aren't available yet. "
        "This can happen around Gameweek transitions."
    )

    st.stop()


# ============================================================
# BUILD SQUAD
# ============================================================

squad = []

for pick in picks_data.get(
    "picks",
    []
):

    player = player_map.get(
        pick.get("element")
    )

    if not player:
        continue

    p = dict(
        player
    )

    p["_selling_price"] = (
        pick.get(
            "selling_price",
            p["now_cost"]
        )
    )

    p["_position"] = pick.get(
        "position"
    )

    p["_captain"] = pick.get(
        "is_captain",
        False
    )

    p["_vice"] = pick.get(
        "is_vice_captain",
        False
    )

    squad.append(
        p
    )


# This global is used by the chip analyser.
squad_for_chip_analysis = squad


# ============================================================
# BANK
# ============================================================

bank = 0

entry_history = get_manager_history(
    manager_id
)

if entry_history:

    current_history = entry_history.get(
        "current",
        []
    )

    if current_history:

        latest = current_history[-1]

        bank = (
            num(
                latest.get(
                    "bank",
                    0
                )
            )
            / 10
        )

# Picks data usually contains the most useful current bank
# value.

entry_history_from_picks = (
    picks_data.get(
        "entry_history",
        {}
    )
)

if entry_history_from_picks:

    bank = (
        num(
            entry_history_from_picks.get(
                "bank",
                bank * 10
            )
        )
        / 10
    )


# ============================================================
# MANAGER HEADER
# ============================================================

first = manager.get(
    "player_first_name",
    ""
)

last = manager.get(
    "player_last_name",
    ""
)

manager_name = (
    first
    + " "
    + last
).strip()

st.subheader(
    f"Welcome, {manager_name or 'Manager'}"
)

h1, h2, h3, h4 = st.columns(4)

h1.metric(
    "Gameweek",
    loaded_gw
)

h2.metric(
    "Overall Rank",
    f"{manager.get('summary_overall_rank', 0):,}"
)

h3.metric(
    "Total Points",
    f"{manager.get('summary_overall_points', 0):,}"
)

h4.metric(
    "Bank",
    f"£{bank:.1f}m"
)


# ============================================================
# TABS
# ============================================================

tab_squad, tab_transfers, tab_captain, tab_chips, tab_players = st.tabs(
    [
        "👥 Squad",
        "🔄 Transfers",
        "©️ Captain",
        "🎯 Chips",
        "📊 Players"
    ]
)


# ============================================================
# SQUAD TAB
# ============================================================

with tab_squad:

    st.header(
        "Your Squad"
    )

    st.caption(
        f"Expected FPL points over the next "
        f"{projection_weeks} Gameweeks."
    )

    rows = []

    for p in squad:

        club = team_map.get(
            p["team"],
            {}
        ).get(
            "short_name",
            "?"
        )

        projection = project_player(
            p,
            CURRENT_GW,
            projection_weeks
        )

        rows.append(
            {
                "Player": p["web_name"],
                "Pos": position_map.get(
                    p["element_type"],
                    "?"
                ),
                "Club": club,
                "Price": (
                    f"£{p['now_cost']/10:.1f}m"
                ),
                "Form": num(
                    p.get("form")
                ),
                "PPG": num(
                    p.get(
                        "points_per_game"
                    )
                ),
                "Expected mins": round(
                    expected_minutes(p)
                ),
                "Projected points": projection[
                    "total"
                ],
                "Status": p.get(
                    "status",
                    ""
                )
            }
        )

    rows.sort(
        key=lambda x:
            x["Projected points"],
        reverse=True
    )

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True
    )


    # --------------------------------------------------------
    # STARTING XI
    # --------------------------------------------------------

    st.divider()

    st.subheader(
        "⭐ Recommended Starting XI"
    )

    gks = [
        p for p in squad
        if p["element_type"] == 1
    ]

    defs = [
        p for p in squad
        if p["element_type"] == 2
    ]

    mids = [
        p for p in squad
        if p["element_type"] == 3
    ]

    fwds = [
        p for p in squad
        if p["element_type"] == 4
    ]


    def sort_projected(
        players_list
    ):

        return sorted(
            players_list,
            key=lambda p:
                project_player(
                    p,
                    CURRENT_GW,
                    1
                )["total"],
            reverse=True
        )


    gks = sort_projected(
        gks
    )

    defs = sort_projected(
        defs
    )

    mids = sort_projected(
        mids
    )

    fwds = sort_projected(
        fwds
    )


    # Start with 1 GK.
    starting = []

    if gks:
        starting.append(
            gks[0]
        )


    # Minimum legal formation:
    # 3 DEF
    # 2 MID
    # 1 FWD

    starting.extend(
        defs[:3]
    )

    starting.extend(
        mids[:2]
    )

    starting.extend(
        fwds[:1]
    )


    remaining = [
        p for p in squad
        if p["id"]
        not in {
            x["id"]
            for x in starting
        }
    ]


    # Add best four while maintaining legal formation.

    while len(starting) < 11:

        candidates = []

        current_defs = sum(
            1
            for p in starting
            if p["element_type"] == 2
        )

        current_mids = sum(
            1
            for p in starting
            if p["element_type"] == 3
        )

        current_fwds = sum(
            1
            for p in starting
            if p["element_type"] == 4
        )

        for p in remaining:

            pos = p[
                "element_type"
            ]

            if pos == 2 and current_defs >= 5:
                continue

            if pos == 3 and current_mids >= 5:
                continue

            if pos == 4 and current_fwds >= 3:
                continue

            candidates.append(
                p
            )

        if not candidates:
            break

        candidates.sort(
            key=lambda p:
                project_player(
                    p,
                    CURRENT_GW,
                    1
                )["total"],
            reverse=True
        )

        best = candidates[0]

        starting.append(
            best
        )

        remaining = [
            p
            for p in remaining
            if p["id"]
            != best["id"]
        ]


    starting_projection = sum(
        project_player(
            p,
            CURRENT_GW,
            1
        )["total"]
        for p in starting
    )


    st.metric(
        "Expected starting XI points",
        f"{starting_projection:.1f}"
    )


    starting_rows = []

    for p in starting:

        proj = project_player(
            p,
            CURRENT_GW,
            1
        )

        starting_rows.append(
            {
                "Player": p["web_name"],
                "Pos": position_map[
                    p["element_type"]
                ],
                "Expected points": round(
                    proj["total"],
                    2
                ),
                "Expected mins": round(
                    proj["weeks"][0]["points"]
                    if proj["weeks"]
                    else 0,
                    2
                )
            }
        )

    st.dataframe(
        starting_rows,
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# TRANSFERS TAB
# ============================================================

with tab_transfers:

    st.header(
        "🔄 Transfer Planner"
    )

    st.write(
        "Transfers are ranked by estimated ACTUAL FPL points "
        f"over the next {projection_weeks} Gameweeks."
    )

    transfer_options = find_transfers(
        squad,
        bank,
        projection_weeks
    )


    if not transfer_options:

        st.success(
            "No significant upgrade has been identified."
        )

    else:

        for i, option in enumerate(
            transfer_options[:10],
            1
        ):

            out = option["out"]
            incoming = option["in"]

            gain = option["gain"]

            hit = hit_analysis(
                gain,
                projection_weeks
            )

            st.subheader(
                f"{i}. "
                f"{out['web_name']} → "
                f"{incoming['web_name']}"
            )

            a, b, c, d = st.columns(4)

            a.metric(
                "Projected gain",
                f"+{gain:.1f}"
            )

            b.metric(
                "After -4",
                f"{hit['net']:+.1f}"
            )

            c.metric(
                "Incoming projection",
                f"{player_projection(incoming, projection_weeks):.1f}"
            )

            d.metric(
                "Outgoing projection",
                f"{player_projection(out, projection_weeks):.1f}"
            )


            if allow_hits:

                st.write(
                    f"**-4 assessment:** "
                    f"{hit['verdict']}"
                )

                if (
                    hit["verdict"]
                    == "🟢 TAKE THE -4"
                ):

                    st.success(
                        "The expected improvement is large enough "
                        "for the model to consider spending 4 points."
                    )

                elif (
                    hit["verdict"]
                    == "🟡 CONSIDER THE -4"
                ):

                    st.warning(
                        "The move may be worthwhile, but the "
                        "projection does not give a large safety margin."
                    )

                else:

                    st.info(
                        "Do not take a -4 for this move based "
                        "on the current projection."
                    )

            else:

                st.info(
                    "Hit recommendations are disabled."
                )

            st.divider()


    # ========================================================
    # BUDGET RESTRUCTURING
    # ========================================================

    st.header(
        "💰 Budget Restructuring"
    )

    st.write(
        "These are cases where selling a relatively expensive "
        "player could free money for a bigger upgrade elsewhere."
    )

    budget_options = budget_moves(
        squad,
        projection_weeks
    )

    if not budget_options:

        st.info(
            "No attractive budget-raising moves found."
        )

    else:

        budget_rows = []

        for option in budget_options:

            out = option["out"]
            incoming = option["in"]

            budget_rows.append(
                {
                    "Sell": out["web_name"],
                    "Buy": incoming["web_name"],
                    "Money freed": (
                        f"£{option['saving']:.1f}m"
                    ),
                    "Projected points sacrificed": (
                        option["loss"]
                    )
                }
            )

        st.dataframe(
            budget_rows,
            use_container_width=True,
            hide_index=True
        )

        st.caption(
            "These are not automatically recommended transfers. "
            "They show where money could be released to fund "
            "a stronger upgrade elsewhere."
        )


# ============================================================
# CAPTAIN TAB
# ============================================================

with tab_captain:

    st.header(
        "©️ Captain Matrix"
    )

    options = captain_options(
        squad
    )

    if options:

        best = options[0]

        st.success(
            f"### Captain: "
            f"{best['player']['web_name']}"
        )

        st.metric(
            "Expected FPL points",
            f"{best['projection']:.2f}"
        )

        if len(options) > 1:

            second = options[1]

            st.write(
                f"**Vice-captain:** "
                f"{second['player']['web_name']} "
                f"({second['projection']:.2f} expected points)"
            )


        captain_rows = []

        for option in options[:10]:

            captain_rows.append(
                {
                    "Player": option[
                        "player"
                    ]["web_name"],
                    "Position": position_map[
                        option[
                            "player"
                        ]["element_type"]
                    ],
                    "Expected points": round(
                        option[
                            "projection"
                        ],
                        2
                    ),
                    "Expected minutes": round(
                        expected_minutes(
                            option[
                                "player"
                            ]
                        )
                    )
                }
            )

        st.dataframe(
            captain_rows,
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# CHIP TAB
# ============================================================

with tab_chips:

    st.header(
        "🎯 Chip Planner"
    )

    st.write(
        "The chip planner looks for Double Gameweeks, "
        "Blank Gameweeks and unusually strong fixture runs."
    )

    chip_results = analyse_chips()

    for chip_name, values in chip_results.items():

        st.subheader(
            chip_name
        )

        if not values:

            st.info(
                "No data available."
            )

            continue

        values = sorted(
            values,
            key=lambda x:
                x["score"],
            reverse=True
        )

        best = values[0]

        st.success(
            f"Best current opportunity: "
            f"**GW{best['gw']}**"
        )

        chip_rows = []

        for x in values[:6]:

            chip_rows.append(
                {
                    "Gameweek": x["gw"],
                    "Opportunity score": round(
                        x["score"],
                        2
                    )
                }
            )

        st.dataframe(
            chip_rows,
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# PLAYER RANKINGS
# ============================================================

with tab_players:

    st.header(
        "📊 Player Expected Points"
    )

    st.caption(
        "This ranking uses estimated FPL points, "
        "not the old form/PPG score."
    )

    ranking = []

    for p in players:

        if p.get("status") == "u":
            continue

        projection = player_projection(
            p,
            projection_weeks
        )

        price = (
            num(
                p.get("now_cost")
            )
            / 10
        )

        if price <= 0:
            continue

        ranking.append(
            {
                "Player": p[
                    "web_name"
                ],
                "Pos": position_map[
                    p[
                        "element_type"
                    ]
                ],
                "Price": (
                    f"£{price:.1f}m"
                ),
                "Expected points": round(
                    projection,
                    2
                ),
                "Points / £m": round(
                    projection
                    / price,
                    2
                ),
                "Form": num(
                    p.get("form")
                ),
                "Ownership": (
                    f"{num(p.get('selected_by_percent')):.1f}%"
                )
            }
        )

    ranking.sort(
        key=lambda x:
            x["Expected points"],
        reverse=True
    )

    st.dataframe(
        ranking[:75],
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# WHY A PLAYER GETS HIS PROJECTED POINTS
# ============================================================

with tab_players:

    st.divider()

    st.header(
        "🔍 Projection Breakdown"
    )

    player_names = [
        p["web_name"]
        for p in squad
    ]

    if player_names:

        selected_name = st.selectbox(
            "Select one of your players",
            player_names
        )

        selected_player = next(
            p
            for p in squad
            if p["web_name"]
            == selected_name
        )

        gw_projection = project_player(
            selected_player,
            CURRENT_GW,
            projection_weeks
        )

        breakdown_rows = []

        for gw_data in gw_projection[
            "weeks"
        ]:

            gw = gw_data[
                "gw"
            ]

            fixtures_for_player = [
                f
                for f in fixture_map.get(
                    selected_player["team"],
                    []
                )
                if (
                    f["gw"] == gw
                    and not f["finished"]
                )
            ]

            total = 0

            for fixture in fixtures_for_player:

                result = expected_points_for_fixture(
                    selected_player,
                    fixture
                )

                total += result[
                    "total"
                ]

            breakdown_rows.append(
                {
                    "GW": gw,
                    "Fixtures": len(
                        fixtures_for_player
                    ),
                    "Expected FPL points": round(
                        total,
                        2
                    )
                }
            )

        st.dataframe(
            breakdown_rows,
            use_container_width=True,
            hide_index=True
        )

        st.metric(
            f"Total expected FPL points "
            f"next {projection_weeks} GWs",
            f"{gw_projection['total']:.2f}"
        )


# ============================================================
# FINAL ASSISTANT SUMMARY
# ============================================================

st.divider()

st.header(
    "🤖 Assistant Manager Summary"
)

if transfer_options:

    best = transfer_options[0]

    hit = hit_analysis(
        best["gain"],
        projection_weeks
    )

    st.write(
        f"**Best transfer:** "
        f"{best['out']['web_name']} → "
        f"{best['in']['web_name']}"
    )

    st.write(
        f"Projected improvement: "
        f"**+{best['gain']:.1f} FPL points** "
        f"over the next {projection_weeks} Gameweeks."
    )

    if (
        allow_hits
        and hit["verdict"]
        == "🟢 TAKE THE -4"
    ):

        st.success(
            f"The model believes the -4 could be justified. "
            f"Estimated gain after the hit: "
            f"**+{hit['net']:.1f} points**."
        )

    else:

        st.info(
            "The model does not currently recommend "
            "taking a -4 for the top move."
        )

else:

    st.success(
        "No major transfer currently stands out."
    )


st.caption(
    "Important: these are probability-based projections, "
    "not guarantees. Football is unpredictable, and the "
    "model should be used alongside team news, injuries, "
    "rotation information and your own judgement."
)
