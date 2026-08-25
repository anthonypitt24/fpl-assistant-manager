from collections import defaultdict
import re

import pandas as pd
import requests
import streamlit as st
from google import genai

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None


# ============================================================
# FPL ASSISTANT MANAGER — ULTIMATE DECISION ENGINE
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)

API = "https://fantasy.premierleague.com/api"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

FIXTURE_HORIZON = 5
PROJECTION_WEEKS = 5
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
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=20
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_info(entry_id):
    return api_get(f"{API}/entry/{entry_id}/")


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
# BASIC HELPERS
# ============================================================

def num(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value, default=0):
    try:
        return int(value)
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


def player_is_available(player):
    return (
        player.get("status") == "a"
        and player.get("chance", 0) >= 75
    )


# ============================================================
# FIXTURE HELPERS
# ============================================================

def fixture_count(fixture_map, team_id, gw):
    return len([
        f for f in fixture_map.get(team_id, [])
        if f["gw"] == gw
    ])


def fixtures_for_gw(fixture_map, team_id, gw):
    return [
        f for f in fixture_map.get(team_id, [])
        if f["gw"] == gw
    ]


def average_fdr(fixture_map, team_id, weeks=None):
    games = sorted(
        fixture_map.get(team_id, []),
        key=lambda x: (x["gw"], not x["home"])
    )

    if weeks is not None:
        selected_gws = sorted(
            set(f["gw"] for f in games)
        )[:weeks]

        games = [
            f for f in games
            if f["gw"] in selected_gws
        ]

    if not games:
        return 3.0

    return sum(
        num(f["difficulty"], 3)
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
        key=lambda x: (x["gw"], not x["home"])
    )[:number]

    output = []

    for fixture in games:
        opponent = team_names.get(
            fixture["opponent"],
            "?"
        )

        location = "H" if fixture["home"] else "A"

        output.append(
            f"GW{fixture['gw']} "
            f"{opponent} ({location}) "
            f"[{fixture['difficulty']}]"
        )

    return (
        " | ".join(output)
        if output
        else "No fixtures"
    )


def fixture_summary(
    fixture_map,
    team_names,
    team_id,
    start_gw,
    weeks=5
):
    rows = []

    for gw in range(
        start_gw,
        start_gw + weeks
    ):
        games = fixtures_for_gw(
            fixture_map,
            team_id,
            gw
        )

        if not games:
            rows.append({
                "gw": gw,
                "fixtures": [],
                "count": 0,
                "avg_fdr": None
            })
            continue

        rows.append({
            "gw": gw,
            "fixtures": games,
            "count": len(games),
            "avg_fdr": (
                sum(
                    f["difficulty"]
                    for f in games
                ) / len(games)
            )
        })

    return rows


# ============================================================
# PLAYER METRICS
# ============================================================

def price_momentum_flag(player):
    net = num(player.get("net_transfers"))
    ownership = max(
        num(player.get("ownership")),
        0.1
    )

    ratio = net / (ownership * 1000)

    if ratio > 0.4:
        return "📈 Likely rise"

    if ratio < -0.4:
        return "📉 Likely fall"

    return "— Stable"


def minutes_factor(player):
    minutes = num(player.get("minutes"))

    if minutes >= 1800:
        return 1.0

    if minutes >= 1200:
        return 0.95

    if minutes >= 800:
        return 0.90

    if minutes >= 400:
        return 0.78

    if minutes >= 200:
        return 0.65

    return 0.50


def attacking_score(player):
    xgi = max(
        num(player.get("xgi90")),
        0
    )

    return min(
        xgi * 8,
        14
    )


def defensive_score(player):
    if player.get("position") not in (
        "GK",
        "DEF"
    ):
        return 0

    xgc = num(
        player.get("xgc90"),
        1.0
    )

    clean_sheet_component = max(
        0,
        (1.5 - xgc) * 4
    )

    return min(
        clean_sheet_component,
        6
    )


# ============================================================
# BLENDED MODEL
# ============================================================

def calc_blended_score(player):
    """
    General player score.

    Designed to balance:
    - historical FPL production
    - current form
    - expected points
    - underlying attacking numbers
    - defensive numbers
    - fixture difficulty
    - availability
    - minutes security
    - doubles/blanks
    """

    ppg = min(
        num(player.get("ppg")) * 1.4,
        9
    )

    form = min(
        num(player.get("form")) * 1.25,
        9
    )

    expected = min(
        num(player.get("ep_next")) * 2.5,
        16
    )

    fdr = num(
        player.get("fdr"),
        3
    )

    fixture_score = max(
        0,
        (3.2 - fdr) * 3
    )

    availability = (
        availability_factor(player) * 5
    )

    attacking = attacking_score(player)

    defensive = defensive_score(player)

    minutes = minutes_factor(player) * 3

    dgw_bonus = (
        5
        if player.get("next_gw_fixtures", 1) >= 2
        else 0
    )

    blank_penalty = (
        7
        if player.get("next_gw_fixtures", 1) == 0
        else 0
    )

    differential_bonus = 0

    if (
        num(player.get("ownership")) < 5
        and num(player.get("xgi90")) >= 0.25
    ):
        differential_bonus = 2

    score = (
        ppg
        + form
        + expected
        + fixture_score
        + availability
        + attacking
        + defensive
        + minutes
        + dgw_bonus
        + differential_bonus
        - blank_penalty
    )

    return round(score, 2)


# ============================================================
# GAMEWEEK PROJECTION ENGINE
# ============================================================

def fixture_multiplier(difficulty):
    """
    FPL difficulty:
    1 = very favourable
    5 = very difficult
    """

    difficulty = num(
        difficulty,
        3
    )

    return max(
        0.65,
        min(
            1.35,
            1.12 - (
                (difficulty - 3) * 0.12
            )
        )
    )


def base_expected_points(player):
    ep = num(
        player.get("ep_next")
    )

    ppg = num(
        player.get("ppg")
    )

    xgi = num(
        player.get("xgi90")
    )

    return max(
        0.5,
        (
            ep * 0.55
            + ppg * 0.20
            + xgi * 2.0
        )
    )


def project_gameweek(
    player,
    fixture_map,
    gw
):
    games = fixtures_for_gw(
        fixture_map,
        player["team_id"],
        gw
    )

    if not games:
        return 0.0

    availability = availability_factor(
        player
    )

    minutes = minutes_factor(
        player
    )

    base = base_expected_points(
        player
    )

    total = 0.0

    for fixture in games:
        multiplier = fixture_multiplier(
            fixture["difficulty"]
        )

        total += (
            base
            * multiplier
            * availability
            * minutes
        )

    return total


def multi_gw_projection(
    player,
    fixture_map,
    start_gw,
    weeks=PROJECTION_WEEKS
):
    total = 0.0

    for gw in range(
        start_gw,
        start_gw + weeks
    ):
        total += project_gameweek(
            player,
            fixture_map,
            gw
        )

    return round(
        total,
        1
    )


def next_gw_projection(
    player,
    fixture_map,
    next_gw
):
    return round(
        project_gameweek(
            player,
            fixture_map,
            next_gw
        ),
        1
    )


# ============================================================
# CAPTAIN MODEL
# ============================================================

