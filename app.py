import streamlit as st
import requests
import pandas as pd
import itertools
from collections import defaultdict

# ============================================================
# FPL ASSISTANT MANAGER — ULTIMATE VERSION
#
# Builds on the fast version. Adds:
# - Budget + 3-per-club aware transfer engine (single AND
#   multi-transfer / sell-two-buy-one chains)
# - Multi-gameweek hit value (not just next-GW gain)
# - Best XI / formation optimiser (opt-in tab, doesn't run
#   unless requested — keeps the app fast by default)
# - Bench Boost calculator using real GW points
# - Triple Captain projection
# - Wildcard rebuild suggestion (budget-constrained squad)
# - Price-change risk flag (transfer momentum proxy)
# - Season rank history chart
# - Deeper mini-league: full rival differential matrix +
#   effective (captaincy-weighted) ownership within league
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)

API = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0"}

FIXTURE_HORIZON = 5
SQUAD_BUDGET = 1000       # tenths of £m, i.e. £100.0m
MAX_PER_CLUB = 3
HIT_PROJECTION_WEEKS = 4  # how many GWs a -4 hit's value is judged over

VALID_FORMATIONS = [
    # (DEF, MID, FWD) — GK is always 1
    (3, 4, 3), (3, 5, 2), (4, 4, 2), (4, 3, 3),
    (4, 5, 1), (5, 4, 1), (5, 3, 2), (5, 2, 3),
]


# ============================================================
# API FUNCTIONS
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def get_bootstrap():
    r = requests.get(f"{API}/bootstrap-static/", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=900, show_spinner=False)
