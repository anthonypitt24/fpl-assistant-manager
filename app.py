from collections import defaultdict
import itertools
from google import genai
import pandas as pd
import requests
import streamlit as st

# ============================================================
# FPL ASSISTANT MANAGER — ULTIMATE DECISION ENGINE
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)

API = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0"}

FIXTURE_HORIZON = 5
PROJECTION_WEEKS = 4
SQUAD_BUDGET = 1000
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
# API HELPERS
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def api_get(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_info(entry_id):
    return api_get(f"{API}/entry/{entry_id}/")


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_picks(entry_id, gameweek):
    return api_get(f"{API}/entry/{entry_id}/event/{gameweek}/picks/")


@st.cache_data(ttl=300, show_spinner=False)
def get_league(league_id):
    return api_get(
        f"{API}/leagues-classic/{league_id}/standings/"
    )


@st.cache_data(ttl=600, show_spinner=False)
def get_team_history(entry_id):
    return api_get(f"{API}/entry/{entry_id}/history/")


@st.cache_data(ttl=300, show_spinner=False)
def get_live_gw(gameweek):
    data = api_get(f"{API}/event/{gameweek}/live/")
    return {
        el["id"]: el["stats"]["total_points"]
        for el in data.get("elements", [])
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


def availability_factor(player):
    chance = player["chance"]

    if chance >= 90:
        return 1.0
    if chance >= 75:
        return 0.85
    if chance >= 50:
        return 0.60
    if chance > 0:
        return 0.30

    return 0.0


def fixture_count(fixture_map, team_id, gw):
    return len([
        f for f in fixture_map.get(team_id, [])
        if f["gw"] == gw
    ])


def average_fdr(fixture_map, team_id, weeks=None):
    games = fixture_map.get(team_id, [])

    if weeks:
        games = games[:weeks]

    if not games:
        return 3.0

    return sum(f["difficulty"] for f in games) / len(games)


def fixture_text(fixture_map, team_names, team_id, number=5):
    games = sorted(
        fixture_map.get(team_id, []),
        key=lambda x: (x["gw"], not x["home"])
    )[:number]

    output = []

    for f in games:
        opponent = team_names.get(f["opponent"], "?")
        location = "H" if f["home"] else "A"

        output.append(
            f"GW{f['gw']} {opponent} ({location}) [{f['difficulty']}]"
        )

    return " | ".join(output) if output else "No fixtures"


# ============================================================
# PLAYER MODEL
# ============================================================

def calc_blended_score(player):
    """
    Decision score.

    Intentionally keeps actual FPL points as only one component
    rather than allowing points/form/PPG to completely dominate.
    """

    ppg = min(player["ppg"] * 1.5, 10)
    form = min(player["form"] * 1.2, 9)
    expected = min(player["ep_next"] * 2.5, 16)

    fixture = max(
        0,
        (3.2 - player["fdr"]) * 3
    )

    availability = availability_factor(player) * 5

    attacking = min(player["xgi90"] * 8, 12)

    defensive = 0

    if player["position"] in ("GK", "DEF"):
        defensive = max(
            0,
            (1.4 - player["xgc90"]) * 4
        )

    dgw_bonus = 7 if player["next_gw_fixtures"] >= 2 else 0
    bgw_penalty = 8 if player["next_gw_fixtures"] == 0 else 0

    ownership_bonus = 0

    if player["ownership"] < 5 and player["xgi90"] >= 0.25:
        ownership_bonus = 2

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

    return round(score, 2)


def calc_multi_gw_projection(
    player,
    fixture_map,
    weeks=PROJECTION_WEEKS
):
    games = sorted(
        fixture_map.get(player["team_id"], []),
        key=lambda x: x["gw"]
    )[:weeks]

    if not games:
        return round(player["ep_next"], 1)

    availability = availability_factor(player)

    base = (
        player["ep_next"] * 0.55
        + player["ppg"] * 0.20
        + player["xgi90"] * 2.0
    )

    total = 0

    for fixture in games:
        difficulty_multiplier = (
            1.0 + ((3 - fixture["difficulty"]) * 0.08)
        )

        total += (
            base
            * difficulty_multiplier
            * availability
        )

    return round(total, 1)


def price_momentum_flag(player):
    net = player["net_transfers"]
    ownership = max(player["ownership"], 0.1)

    ratio = net / (ownership * 1000)

    if ratio > 0.4:
        return "📈 Likely rise"

    if ratio < -0.4:
        return "📉 Likely fall"

    return "— Stable"


def price_change_value(player):
    change = player.get("price_change", 0)

    if change > 0:
        return f"📈 +£{change / 10:.1f}m"

    if change < 0:
        return f"📉 -£{abs(change) / 10:.1f}m"

    return "—"


# ============================================================
# LOAD FPL DATA
# ============================================================

@st.cache_data(
    ttl=900,
    show_spinner="Loading FPL data..."
)
def load_fpl_data():

    bootstrap = api_get(f"{API}/bootstrap-static/")
    fixtures_raw = api_get(f"{API}/fixtures/")

    events = bootstrap.get("events", [])
    raw_players = bootstrap.get("elements", [])
    raw_teams = bootstrap.get("teams", [])

    teams = {
        t["id"]: t
        for t in raw_teams
    }

    team_names = {
        t["id"]: t.get("short_name", "?")
        for t in raw_teams
    }

    positions = {
        1: "GK",
        2: "DEF",
        3: "MID",
        4: "FWD",
    }

    current_event = next(
        (e for e in events if e.get("is_current")),
        None
    )

    next_event = next(
        (e for e in events if e.get("is_next")),
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
    # FIXTURES
    # --------------------------------------------------------

    fixture_map = defaultdict(list)

    for fixture in fixtures_raw:

        gw = fixture.get("event")

        if gw is None:
            continue

        if gw < next_gw:
            continue

        if gw > next_gw + FIXTURE_HORIZON - 1:
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

            "price": p.get(
                "now_cost",
                0
            ) / 10,

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

            "ep_this": num(
                p.get("ep_this")
            ),

            "ownership": num(
                p.get("selected_by_percent")
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

            "price_change_overall": p.get(
                "cost_change_start",
                0
            ),
        }

        player["fdr"] = average_fdr(
            fixture_map,
            team_id
        )

        player["next_gw_fixtures"] = fixture_count(
            fixture_map,
            team_id,
            next_gw
        )

        player["fixtures"] = fixture_text(
            fixture_map,
            team_names,
            team_id,
            FIXTURE_HORIZON
        )

        player["blended"] = calc_blended_score(
            player
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
        "fixture_map": dict(fixture_map),
        "players": players,
        "player_by_id": player_by_id,
    }


# ============================================================
# INITIAL DATA LOAD
# ============================================================

try:

    data = load_fpl_data()

except Exception as e:

    st.error(
        "⚠️ FPL data could not be loaded."
    )

    st.info(
        "The official FPL API may be temporarily "
        "unavailable. Try refreshing the page."
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


def multi_gw_projection(
    player,
    weeks=PROJECTION_WEEKS
):

    if weeks == PROJECTION_WEEKS:
        return player["projection_4gw"]

    return calc_multi_gw_projection(
        player,
        fixture_map,
        weeks
    )


# ============================================================
# PLAYER STATUS
# ============================================================

def player_status(player):

    if player["status"] != "a":
        return "🔴 Unavailable"

    if player["chance"] < 50:
        return "🔴 Major doubt"

    if player["chance"] < 75:
        return "🟠 Rotation / injury risk"

    if player["next_gw_fixtures"] == 0:
        return "⚠️ Blank GW"

    if player["next_gw_fixtures"] >= 2:
        return "⚡ Double GW"

    if player["form"] >= 5:
        return "🟢 Excellent form"

    if player["form"] >= 3.5:
        return "🟢 Good form"

    if player["form"] < 2.5:
        return "🔴 Poor form"

    return "🟡 Monitor"


# ============================================================
# MY TEAM
# ============================================================

def load_my_team(entry_id):

    data = get_entry_picks(
        entry_id,
        current_gw
    )

    squad = []

    for pick in data.get("picks", []):

        player = player_by_id.get(
            pick["element"]
        )

        if not player:
            continue

        copied = player.copy()

        copied["is_captain"] = pick.get(
            "is_captain",
            False
        )

        copied["is_vice"] = pick.get(
            "is_vice_captain",
            False
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


def squad_club_counts(
    squad,
    exclude_id=None
):

    counts = defaultdict(int)

    for p in squad:

        if p["id"] == exclude_id:
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

    club_counts = squad_club_counts(
        squad
    )

    suggestions = []

    for outgoing in squad:

        candidates = [
            p for p in players
            if p["position"]
            == outgoing["position"]

            and p["id"]
            not in owned_ids

            and p["status"] == "a"

            and p["chance"] > 0
        ]

        candidates.sort(
            key=blended_score,
            reverse=True
        )

        for incoming in candidates[:30]:

            available_money = (
                bank
                + outgoing["price"]
            )

            if incoming["price"] > available_money:
                continue

            projected_count = club_counts[
                incoming["team_id"]
            ]

            if (
                incoming["team_id"]
                == outgoing["team_id"]
            ):
                projected_count -= 1

            if projected_count + 1 > MAX_PER_CLUB:
                continue

            immediate_gain = (
                blended_score(incoming)
                - blended_score(outgoing)
            )

            projected_gain = (
                multi_gw_projection(incoming)
                - multi_gw_projection(outgoing)
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

            if free_transfers > 0:

                if projected_gain < 2:
                    continue

            else:

                if projected_gain < 4:
                    continue

            suggestions.append({
                "out": outgoing,
                "in": incoming,
                "gain": immediate_gain,
                "projected_gain": projected_gain,
                "hit": hit,
                "net_gain": net_gain,
                "cost_difference": (
                    incoming["price"]
                    - outgoing["price"]
                )
            })

    suggestions.sort(
        key=lambda x: x["net_gain"],
        reverse=True
    )

    return suggestions[:10]


# ============================================================
# TRANSFER DECISION
# ============================================================

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
            "colour": "green",
            "reason": (
                "No transfer currently clears "
                "the model's minimum improvement."
            ),
            "suggestions": []
        }

    best = suggestions[0]

    if free_transfers > 0:

        if best["projected_gain"] >= 5:

            decision = "TRANSFER"

            reason = (
                f"{best['in']['name']} projects "
                f"{best['projected_gain']:+.1f} points "
                f"over the next {PROJECTION_WEEKS} "
                "gameweeks compared with "
                f"{best['out']['name']}."
            )

        else:

            decision = "ROLL"

            reason = (
                "There is a possible upgrade, "
                "but the projected gain is not "
                "large enough to force a transfer."
            )

    else:

        if best["net_gain"] >= 2:

            decision = "TAKE HIT"

            reason = (
                f"The transfer projects "
                f"{best['projected_gain']:+.1f} points "
                f"before the -4 hit and "
                f"{best['net_gain']:+.1f} after it."
            )

        else:

            decision = "ROLL"

            reason = (
                "The best available move does not "
                "justify taking a -4."
            )

    return {
        "decision": decision,
        "colour": (
            "green"
            if decision == "TRANSFER"
            else "orange"
            if decision == "TAKE HIT"
            else "blue"
        ),
        "reason": reason,
        "suggestions": suggestions
    }


# ============================================================
# MULTI TRANSFER ENGINE
# ============================================================

def multi_transfer_suggestions(
    squad,
    bank,
    free_transfers,
    max_results=6
):

    owned_ids = {
        p["id"]
        for p in squad
    }

    club_counts = squad_club_counts(
        squad
    )

    results = []

    candidates_by_position = defaultdict(
        list
    )

    for p in squad:

        if p["position"] != "GK":

            candidates_by_position[
                p["position"]
            ].append(p)

    hit_cost = (
        0
        if free_transfers >= 2
        else 4
        if free_transfers == 1
        else 8
    )

    for position, group in (
        candidates_by_position.items()
    ):

        if len(group) < 2:
            continue

        weakest_pairs = sorted(
            itertools.combinations(
                group,
                2
            ),
            key=lambda pair:
            (
                blended_score(pair[0])
                + blended_score(pair[1])
            )
        )[:5]

        premiums = [
            p for p in players
            if p["position"] == position
            and p["id"] not in owned_ids
            and p["status"] == "a"
            and p["chance"] >= 75
        ]

        premiums.sort(
            key=blended_score,
            reverse=True
        )

        for out_a, out_b in weakest_pairs:

            available_money = (
                bank
                + out_a["price"]
                + out_b["price"]
            )

            projected_counts = (
                club_counts.copy()
            )

            projected_counts[
                out_a["team_id"]
            ] -= 1

            projected_counts[
                out_b["team_id"]
            ] -= 1

            for incoming in premiums[:10]:

                if (
                    incoming["price"]
                    > available_money
                ):
                    continue

                if (
                    projected_counts[
                        incoming["team_id"]
                    ] + 1
                    > MAX_PER_CLUB
                ):
                    continue

                projected_gain = (
                    multi_gw_projection(
                        incoming
                    )
                    - multi_gw_projection(
                        out_a
                    )
                    - multi_gw_projection(
                        out_b
                    )
                )

                if (
                    projected_gain
                    - hit_cost
                    < 6
                ):
                    continue

                results.append({
                    "out": [
                        out_a,
                        out_b
                    ],
                    "in": incoming,
                    "money_freed": round(
                        available_money
                        - incoming["price"],
                        1
                    ),
                    "projected_gain": round(
                        projected_gain,
                        1
                    ),
                    "hit": hit_cost
                })

    results.sort(
        key=lambda x:
        x["projected_gain"]
        - x["hit"],
        reverse=True
    )

    return results[:max_results]


# ============================================================
# SELL TO FUND
# ============================================================

def sell_to_fund_recommendations(
    squad,
    bank
):

    recommendations = []

    owned_ids = {
        p["id"]
        for p in squad
    }

    club_counts = squad_club_counts(
        squad
    )

    for outgoing in squad:

        if outgoing["price"] < 5:
            continue

        replacements = [
            p for p in players
            if p["position"]
            == outgoing["position"]

            and p["id"]
            not in owned_ids

            and p["status"] == "a"

            and p["chance"] >= 75
        ]

        replacements.sort(
            key=blended_score,
            reverse=True
        )

        for incoming in replacements[:10]:

            saving = (
                outgoing["price"]
                - incoming["price"]
            )

            if saving < 0.5:
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

            gain = (
                blended_score(incoming)
                - blended_score(outgoing)
            )

            if gain >= -5:

                recommendations.append({
                    "out": outgoing,
                    "in": incoming,
                    "saving": saving,
                    "gain": gain
                })

    recommendations.sort(
        key=lambda x: x["saving"],
        reverse=True
    )

    return recommendations[:8]


# ============================================================
# CAPTAIN
# ============================================================

def captain_recommendations(squad):

    available = [
        p for p in squad
        if p["chance"] >= 75
        and p["status"] == "a"
        and p["next_gw_fixtures"] > 0
    ]

    available.sort(
        key=lambda p: (
            p["blended"]
            + p["ep_next"]
            + (
                p["next_gw_fixtures"]
                * 3
            )
        ),
        reverse=True
    )

    return available[:5]


def captain_reason(player):

    reasons = []

    if player["next_gw_fixtures"] >= 2:
        reasons.append("Double GW")

    if player["form"] >= 5:
        reasons.append("excellent form")

    if player["xgi90"] >= 0.5:
        reasons.append("elite xGI/90")

    if player["fdr"] <= 2.5:
        reasons.append("strong fixtures")

    if not reasons:
        reasons.append("best overall projection")

    return ", ".join(reasons)


def triple_captain_projection(squad):

    candidates = captain_recommendations(
        squad
    )

    if not candidates:
        return None

    top = candidates[0]

    projected = (
        top["ep_next"]
        * max(
            top["next_gw_fixtures"],
            1
        )
    )

    typical = top["ppg"]

    return {
        "player": top,
        "projected_single_gw": round(
            projected,
            1
        ),
        "typical_ppg": round(
            typical,
            1
        ),
        "uplift": round(
            projected - typical,
            1
        )
    }


# ============================================================
# HOLD / SELL
# ============================================================

def hold_sell(player):

    score = blended_score(player)

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

    if player["form"] >= 5:
        return "🟢 STRONG HOLD"

    if player["ppg"] >= 5:
        return "🟢 HOLD"

    if score > 25:
        return "🟢 HOLD"

    return "🟡 MONITOR"


# ============================================================
# FIXTURE SWINGS
# ============================================================

def fixture_swings():

    current_rows = []

    for team_id in teams:

        current_fdr = average_fdr(
            fixture_map,
            team_id,
            weeks=2
        )

        longer_fdr = average_fdr(
            fixture_map,
            team_id,
            weeks=FIXTURE_HORIZON
        )

        swing = (
            current_fdr
            - longer_fdr
        )

        current_rows.append({
            "Team": team_names.get(
                team_id,
                "?"
            ),
            "Next 2 GW FDR": round(
                current_fdr,
                2
            ),
            "Next 5 GW FDR": round(
                longer_fdr,
                2
            ),
            "Swing": round(
                swing,
                2
            )
        })

    return sorted(
        current_rows,
        key=lambda x: x["Swing"]
    )


# ============================================================
# DIFFERENTIAL FINDER
# ============================================================

def differential_players(
    position="ALL",
    maximum_ownership=10
):

    pool = [
        p for p in players
        if p["status"] == "a"
        and p["chance"] >= 75
        and p["ownership"]
        <= maximum_ownership
    ]

    if position != "ALL":

        pool = [
            p for p in pool
            if p["position"] == position
        ]

    pool.sort(
        key=lambda p: (
            p["blended"]
            + p["projection_4gw"]
        ),
        reverse=True
    )

    return pool[:15]


# ============================================================
# PRICE WATCH
# ============================================================

def price_watch():

    rises = sorted(
        [
            p for p in players
            if p["status"] == "a"
        ],
        key=lambda p: p["net_transfers"],
        reverse=True
    )[:10]

    falls = sorted(
        [
            p for p in players
            if p["status"] == "a"
        ],
        key=lambda p: p["net_transfers"]
    )[:10]

    return rises, falls


# ============================================================
# BEST XI
# ============================================================

def best_xi(squad):

    by_position = defaultdict(list)

    for p in squad:

        by_position[
            p["position"]
        ].append(p)

    for pos in by_position:

        by_position[pos].sort(
            key=blended_score,
            reverse=True
        )

    gks = by_position.get(
        "GK",
        []
    )

    if not gks:
        return None, None

    best_formation = None
    best_score = -1
    best_lineup = None

    for defs, mids, fwds in VALID_FORMATIONS:

        available_def = by_position.get(
            "DEF",
            []
        )

        available_mid = by_position.get(
            "MID",
            []
        )

        available_fwd = by_position.get(
            "FWD",
            []
        )

        if (
            len(available_def) < defs
            or len(available_mid) < mids
            or len(available_fwd) < fwds
        ):
            continue

        lineup = (
            [gks[0]]
            + available_def[:defs]
            + available_mid[:mids]
            + available_fwd[:fwds]
        )

        total = sum(
            blended_score(p)
            for p in lineup
        )

        if total > best_score:

            best_score = total
            best_formation = (
                f"{defs}-{mids}-{fwds}"
            )
            best_lineup = lineup

    bench = [
        p for p in squad
        if p not in (
            best_lineup or []
        )
    ]

    return {
        "formation": best_formation,
        "lineup": best_lineup,
        "bench": bench
    }, best_score


# ============================================================
# WILDCARD
# ============================================================

def wildcard_rebuild(
    total_budget=SQUAD_BUDGET
):

    budget = total_budget / 10

    club_counts = defaultdict(int)

    squad = []

    quota = {
        "GK": 2,
        "DEF": 5,
        "MID": 5,
        "FWD": 3
    }

    pool_by_position = defaultdict(
        list
    )

    for p in players:

        if (
            p["status"] == "a"
            and p["chance"] >= 75
        ):
            pool_by_position[
                p["position"]
            ].append(p)

    for pos in pool_by_position:

        pool_by_position[pos].sort(
            key=blended_score,
            reverse=True
        )

    for position, count in quota.items():

        candidates = pool_by_position[
            position
        ]

        picked = 0

        for p in candidates:

            if picked >= count:
                break

            if (
                club_counts[
                    p["team_id"]
                ]
                >= MAX_PER_CLUB
            ):
                continue

            remaining_slots = (
                sum(quota.values())
                - len(squad)
                - 1
            )

            if (
                budget
                - p["price"]
                < remaining_slots * 4
            ):
                continue

            squad.append(p)

            club_counts[
                p["team_id"]
            ] += 1

            budget -= p["price"]

            picked += 1

    return squad, round(
        budget,
        1
    )


# ============================================================
# CHIP ANALYSIS
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
        p for p in squad
        if p.get("multiplier", 1) == 0
    ]

    rows = []

    total = 0

    for p in bench:

        pts = live_points.get(
            p["id"],
            0
        )

        total += pts

        rows.append({
            "Player": p["name"],
            "GW Points": pts
        })

    return rows, total


def chip_recommendation(
    squad,
    history
):

    recommendations = []

    used_chips = []

    if history:

        for chip in history.get(
            "chips",
            []
        ):

            name = chip.get(
                "name",
                ""
            )

            if name:
                used_chips.append(name)

    dgw_count = sum(
        1 for p in squad
        if p["next_gw_fixtures"] >= 2
    )

    bgw_count = sum(
        1 for p in squad
        if p["next_gw_fixtures"] == 0
    )

    if dgw_count >= 7:

        recommendations.append(
            "⚡ Strong Double-GW setup. "
            "Bench Boost or Triple Captain "
            "could be worth considering."
        )

    elif dgw_count >= 5:

        recommendations.append(
            "⚡ You have a reasonable number "
            "of Double-GW players. Monitor "
            "the upcoming chip window."
        )

    if bgw_count >= 4:

        recommendations.append(
            "⚠️ Several of your players blank "
            "next GW. Free Hit or transfers "
            "may become useful."
        )

    if not recommendations:

        recommendations.append(
            "No obvious immediate chip play. "
            "Holding chips for a stronger "
            "DGW/BGW opportunity looks sensible."
        )

    return recommendations, used_chips


# ============================================================
# DASHBOARD DATA
# ============================================================

def dashboard_summary(
    squad,
    bank,
    free_transfers
):

    decision = transfer_decision(
        squad,
        bank,
        free_transfers
    )

    captain_list = captain_recommendations(
        squad
    )

    sell_players = [
        p for p in squad
        if hold_sell(p)
        in [
            "🔴 SELL",
            "🔴 SELL / REPLACE"
        ]
    ]

    monitor_players = [
        p for p in squad
        if hold_sell(p)
        in [
            "🟠 CONSIDER SELLING",
            "🟡 MONITOR",
            "🟡 MONITOR — BLANK"
        ]
    ]

    dgw_players = [
        p for p in squad
        if p["next_gw_fixtures"] >= 2
    ]

    blank_players = [
        p for p in squad
        if p["next_gw_fixtures"] == 0
    ]

    return {
        "decision": decision,
        "captain": (
            captain_list[0]
            if captain_list
            else None
        ),
        "vice": (
            captain_list[1]
            if len(captain_list) > 1
            else None
        ),
        "sell": sell_players,
        "monitor": monitor_players,
        "dgw": dgw_players,
        "blank": blank_players
    }


# ============================================================
# PAGE HEADER
# ============================================================

st.title(
    "⚽ FPL Assistant Manager"
)

st.caption(
    f"GW{current_gw} | Planning GW{next_gw} | "
    "FPL data + underlying stats + fixture projections"
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Manager Settings"
    )

    entry_id = st.text_input(
        "Your FPL Team ID",
        value="",
        help=(
            "Your Team ID is in the URL "
            "of your FPL team."
        )
    )

    league_id = st.text_input(
        "Mini-League ID (optional)",
        value=""
    )

    free_transfers = st.number_input(
        "Free transfers available",
        min_value=0,
        max_value=5,
        value=1
    )

    st.divider()

    st.caption(
        "Expensive calculations such as "
        "Best XI, Wildcard and rival squads "
        "only run when you press their buttons."
    )


# ============================================================
# TABS
# ============================================================

tabs = st.tabs([
    "🏠 Dashboard",
    "👤 My Team",
    "🔄 Transfers",
    "🩺 Hold / Sell",
    "🧢 Captain",
    "📊 Player Rankings",
    "📅 Fixtures",
    "🎯 Differentials",
    "💰 Price Watch",
    "💊 Chips",
    "🕵️ Mini-League",
    "🏆 Best XI",
    "🛠️ Wildcard",
    "💬 AI Assistant",
])


# ============================================================
# LOAD TEAM
# ============================================================

team_data = None
my_squad = []

if entry_id:

    try:

        team_data, my_squad = load_my_team(
            int(entry_id)
        )

    except Exception:

        st.error(
            "Couldn't load your FPL team. "
            "Check the Team ID."
        )


# ============================================================
# TAB 1 — DASHBOARD
# ============================================================

with tabs[0]:

    st.header(
        f"🏠 GW{next_gw} Manager Dashboard"
    )

    if not my_squad:

        st.info(
            "Enter your FPL Team ID in the "
            "sidebar to activate your "
            "personal dashboard."
        )

        st.subheader(
            "What's included"
        )

        c1, c2, c3 = st.columns(3)

        c1.metric(
            "Player database",
            len(players)
        )

        c2.metric(
            "Planning horizon",
            f"{FIXTURE_HORIZON} GWs"
        )

        c3.metric(
            "Projection",
            f"{PROJECTION_WEEKS} GWs"
        )

        st.divider()

        st.subheader(
            "🔥 Current standout players"
        )

        top = sorted(
            players,
            key=blended_score,
            reverse=True
        )[:10]

        dashboard_rows = []

        for p in top:

            dashboard_rows.append({
                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Form": round(
                    p["form"],
                    1
                ),
                "xGI/90": round(
                    p["xgi90"],
                    2
                ),
                "FDR": round(
                    p["fdr"],
                    1
                ),
                "Projection": p[
                    "projection_4gw"
                ]
            })

        st.dataframe(
            pd.DataFrame(
                dashboard_rows
            ),
            use_container_width=True,
            hide_index=True
        )

    else:

        entry_history = team_data.get(
            "entry_history",
            {}
        )

        bank = (
            entry_history.get(
                "bank",
                0
            ) / 10
        )

        team_value = (
            entry_history.get(
                "value",
                0
            ) / 10
        )

        total_points = entry_history.get(
            "total_points",
            0
        )

        gw_points = entry_history.get(
            "points",
            0
        )

        summary = dashboard_summary(
            my_squad,
            bank,
            free_transfers
        )

        # ----------------------------------------------------
        # TOP METRICS
        # ----------------------------------------------------

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "GW Points",
            gw_points
        )

        c2.metric(
            "Season Points",
            total_points
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

        # ----------------------------------------------------
        # TRANSFER DECISION
        # ----------------------------------------------------

        decision = summary[
            "decision"
        ]

        if decision["decision"] == "TRANSFER":

            st.success(
                f"### 🟢 {decision['decision']}\n\n"
                f"{decision['reason']}"
            )

        elif decision["decision"] == "TAKE HIT":

            st.warning(
                f"### 🟠 {decision['decision']}\n\n"
                f"{decision['reason']}"
            )

        else:

            st.info(
                f"### 🔵 {decision['decision']}\n\n"
                f"{decision['reason']}"
            )

        if decision["suggestions"]:

            best = decision[
                "suggestions"
            ][0]

            st.write(
                f"**Best move:** "
                f"{best['out']['name']} ➡️ "
                f"{best['in']['name']}"
            )

            c1, c2, c3 = st.columns(3)

            c1.metric(
                "4-GW improvement",
                f"+{best['projected_gain']:.1f}"
            )

            c2.metric(
                "Hit",
                f"-{best['hit']}"
            )

            c3.metric(
                "Net improvement",
                f"+{best['net_gain']:.1f}"
            )

        st.divider()

        # ----------------------------------------------------
        # CAPTAIN
        # ----------------------------------------------------

        captain = summary[
            "captain"
        ]

        vice = summary[
            "vice"
        ]

        c1, c2 = st.columns(2)

        with c1:

            if captain:

                st.success(
                    f"### 👑 Captain: "
                    f"{captain['name']}"
                )

                st.write(
                    f"{captain['team']} | "
                    f"Form {captain['form']:.1f} | "
                    f"xGI/90 {captain['xgi90']:.2f}"
                )

                st.write(
                    f"**Why:** "
                    f"{captain_reason(captain)}"
                )

        with c2:

            if vice:

                st.info(
                    f"### 🥈 Vice: "
                    f"{vice['name']}"
                )

                st.write(
                    f"{vice['team']} | "
                    f"Form {vice['form']:.1f} | "
                    f"xGI/90 {vice['xgi90']:.2f}"
                )

        st.divider()

        # ----------------------------------------------------
        # SQUAD HEALTH
        # ----------------------------------------------------

        st.subheader(
            "🩺 Squad Health"
        )

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Strong holds",
            15
            - len(summary["sell"])
            - len(summary["monitor"])
        )

        c2.metric(
            "Monitor",
            len(summary["monitor"])
        )

        c3.metric(
            "Sell / replace",
            len(summary["sell"])
        )

        c4.metric(
            "Double GW",
            len(summary["dgw"])
        )

        if summary["sell"]:

            st.warning(
                "**Players requiring attention:** "
                + ", ".join(
                    p["name"]
                    for p in summary["sell"]
                )
            )

        if summary["blank"]:

            st.warning(
                "**Blank GW players:** "
                + ", ".join(
                    p["name"]
                    for p in summary["blank"]
                )
            )

        # ----------------------------------------------------
        # FIXTURE SWINGS
        # ----------------------------------------------------

        st.divider()

        st.subheader(
            "🔥 Fixture Swings"
        )

        swings = fixture_swings()

        easier = swings[:5]

        harder = swings[-5:][::-1]

        c1, c2 = st.columns(2)

        with c1:

            st.markdown(
                "**🟢 Improving fixtures**"
            )

            for row in easier:

                st.write(
                    f"**{row['Team']}** — "
                    f"{row['Next 2 GW FDR']:.1f} → "
                    f"{row['Next 5 GW FDR']:.1f}"
                )

        with c2:

            st.markdown(
                "**🔴 Worsening fixtures**"
            )

            for row in harder:

                st.write(
                    f"**{row['Team']}** — "
                    f"{row['Next 2 GW FDR']:.1f} → "
                    f"{row['Next 5 GW FDR']:.1f}"
                )

        st.divider()

        st.caption(
            "The dashboard is designed to answer "
            "the important question first: "
            "'What should I actually do this week?'"
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
            "Enter your FPL Team ID first."
        )

    else:

        entry_history = team_data.get(
            "entry_history",
            {}
        )

        bank = (
            entry_history.get(
                "bank",
                0
            ) / 10
        )

        team_value = (
            entry_history.get(
                "value",
                0
            ) / 10
        )

        total_points = entry_history.get(
            "total_points",
            0
        )

        gw_points = entry_history.get(
            "points",
            0
        )

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "GW Points",
            gw_points
        )

        c2.metric(
            "Season Points",
            total_points
        )

        c3.metric(
            "Team Value",
            f"£{team_value:.1f}m"
        )

        c4.metric(
            "Bank",
            f"£{bank:.1f}m"
        )

        rows = []

        for p in my_squad:

            role = (
                "© Captain"
                if p.get("is_captain")
                else "VC"
                if p.get("is_vice")
                else ""
            )

            rows.append({

                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Role": role,
                "Price": (
                    f"£{p['price']:.1f}m"
                ),
                "FPL Points": p["points"],
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
                "Next GW": p[
                    "next_gw_fixtures"
                ],
                "FDR": round(
                    p["fdr"],
                    1
                ),
                "Price": price_momentum_flag(
                    p
                ),
                "Status": player_status(
                    p
                )
            })

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True
        )

        st.divider()

        st.subheader(
            "📈 Squad points"
        )

        points_df = pd.DataFrame(
            [
                {
                    "Player": p["name"],
                    "Points": p["points"]
                }
                for p in my_squad
            ]
        ).sort_values(
            "Points",
            ascending=False
        )

        st.bar_chart(
            points_df.set_index(
                "Player"
            )
        )

        st.divider()

        st.subheader(
            "📉 Season rank history"
        )

        try:

            history = get_team_history(
                int(entry_id)
            )

            season_rows = [
                {
                    "GW": h["event"],
                    "Overall Rank":
                    h["overall_rank"]
                }
                for h in history.get(
                    "current",
                    []
                )
            ]

            if season_rows:

                rank_df = (
                    pd.DataFrame(
                        season_rows
                    )
                    .set_index("GW")
                )

                st.line_chart(
                    rank_df
                )

                st.caption(
                    "Lower rank is better."
                )

        except Exception:

            st.caption(
                "Rank history unavailable."
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
            "Load your FPL team first."
        )

    else:

        entry_history = team_data.get(
            "entry_history",
            {}
        )

        bank = (
            entry_history.get(
                "bank",
                0
            ) / 10
        )

        decision = transfer_decision(
            my_squad,
            bank,
            free_transfers
        )

        st.write(
            f"Bank: **£{bank:.1f}m** | "
            f"Free transfers: **{free_transfers}**"
        )

        if decision["decision"] == "ROLL":

            st.info(
                f"### 🔵 ROLL TRANSFER\n\n"
                f"{decision['reason']}"
            )

        elif decision["decision"] == "TRANSFER":

            st.success(
                f"### 🟢 MAKE A TRANSFER\n\n"
                f"{decision['reason']}"
            )

        else:

            st.warning(
                f"### 🟠 CONSIDER THE HIT\n\n"
                f"{decision['reason']}"
            )

        st.divider()

        suggestions = decision[
            "suggestions"
        ]

        if not suggestions:

            st.success(
                "No strong transfer recommendation."
            )

        else:

            for number, s in enumerate(
                suggestions,
                1
            ):

                outgoing = s["out"]
                incoming = s["in"]

                st.markdown(
                    f"### {number}. "
                    f"{outgoing['name']} ➡️ "
                    f"{incoming['name']}"
                )

                c1, c2, c3, c4 = st.columns(4)

                c1.metric(
                    "OUT",
                    outgoing["points"]
                )

                c2.metric(
                    "IN",
                    incoming["points"]
                )

                c3.metric(
                    f"{PROJECTION_WEEKS} GW gain",
                    f"+{s['projected_gain']:.1f}"
                )

                c4.metric(
                    "Net gain",
                    f"+{s['net_gain']:.1f}"
                )

                st.write(
                    f"**OUT:** "
                    f"{outgoing['team']} "
                    f"£{outgoing['price']:.1f}m"
                )

                st.write(
                    f"**IN:** "
                    f"{incoming['team']} "
                    f"£{incoming['price']:.1f}m"
                )

                st.write(
                    f"Fixtures: "
                    f"{incoming['fixtures']}"
                )

                st.write(
                    f"Price trend: "
                    f"{price_momentum_flag(incoming)}"
                )

                if s["hit"]:

                    st.warning(
                        "This transfer includes "
                        "a -4 hit."
                    )

                st.divider()

        # ----------------------------------------------------
        # MULTI TRANSFERS
        # ----------------------------------------------------

        st.subheader(
            "🔄 Multi-transfer chains"
        )

        st.caption(
            "Optional calculation: sell two "
            "players and upgrade to one "
            "premium player."
        )

        if st.button(
            "Find multi-transfer chains"
        ):

            multi = multi_transfer_suggestions(
                my_squad,
                bank,
                free_transfers
            )

            if not multi:

                st.info(
                    "No multi-transfer chain "
                    "currently clears the model."
                )

            else:

                for s in multi:

                    out_names = " + ".join(
                        p["name"]
                        for p in s["out"]
                    )

                    st.write(
                        f"**SELL {out_names}** "
                        f"➡️ **BUY "
                        f"{s['in']['name']}**"
                    )

                    st.write(
                        f"Money freed: "
                        f"£{s['money_freed']:.1f}m | "
                        f"Projected gain: "
                        f"+{s['projected_gain']:.1f} | "
                        f"Hit: -{s['hit']}"
                    )

        st.divider()

        # ----------------------------------------------------
        # SELL TO FUND
        # ----------------------------------------------------

        st.subheader(
            "💰 Sell a premium player "
            "to free money"
        )

        funding = (
            sell_to_fund_recommendations(
                my_squad,
                bank
            )
        )

        if not funding:

            st.info(
                "No obvious downgrade-to-fund "
                "opportunity."
            )

        else:

            for s in funding:

                st.write(
                    f"**SELL "
                    f"{s['out']['name']}** "
                    f"£{s['out']['price']:.1f}m "
                    f"→ **BUY "
                    f"{s['in']['name']}** "
                    f"£{s['in']['price']:.1f}m"
                )

                st.write(
                    f"Frees "
                    f"**£{s['saving']:.1f}m** | "
                    f"Score change: "
                    f"{s['gain']:+.1f}"
                )