def captain_score(
    player,
    fixture_map,
    next_gw
):
    if not player_is_available(player):
        return -999

    gw_fixtures = fixtures_for_gw(
        fixture_map,
        player["team_id"],
        next_gw
    )

    if not gw_fixtures:
        return -999

    projection = project_gameweek(
        player,
        fixture_map,
        next_gw
    )

    xgi = num(
        player.get("xgi90")
    )

    form = num(
        player.get("form")
    )

    ppg = num(
        player.get("ppg")
    )

    reliability = minutes_factor(
        player
    )

    score = (
        projection * 2.0
        + xgi * 4
        + form * 0.7
        + ppg * 0.5
        + reliability * 2
    )

    if len(gw_fixtures) >= 2:
        score *= 1.15

    return round(
        score,
        2
    )


def captain_recommendations(
    squad,
    fixture_map,
    next_gw
):
    available = [
        p
        for p in squad
        if player_is_available(p)
        and p.get("next_gw_fixtures", 0) > 0
    ]

    for player in available:
        player["_captain_score"] = captain_score(
            player,
            fixture_map,
            next_gw
        )

    available.sort(
        key=lambda p: p["_captain_score"],
        reverse=True
    )

    return available[:5]


# ============================================================
# TRANSFER ENGINE
# ============================================================

def squad_club_counts(
    squad,
    exclude_id=None
):
    counts = defaultdict(int)

    for player in squad:
        if player["id"] == exclude_id:
            continue

        counts[
            player["team_id"]
        ] += 1

    return counts


def transfer_candidate_score(
    outgoing,
    incoming,
    fixture_map,
    next_gw,
    weeks,
    hit
):
    outgoing_projection = (
        multi_gw_projection(
            outgoing,
            fixture_map,
            next_gw,
            weeks
        )
    )

    incoming_projection = (
        multi_gw_projection(
            incoming,
            fixture_map,
            next_gw,
            weeks
        )
    )

    raw_gain = (
        incoming_projection
        - outgoing_projection
    )

    next_gw_gain = (
        next_gw_projection(
            incoming,
            fixture_map,
            next_gw
        )
        -
        next_gw_projection(
            outgoing,
            fixture_map,
            next_gw
        )
    )

    fixture_gain = (
        outgoing["fdr"]
        - incoming["fdr"]
    )

    score = (
        raw_gain
        + next_gw_gain * 0.45
        + fixture_gain * 1.2
        - hit
    )

    return {
        "outgoing_projection": outgoing_projection,
        "incoming_projection": incoming_projection,
        "projected_gain": raw_gain,
        "next_gw_gain": next_gw_gain,
        "fixture_gain": fixture_gain,
        "net_gain": raw_gain - hit,
        "score": score
    }


def transfer_suggestions(
    squad,
    bank,
    free_transfers,
    weeks=PROJECTION_WEEKS
):
    owned_ids = {
        p["id"]
        for p in squad
    }

    club_counts = squad_club_counts(
        squad
    )

    suggestions = []

    hit = (
        0
        if free_transfers > 0
        else TRANSFER_HIT
    )

    for outgoing in squad:

        candidates = [
            p
            for p in players
            if (
                p["id"] not in owned_ids
                and p["position"]
                == outgoing["position"]
                and p["status"] == "a"
                and p["chance"] >= 75
            )
        ]

        available_budget = (
            bank
            + outgoing["price"]
        )

        candidates = [
            p
            for p in candidates
            if p["price"]
            <= available_budget
        ]

        candidates.sort(
            key=blended_score,
            reverse=True
        )

        # Avoid calculating hundreds of combinations
        for incoming in candidates[:40]:

            projected_counts = (
                club_counts.copy()
            )

            projected_counts[
                outgoing["team_id"]
            ] -= 1

            projected_counts[
                incoming["team_id"]
            ] += 1

            if (
                projected_counts[
                    incoming["team_id"]
                ] > MAX_PER_CLUB
            ):
                continue

            metrics = transfer_candidate_score(
                outgoing,
                incoming,
                fixture_map,
                next_gw,
                weeks,
                hit
            )

            # Avoid silly moves
            if metrics["projected_gain"] < 1.0:
                continue

            if free_transfers > 0:
                # Free transfer:
                # favour genuine upgrades.
                if (
                    metrics["projected_gain"] < 2.0
                    and metrics["fixture_gain"] < 0.5
                ):
                    continue

            else:
                # Hit:
                # require stronger upside.
                if metrics["net_gain"] < 1.5:
                    continue

            suggestions.append({
                "out": outgoing,
                "in": incoming,
                "hit": hit,
                "cost_difference": (
                    incoming["price"]
                    - outgoing["price"]
                ),
                **metrics
            })

    suggestions.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return suggestions[:15]


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
                "No transfer provides enough "
                "projected improvement to justify "
                "using the transfer."
            ),
            "suggestions": []
        }

    best = suggestions[0]

    if free_transfers > 0:

        if (
            best["projected_gain"] >= 4.0
            or (
                best["projected_gain"] >= 2.5
                and best["fixture_gain"] >= 0.5
            )
        ):
            decision = "TRANSFER"

            reason = (
                f"{best['in']['name']} projects "
                f"+{best['projected_gain']:.1f} points "
                f"over the next {PROJECTION_WEEKS} GWs "
                f"versus {best['out']['name']}."
            )

        else:
            decision = "ROLL"

            reason = (
                "There is an upgrade available, "
                "but the model does not consider it "
                "strong enough to sacrifice the "
                "transfer."
            )

    else:

        if best["net_gain"] >= 2.0:
            decision = "TAKE HIT"

            reason = (
                f"{best['in']['name']} projects "
                f"+{best['projected_gain']:.1f} points, "
                f"leaving +{best['net_gain']:.1f} "
                f"after the -4 hit."
            )

        else:
            decision = "ROLL"

            reason = (
                "The best available move does not "
                "justify the -4 point penalty."
            )

    return {
        "decision": decision,
        "reason": reason,
        "suggestions": suggestions
    }


# ============================================================
# HOLD / SELL
# ============================================================

def hold_sell(player):
    if player["status"] != "a":
        return "🔴 SELL / REPLACE"

    if player["chance"] < 50:
        return "🔴 SELL / REPLACE"

    if player["chance"] < 75:
        return "🟠 CONSIDER SELLING"

    if player["next_gw_fixtures"] == 0:
        return "🟡 MONITOR — BLANK"

    if (
        player["form"] < 2.5
        and player["ppg"] < 3
        and player["minutes"] > 300
        and player["fdr"] >= 3.5
    ):
        return "🔴 SELL"

    if (
        player["form"] >= 5
        or player["ppg"] >= 5
    ):
        return "🟢 STRONG HOLD"

    if player["fdr"] <= 2.6:
        return "🟢 HOLD — GOOD FIXTURES"

    return "🟡 MONITOR"


# ============================================================
# BEST XI
# ============================================================