def get_fixtures():
    r = requests.get(f"{API}/fixtures/", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_info(entry_id):
    r = requests.get(f"{API}/entry/{entry_id}/", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_picks(entry_id, gameweek):
    r = requests.get(f"{API}/entry/{entry_id}/event/{gameweek}/picks/", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300, show_spinner=False)
def get_league(league_id):
    r = requests.get(f"{API}/leagues-classic/{league_id}/standings/", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=600, show_spinner=False)
def get_team_history(entry_id):
    r = requests.get(f"{API}/entry/{entry_id}/history/", headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300, show_spinner=False)
def get_live_gw(gameweek):
    """Actual per-player points for a specific gameweek (needed for
    Bench Boost calculation — total_points is season-to-date, not per-GW)."""
    r = requests.get(f"{API}/event/{gameweek}/live/", headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    return {
        el["id"]: el["stats"]["total_points"]
        for el in data.get("elements", [])
    }


# ============================================================
# LOAD DATA
# ============================================================

try:
    bootstrap = get_bootstrap()
    fixtures = get_fixtures()
except Exception:
    st.error("⚠️ FPL data could not be loaded.")
    st.info("The official FPL API may be temporarily unavailable. Try refreshing.")
    st.stop()


# ============================================================
# BASIC DATA
# ============================================================

events = bootstrap.get("events", [])
raw_players = bootstrap.get("elements", [])
raw_teams = bootstrap.get("teams", [])

teams = {t["id"]: t for t in raw_teams}
team_names = {t["id"]: t.get("short_name", "?") for t in raw_teams}
positions = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

current_event = next((e for e in events if e.get("is_current")), None)
next_event = next((e for e in events if e.get("is_next")), None)

current_gw = current_event["id"] if current_event else 1
next_gw = next_event["id"] if next_event else current_gw + 1


def num(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


# ============================================================
# FIXTURE ENGINE
# ============================================================

def build_fixture_map():
    result = defaultdict(list)
    for fixture in fixtures:
        gw = fixture.get("event")
        if gw is None or gw < next_gw or gw > next_gw + FIXTURE_HORIZON - 1:
            continue
        home = fixture.get("team_h")
        away = fixture.get("team_a")
        if home:
            result[home].append({
                "gw": gw, "home": True, "opponent": away,
                "difficulty": fixture.get("team_h_difficulty", 3)
            })
        if away:
            result[away].append({
                "gw": gw, "home": False, "opponent": home,
                "difficulty": fixture.get("team_a_difficulty", 3)
            })
    return result


fixture_map = build_fixture_map()


def fixture_count(team_id, gw):
    return len([f for f in fixture_map.get(team_id, []) if f["gw"] == gw])


def average_fdr(team_id):
    games = fixture_map.get(team_id, [])
    if not games:
        return 3.0
    return sum(f["difficulty"] for f in games) / len(games)


def fixture_text(team_id, number=5):
    games = sorted(fixture_map.get(team_id, []), key=lambda x: (x["gw"], not x["home"]))[:number]
    out = []
    for f in games:
        opponent = team_names.get(f["opponent"], "?")
        location = "H" if f["home"] else "A"
        out.append(f"GW{f['gw']} {opponent} ({location}) [{f['difficulty']}]")
    return " | ".join(out) if out else "No fixtures"


# ============================================================
# PLAYER DATABASE
# ============================================================

players = []

for p in raw_players:
    team_id = p["team"]
    chance = p.get("chance_of_playing_next_round")
    if chance is None:
        chance = 100

    transfers_in = p.get("transfers_in_event", 0)
    transfers_out = p.get("transfers_out_event", 0)
    net_transfers = transfers_in - transfers_out

    player = {
        "id": p["id"],
        "name": p.get("web_name", "?"),
        "full_name": f"{p.get('first_name', '')} {p.get('second_name', '')}".strip(),
        "position": positions.get(p.get("element_type"), "?"),
        "team_id": team_id,
        "team": team_names.get(team_id, "?"),
        "price": p.get("now_cost", 0) / 10,
        "points": p.get("total_points", 0),
        "ppg": num(p.get("points_per_game")),
        "form": num(p.get("form")),
        "minutes": p.get("minutes", 0),
        "goals": p.get("goals_scored", 0),
        "assists": p.get("assists", 0),
        "clean_sheets": p.get("clean_sheets", 0),
        "bonus": p.get("bonus", 0),
        "bps": p.get("bps", 0),
        "ep_next": num(p.get("ep_next")),
        "ep_this": num(p.get("ep_this")),
        "ownership": num(p.get("selected_by_percent")),
        "chance": chance,
        "status": p.get("status", "a"),
        "news": p.get("news", ""),
        "xgi90": num(p.get("expected_goal_involvements_per_90")),
        "xgc90": num(p.get("expected_goals_conceded_per_90")),
        "ict": num(p.get("ict_index")),
        "transfers_in": transfers_in,
        "transfers_out": transfers_out,
        "net_transfers": net_transfers,
        "price_change": p.get("cost_change_event", 0),
        "price_change_overall": p.get("cost_change_start", 0),
    }

    player["fdr"] = average_fdr(team_id)
    player["next_gw_fixtures"] = fixture_count(team_id, next_gw)
    player["fixtures"] = fixture_text(team_id, FIXTURE_HORIZON)

    players.append(player)

player_by_id = {p["id"]: p for p in players}


# ============================================================
# PLAYER SCORING
# ============================================================

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
    return 0


def blended_score(player):
    """Ranking score, not a points prediction. Actual points earned
    remain the biggest single component by design."""
    actual_points = player["points"]
    ppg_component = min(player["ppg"] * 2.0, 15)
    form_component = min(player["form"] * 1.5, 12)
    expected_component = min(player["ep_next"] * 2.0, 15)
    fixture_component = (3.0 - player["fdr"]) * 3.0
    availability = availability_factor(player) * 5
    dgw_bonus = 8 if player["next_gw_fixtures"] >= 2 else 0
    bgw_penalty = 20 if player["next_gw_fixtures"] == 0 else 0

    # Defenders/GKs get a small boost for low expected goals conceded —
    # clean sheet potential wasn't otherwise represented.
    defensive_component = 0
    if player["position"] in ("GK", "DEF") and player["xgc90"] > 0:
        defensive_component = max(0, (1.3 - player["xgc90"]) * 4)

    return round(
        actual_points + ppg_component + form_component + expected_component
        + fixture_component + availability + dgw_bonus - bgw_penalty
        + defensive_component,
        2
    )


def multi_gw_projection(player, weeks=HIT_PROJECTION_WEEKS):
    """Rough projected points over the next N gameweeks, blending
    official ep_next with recent form and fixture ease. This is what
    a hit's cost should be weighed against — not a single GW swing."""
    games = sorted(fixture_map.get(player["team_id"], []), key=lambda x: x["gw"])[:weeks]
    if not games:
        return player["ep_next"]

    base_per_fixture = (player["ep_next"] * 0.6) + (player["ppg"] * 0.4)
    total = 0.0
    for g in games:
        difficulty_multiplier = 1.0 + ((3 - g["difficulty"]) * 0.08)
        total += base_per_fixture * difficulty_multiplier * availability_factor(player)
    return round(total, 1)


def price_momentum_flag(player):
    """Proxy for imminent price change based on net transfer activity.
    FPL's real threshold is undisclosed and ownership-relative, so this
    is directional, not a guarantee."""
    net = player["net_transfers"]
    ownership = max(player["ownership"], 0.1)
    ratio = net / (ownership * 1000)

    if ratio > 0.4:
        return "📈 Likely to rise"
    if ratio < -0.4:
        return "📉 Likely to fall"
    return "— Stable"


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
    data = get_entry_picks(entry_id, current_gw)
    squad = []
    for pick in data.get("picks", []):
        player = player_by_id.get(pick["element"])
        if player:
            copied = player.copy()
            copied["is_captain"] = pick.get("is_captain", False)
            copied["is_vice"] = pick.get("is_vice_captain", False)
            copied["multiplier"] = pick.get("multiplier", 1)
            copied["position_slot"] = pick.get("position", 0)
            squad.append(copied)
    return data, squad


def squad_club_counts(squad, exclude_id=None):
    counts = defaultdict(int)
    for p in squad:
        if p["id"] != exclude_id:
            counts[p["team_id"]] += 1
    return counts


# ============================================================
# TRANSFER ENGINE — budget + 3-per-club aware, single transfers
# ============================================================

def transfer_suggestions(squad, bank, free_transfers):
    owned_ids = {p["id"] for p in squad}
    club_counts = squad_club_counts(squad)
    suggestions = []

    for outgoing in squad:
        same_position = [
            p for p in players
            if p["position"] == outgoing["position"]
            and p["id"] not in owned_ids
            and p["status"] == "a"
            and p["chance"] > 0
        ]
        same_position.sort(key=blended_score, reverse=True)

        for incoming in same_position[:20]:
            available_money = bank + outgoing["price"]
            if incoming["price"] > available_money:
                continue

            # 3-per-club constraint: check the count AFTER removing the
            # outgoing player and adding the incoming one.
            projected_count = club_counts[incoming["team_id"]]
            if incoming["team_id"] == outgoing["team_id"]:
                projected_count -= 1  # outgoing's club slot frees up
            if projected_count + 1 > MAX_PER_CLUB:
                continue

            gain = blended_score(incoming) - blended_score(outgoing)
            projected_gain = multi_gw_projection(incoming) - multi_gw_projection(outgoing)
            hit = 0 if free_transfers > 0 else 4

            if hit == 4:
                # Judge the hit against points over the projection window,
                # not just next GW — a -4 pays for itself if the swap
                # nets more than 4 points across HIT_PROJECTION_WEEKS.
                if projected_gain < 4:
                    continue
            else:
                if gain < 2:
                    continue

            suggestions.append({
                "out": outgoing,
                "in": incoming,
                "gain": gain,
                "projected_gain": projected_gain,
                "hit": hit,
                "cost_difference": incoming["price"] - outgoing["price"],
            })

    suggestions.sort(key=lambda x: (x["projected_gain"] - x["hit"]), reverse=True)
    return suggestions[:10]


# ============================================================
# MULTI-TRANSFER ENGINE — sell two, buy one premium
# ============================================================

def multi_transfer_suggestions(squad, bank, free_transfers, max_results=6):
    """Finds sell-two-buy-one-premium moves that a 1-for-1 engine can't
    see: freeing two mid-price players to afford one standout, while
    respecting budget and the 3-per-club limit."""
    owned_ids = {p["id"] for p in squad}
    club_counts = squad_club_counts(squad)
    results = []

    # Only consider outfield positions in pairs (rarely worth doing this
    # with goalkeepers) and cap combinations to keep it fast.
    candidates_by_position = defaultdict(list)
    for p in squad:
        if p["position"] != "GK":
            candidates_by_position[p["position"]].append(p)

    hit_cost = 0 if free_transfers >= 2 else (4 if free_transfers == 1 else 8)

    for position, group in candidates_by_position.items():
        if len(group) < 2:
            continue

        # Weakest two first — cheapest way to fund an upgrade.
        weakest_pairs = sorted(
            itertools.combinations(group, 2),
            key=lambda pair: blended_score(pair[0]) + blended_score(pair[1])
        )[:5]

        premiums = [
            p for p in players
            if p["position"] == position
            and p["id"] not in owned_ids
            and p["status"] == "a"
            and p["chance"] >= 75
        ]
        premiums.sort(key=blended_score, reverse=True)

        for out_a, out_b in weakest_pairs:
            available_money = bank + out_a["price"] + out_b["price"]

            projected_counts = club_counts.copy()
            projected_counts[out_a["team_id"]] -= 1
            projected_counts[out_b["team_id"]] -= 1

            for incoming in premiums[:10]:
                if incoming["price"] > available_money:
                    continue
                if projected_counts[incoming["team_id"]] + 1 > MAX_PER_CLUB:
                    continue

                projected_gain = (
                    multi_gw_projection(incoming)
                    - multi_gw_projection(out_a)
                    - multi_gw_projection(out_b)
                )
                # This leaves one squad slot empty in this simplified
                # model — treat as "and one more free-agent-quality player"
                # rather than a literal 2-for-1.
                if projected_gain - hit_cost < 6:
                    continue

                results.append({
                    "out": [out_a, out_b],
                    "in": incoming,
                    "money_freed": round(available_money - incoming["price"], 1),
                    "projected_gain": round(projected_gain, 1),
                    "hit": hit_cost,
                })

    results.sort(key=lambda x: (x["projected_gain"] - x["hit"]), reverse=True)
    return results[:max_results]


# ============================================================
# SELL-TO-FUND ENGINE
# ============================================================

def sell_to_fund_recommendations(squad, bank):
    recommendations = []
    owned_ids = {p["id"] for p in squad}
    club_counts = squad_club_counts(squad)

    for outgoing in squad:
        if outgoing["price"] < 5.0:
            continue

        replacements = [
            p for p in players
            if p["position"] == outgoing["position"]
            and p["id"] not in owned_ids
            and p["status"] == "a"
            and p["chance"] >= 75
        ]
        replacements.sort(key=blended_score, reverse=True)

        for incoming in replacements[:10]:
            saving = outgoing["price"] - incoming["price"]
            if saving < 0.5:
                continue

            projected_count = club_counts[incoming["team_id"]]
            if incoming["team_id"] == outgoing["team_id"]:
                projected_count -= 1
            if projected_count + 1 > MAX_PER_CLUB:
                continue

            gain = blended_score(incoming) - blended_score(outgoing)
            if gain >= -5:
                recommendations.append({
                    "out": outgoing, "in": incoming, "saving": saving, "gain": gain
                })

    recommendations.sort(key=lambda x: x["saving"], reverse=True)
    return recommendations[:8]


# ============================================================
# CAPTAIN RECOMMENDATION
# ============================================================

def captain_recommendations(squad):
    available = [
        p for p in squad
        if p["chance"] >= 75 and p["status"] == "a" and p["next_gw_fixtures"] > 0
    ]
    available.sort(key=blended_score, reverse=True)
    return available[:5]


def triple_captain_projection(squad):
    """Compares the top captain pick's projected haul this GW against
    their own recent scoring history, to flag whether a DGW/soft
    fixture genuinely looks like a Triple Captain-worthy spike."""
    candidates = captain_recommendations(squad)
    if not candidates:
        return None
    top = candidates[0]
    projected_single_gw = top["ep_next"] * max(top["next_gw_fixtures"], 1)
    typical = top["ppg"]
    uplift = projected_single_gw - typical
    return {
        "player": top,
        "projected_single_gw": round(projected_single_gw, 1),
        "typical_ppg": round(typical, 1),
        "uplift": round(uplift, 1),
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
    if player["form"] < 2.5 and player["ppg"] < 3 and player["minutes"] > 300:
        return "🔴 SELL"
    if player["form"] >= 5:
        return "🟢 STRONG HOLD"
    if player["ppg"] >= 5:
        return "🟢 HOLD"
    if score > 50:
        return "🟢 HOLD"
    return "🟡 MONITOR"


# ============================================================
# BEST XI OPTIMISER (opt-in — not run automatically)
# ============================================================

def best_xi(squad):
    """Picks the highest blended-score valid formation from the 15-man
    squad. Deliberately simple (pick best N per position per formation,
    take the best formation) rather than a full ILP — fast, and good
    enough since squads are only 15 players."""
    by_position = defaultdict(list)
    for p in squad:
        by_position[p["position"]].append(p)

    for pos in by_position:
        by_position[pos].sort(key=blended_score, reverse=True)

    gks = by_position.get("GK", [])
    if not gks:
        return None, None

    best_formation = None
    best_score = -1
    best_lineup = None

    for defs, mids, fwds in VALID_FORMATIONS:
        available_def = by_position.get("DEF", [])
        available_mid = by_position.get("MID", [])
        available_fwd = by_position.get("FWD", [])

        if len(available_def) < defs or len(available_mid) < mids or len(available_fwd) < fwds:
            continue

        lineup = [gks[0]] + available_def[:defs] + available_mid[:mids] + available_fwd[:fwds]
        total = sum(blended_score(p) for p in lineup)

        if total > best_score:
            best_score = total
            best_formation = f"{defs}-{mids}-{fwds}"
            best_lineup = lineup

    bench = [p for p in squad if p not in (best_lineup or [])]
    return {"formation": best_formation, "lineup": best_lineup, "bench": bench}, best_score


# ============================================================
# WILDCARD REBUILD SUGGESTION (opt-in)
# ============================================================

def wildcard_rebuild(total_budget=SQUAD_BUDGET):
    """Suggests a fresh 15-man squad within budget and the 3-per-club
    rule, maximising blended score. Greedy construction, not a true
    optimiser — a good starting point to manually refine, not a final
    answer."""
    budget = total_budget / 10
    club_counts = defaultdict(int)
    squad = []
    quota = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}

    pool_by_position = defaultdict(list)
    for p in players:
        if p["status"] == "a" and p["chance"] >= 75:
            pool_by_position[p["position"]].append(p)
    for pos in pool_by_position:
        pool_by_position[pos].sort(key=blended_score, reverse=True)

    # Reserve minimum spend for cheap bench fodder so the whole budget
    # isn't burned on starters with no bench left to afford.
    for position, count in quota.items():
        candidates = pool_by_position[position]
        picked = 0
        for p in candidates:
            if picked >= count:
                break
            if club_counts[p["team_id"]] >= MAX_PER_CLUB:
                continue
            remaining_slots = sum(quota.values()) - len(squad) - 1
            if budget - p["price"] < remaining_slots * 4.0:
                continue  # keep enough for remaining minimum-price slots
            squad.append(p)
            club_counts[p["team_id"]] += 1
            budget -= p["price"]
            picked += 1

    return squad, round(budget, 1)


# ============================================================
# CHIP ANALYSIS
# ============================================================

def bench_boost_value(squad, entry_id):
    """Uses actual live GW points (not season totals) for bench players
    to show exactly what Bench Boost would have added this gameweek."""
    try:
        live_points = get_live_gw(current_gw)
    except Exception:
        return None

    bench = [p for p in squad if p.get("multiplier", 1) == 0]
    rows = []
    total = 0
    for p in bench:
        pts = live_points.get(p["id"], 0)
        total += pts
        rows.append({"Player": p["name"], "GW Points": pts})
    return rows, total


def chip_recommendation(squad, history):
    recommendations = []
    used_chips = []

    if history:
        for chip in history.get("chips", []):
            name = chip.get("name", "")
            if name:
                used_chips.append(name)

    squad_dgw_count = sum(1 for p in squad if p["next_gw_fixtures"] >= 2)
    squad_bgw_count = sum(1 for p in squad if p["next_gw_fixtures"] == 0)

    if squad_dgw_count >= 5:
        recommendations.append(
            "⚡ Your squad has a strong number of Double-GW players. "
            "Consider whether a Bench Boost or Triple Captain window is approaching."
        )

    if squad_bgw_count >= 4:
        recommendations.append(
            "⚠️ You have several players without a fixture next GW. "
            "This could become a useful Wildcard / Free Hit planning point."
        )

    if not recommendations:
        recommendations.append(
            "No obvious reason to use a chip immediately. "
            "Holding chips for a stronger DGW/BGW opportunity is sensible."
        )

    return recommendations, used_chips


# ============================================================
# PAGE HEADER
# ============================================================

st.title("⚽ FPL Assistant Manager")
st.caption(
    f"GW{current_gw} | Planning for GW{next_gw} | "
    "Actual FPL points + form + fixtures + expected points"
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:
    st.header("⚙️ Manager Settings")

    entry_id = st.text_input(
        "Your FPL Team ID", value="",
        help="Your Team ID is in the URL of your FPL team."
    )

    league_id = st.text_input("Mini-League ID (optional)", value="")

    free_transfers = st.number_input(
        "Free transfers available", min_value=0, max_value=5, value=1
    )

    st.divider()
    st.caption(
        "Best XI, Wildcard Rebuild and the multi-transfer chain finder "
        "are opt-in (buttons in their tabs) — they do more computation "
        "than the core tabs, so they don't run automatically."
    )


# ============================================================
# TABS
# ============================================================

tabs = st.tabs([
    "👤 My Team",
    "🔁 Transfers",
    "🩺 Hold / Sell",
    "🧢 Captain",
    "📊 Player Rankings",
    "📅 Fixtures",
    "💊 Chips",
    "🕵️ Mini-League",
    "🏆 Best XI",
    "🛠️ Wildcard",
]) 


# ============================================================
# LOAD TEAM
# ============================================================

team_data = None
my_squad = []

if entry_id:
    try:
        team_data, my_squad = load_my_team(int(entry_id))
    except Exception:
        st.error("Couldn't load your FPL team. Check the Team ID.")


# ============================================================
# TAB 1 — MY TEAM
# ============================================================

with tabs[0]:
    st.header("👤 My FPL Team")

    if not my_squad:
        st.info("Enter your FPL Team ID in the sidebar.")
    else:
        entry_history = team_data.get("entry_history", {})
        bank = entry_history.get("bank", 0) / 10
        team_value = entry_history.get("value", 0) / 10
        total_points = entry_history.get("total_points", 0)
        gw_points = entry_history.get("points", 0)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("GW Points", gw_points)
        c2.metric("Season Points", total_points)
        c3.metric("Team Value", f"£{team_value:.1f}m")
        c4.metric("Bank", f"£{bank:.1f}m")

        st.divider()

        rows = []
        for p in my_squad:
            role = "© Captain" if p.get("is_captain") else ("VC" if p.get("is_vice") else "")
            rows.append({
                "Player": p["name"], "Club": p["team"], "Pos": p["position"],
                "Role": role, "Price": f"£{p['price']:.1f}m", "FPL Points": p["points"],
                "PPG": round(p["ppg"], 1), "Form": round(p["form"], 1),
                "Minutes": p["minutes"], "Next GW": p["next_gw_fixtures"],
                "FDR": round(p["fdr"], 1), "Price Trend": price_momentum_flag(p),
                "Status": player_status(p),
            })

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("📈 Your squad by actual FPL points")
        points_df = pd.DataFrame(
            [{"Player": p["name"], "Points": p["points"]} for p in my_squad]
        ).sort_values("Points", ascending=False)
        st.bar_chart(points_df.set_index("Player"))

        st.divider()
        st.subheader("📉 Season rank history")
        try:
            history = get_team_history(int(entry_id))
            season_rows = [
                {"GW": h["event"], "Overall Rank": h["overall_rank"]}
                for h in history.get("current", [])
            ]
            if season_rows:
                rank_df = pd.DataFrame(season_rows).set_index("GW")
                st.line_chart(rank_df)
                st.caption("Lower is better — this is your overall rank, not points.")
        except Exception:
            st.caption("Rank history unavailable right now.")


# ============================================================
# TAB 2 — TRANSFERS
# ============================================================

with tabs[1]:
    st.header("🔁 Transfer Recommendations")

    if not my_squad:
        st.info("Load your FPL team first.")
    else:
        entry_history = team_data.get("entry_history", {})
        bank = entry_history.get("bank", 0) / 10

        suggestions = transfer_suggestions(my_squad, bank, free_transfers)

        st.write(f"Bank: **£{bank:.1f}m** | Free transfers: **{free_transfers}**")
        st.caption(
            f"Hit decisions are judged against projected points over the next "
            f"{HIT_PROJECTION_WEEKS} gameweeks, not just the next one."
        )

        if not suggestions:
            st.success("No strong transfer recommendation. Your squad may be worth holding.")
        else:
            for number, s in enumerate(suggestions, 1):
                outgoing, incoming = s["out"], s["in"]
                hit_text = "FREE TRANSFER" if s["hit"] == 0 else "-4 HIT"
                cost = s["cost_difference"]
                money_text = (
                    f"Costs £{cost:.1f}m more" if cost > 0
                    else (f"Frees £{abs(cost):.1f}m" if cost < 0 else "Same price")
                )

                st.markdown(f"### {number}. {outgoing['name']} ➡️ {incoming['name']}")

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("OUT Points", outgoing["points"])
                c2.metric("IN Points", incoming["points"])
                c3.metric(f"{HIT_PROJECTION_WEEKS}GW Projected Gain", f"+{s['projected_gain']:.1f}")
                c4.metric("Transfer", hit_text)

                st.write(f"**OUT:** {outgoing['team']} £{outgoing['price']:.1f}m")
                st.write(f"**IN:** {incoming['team']} £{incoming['price']:.1f}m")
                st.write(f"💰 {money_text} | Fixtures: {incoming['fixtures']}")
                st.write(f"Price trend (IN): {price_momentum_flag(incoming)}")

                if s["hit"] == 4:
                    st.warning(
                        "This -4 is only shown because the projected gain over "
                        f"{HIT_PROJECTION_WEEKS} GWs outweighs the hit — still your call."
                    )

                st.divider()

        st.subheader("🔀 Multi-transfer chains (sell two, buy one premium)")
        st.caption("Off by default — computes more combinations than the single-transfer list above.")

        if st.button("Find multi-transfer chains"):
            multi = multi_transfer_suggestions(my_squad, bank, free_transfers)
            if not multi:
                st.info("No multi-transfer chain clears its bar right now.")
            else:
                for s in multi:
                    out_names = " + ".join(p["name"] for p in s["out"])
                    st.write(f"**SELL {out_names}** ➡️ **BUY {s['in']['name']}**")
                    st.write(
                        f"Money freed: £{s['money_freed']:.1f}m | "
                        f"{HIT_PROJECTION_WEEKS}GW projected gain: +{s['projected_gain']:.1f} | "
                        f"Hit: -{s['hit']}"
                    )
                    st.divider()

        st.subheader("💰 Sell a premium player to free money")
        funding = sell_to_fund_recommendations(my_squad, bank)
        if not funding:
            st.info("No obvious downgrade-to-fund opportunity.")
        else:
            for s in funding:
                st.write(
                    f"**SELL {s['out']['name']}** £{s['out']['price']:.1f}m → "
                    f"**BUY {s['in']['name']}** £{s['in']['price']:.1f}m"
                )
                st.write(f"💰 Frees **£{s['saving']:.1f}m** | Score change: {s['gain']:+.1f}")


# ============================================================
# TAB 3 — HOLD / SELL
# ============================================================

with tabs[2]:
    st.header("🩺 Hold / Sell Diagnostics")

    if not my_squad:
        st.info("Load your FPL team first.")
    else:
        rows = []
        for p in my_squad:
            rows.append({
                "Player": p["name"], "Club": p["team"], "Pos": p["position"],
                "FPL Points": p["points"], "PPG": round(p["ppg"], 1),
                "Form": round(p["form"], 1), "Minutes": p["minutes"],
                "Next Fixture": p["next_gw_fixtures"], "FDR": round(p["fdr"], 1),
                "Price Trend": price_momentum_flag(p), "Decision": hold_sell(p),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(
            "Actual FPL points are the foundation of the assessment; form, minutes, "
            "availability, price trend and fixtures are used to judge the trend."
        )


# ============================================================
# TAB 4 — CAPTAIN
# ============================================================

with tabs[3]:
    st.header("🧢 Captain & Vice-Captain")

    if not my_squad:
        st.info("Load your FPL team first.")
    else:
        captain_list = captain_recommendations(my_squad)

        if captain_list:
            captain = captain_list[0]
            vice = captain_list[1] if len(captain_list) > 1 else None

            c1, c2 = st.columns(2)
            with c1:
                st.success(f"👑 CAPTAIN\n\n### {captain['name']}\n{captain['team']} | {captain['points']} FPL points")
                st.write(f"Form: **{captain['form']:.1f}**")
                st.write(f"PPG: **{captain['ppg']:.1f}**")
                st.write(f"Next GW fixtures: **{captain['next_gw_fixtures']}**")
                st.write(captain["fixtures"])

            with c2:
                if vice:
                    st.info(f"🥈 VICE-CAPTAIN\n\n### {vice['name']}\n{vice['team']} | {vice['points']} FPL points")
                    st.write(f"Form: **{vice['form']:.1f}**")
                    st.write(f"PPG: **{vice['ppg']:.1f}**")

            st.divider()
            st.subheader("💎 Triple Captain check")
            tc = triple_captain_projection(my_squad)
            if tc:
                st.write(
                    f"**{tc['player']['name']}** — projected **{tc['projected_single_gw']}** pts "
                    f"this GW vs a typical **{tc['typical_ppg']}** PPG (uplift: {tc['uplift']:+.1f})"
                )
                if tc["uplift"] >= 3:
                    st.success("This looks like a genuine spike — a plausible Triple Captain window.")
                else:
                    st.caption("No unusual spike detected — probably not worth a Triple Captain here.")


# ============================================================
# TAB 5 — PLAYER RANKINGS
# ============================================================

with tabs[4]:
    st.header("📊 Player Rankings")

    ranking_type = st.radio(
        "Rank players by",
        ["Actual FPL Points", "Blended Score", "Form", "PPG", "Value"],
        horizontal=True
    )
    position_filter = st.selectbox("Position", ["ALL", "GK", "DEF", "MID", "FWD"])
    search = st.text_input("Search player or club")

    filtered = players.copy()
    if position_filter != "ALL":
        filtered = [p for p in filtered if p["position"] == position_filter]
    if search:
        s = search.lower()
        filtered = [
            p for p in filtered
            if s in p["name"].lower() or s in p["full_name"].lower() or s in p["team"].lower()
        ]

    if ranking_type == "Actual FPL Points":
        filtered.sort(key=lambda p: p["points"], reverse=True)
    elif ranking_type == "Blended Score":
        filtered.sort(key=blended_score, reverse=True)
    elif ranking_type == "Form":
        filtered.sort(key=lambda p: p["form"], reverse=True)
    elif ranking_type == "PPG":
        filtered.sort(key=lambda p: p["ppg"], reverse=True)
    else:
        filtered.sort(key=lambda p: (p["points"] / p["price"] if p["price"] > 0 else 0), reverse=True)

    rows = []
    for p in filtered[:100]:
        rows.append({
            "Player": p["name"], "Club": p["team"], "Pos": p["position"],
            "Price": f"£{p['price']:.1f}m", "FPL Points": p["points"],
            "PPG": round(p["ppg"], 1), "Form": round(p["form"], 1),
            "Minutes": p["minutes"], "Goals": p["goals"], "Assists": p["assists"],
            "Bonus": p["bonus"], "EP Next": round(p["ep_next"], 1), "FDR": round(p["fdr"], 1),
            "Ownership": f"{p['ownership']:.1f}%", "Price Trend": price_momentum_flag(p),
            "Blended": round(blended_score(p), 1),
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ============================================================
# TAB 6 — FIXTURES
# ============================================================

with tabs[5]:
    st.header("📅 Fixture Difficulty")
    st.write(f"Looking ahead from GW{next_gw} over the next {FIXTURE_HORIZON} gameweeks.")

    fixture_rows = []
    for team_id, team in teams.items():
        fixture_rows.append({
            "Team": team.get("short_name", "?"),
            "Average FDR": round(average_fdr(team_id), 2),
            "Fixtures": fixture_text(team_id, FIXTURE_HORIZON),
        })

    fixture_df = pd.DataFrame(fixture_rows).sort_values("Average FDR")
    st.dataframe(fixture_df, use_container_width=True, hide_index=True)

    st.divider()

    dgw_teams, bgw_teams = [], []
    for team_id in teams:
        count = fixture_count(team_id, next_gw)
        if count >= 2:
            dgw_teams.append(team_names[team_id])
        elif count == 0:
            bgw_teams.append(team_names[team_id])

    if dgw_teams:
        st.success(f"⚡ GW{next_gw} Double Gameweek: " + ", ".join(dgw_teams))
    if bgw_teams:
        st.warning(f"⚠️ GW{next_gw} Blank Gameweek: " + ", ".join(bgw_teams))


# ============================================================
# TAB 7 — CHIPS
# ============================================================

with tabs[6]:
    st.header("💊 Chip Strategy")

    if not my_squad:
        st.info("Load your FPL team to analyse your chip position.")
    else:
        try:
            history = get_team_history(int(entry_id))
        except Exception:
            history = None

        recommendations, used_chips = chip_recommendation(my_squad, history)

        st.subheader("Current recommendation")
        for r in recommendations:
            st.info(r)

        st.divider()
        st.subheader("Your next-gameweek squad situation")

        dgw_count = sum(1 for p in my_squad if p["next_gw_fixtures"] >= 2)
        bgw_count = sum(1 for p in my_squad if p["next_gw_fixtures"] == 0)

        c1, c2 = st.columns(2)
        c1.metric("Players with 2+ fixtures", dgw_count)
        c2.metric("Players with no fixture", bgw_count)

        if used_chips:
            st.write("**Chips already recorded:** " + ", ".join(used_chips))
        else:
            st.write("No previously used chips were detected from the available history.")

        st.divider()
        st.subheader("🪑 Bench Boost calculator")
        st.caption(f"Uses actual GW{current_gw} points, not season totals.")

        bb = bench_boost_value(my_squad, entry_id)
        if bb:
            rows, total = bb
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.metric(f"Bench Boost would have added (GW{current_gw})", f"+{total} pts")
            else:
                st.caption("No bench data available for this gameweek yet.")
        else:
            st.caption("Live GW data unavailable right now.")


# ============================================================
# TAB 8 — MINI LEAGUE
# ============================================================

with tabs[7]:
    st.header("🕵️ Mini-League Rival Analysis")

    if not league_id:
        st.info("Enter your Mini-League ID in the sidebar.")
    elif not my_squad:
        st.info("Load your FPL team first.")
    else:
        try:
            league = get_league(int(league_id))
            standings = league.get("standings", {}).get("results", [])

            if not standings:
                st.warning("No league standings found.")
            else:
                my_entry = next((x for x in standings if x["entry"] == int(entry_id)), None)
                leader = standings[0]

                if my_entry:
                    gap = leader["total"] - my_entry["total"]

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Your Rank", f"#{my_entry['rank']}")
                    c2.metric("Leader", leader["player_name"])
                    c3.metric("Points Behind", gap)

                    st.divider()

                    # Pull picks for every rival in the league (top 10 to
                    # keep this fast) to build a full differential + EO view.
                    rivals = standings[:10]
                    rival_picks = {}
                    for rival in rivals:
                        try:
                            data = get_entry_picks(rival["entry"], current_gw)
                            rival_picks[rival["entry"]] = data.get("picks", [])
                        except Exception:
                            continue

                    my_ids = {p["id"] for p in my_squad}

                    st.subheader("Effective ownership within this league")
                    st.caption(
                        f"Captaincy-weighted ownership across the top {len(rival_picks)} "
                        "teams in your league — a player owned AND captained widely is a "
                        "much bigger risk/reward than raw ownership shows."
                    )

                    eo_counts = defaultdict(float)
                    for entry, picks in rival_picks.items():
                        for pick in picks:
                            weight = pick.get("multiplier", 1)
                            eo_counts[pick["element"]] += weight

                    eo_rows = []
                    for pid, weight in sorted(eo_counts.items(), key=lambda x: x[1], reverse=True)[:15]:
                        p = player_by_id.get(pid)
                        if p:
                            eo_rows.append({
                                "Player": p["name"],
                                "Effective Ownership (weighted)": round(weight / max(len(rival_picks), 1) * 100, 1),
                                "In Your Squad": "✅" if pid in my_ids else "—",
                            })
                    st.dataframe(pd.DataFrame(eo_rows), use_container_width=True, hide_index=True)

                    st.divider()
                    st.subheader("Full rival differential matrix")

                    matrix_rows = []
                    for rival in rivals:
                        rival_ids = {pick["id"] for pick in [
                            {"id": pick["element"]} for pick in rival_picks.get(rival["entry"], [])
                        ]}
                        overlap = len(my_ids & rival_ids)
                        matrix_rows.append({
                            "Rival": rival["player_name"],
                            "Rank": rival["rank"],
                            "Total Points": rival["total"],
                            "Squad Overlap": f"{overlap}/15",
                            "Differentials (theirs)": len(rival_ids - my_ids),
                        })
                    st.dataframe(pd.DataFrame(matrix_rows), use_container_width=True, hide_index=True)

                    st.divider()
                    st.subheader(f"Head-to-head vs {leader['player_name']} (leader)")

                    leader_ids = {pick["element"] for pick in rival_picks.get(leader["entry"], [])}
                    shared = my_ids & leader_ids
                    leader_only = leader_ids - my_ids
                    my_only = my_ids - leader_ids
                    overlap_pct = len(shared) / 15 * 100

                    st.write(f"Squad overlap: **{overlap_pct:.0f}%**")

                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("**Your differentials**")
                        for pid in my_only:
                            p = player_by_id.get(pid)
                            if p:
                                st.write(f"• {p['name']} — {p['points']} pts")
                    with col2:
                        st.markdown("**Leader's differentials**")
                        for pid in leader_only:
                            p = player_by_id.get(pid)
                            if p:
                                st.write(f"• {p['name']} — {p['points']} pts")
                else:
                    st.warning("Your Team ID wasn't found in this league.")

        except Exception:
            st.error("Couldn't load the mini-league.")


# ============================================================
# TAB 9 — BEST XI (opt-in)
# ============================================================

with tabs[8]:
    st.header("🏆 Best XI From Your Squad")
    st.caption(
        "Finds the highest-scoring valid formation from your 15 players. "
        "This does not change your actual FPL lineup — it's a suggestion."
    )

    if not my_squad:
        st.info("Load your FPL team first.")
    elif st.button("Calculate Best XI"):
        result, score = best_xi(my_squad)
        if not result:
            st.warning("Couldn't build a valid XI from this squad.")
        else:
            st.success(f"Best formation: **{result['formation']}** (total score: {score:.1f})")

            lineup_rows = [{
                "Player": p["name"], "Pos": p["position"], "Club": p["team"],
                "FPL Points": p["points"], "Blended Score": round(blended_score(p), 1),
            } for p in result["lineup"]]
            st.dataframe(pd.DataFrame(lineup_rows), use_container_width=True, hide_index=True)

            st.subheader("Bench (in order)")
            bench_rows = [{
                "Player": p["name"], "Pos": p["position"],
                "Blended Score": round(blended_score(p), 1),
            } for p in sorted(result["bench"], key=blended_score, reverse=True)]
            st.dataframe(pd.DataFrame(bench_rows), use_container_width=True, hide_index=True)


# ============================================================
# TAB 10 — WILDCARD REBUILD (opt-in)
# ============================================================

with tabs[9]:
    st.header("🛠️ Wildcard Rebuild Suggestion")
    st.caption(
        "Builds a fresh 15-man squad from scratch within £100m and the "
        "3-per-club rule, maximising blended score. Greedy, not exhaustive — "
        "treat this as a strong starting point to refine manually, not a final answer."
    )

    if st.button("Suggest a Wildcard squad"):
        squad, leftover = wildcard_rebuild()

        if len(squad) < 15:
            st.warning(
                f"Only found {len(squad)}/15 players within budget constraints — "
                "try refining manually from this partial squad."
            )

        total_cost = SQUAD_BUDGET / 10 - leftover
        st.metric("Squad cost", f"£{total_cost:.1f}m")
        st.metric("Money left in bank", f"£{leftover:.1f}m")

        rows = [{
            "Player": p["name"], "Pos": p["position"], "Club": p["team"],
            "Price": f"£{p['price']:.1f}m", "FPL Points": p["points"],
            "Form": round(p["form"], 1), "Blended Score": round(blended_score(p), 1),
        } for p in sorted(squad, key=lambda p: (p["position"], -blended_score(p)))]

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ============================================================
# FOOTER
# ============================================================

st.divider()
st.caption("⚽ FPL Assistant Manager — ultimate version")
st.caption(
    "Actual FPL points are the primary performance measure. Form, PPG, minutes, "
    "availability, official expected points, fixtures, price momentum and "
    "multi-gameweek projections are secondary factors."
)
st.caption(
    "Best XI, Wildcard Rebuild and multi-transfer chains are simplified "
    "heuristics, not brute-force optimisers — they're built for speed, not "
    "mathematical guarantees of the single best possible answer."
)