# ============================================================
# TAB 4 — HOLD / SELL
# ============================================================

with tabs[3]:

    st.header(
        "🩺 Hold / Sell Diagnostics"
    )

    if not my_squad:

        st.info(
            "Load your FPL team first."
        )

    else:

        rows = []

        for p in my_squad:

            rows.append({

                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "FPL Points": p["points"],
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
                "Next GW": p[
                    "next_gw_fixtures"
                ],
                "FDR": round(
                    p["fdr"],
                    1
                ),
                "Price Trend":
                price_momentum_flag(p),
                "Decision":
                hold_sell(p)
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
        "🧢 Captain & Vice-Captain"
    )

    if not my_squad:

        st.info(
            "Load your FPL team first."
        )

    else:

        captain_list = (
            captain_recommendations(
                my_squad
            )
        )

        if captain_list:

            captain = captain_list[0]

            vice = (
                captain_list[1]
                if len(captain_list) > 1
                else None
            )

            c1, c2 = st.columns(2)

            with c1:

                st.success(
                    f"## 👑 {captain['name']}"
                )

                st.write(
                    f"{captain['team']} | "
                    f"{captain['points']} points"
                )

                st.write(
                    f"Form: "
                    f"**{captain['form']:.1f}**"
                )

                st.write(
                    f"xGI/90: "
                    f"**{captain['xgi90']:.2f}**"
                )

                st.write(
                    f"PPG: "
                    f"**{captain['ppg']:.1f}**"
                )

                st.write(
                    f"Fixtures: "
                    f"**{captain['next_gw_fixtures']}**"
                )

                st.write(
                    f"**Why:** "
                    f"{captain_reason(captain)}"
                )

            with c2:

                if vice:

                    st.info(
                        f"## 🥈 {vice['name']}"
                    )

                    st.write(
                        f"{vice['team']} | "
                        f"{vice['points']} points"
                    )

                    st.write(
                        f"Form: "
                        f"**{vice['form']:.1f}**"
                    )

                    st.write(
                        f"xGI/90: "
                        f"**{vice['xgi90']:.2f}**"
                    )

            st.divider()

            st.subheader(
                "💎 Triple Captain check"
            )

            tc = triple_captain_projection(
                my_squad
            )

            if tc:

                st.write(
                    f"**{tc['player']['name']}** "
                    f"projects "
                    f"**{tc['projected_single_gw']}** "
                    f"points this GW versus "
                    f"typical PPG of "
                    f"**{tc['typical_ppg']}**."
                )

                if tc["uplift"] >= 3:

                    st.success(
                        "Potential Triple Captain "
                        "spike detected."
                    )

                else:

                    st.caption(
                        "No unusually strong "
                        "Triple Captain spike detected."
                    )


# ============================================================
# TAB 6 — PLAYER RANKINGS
# ============================================================

with tabs[5]:

    st.header(
        "📊 Player Rankings"
    )

    ranking_type = st.radio(
        "Rank players by",
        [
            "Actual FPL Points",
            "Blended Score",
            "4-GW Projection",
            "Opta xGI/90",
            "Form",
            "PPG",
            "Value"
        ],
        horizontal=True
    )

    position_filter = st.selectbox(
        "Position",
        [
            "ALL",
            "GK",
            "DEF",
            "MID",
            "FWD"
        ]
    )

    search = st.text_input(
        "Search player or club"
    )

    filtered = players.copy()

    if position_filter != "ALL":

        filtered = [
            p for p in filtered
            if p["position"]
            == position_filter
        ]

    if search:

        search_lower = search.lower()

        filtered = [
            p for p in filtered
            if (
                search_lower
                in p["name"].lower()
                or
                search_lower
                in p["full_name"].lower()
                or
                search_lower
                in p["team"].lower()
            )
        ]

    if ranking_type == "Actual FPL Points":

        filtered.sort(
            key=lambda p:
            p["points"],
            reverse=True
        )

    elif ranking_type == "Blended Score":

        filtered.sort(
            key=blended_score,
            reverse=True
        )

    elif ranking_type == "4-GW Projection":

        filtered.sort(
            key=lambda p:
            p["projection_4gw"],
            reverse=True
        )

    elif ranking_type == "Opta xGI/90":

        filtered.sort(
            key=lambda p:
            p["xgi90"],
            reverse=True
        )

    elif ranking_type == "Form":

        filtered.sort(
            key=lambda p:
            p["form"],
            reverse=True
        )

    elif ranking_type == "PPG":

        filtered.sort(
            key=lambda p:
            p["ppg"],
            reverse=True
        )

    else:

        filtered.sort(
            key=lambda p:
            (
                p["points"]
                / p["price"]
                if p["price"] > 0
                else 0
            ),
            reverse=True
        )

    rows = []

    for p in filtered[:100]:

        rows.append({

            "Player": p["name"],
            "Club": p["team"],
            "Pos": p["position"],
            "Price":
            f"£{p['price']:.1f}m",
            "FPL Points":
            p["points"],
            "xGI/90":
            round(p["xgi90"], 2),
            "xGC/90":
            round(p["xgc90"], 2),
            "PPG":
            round(p["ppg"], 1),
            "Form":
            round(p["form"], 1),
            "Minutes":
            p["minutes"],
            "Goals":
            p["goals"],
            "Assists":
            p["assists"],
            "EP Next":
            round(p["ep_next"], 1),
            "4-GW Projection":
            p["projection_4gw"],
            "FDR":
            round(p["fdr"], 1),
            "Ownership":
            f"{p['ownership']:.1f}%",
            "Price Trend":
            price_momentum_flag(p),
            "Blended":
            round(
                blended_score(p),
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
        "📅 Fixture Difficulty"
    )

    st.write(
        f"Looking ahead from GW{next_gw} "
        f"over the next "
        f"{FIXTURE_HORIZON} gameweeks."
    )

    if my_squad:

        st.subheader(
            "👤 Your Squad Fixtures"
        )

        squad_fixture_rows = []

        for p in my_squad:

            squad_fixture_rows.append({

                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Avg FDR":
                round(p["fdr"], 2),
                "Fixtures":
                p["fixtures"]
            })

        squad_fdr_df = (
            pd.DataFrame(
                squad_fixture_rows
            )
            .sort_values(
                "Avg FDR"
            )
        )

        st.dataframe(
            squad_fdr_df,
            use_container_width=True,
            hide_index=True
        )

        st.divider()

    st.subheader(
        "🏟️ All Teams"
    )

    fixture_rows = []

    for team_id, team in teams.items():

        fixture_rows.append({

            "Team":
            team.get(
                "short_name",
                "?"
            ),

            "Average FDR":
            round(
                average_fdr(
                    fixture_map,
                    team_id
                ),
                2
            ),

            "Fixtures":
            fixture_text(
                fixture_map,
                team_names,
                team_id,
                FIXTURE_HORIZON
            )
        })

    fixture_df = (
        pd.DataFrame(
            fixture_rows
        )
        .sort_values(
            "Average FDR"
        )
    )

    st.dataframe(
        fixture_df,
        use_container_width=True,
        hide_index=True
    )

    st.divider()

    st.subheader(
        "🔥 Fixture Swings"
    )

    swing_df = pd.DataFrame(
        fixture_swings()
    )

    st.dataframe(
        swing_df,
        use_container_width=True,
        hide_index=True
    )

    dgw_teams = []
    bgw_teams = []

    for team_id in teams:

        count = fixture_count(
            fixture_map,
            team_id,
            next_gw
        )

        if count >= 2:

            dgw_teams.append(
                team_names[team_id]
            )

        elif count == 0:

            bgw_teams.append(
                team_names[team_id]
            )

    if dgw_teams:

        st.success(
            f"⚡ GW{next_gw} Double GW: "
            + ", ".join(dgw_teams)
        )

    if bgw_teams:

        st.warning(
            f"⚠️ GW{next_gw} Blank GW: "
            + ", ".join(bgw_teams)
        )


# ============================================================
# TAB 8 — DIFFERENTIALS
# ============================================================

with tabs[7]:

    st.header(
        "🎯 Differential Finder"
    )

    st.caption(
        "Low-owned players with strong "
        "underlying numbers, form and "
        "fixture projections."
    )

    ownership_limit = st.slider(
        "Maximum ownership",
        min_value=1,
        max_value=20,
        value=10
    )

    differential_position = st.selectbox(
        "Position",
        [
            "ALL",
            "GK",
            "DEF",
            "MID",
            "FWD"
        ],
        key="differential_position"
    )

    differentials = differential_players(
        differential_position,
        ownership_limit
    )

    rows = []

    for p in differentials:

        rows.append({

            "Player": p["name"],
            "Club": p["team"],
            "Pos": p["position"],
            "Price":
            f"£{p['price']:.1f}m",
            "Ownership":
            f"{p['ownership']:.1f}%",
            "Form":
            round(p["form"], 1),
            "xGI/90":
            round(p["xgi90"], 2),
            "FDR":
            round(p["fdr"], 1),
            "4-GW Projection":
            p["projection_4gw"],
            "Blended":
            round(
                p["blended"],
                1
            )
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True
    )

    if differentials:

        best = differentials[0]

        st.success(
            f"🎯 **Best differential:** "
            f"{best['name']} "
            f"({best['ownership']:.1f}% owned)"
        )

        st.write(
            f"Form: **{best['form']:.1f}** | "
            f"xGI/90: **{best['xgi90']:.2f}** | "
            f"FDR: **{best['fdr']:.1f}** | "
            f"4-GW projection: "
            f"**{best['projection_4gw']}**"
        )


# ============================================================
# TAB 9 — PRICE WATCH
# ============================================================

with tabs[8]:

    st.header(
        "💰 Price Change Watch"
    )

    st.caption(
        "Players showing the strongest "
        "transfer momentum."
    )

    rises, falls = price_watch()

    c1, c2 = st.columns(2)

    with c1:

        st.subheader(
            "📈 Possible Risers"
        )

        rows = []

        for p in rises:

            rows.append({

                "Player": p["name"],
                "Club": p["team"],
                "Price":
                f"£{p['price']:.1f}m",
                "Net Transfers":
                p["net_transfers"],
                "Price Change":
                price_change_value(p),
                "Ownership":
                f"{p['ownership']:.1f}%"
            })

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True
        )

    with c2:

        st.subheader(
            "📉 Possible Fallers"
        )

        rows = []

        for p in falls:

            rows.append({

                "Player": p["name"],
                "Club": p["team"],
                "Price":
                f"£{p['price']:.1f}m",
                "Net Transfers":
                p["net_transfers"],
                "Price Change":
                price_change_value(p),
                "Ownership":
                f"{p['ownership']:.1f}%"
            })

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# TAB 10 — CHIPS
# ============================================================

with tabs[9]:

    st.header(
        "💊 Chip Strategy"
    )

    if not my_squad:

        st.info(
            "Load your FPL team first."
        )

    else:

        try:

            history = get_team_history(
                int(entry_id)
            )

        except Exception:

            history = None

        recommendations, used_chips = (
            chip_recommendation(
                my_squad,
                history
            )
        )

        for recommendation in recommendations:

            st.info(
                recommendation
            )

        st.divider()

        dgw_count = sum(
            1 for p in my_squad
            if p["next_gw_fixtures"] >= 2
        )

        bgw_count = sum(
            1 for p in my_squad
            if p["next_gw_fixtures"] == 0
        )

        c1, c2 = st.columns(2)

        c1.metric(
            "Players with 2+ fixtures",
            dgw_count
        )

        c2.metric(
            "Players with no fixture",
            bgw_count
        )

        if used_chips:

            st.write(
                "**Chips recorded:** "
                + ", ".join(
                    used_chips
                )
            )

        st.divider()

        st.subheader(
            "🪑 Bench Boost calculator"
        )

        bb = bench_boost_value(
            my_squad,
            entry_id
        )

        if bb:

            rows, total = bb

            if rows:

                st.dataframe(
                    pd.DataFrame(rows),
                    use_container_width=True,
                    hide_index=True
                )

                st.metric(
                    f"Bench points in GW{current_gw}",
                    f"+{total}"
                )


# ============================================================
# TAB 11 — MINI LEAGUE
# ============================================================

with tabs[10]:

    st.header(
        "🕵️ Mini-League Rival Analysis"
    )

    if not league_id:

        st.info(
            "Enter your Mini-League ID."
        )

    elif not my_squad:

        st.info(
            "Load your FPL team first."
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

            if not standings:

                st.warning(
                    "No standings found."
                )

            else:

                my_entry = next(
                    (
                        x for x in standings
                        if x["entry"]
                        == int(entry_id)
                    ),
                    None
                )

                leader = standings[0]

                if my_entry:

                    gap = (
                        leader["total"]
                        - my_entry["total"]
                    )

                    c1, c2, c3 = st.columns(3)

                    c1.metric(
                        "Your Rank",
                        f"#{my_entry['rank']}"
                    )

                    c2.metric(
                        "Leader",
                        leader["player_name"]
                    )

                    c3.metric(
                        "Points Behind",
                        gap
                    )

                st.divider()

                rivals = standings[:10]

                if st.button(
                    "🔄 Load rival squads"
                ):

                    rival_picks = {}

                    for rival in rivals:

                        try:

                            data = get_entry_picks(
                                rival["entry"],
                                current_gw
                            )

                            rival_picks[
                                rival["entry"]
                            ] = data.get(
                                "picks",
                                []
                            )

                        except Exception:

                            continue

                    st.session_state[
                        "rival_picks"
                    ] = rival_picks

                    st.session_state[
                        "rival_cache_key"
                    ] = (
                        f"{league_id}_"
                        f"{current_gw}"
                    )

                cache_key = (
                    f"{league_id}_"
                    f"{current_gw}"
                )

                if (
                    st.session_state.get(
                        "rival_cache_key"
                    )
                    != cache_key
                ):

                    st.info(
                        "Press the button to "
                        "fetch rival squads."
                    )

                else:

                    rival_picks = (
                        st.session_state[
                            "rival_picks"
                        ]
                    )

                    my_ids = {
                        p["id"]
                        for p in my_squad
                    }

                    st.subheader(
                        "Effective Ownership"
                    )

                    eo_counts = (
                        defaultdict(float)
                    )

                    for picks in (
                        rival_picks.values()
                    ):

                        for pick in picks:

                            weight = pick.get(
                                "multiplier",
                                1
                            )

                            eo_counts[
                                pick["element"]
                            ] += weight

                    eo_rows = []

                    for pid, weight in sorted(
                        eo_counts.items(),
                        key=lambda x:
                        x[1],
                        reverse=True
                    )[:15]:

                        p = player_by_id.get(
                            pid
                        )

                        if p:

                            eo_rows.append({

                                "Player":
                                p["name"],

                                "Effective Ownership":
                                round(
                                    weight
                                    / max(
                                        len(
                                            rival_picks
                                        ),
                                        1
                                    )
                                    * 100,
                                    1
                                ),

                                "In Your Squad":
                                "✅"
                                if pid in my_ids
                                else "—"
                            })

                    st.dataframe(
                        pd.DataFrame(
                            eo_rows
                        ),
                        use_container_width=True,
                        hide_index=True
                    )

                    st.divider()

                    st.subheader(
                        "Rival Differentials"
                    )

                    matrix_rows = []

                    for rival in rivals:

                        rival_ids = {
                            pick["element"]
                            for pick in
                            rival_picks.get(
                                rival["entry"],
                                []
                            )
                        }

                        overlap = len(
                            my_ids
                            & rival_ids
                        )

                        matrix_rows.append({

                            "Rival":
                            rival["player_name"],

                            "Rank":
                            rival["rank"],

                            "Total":
                            rival["total"],

                            "Squad Overlap":
                            f"{overlap}/15",

                            "Their Differentials":
                            len(
                                rival_ids
                                - my_ids
                            )
                        })

                    st.dataframe(
                        pd.DataFrame(
                            matrix_rows
                        ),
                        use_container_width=True,
                        hide_index=True
                    )

        except Exception:

            st.error(
                "Couldn't load the mini-league."
            )


# ============================================================
# TAB 12 — BEST XI
# ============================================================

with tabs[11]:

    st.header(
        "🏆 Best XI From Your Squad"
    )

    st.caption(
        "This only selects players from "
        "your current 15-man squad."
    )

    if not my_squad:

        st.info(
            "Load your FPL team first."
        )

    elif st.button(
        "Calculate Best XI"
    ):

        result, score = best_xi(
            my_squad
        )

        if not result:

            st.warning(
                "Couldn't build a valid XI."
            )

        else:

            st.success(
                f"Best formation: "
                f"**{result['formation']}** "
                f"| Score: "
                f"**{score:.1f}**"
            )

            lineup_rows = []

            for p in result["lineup"]:

                lineup_rows.append({

                    "Player":
                    p["name"],

                    "Pos":
                    p["position"],

                    "Club":
                    p["team"],

                    "FPL Points":
                    p["points"],

                    "xGI/90":
                    round(
                        p["xgi90"],
                        2
                    ),

                    "Blended Score":
                    round(
                        blended_score(p),
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

            for p in sorted(
                result["bench"],
                key=blended_score,
                reverse=True
            ):

                bench_rows.append({

                    "Player":
                    p["name"],

                    "Pos":
                    p["position"],

                    "Blended Score":
                    round(
                        blended_score(p),
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


# ============================================================
# TAB 13 — WILDCARD
# ============================================================

with tabs[12]:

    st.header(
        "🛠️ Wildcard Rebuild"
    )

    st.caption(
        "Builds a fresh 15-man squad "
        "within £100m."
    )

    if st.button(
        "Suggest a Wildcard squad"
    ):

        squad, leftover = wildcard_rebuild()

        if len(squad) < 15:

            st.warning(
                f"Only found "
                f"{len(squad)}/15 players."
            )

        total_cost = (
            SQUAD_BUDGET / 10
            - leftover
        )

        c1, c2 = st.columns(2)

        c1.metric(
            "Squad cost",
            f"£{total_cost:.1f}m"
        )

        c2.metric(
            "Money remaining",
            f"£{leftover:.1f}m"
        )

        rows = []

        for p in sorted(
            squad,
            key=lambda p:
            (
                p["position"],
                -blended_score(p)
            )
        ):

            rows.append({

                "Player":
                p["name"],

                "Pos":
                p["position"],

                "Club":
                p["team"],

                "Price":
                f"£{p['price']:.1f}m",

                "FPL Points":
                p["points"],

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

                "Blended":
                round(
                    blended_score(p),
                    1
                )
            })

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# TAB 14 — AI ASSISTANT
# ============================================================

with tabs[13]:

    st.header(
        "💬 FPL AI Assistant"
    )

    if "GEMINI_API_KEY" not in st.secrets:

        st.warning(
            "GEMINI_API_KEY not found in "
            "Streamlit Secrets."
        )

    else:

        pin_input = st.text_input(
            "Enter Manager PIN",
            type="password",
            placeholder="Enter 4-digit PIN"
        )

        if pin_input != "2325":

            if pin_input:

                st.error(
                    "Incorrect PIN."
                )

            else:

                st.info(
                    "🔒 Enter Manager PIN "
                    "to activate the assistant."
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

            if "messages" not in st.session_state:

                st.session_state.messages = []

            for msg in (
                st.session_state.messages
            ):

                with st.chat_message(
                    msg["role"]
                ):

                    st.markdown(
                        msg["content"]
                    )

            if prompt := st.chat_input(
                "Ask about your team..."
            ):

                st.session_state.messages.append(
                    {
                        "role": "user",
                        "content": prompt
                    }
                )

                with st.chat_message(
                    "user"
                ):

                    st.markdown(
                        prompt
                    )

                with st.chat_message(
                    "assistant"
                ):

                    with st.spinner(
                        "Analysing FPL data..."
                    ):

                        try:

                            top_picks = sorted(
                                players,
                                key=blended_score,
                                reverse=True
                            )[:15]

                            picks_summary = "\n".join(
                                [
                                    (
                                        f"- {p['name']} "
                                        f"({p['team']}, "
                                        f"{p['position']}): "
                                        f"£{p['price']:.1f}m | "
                                        f"Form {p['form']:.1f} | "
                                        f"xGI/90 "
                                        f"{p['xgi90']:.2f} | "
                                        f"FDR "
                                        f"{p['fdr']:.1f} | "
                                        f"4GW "
                                        f"{p['projection_4gw']}"
                                    )
                                    for p in top_picks
                                ]
                            )

                            if my_squad:

                                squad_context = "\n".join(
                                    [
                                        (
                                            f"- {p['name']} "
                                            f"({p['team']}, "
                                            f"{p['position']}): "
                                            f"£{p['price']:.1f}m | "
                                            f"Form "
                                            f"{p['form']:.1f} | "
                                            f"xGI/90 "
                                            f"{p['xgi90']:.2f} | "
                                            f"FDR "
                                            f"{p['fdr']:.1f} | "
                                            f"Fixtures "
                                            f"{p['fixtures']} | "
                                            f"Status "
                                            f"{player_status(p)}"
                                        )
                                        for p in my_squad
                                    ]
                                )

                                entry_history = (
                                    team_data.get(
                                        "entry_history",
                                        {}
                                    )
                                )

                                bank = (
                                    entry_history.get(
                                        "bank",
                                        0
                                    ) / 10
                                )

                                decision = (
                                    transfer_decision(
                                        my_squad,
                                        bank,
                                        free_transfers
                                    )
                                )

                                captain_list = (
                                    captain_recommendations(
                                        my_squad
                                    )
                                )

                                manager_summary = (
                                    f"\nManager recommendation:\n"
                                    f"- Transfer decision: "
                                    f"{decision['decision']}\n"
                                    f"- Reason: "
                                    f"{decision['reason']}\n"
                                    f"- Captain: "
                                    f"{captain_list[0]['name'] if captain_list else 'N/A'}\n"
                                )

                            else:

                                squad_context = (
                                    "No squad loaded."
                                )

                                manager_summary = ""

                            live_data_payload = f"""
You are an elite Fantasy Premier League strategist.

Current GW:
GW{current_gw}

Planning GW:
GW{next_gw}

Manager squad:
{squad_context}

Manager summary:
{manager_summary}

Top players:
{picks_summary}

User question:
{prompt}
"""

                            response = client.models.generate_content(

                                model="gemini-2.5-flash",

                                contents=live_data_payload,

                                config={
                                    "system_instruction": (
                                        "Give practical FPL advice. "
                                        "Use the supplied current squad, "
                                        "fixtures, form, xGI/90, availability "
                                        "and projections. "
                                        "Do not invent players or statistics. "
                                        "If information is uncertain, say so. "
                                        "Prioritise actionable decisions."
                                    )
                                }
                            )

                            answer = response.text

                            st.markdown(
                                answer
                            )

                            st.session_state.messages.append(
                                {
                                    "role":
                                    "assistant",
                                    "content":
                                    answer
                                }
                            )

                        except Exception as e:

                            st.error(
                                f"Gemini error: {e}"
                            )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "⚽ FPL Assistant Manager — "
    "Ultimate Decision Engine"
)

st.caption(
    "Data: Official Fantasy Premier League API "
    "and underlying player statistics supplied "
    "by the FPL data feed."
)