def best_xi(squad):
    """
    IMPORTANT:
    This ONLY selects players already in the user's squad.
    It never searches the wider player pool.
    """

    by_pos = defaultdict(list)

    for player in squad:
        by_pos[
            player["position"]
        ].append(player)

    for position in by_pos:
        by_pos[position].sort(
            key=blended_score,
            reverse=True
        )

    gks = by_pos.get("GK", [])
    defs = by_pos.get("DEF", [])
    mids = by_pos.get("MID", [])
    fwds = by_pos.get("FWD", [])

    if not gks:
        return None, 0

    best_formation = None
    best_score = -1
    best_lineup = None

    for d, m, f in VALID_FORMATIONS:

        if len(defs) < d:
            continue

        if len(mids) < m:
            continue

        if len(fwds) < f:
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
            best_formation = (
                f"{d}-{m}-{f}"
            )
            best_lineup = lineup

    if not best_lineup:
        return None, 0

    bench = [
        p
        for p in squad
        if p not in best_lineup
    ]

    return {
        "formation": best_formation,
        "lineup": best_lineup,
        "bench": bench
    }, best_score


# ============================================================
# CHIP ENGINE
# ============================================================

def bench_strength_score(
    player,
    fixture_map,
    next_gw
):
    return (
        next_gw_projection(
            player,
            fixture_map,
            next_gw
        )
        * availability_factor(player)
    )


def chip_analysis(
    squad,
    fixture_map,
    next_gw
):
    if not squad:
        return {
            "bench_boost": 0,
            "free_hit": 0,
            "triple_captain": 0,
            "wildcard": 0,
            "message": "Load your squad first."
        }

    next_gw_rows = []

    for player in squad:
        projected = next_gw_projection(
            player,
            fixture_map,
            next_gw
        )

        next_gw_rows.append({
            "player": player,
            "projection": projected,
            "fixtures": player[
                "next_gw_fixtures"
            ]
        })

    starters, _ = best_xi(squad)

    if starters:
        starting_ids = {
            p["id"]
            for p in starters["lineup"]
        }
    else:
        starting_ids = set()

    bench = [
        row
        for row in next_gw_rows
        if row["player"]["id"]
        not in starting_ids
    ]

    bench_projection = sum(
        row["projection"]
        for row in bench
    )

    strong_bench = sum(
        1
        for row in bench
        if row["projection"] >= 3.5
        and row["player"]["chance"] >= 75
        and row["fixtures"] > 0
    )

    blank_count = sum(
        1
        for row in next_gw_rows
        if row["fixtures"] == 0
    )

    double_count = sum(
        1
        for row in next_gw_rows
        if row["fixtures"] >= 2
    )

    captain_options = captain_recommendations(
        squad,
        fixture_map,
        next_gw
    )

    tc_score = 0

    if captain_options:
        top = captain_options[0]

        tc_score = (
            next_gw_projection(
                top,
                fixture_map,
                next_gw
            )
            + (
                2
                if top["next_gw_fixtures"] >= 2
                else 0
            )
        )

    free_hit_score = (
        blank_count * 2
        + max(
            0,
            4 - strong_bench
        )
    )

    wildcard_score = 0

    weak_players = sum(
        1
        for p in squad
        if (
            p["chance"] < 75
            or p["fdr"] >= 3.8
            or p["form"] < 2.5
        )
    )

    if weak_players >= 5:
        wildcard_score = 8
    elif weak_players >= 3:
        wildcard_score = 5
    else:
        wildcard_score = 2

    if free_hit_score >= 8:
        message = (
            "⚠️ Free Hit is worth considering. "
            "Your squad has a significant number "
            "of blanking players or weak coverage."
        )

    elif (
        strong_bench >= 3
        and double_count >= 4
    ):
        message = (
            "⚡ Bench Boost window looks promising. "
            "You have multiple playable bench assets "
            "with strong next-GW potential."
        )

    elif tc_score >= 8:
        message = (
            "👑 Triple Captain candidate detected. "
            "Your best captain has unusually strong "
            "next-GW projection."
        )

    elif wildcard_score >= 7:
        message = (
            "🃏 Wildcard is worth monitoring. "
            "Several squad members have poor "
            "availability, form or fixtures."
        )

    else:
        message = (
            "✅ Hold chips for now. "
            "No major chip opportunity is detected."
        )

    return {
        "bench_boost": round(
            bench_projection,
            1
        ),
        "free_hit": free_hit_score,
        "triple_captain": round(
            tc_score,
            1
        ),
        "wildcard": wildcard_score,
        "strong_bench": strong_bench,
        "blank_count": blank_count,
        "double_count": double_count,
        "message": message
    }


# ============================================================
# BENCH BOOST — LIVE/HISTORICAL
# ============================================================

def bench_boost_value(
    squad,
    current_gw
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
        if p.get("multiplier", 1) == 0
    ]

    rows = []
    total = 0

    for player in bench:
        points = live_points.get(
            player["id"],
            0
        )

        total += points

        rows.append({
            "Player": player["name"],
            "GW Points": points
        })

    return rows, total


# ============================================================
# STRATEGY BRIEFING
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
        squad,
        fixture_map,
        next_gw
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

    transfer_eval = transfer_decision(
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

    chips = chip_analysis(
        squad,
        fixture_map,
        next_gw
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
        "chip_advice": chips["message"],
        "chips": chips
    }


# ============================================================
# DATA LOADER
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
        t["id"]: t.get(
            "short_name",
            "?"
        )
        for t in raw_teams
    }

    positions = {
        1: "GK",
        2: "DEF",
        3: "MID",
        4: "FWD"
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

    current_gw = (
        current_event["id"]
        if current_event
        else 1
    )

    next_gw = (
        next_event["id"]
        if next_event
        else current_gw + 1
    )

    # --------------------------------------------------------
    # FIXTURE MAP
    # --------------------------------------------------------

    fixture_map = defaultdict(list)

    for fixture in fixtures_raw:

        gw = fixture.get("event")

        if gw is None:
            continue

        if gw < next_gw:
            continue

        if gw > (
            next_gw
            + FIXTURE_HORIZON
            - 1
        ):
            continue

        home = fixture.get("team_h")
        away = fixture.get("team_a")

        if home:

            fixture_map[home].append({
                "gw": gw,
                "home": True,
                "opponent": away,
                "difficulty": fixture.get(
                    "team_h_difficulty",
                    3
                )
            })

        if away:

            fixture_map[away].append({
                "gw": gw,
                "home": False,
                "opponent": home,
                "difficulty": fixture.get(
                    "team_a_difficulty",
                    3
                )
            })

    # --------------------------------------------------------
    # PLAYERS
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
                "transfers_in_event",
                0
            )
        )

        transfers_out = safe_int(
            raw.get(
                "transfers_out_event",
                0
            )
        )

        player = {
            "id": raw.get("id"),
            "name": raw.get(
                "web_name",
                "?"
            ),
            "full_name": (
                f"{raw.get('first_name', '')} "
                f"{raw.get('second_name', '')}"
            ).strip(),

            "position": positions.get(
                raw.get("element_type"),
                "?"
            ),

            "team_id": team_id,

            "team": team_names.get(
                team_id,
                "?"
            ),

            "price": num(
                raw.get("now_cost")
            ) / 10,

            "points": safe_int(
                raw.get("total_points", 0)
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
                raw.get("minutes", 0)
            ),

            "goals": safe_int(
                raw.get("goals_scored", 0)
            ),

            "assists": safe_int(
                raw.get("assists", 0)
            ),

            "clean_sheets": safe_int(
                raw.get("clean_sheets", 0)
            ),

            "bonus": safe_int(
                raw.get("bonus", 0)
            ),

            "bps": safe_int(
                raw.get("bps", 0)
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
                100
            ),

            "status": raw.get(
                "status",
                "a"
            ),

            "news": raw.get(
                "news",
                ""
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
                    "cost_change_event",
                    0
                )
            )
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

        player["fixtures"] = fixture_text(
            fixture_map,
            team_names,
            team_id,
            FIXTURE_HORIZON
        )

        player["next_gw_projection"] = (
            next_gw_projection(
                player,
                fixture_map,
                next_gw
            )
        )

        player["projection_5gw"] = (
            multi_gw_projection(
                player,
                fixture_map,
                next_gw,
                PROJECTION_WEEKS
            )
        )

        player["blended"] = (
            calc_blended_score(
                player
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
        "player_by_id": player_by_id
    }


# ============================================================
# LOAD FPL DATA SAFELY
# ============================================================

try:
    data = load_fpl_data()

except Exception as error:
    st.error(
        "⚠️ FPL API temporarily unavailable."
    )

    with st.expander(
        "Technical details"
    ):
        st.code(
            str(error)
        )

    st.stop()


teams = data["teams"]
team_names = data["team_names"]
current_gw = data["current_gw"]
next_gw = data["next_gw"]
fixture_map = data["fixture_map"]
players = data["players"]
player_by_id = data["player_by_id"]


def blended_score(player):
    return player["blended"]


# ============================================================
# LOAD USER SQUAD
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

        player = player_by_id.get(
            pick.get("element")
        )

        if not player:
            continue

        copied = player.copy()

        copied["is_captain"] = (
            pick.get(
                "is_captain",
                False
            )
        )

        copied["is_vice"] = (
            pick.get(
                "is_vice_captain",
                False
            )
        )

        copied["multiplier"] = pick.get(
            "multiplier",
            1
        )

        copied["position_slot"] = pick.get(
            "position",
            0
        )

        squad.append(copied)

    return data, squad


# ============================================================
# PLAYER STATUS
# ============================================================

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


# ============================================================
# YOUTUBE
# ============================================================

def extract_video_id(
    url_or_id
):
    value = (
        url_or_id
        .strip()
    )

    if (
        len(value) == 11
        and " " not in value
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
            value
        )

        if match:
            return match.group(1)

    return None


@st.cache_data(
    ttl=1800,
    show_spinner=False
)
def fetch_youtube_transcript(
    video_identifier
):

    if YouTubeTranscriptApi is None:
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

        api = YouTubeTranscriptApi()

        transcript = api.fetch(
            video_id,
            languages=["en"]
        )

        lines = []

        for snippet in transcript:

            if hasattr(
                snippet,
                "text"
            ):
                lines.append(
                    snippet.text
                )

            elif isinstance(
                snippet,
                dict
            ):
                lines.append(
                    snippet.get(
                        "text",
                        ""
                    )
                )

        text = " ".join(
            lines
        ).strip()

        if not text:
            return (
                None,
                "Transcript was empty."
            )

        return text, None

    except TypeError:

        # Compatibility with older versions
        try:

            if hasattr(
                YouTubeTranscriptApi,
                "get_transcript"
            ):

                old_transcript = (
                    YouTubeTranscriptApi
                    .get_transcript(
                        video_id
                    )
                )

                text = " ".join(
                    item.get(
                        "text",
                        ""
                    )
                    for item in old_transcript
                )

                return text, None

        except Exception:
            pass

        return (
            None,
            "Could not retrieve transcript "
            "with the installed version."
        )

    except Exception as error:

        return (
            None,
            f"Could not retrieve transcript: {error}"
        )


# ============================================================
# AI CONTEXT
# ============================================================

def build_ai_context(
    squad,
    bank=0,
    free_transfers=1
):

    if squad:

        squad_context = "\n".join(
            [
                (
                    f"- {p['name']} "
                    f"({p['team']} {p['position']}): "
                    f"£{p['price']:.1f}m | "
                    f"Form {p['form']:.1f} | "
                    f"PPG {p['ppg']:.1f} | "
                    f"xGI/90 {p['xgi90']:.2f} | "
                    f"xGC/90 {p['xgc90']:.2f} | "
                    f"FDR {p['fdr']:.1f} | "
                    f"Next GW projection "
                    f"{p['next_gw_projection']:.1f} | "
                    f"5GW projection "
                    f"{p['projection_5gw']:.1f} | "
                    f"Status "
                    f"{player_status(p)}"
                )
                for p in squad
            ]
        )

    else:
        squad_context = (
            "No squad loaded."
        )

    captain_list = captain_recommendations(
        squad,
        fixture_map,
        next_gw
    ) if squad else []

    captain_context = "\n".join(
        [
            (
                f"{i + 1}. "
                f"{p['name']} "
                f"— captain score "
                f"{p['_captain_score']:.1f}, "
                f"GW projection "
                f"{p['next_gw_projection']:.1f}"
            )
            for i, p in enumerate(
                captain_list[:5]
            )
        ]
    )

    transfer_context = ""

    if squad:
        transfer_eval = transfer_decision(
            squad,
            bank,
            free_transfers
        )

        transfer_context = "\n".join(
            [
                (
                    f"{i + 1}. "
                    f"{s['out']['name']} -> "
                    f"{s['in']['name']} | "
                    f"Projected gain "
                    f"+{s['projected_gain']:.1f} | "
                    f"Net gain "
                    f"+{s['net_gain']:.1f} | "
                    f"Fixture gain "
                    f"{s['fixture_gain']:+.1f}"
                )
                for i, s in enumerate(
                    transfer_eval[
                        "suggestions"
                    ][:5]
                )
            ]
        )

    top_players = sorted(
        players,
        key=blended_score,
        reverse=True
    )[:15]

    player_context = "\n".join(
        [
            (
                f"- {p['name']} "
                f"({p['team']}, {p['position']}): "
                f"Score {p['blended']:.1f}, "
                f"xGI/90 {p['xgi90']:.2f}, "
                f"Form {p['form']:.1f}, "
                f"FDR {p['fdr']:.1f}"
            )
            for p in top_players
        ]
    )

    return f"""
GAMEWEEK
Current GW: {current_gw}
Planning for GW: {next_gw}

MANAGER
Bank: £{bank:.1f}m
Free Transfers: {free_transfers}

MANAGER SQUAD
{squad_context}

CAPTAIN RANKING
{captain_context or "Unavailable"}

TRANSFER MODEL
{transfer_context or "No transfer recommendations."}

TOP PLAYERS IN MODEL
{player_context}

IMPORTANT:
- Use the mathematical model as evidence, not as an absolute rule.
- Do not invent player statistics.
- Explain uncertainty.
- Prioritise the manager's actual squad when giving transfer advice.
"""


# ============================================================
# UI HEADER
# ============================================================

st.title(
    "⚽ FPL Assistant Manager"
)

st.caption(
    f"GW{current_gw} | "
    f"Planning for GW{next_gw} | "
    f"FPL Underlying Metrics + "
    f"Decision Engine + Creator Intelligence"
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
            "FPL team/points page."
        )
    )

    league_id = st.text_input(
        "Mini-League ID (optional)",
        value=""
    )

    free_transfers = st.number_input(
        "Free Transfers Available",
        min_value=0,
        max_value=5,
        value=1
    )

    st.divider()

    st.caption(
        "FPL Assistant Manager"
    )

    st.caption(
        "Data refreshes automatically."
    )


# ============================================================
# TABS
# ============================================================

tabs = st.tabs([
    "📋 Strategy Briefing",
    "👤 My Team",
    "🔄 Transfers",
    "🩺 Hold / Sell",
    "🧢 Captain",
    "📊 Player Rankings",
    "📅 Fixtures",
    "💊 Chips",
    "🕵️ Mini-League",
    "🏆 Best XI",
    "📺 Creator Consensus",
    "💬 AI Assistant",
])


# ============================================================
# LOAD SQUAD
# ============================================================

team_data = None
my_squad = []

if entry_id:

    try:

        numeric_entry_id = int(
            entry_id
        )

        team_data, my_squad = (
            load_my_team(
                numeric_entry_id
            )
        )

    except ValueError:

        st.sidebar.error(
            "Team ID must be a number."
        )

    except Exception as error:

        st.sidebar.error(
            "Couldn't load squad."
        )

        with st.sidebar.expander(
            "Technical details"
        ):
            st.code(
                str(error)
            )


# ============================================================
# TAB 1 — STRATEGY
# ============================================================

with tabs[0]:

    st.header(
        f"📋 Gameweek {next_gw} "
        f"Strategy Briefing"
    )

    if not my_squad:

        st.info(
            "Enter your FPL Team ID in "
            "the sidebar to generate "
            "your strategic briefing."
        )

    else:

        entry_hist = team_data.get(
            "entry_history",
            {}
        )

        bank = (
            num(
                entry_hist.get(
                    "bank",
                    0
                )
            ) / 10
        )

        briefing = (
            generate_manager_briefing(
                my_squad,
                bank,
                free_transfers
            )
        )

        st.subheader(
            f"⚡ GW{next_gw} Directives"
        )

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Transfer Move",
            briefing[
                "t_dec"
            ]["decision"]
        )

        col2.metric(
            "Captain",
            (
                briefing["top_cap"]["name"]
                if briefing["top_cap"]
                else "N/A"
            )
        )

        col3.metric(
            "Vice-Captain",
            (
                briefing["vice_cap"]["name"]
                if briefing["vice_cap"]
                else "N/A"
            )
        )

        st.markdown(
            "**Transfer Assessment:** "
            + briefing["t_dec"]["reason"]
        )

        if briefing["injuries"]:

            st.warning(
                "🚨 **Flagged / Injured:** "
                + ", ".join(
                    [
                        f"{p['name']} "
                        f"({p['news'] or 'Doubt'})"
                        for p in briefing[
                            "injuries"
                        ]
                    ]
                )
            )

        if briefing["blanks"]:

            st.error(
                "⚠️ **Blanking Next GW:** "
                + ", ".join(
                    p["name"]
                    for p in briefing[
                        "blanks"
                    ]
                )
            )

        if briefing["doubles"]:

            st.success(
                "⚡ **Double GW Assets:** "
                + ", ".join(
                    p["name"]
                    for p in briefing[
                        "doubles"
                    ]
                )
            )

        st.divider()

        st.subheader(
            "🔭 5-Gameweek Squad Horizon"
        )

        avg_fdr = (
            briefing[
                "squad_avg_fdr"
            ]
        )

        fdr_eval = (
            "🟢 Favourable"
            if avg_fdr < 2.9
            else (
                "🔴 Difficult"
                if avg_fdr > 3.2
                else "🟡 Balanced"
            )
        )

        st.write(
            f"**Squad Schedule Rating:** "
            f"{fdr_eval} "
            f"(Avg FDR: {avg_fdr:.2f})"
        )

        ca, cb = st.columns(2)

        with ca:

            st.markdown(
                "**🟢 Prime Fixture Runs:**"
            )

            for player in briefing[
                "easiest_run"
            ]:

                st.write(
                    f"• **{player['name']}** "
                    f"({player['team']}) — "
                    f"Avg FDR "
                    f"{player['fdr']:.1f} | "
                    f"{player['fixtures']}"
                )

        with cb:

            st.markdown(
                "**🔴 Tough Fixture Runs:**"
            )

            for player in briefing[
                "hardest_run"
            ]:

                st.write(
                    f"• **{player['name']}** "
                    f"({player['team']}) — "
                    f"Avg FDR "
                    f"{player['fdr']:.1f} | "
                    f"{player['fixtures']}"
                )

        st.divider()

        st.subheader(
            "💊 Chip Deployment"
        )

        st.markdown(
            briefing["chip_advice"]
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
            "Enter your FPL Team ID "
            "in the sidebar."
        )

    else:

        entry_hist = team_data.get(
            "entry_history",
            {}
        )

        bank = (
            num(
                entry_hist.get(
                    "bank",
                    0
                )
            ) / 10
        )

        team_value = (
            num(
                entry_hist.get(
                    "value",
                    0
                )
            ) / 10
        )

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "GW Points",
            entry_hist.get(
                "points",
                0
            )
        )

        c2.metric(
            "Total Points",
            entry_hist.get(
                "total_points",
                0
            )
        )

        c3.metric(
            "Team Value",
            f"£{team_value:.1f}m"
        )

        c4.metric(
            "Bank",
            f"£{bank:.1f}m"
        )

        st.divider()

        squad_rows = []

        for p in my_squad:

            role = ""

            if p.get(
                "is_captain"
            ):
                role = "© Captain"

            elif p.get(
                "is_vice"
            ):
                role = "VC"

            squad_rows.append({
                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Role": role,
                "Price": f"£{p['price']:.1f}m",
                "Points": p["points"],
                "PPG": round(
                    p["ppg"],
                    1
                ),
                "Form": round(
                    p["form"],
                    1
                ),
                "xGI/90": round(
                    p["xgi90"],
                    2
                ),
                "Next GW": round(
                    p["next_gw_projection"],
                    1
                ),
                "5 GW": round(
                    p["projection_5gw"],
                    1
                ),
                "FDR": round(
                    p["fdr"],
                    1
                ),
                "Status": player_status(p)
            })

        st.dataframe(
            pd.DataFrame(
                squad_rows
            ),
            use_container_width=True,
            hide_index=True
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

        entry_hist = team_data.get(
            "entry_history",
            {}
        )

        bank = (
            num(
                entry_hist.get(
                    "bank",
                    0
                )
            ) / 10
        )

        evaluation = (
            transfer_decision(
                my_squad,
                bank,
                free_transfers
            )
        )

        if evaluation[
            "decision"
        ] == "TRANSFER":

            st.success(
                f"**Model Recommendation:** "
                f"{evaluation['decision']} — "
                f"{evaluation['reason']}"
            )

        elif evaluation[
            "decision"
        ] == "TAKE HIT":

            st.warning(
                f"**Model Recommendation:** "
                f"{evaluation['decision']} — "
                f"{evaluation['reason']}"
            )

        else:

            st.info(
                f"**Model Recommendation:** "
                f"{evaluation['decision']} — "
                f"{evaluation['reason']}"
            )

        st.divider()

        if not evaluation[
            "suggestions"
        ]:

            st.info(
                "No sufficiently strong "
                "transfer identified."
            )

        else:

            for i, suggestion in enumerate(
                evaluation[
                    "suggestions"
                ][:8],
                1
            ):

                out_player = (
                    suggestion["out"]
                )

                in_player = (
                    suggestion["in"]
                )

                difference = (
                    suggestion[
                        "cost_difference"
                    ]
                )

                if difference > 0:

                    cost_text = (
                        f"Costs +£"
                        f"{difference:.1f}m"
                    )

                elif difference < 0:

                    cost_text = (
                        f"Frees £"
                        f"{abs(difference):.1f}m"
                    )

                else:

                    cost_text = (
                        "Equal Price"
                    )

                st.markdown(
                    f"### {i}. "
                    f"{out_player['name']} "
                    f"➡️ "
                    f"{in_player['name']} "
                    f"({cost_text})"
                )

                c1, c2, c3, c4 = (
                    st.columns(4)
                )

                c1.metric(
                    "5GW Gain",
                    f"+{suggestion['projected_gain']:.1f}"
                )

                c2.metric(
                    "Net After Hit",
                    f"+{suggestion['net_gain']:.1f}"
                )

                c3.metric(
                    "Fixture Swing",
                    f"{suggestion['fixture_gain']:+.1f}"
                )

                c4.metric(
                    "Next GW Gain",
                    f"{suggestion['next_gw_gain']:+.1f}"
                )

                st.write(
                    f"**{in_player['name']}** "
                    f"fixtures: "
                    f"{in_player['fixtures']}"
                )

                st.write(
                    f"Status: "
                    f"{player_status(in_player)} "
                    f"| "
                    f"{price_momentum_flag(in_player)}"
                )

                st.divider()


# ============================================================
# TAB 4 — HOLD / SELL
# ============================================================

with tabs[3]:

    st.header(
        "🩺 Hold / Sell Diagnostics"
    )

    if not my_squad:

        st.info(
            "Load your squad first."
        )

    else:

        rows = []

        for player in my_squad:

            rows.append({
                "Player": player["name"],
                "Club": player["team"],
                "Pos": player["position"],
                "Points": player["points"],
                "Form": round(
                    player["form"],
                    1
                ),
                "xGI/90": round(
                    player["xgi90"],
                    2
                ),
                "Next GW": round(
                    player[
                        "next_gw_projection"
                    ],
                    1
                ),
                "5 GW": round(
                    player[
                        "projection_5gw"
                    ],
                    1
                ),
                "Avg FDR": round(
                    player["fdr"],
                    1
                ),
                "Price Trend":
                    price_momentum_flag(
                        player
                    ),
                "Action":
                    hold_sell(
                        player
                    )
            })

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# TAB 5 — CAPTAIN
# ============================================================

with tabs[4]:

    st.header(
        "🧢 Captaincy & Armband Analysis"
    )

    if not my_squad:

        st.info(
            "Load your squad first."
        )

    else:

        captains = (
            captain_recommendations(
                my_squad,
                fixture_map,
                next_gw
            )
        )

        if captains:

            captain = captains[0]

            vice = (
                captains[1]
                if len(captains) > 1
                else None
            )

            ca, cb = st.columns(2)

            with ca:

                st.success(
                    f"👑 **CAPTAIN:** "
                    f"{captain['name']} "
                    f"({captain['team']})"
                )

                st.write(
                    f"Captain Score: "
                    f"**{captain['_captain_score']:.1f}**"
                )

                st.write(
                    f"xGI/90: "
                    f"**{captain['xgi90']:.2f}**"
                )

                st.write(
                    f"Form: "
                    f"**{captain['form']:.1f}**"
                )

                st.write(
                    f"Next GW projection: "
                    f"**{captain['next_gw_projection']:.1f}**"
                )

                st.write(
                    f"Fixtures: "
                    f"{captain['fixtures']}"
                )

            with cb:

                if vice:

                    st.info(
                        f"🥈 **VICE-CAPTAIN:** "
                        f"{vice['name']} "
                        f"({vice['team']})"
                    )

                    st.write(
                        f"Captain Score: "
                        f"**{vice['_captain_score']:.1f}**"
                    )

                    st.write(
                        f"xGI/90: "
                        f"**{vice['xgi90']:.2f}**"
                    )

                    st.write(
                        f"Next GW projection: "
                        f"**{vice['next_gw_projection']:.1f}**"
                    )

            st.divider()

            st.subheader(
                "Captain Shortlist"
            )

            captain_rows = [
                {
                    "Rank": i + 1,
                    "Player": p["name"],
                    "Club": p["team"],
                    "Captain Score":
                        round(
                            p["_captain_score"],
                            1
                        ),
                    "GW Projection":
                        round(
                            p[
                                "next_gw_projection"
                            ],
                            1
                        ),
                    "xGI/90":
                        round(
                            p["xgi90"],
                            2
                        ),
                    "Form":
                        round(
                            p["form"],
                            1
                        ),
                    "FDR":
                        round(
                            p["fdr"],
                            1
                        ),
                    "Fixtures":
                        p[
                            "next_gw_fixtures"
                        ]
                }
                for i, p in enumerate(
                    captains
                )
            ]

            st.dataframe(
                pd.DataFrame(
                    captain_rows
                ),
                use_container_width=True,
                hide_index=True
            )


# ============================================================
# TAB 6 — PLAYER RANKINGS
# ============================================================

with tabs[5]:

    st.header(
        "📊 Player Rankings & "
        "Underlying Metrics"
    )

    sort_by = st.radio(
        "Sort By",
        [
            "Blended Model Score",
            "5GW Projection",
            "Next GW Projection",
            "xGI/90",
            "FPL Points",
            "Form",
            "PPG"
        ],
        horizontal=True
    )

    pos_filter = st.selectbox(
        "Position",
        [
            "ALL",
            "GK",
            "DEF",
            "MID",
            "FWD"
        ]
    )

    pool = [
        p
        for p in players
        if (
            pos_filter == "ALL"
            or p["position"]
            == pos_filter
        )
    ]

    if sort_by == (
        "Blended Model Score"
    ):

        pool.sort(
            key=blended_score,
            reverse=True
        )

    elif sort_by == (
        "5GW Projection"
    ):

        pool.sort(
            key=lambda p:
                p["projection_5gw"],
            reverse=True
        )

    elif sort_by == (
        "Next GW Projection"
    ):

        pool.sort(
            key=lambda p:
                p["next_gw_projection"],
            reverse=True
        )

    elif sort_by == "xGI/90":

        pool.sort(
            key=lambda p:
                p["xgi90"],
            reverse=True
        )

    elif sort_by == "FPL Points":

        pool.sort(
            key=lambda p:
                p["points"],
            reverse=True
        )

    elif sort_by == "Form":

        pool.sort(
            key=lambda p:
                p["form"],
            reverse=True
        )

    elif sort_by == "PPG":

        pool.sort(
            key=lambda p:
                p["ppg"],
            reverse=True
        )

    rows = []

    for p in pool[:100]:

        rows.append({
            "Player": p["name"],
            "Club": p["team"],
            "Pos": p["position"],
            "Price": f"£{p['price']:.1f}m",
            "Points": p["points"],
            "xGI/90": round(
                p["xgi90"],
                2
            ),
            "xGC/90": round(
                p["xgc90"],
                2
            ),
            "Form": round(
                p["form"],
                1
            ),
            "PPG": round(
                p["ppg"],
                1
            ),
            "Next GW": round(
                p["next_gw_projection"],
                1
            ),
            "5 GW": round(
                p["projection_5gw"],
                1
            ),
            "FDR": round(
                p["fdr"],
                1
            ),
            "Score": round(
                p["blended"],
                1
            )
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# TAB 7 — FIXTURES
# ============================================================

with tabs[6]:

    st.header(
        "📅 Fixture Difficulty & Swings"
    )

    if my_squad:

        st.subheader(
            "👤 Your Squad Fixture Difficulty"
        )

        rows = []

        for p in my_squad:

            rows.append({
                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Avg FDR": round(
                    p["fdr"],
                    2
                ),
                "Next GW Projection":
                    round(
                        p[
                            "next_gw_projection"
                        ],
                        1
                    ),
                "5 GW Projection":
                    round(
                        p[
                            "projection_5gw"
                        ],
                        1
                    ),
                "Upcoming Schedule":
                    p["fixtures"]
            })

        st.dataframe(
            pd.DataFrame(rows).sort_values(
                "Avg FDR"
            ),
            use_container_width=True,
            hide_index=True
        )

        st.divider()

    st.subheader(
        "🔥 Fixture Swings"
    )

    improving = []
    worsening = []

    for team_id in teams:

        near = average_fdr(
            fixture_map,
            team_id,
            weeks=2
        )

        later = average_fdr(
            fixture_map,
            team_id,
            weeks=5
        )

        if later < near - 0.2:

            improving.append({
                "Club":
                    team_names.get(
                        team_id,
                        "?"
                    ),
                "Next 2 GW FDR":
                    round(
                        near,
                        1
                    ),
                "Next 5 GW FDR":
                    round(
                        later,
                        1
                    ),
                "Swing":
                    round(
                        later - near,
                        1
                    )
            })

        elif later > near + 0.2:

            worsening.append({
                "Club":
                    team_names.get(
                        team_id,
                        "?"
                    ),
                "Next 2 GW FDR":
                    round(
                        near,
                        1
                    ),
                "Next 5 GW FDR":
                    round(
                        later,
                        1
                    ),
                "Swing":
                    round(
                        later - near,
                        1
                    )
            })

    col1, col2 = st.columns(2)

    with col1:

        st.markdown(
            "### 🟢 Improving"
        )

        if improving:

            st.dataframe(
                pd.DataFrame(
                    improving
                ).sort_values(
                    "Swing"
                ),
                use_container_width=True,
                hide_index=True
            )

        else:

            st.info(
                "No major improving "
                "fixture swings detected."
            )

    with col2:

        st.markdown(
            "### 🔴 Worsening"
        )

        if worsening:

            st.dataframe(
                pd.DataFrame(
                    worsening
                ).sort_values(
                    "Swing",
                    ascending=False
                ),
                use_container_width=True,
                hide_index=True
            )

        else:

            st.info(
                "No major worsening "
                "fixture swings detected."
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

        chips = chip_analysis(
            my_squad,
            fixture_map,
            next_gw
        )

        st.success(
            chips["message"]
        )

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Bench Projection",
            f"{chips['bench_boost']:.1f}"
        )

        c2.metric(
            "Strong Bench",
            chips["strong_bench"]
        )

        c3.metric(
            "Blank Players",
            chips["blank_count"]
        )

        c4.metric(
            "Double Players",
            chips["double_count"]
        )

        st.divider()

        st.subheader(
            "🪑 Historical Bench Boost Check"
        )

        bb = bench_boost_value(
            my_squad,
            current_gw
        )

        if bb:

            rows, total = bb

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True
            )

            st.metric(
                f"Bench GW{current_gw} Points",
                f"+{total}"
            )

        st.divider()

        st.subheader(
            "Chip Scores"
        )

        chip_rows = [
            {
                "Chip":
                    "Bench Boost",
                "Model Score":
                    chips["bench_boost"],
                "Assessment":
                    (
                        "Strong"
                        if chips["strong_bench"] >= 3
                        else "Monitor"
                    )
            },
            {
                "Chip":
                    "Free Hit",
                "Model Score":
                    chips["free_hit"],
                "Assessment":
                    (
                        "Strong"
                        if chips["free_hit"] >= 8
                        else "Monitor"
                    )
            },
            {
                "Chip":
                    "Triple Captain",
                "Model Score":
                    chips["triple_captain"],
                "Assessment":
                    (
                        "Strong"
                        if chips[
                            "triple_captain"
                        ] >= 8
                        else "Monitor"
                    )
            },
            {
                "Chip":
                    "Wildcard",
                "Model Score":
                    chips["wildcard"],
                "Assessment":
                    (
                        "Strong"
                        if chips["wildcard"] >= 7
                        else "Monitor"
                    )
            }
        ]

        st.dataframe(
            pd.DataFrame(
                chip_rows
            ),
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# TAB 9 — MINI LEAGUE
# ============================================================

with tabs[8]:

    st.header(
        "🕵️ Mini-League Rival Analysis"
    )

    if not league_id:

        st.info(
            "Enter your Mini-League ID "
            "in the sidebar."
        )

    else:

        try:

            league = get_league(
                int(league_id)
            )

            standings = (
                league
                .get("standings", {})
                .get("results", [])
            )

            standings = standings[:20]

            rows = [
                {
                    "Rank":
                        r["rank"],
                    "Manager":
                        r["player_name"],
                    "Team":
                        r["entry_name"],
                    "Total Points":
                        r["total"],
                    "GW Points":
                        r["event_total"]
                }
                for r in standings
            ]

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True
            )

        except Exception as error:

            st.error(
                "Couldn't retrieve "
                "mini-league."
            )

            with st.expander(
                "Technical details"
            ):
                st.code(
                    str(error)
                )


# ============================================================
# TAB 10 — BEST XI
# ============================================================

with tabs[9]:

    st.header(
        "🏆 Best Starting XI"
    )

    st.caption(
        "This feature only selects "
        "players already in your squad."
    )

    if not my_squad:

        st.info(
            "Load your squad first."
        )

    elif st.button(
        "Compute Best Starting Lineup"
    ):

        result, score = best_xi(
            my_squad
        )

        if result:

            st.success(
                f"Optimal Formation: "
                f"**{result['formation']}** "
                f"(Model Score: "
                f"{score:.1f})"
            )

            lineup_rows = []

            for p in result[
                "lineup"
            ]:

                lineup_rows.append({
                    "Player":
                        p["name"],
                    "Club":
                        p["team"],
                    "Pos":
                        p["position"],
                    "Next GW":
                        round(
                            p[
                                "next_gw_projection"
                            ],
                            1
                        ),
                    "xGI/90":
                        round(
                            p["xgi90"],
                            2
                        ),
                    "Form":
                        round(
                            p["form"],
                            1
                        ),
                    "Model Score":
                        round(
                            p["blended"],
                            1
                        )
                })

            st.dataframe(
                pd.DataFrame(
                    lineup_rows
                ),
                use_container_width=True,
                hide_index=True
            )

            st.subheader(
                "🪑 Bench"
            )

            bench_rows = []

            for p in result[
                "bench"
            ]:

                bench_rows.append({
                    "Player":
                        p["name"],
                    "Club":
                        p["team"],
                    "Pos":
                        p["position"],
                    "Next GW":
                        round(
                            p[
                                "next_gw_projection"
                            ],
                            1
                        ),
                    "Model Score":
                        round(
                            p["blended"],
                            1
                        )
                })

            st.dataframe(
                pd.DataFrame(
                    bench_rows
                ),
                use_container_width=True,
                hide_index=True
            )

        else:

            st.error(
                "Unable to construct "
                "a valid starting XI."
            )


# ============================================================
# TAB 11 — CREATOR CONSENSUS
# ============================================================

with tabs[10]:

    st.header(
        "📺 Creator Intelligence "
        "& Video Stress-Testing"
    )

    st.caption(
        "Paste a YouTube FPL video and "
        "compare its recommendations "
        "against the objective model."
    )

    st.markdown(
        "### 🎙️ Monitored Creator Channels"
    )

    columns = st.columns(5)

    for i, (
        creator,
        url
    ) in enumerate(
        CREATOR_CHANNELS.items()
    ):

        columns[i].markdown(
            f"**[{creator}]({url})**"
        )

    st.divider()

    video_input = st.text_input(
        "YouTube Video URL or ID",
        placeholder=(
            "https://www.youtube.com/watch?v=..."
        )
    )

    if st.button(
        "Analyze Creator Video"
    ):

        if not video_input:

            st.warning(
                "Paste a YouTube video first."
            )

        elif (
            "GEMINI_API_KEY"
            not in st.secrets
        ):

            st.error(
                "GEMINI_API_KEY is missing "
                "from Streamlit Secrets."
            )

        else:

            with st.spinner(
                "Extracting transcript "
                "and stress-testing advice..."
            ):

                transcript, error = (
                    fetch_youtube_transcript(
                        video_input
                    )
                )

                if error:

                    st.error(
                        error
                    )

                else:

                    try:

                        client = genai.Client(
                            api_key=st.secrets[
                                "GEMINI_API_KEY"
                            ]
                        )

                        entry_hist = (
                            team_data.get(
                                "entry_history",
                                {}
                            )
                            if team_data
                            else {}
                        )

                        bank = (
                            num(
                                entry_hist.get(
                                    "bank",
                                    0
                                )
                            ) / 10
                        )

                        ai_context = (
                            build_ai_context(
                                my_squad,
                                bank,
                                free_transfers
                            )
                        )

                        prompt = f"""
You are stress-testing an FPL creator video.

TRANSCRIPT
{transcript[:12000]}

FPL MODEL CONTEXT
{ai_context}

TASK

1. Summarise the creator's important recommendations.

2. Identify:
- transfers
- captain choices
- players to buy
- players to sell
- players to avoid
- chip suggestions

3. Compare their recommendations with the
mathematical FPL model.

4. Specifically consider:
- next GW projection
- 5 GW projection
- xGI/90
- xGC/90
- form
- minutes security
- fixture difficulty
- blank GWs
- double GWs
- availability

5. Give a final verdict:
- FOLLOW
- PARTIALLY FOLLOW
- IGNORE

6. Explain exactly why.

Do not blindly favour either the creator
or the mathematical model.
"""

                        response = (
                            client.models
                            .generate_content(
                                model=(
                                    "gemini-2.5-flash"
                                ),
                                contents=prompt,
                                config={
                                    "system_instruction":
                                    (
                                        "You are an "
                                        "objective "
                                        "elite FPL "
                                        "analyst."
                                    )
                                }
                            )
                        )

                        st.subheader(
                            "📋 Creator Analysis "
                            "& Statistical Verdict"
                        )

                        st.markdown(
                            response.text
                        )

                    except Exception as error:

                        st.error(
                            "Gemini API Error: "
                            f"{error}"
                        )


# ============================================================
# TAB 12 — AI ASSISTANT
# ============================================================

with tabs[11]:

    st.header(
        "💬 FPL AI Assistant"
    )

    if (
        "GEMINI_API_KEY"
        not in st.secrets
    ):

        st.warning(
            "GEMINI_API_KEY not found "
            "in Streamlit Secrets."
        )

    else:

        pin = st.text_input(
            "Enter Manager PIN "
            "to unlock Assistant",
            type="password",
            placeholder="Enter 4-digit PIN"
        )

        if pin != "2325":

            if pin:
                st.error(
                    "Incorrect PIN."
                )
            else:
                st.info(
                    "🔒 Enter Manager PIN "
                    "to activate the AI Assistant."
                )

        else:

            st.success(
                "🔓 Assistant unlocked."
            )

            client = genai.Client(
                api_key=st.secrets[
                    "GEMINI_API_KEY"
                ]
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
                        message["content"]
                    )

            user_prompt = st.chat_input(
                "Ask about transfers, "
                "captaincy, fixtures, "
                "chips or your squad..."
            )

            if user_prompt:

                st.session_state.messages.append({
                    "role": "user",
                    "content": user_prompt
                })

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
                        "Analysing your "
                        "live FPL context..."
                    ):

                        try:

                            entry_hist = (
                                team_data.get(
                                    "entry_history",
                                    {}
                                )
                                if team_data
                                else {}
                            )

                            bank = (
                                num(
                                    entry_hist.get(
                                        "bank",
                                        0
                                    )
                                ) / 10
                            )

                            context = (
                                build_ai_context(
                                    my_squad,
                                    bank,
                                    free_transfers
                                )
                            )

                            payload = f"""
You are the FPL Assistant Manager.

CURRENT DATA
{context}

MANAGER QUESTION
{user_prompt}

INSTRUCTIONS

Give a practical FPL answer.

When appropriate:
- reference the manager's actual squad
- compare specific transfer options
- discuss captaincy
- discuss fixture difficulty
- discuss xGI/90
- discuss form
- discuss minutes/security
- discuss blanks/doubles
- discuss chip timing
- mention uncertainty

Never invent statistics.

If the model disagrees with a popular
FPL opinion, explain why.

Give a clear final recommendation.
"""

                            response = (
                                client.models
                                .generate_content(
                                    model=(
                                        "gemini-2.5-flash"
                                    ),
                                    contents=payload,
                                    config={
                                        "system_instruction":
                                        (
                                            "You are an "
                                            "elite FPL "
                                            "strategist. "
                                            "Use the "
                                            "provided "
                                            "data and "
                                            "reason "
                                            "carefully."
                                        )
                                    }
                                )
                            )

                            answer = (
                                response.text
                            )

                            st.markdown(
                                answer
                            )

                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": answer
                            })

                        except Exception as error:

                            st.error(
                                "Gemini error: "
                                f"{error}"
                            )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "⚽ FPL Assistant Manager — "
    "Official FPL API + FPL underlying "
    "metrics + decision engine."
            ) 
