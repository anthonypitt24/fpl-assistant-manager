
import streamlit as st
import requests
import pandas as pd
from itertools import combinations
from collections import defaultdict

# ============================================================
# FPL ASSISTANT MANAGER v2
# Blends actual points + expected points + fixture difficulty
# Supports building a fresh £100m squad OR loading your own team
# ============================================================

st.set_page_config(page_title="FPL Assistant Manager", page_icon="⚽", layout="wide")

API = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0"}
BUDGET = 100.0
FDR_HORIZON = 5  # gameweeks to look ahead for fixture difficulty


# ============================================================
# DATA LOADERS
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def get_bootstrap():
    r = requests.get(f"{API}/bootstrap-static/", headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=900, show_spinner=False)
def get_fixtures():
    r = requests.get(f"{API}/fixtures/", headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_picks(entry_id, event_id):
    r = requests.get(f"{API}/entry/{entry_id}/event/{event_id}/picks/", headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=900, show_spinner=False)
def get_entry_info(entry_id):
    r = requests.get(f"{API}/entry/{entry_id}/", headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


try:
    data = get_bootstrap()
    fixtures = get_fixtures()
except Exception as e:
    st.error("Unable to load FPL data.")
    st.code(str(e))
    st.stop()


# ============================================================
# BASIC DATA
# ============================================================

teams = {t["id"]: t for t in data["teams"]}
position_names = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
events = data["events"]

current_event = next((e for e in events if e.get("is_current")), None)
next_event = next((e for e in events if e.get("is_next")), None)
current_gw = current_event["id"] if current_event else 1
upcoming_gw = next_event["id"] if next_event else current_gw + 1


# ============================================================
# FIXTURE DIFFICULTY / DGW / BGW ENGINE
# ============================================================

def build_team_fixture_map(fixtures, from_gw, horizon):
    """For each team, list its fixtures (with difficulty) in the next `horizon` gameweeks."""
    fmap = defaultdict(list)
    for fx in fixtures:
        gw = fx.get("event")
        if gw is None or gw < from_gw or gw >= from_gw + horizon:
            continue
        fmap[fx["team_h"]].append({"gw": gw, "difficulty": fx["team_h_difficulty"], "home": True, "opp": fx["team_a"]})
        fmap[fx["team_a"]].append({"gw": gw, "difficulty": fx["team_a_difficulty"], "home": False, "opp": fx["team_h"]})
    return fmap


team_fixture_map = build_team_fixture_map(fixtures, upcoming_gw, FDR_HORIZON)


def team_fdr_score(team_id):
    """Lower is easier. Average difficulty over the horizon; 3.0 (neutral) if no fixtures found."""
    fx = team_fixture_map.get(team_id, [])
    if not fx:
        return 3.0
    return sum(f["difficulty"] for f in fx) / len(fx)


def next_gw_fixture_count(team_id, gw):
    return sum(1 for f in team_fixture_map.get(team_id, []) if f["gw"] == gw)


def team_fixture_string(team_id, n=3):
    fx = sorted(team_fixture_map.get(team_id, []), key=lambda f: f["gw"])[:n]
    parts = []
    for f in fx:
        opp = teams.get(f["opp"], {}).get("short_name", "?")
        loc = "H" if f["home"] else "A"
        parts.append(f"{opp}({loc}) [{f['difficulty']}]")
    return ", ".join(parts) if parts else "No fixtures"


# ============================================================
# BUILD PLAYER DATA
# ============================================================

def safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


players = []
for p in data["elements"]:
    position = position_names[p["element_type"]]
    team = teams.get(p["team"], {})
    price = p["now_cost"] / 10
    total_points = p.get("total_points", 0)
    ppg = safe_float(p.get("points_per_game"))
    form = safe_float(p.get("form"))
    minutes = p.get("minutes", 0)
    chance = p.get("chance_of_playing_next_round")
    if chance is None:
        chance = 100
    ep_next = safe_float(p.get("ep_next"))
    xgi90 = safe_float(p.get("expected_goal_involvements_per_90"))
    xgc90 = safe_float(p.get("expected_goals_conceded_per_90"))
    ict = safe_float(p.get("ict_index"))
    fdr = team_fdr_score(p["team"])
    fixtures_next_gw = next_gw_fixture_count(p["team"], upcoming_gw)

    players.append({
        "id": p["id"],
        "name": p["web_name"],
        "full_name": f"{p['first_name']} {p['second_name']}",
        "position": position,
        "team_id": p["team"],
        "team": team.get("short_name", "?"),
        "price": price,
        "points": total_points,
        "ppg": ppg,
        "form": form,
        "minutes": minutes,
        "chance": chance,
        "ownership": safe_float(p.get("selected_by_percent")),
        "status": p.get("status", "a"),
        "news": p.get("news", ""),
        "ep_next": ep_next,
        "xgi90": xgi90,
        "xgc90": xgc90,
        "ict": ict,
        "fdr": fdr,
        "fixtures_next_gw": fixtures_next_gw,
        "fixture_str": team_fixture_string(p["team"]),
        "transfers_in_event": p.get("transfers_in_event", 0),
        "transfers_out_event": p.get("transfers_out_event", 0),
        "cost_change_event": p.get("cost_change_event", 0),
    })


# ============================================================
# PLAYER RANKING (blended: actual points + form + xPts + fixtures)
# ============================================================

def player_rank(p, fixture_weight=1.0):
    """
    Blended score:
      - Actual points earned (primary, proven output)
      - PPG and recent form (recency-weighted)
      - Official next-gameweek expected points (ep_next)
      - Fixture difficulty adjustment (easier run = bonus, harder = penalty)
      - Availability
      - Blank-gameweek penalty: 0 fixtures next GW tanks the score
    """
    points = p["points"]
    ppg_bonus = min(p["ppg"] * 2, 15)
    form_bonus = min(p["form"], 10)
    availability_bonus = (p["chance"] / 100) * 5
    ep_bonus = p["ep_next"] * 3
    # FDR is 1 (easy) to 5 (hard); convert to a bonus centered on 0
    fdr_bonus = (3.0 - p["fdr"]) * 2 * fixture_weight
    blank_penalty = -50 if p["fixtures_next_gw"] == 0 else 0
    dgw_bonus = 8 if p["fixtures_next_gw"] >= 2 else 0

    return (
        points
        + ppg_bonus
        + form_bonus
        + availability_bonus
        + ep_bonus
        + fdr_bonus
        + blank_penalty
        + dgw_bonus
    )


def usable(p):
    if p["status"] not in ["a", "d"]:
        return False
    if p["chance"] <= 0:
        return False
    if p["price"] <= 0:
        return False
    return True


usable_players = [p for p in players if usable(p)]


# ============================================================
# TEAM BUILDER (greedy + repair, budget & club-limit aware)
# ============================================================

def build_best_team(fixture_weight=1.0):
    def rank(p):
        return player_rank(p, fixture_weight)

    by_position = {}
    limits = {"GK": 10, "DEF": 25, "MID": 30, "FWD": 20}
    for pos in ["GK", "DEF", "MID", "FWD"]:
        pool = [p for p in usable_players if p["position"] == pos]
        pool.sort(key=rank, reverse=True)
        by_position[pos] = pool[:limits[pos]]

    squad = []
    required = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
    for pos, amount in required.items():
        squad.extend(by_position[pos][:amount])

    def club_counts(team):
        counts = {}
        for p in team:
            counts[p["team_id"]] = counts.get(p["team_id"], 0) + 1
        return counts

    while True:
        counts = club_counts(squad)
        overloaded = [club for club, count in counts.items() if count > 3]
        if not overloaded:
            break
        club = overloaded[0]
        candidates = [p for p in squad if p["team_id"] == club]
        remove = min(candidates, key=rank)
        squad.remove(remove)
        replacement = None
        for p in by_position[remove["position"]]:
            if p["id"] in [x["id"] for x in squad]:
                continue
            test = squad + [p]
            test_counts = club_counts(test)
            if all(v <= 3 for v in test_counts.values()):
                replacement = p
                break
        if replacement:
            squad.append(replacement)

    def total_cost(team):
        return sum(p["price"] for p in team)

    attempts = 0
    while total_cost(squad) > BUDGET and attempts < 100:
        attempts += 1
        best_move = None
        current_ids = {p["id"] for p in squad}
        for outgoing in squad:
            pool = by_position[outgoing["position"]]
            for incoming in pool:
                if incoming["id"] in current_ids:
                    continue
                if incoming["price"] >= outgoing["price"]:
                    continue
                new_team = [p for p in squad if p["id"] != outgoing["id"]] + [incoming]
                new_counts = club_counts(new_team)
                if any(v > 3 for v in new_counts.values()):
                    continue
                if total_cost(new_team) > BUDGET:
                    continue
                points_loss = rank(outgoing) - rank(incoming)
                money_saved = outgoing["price"] - incoming["price"]
                efficiency = money_saved * 10 - points_loss
                if best_move is None or efficiency > best_move["efficiency"]:
                    best_move = {"out": outgoing, "in": incoming, "efficiency": efficiency}
        if best_move is None:
            break
        squad.remove(best_move["out"])
        squad.append(best_move["in"])

    if len(squad) != 15:
        return []
    if total_cost(squad) > BUDGET:
        return []
    counts = club_counts(squad)
    if any(v > 3 for v in counts.values()):
        return []
    for pos, amount in required.items():
        if sum(p["position"] == pos for p in squad) != amount:
            return []
    return squad


# ============================================================
# BEST STARTING XI
# ============================================================

def best_starting_xi(squad, use_blended=False, fixture_weight=1.0):
    gks = [p for p in squad if p["position"] == "GK"]
    defs = [p for p in squad if p["position"] == "DEF"]
    mids = [p for p in squad if p["position"] == "MID"]
    fwds = [p for p in squad if p["position"] == "FWD"]

    def score_of(p):
        return player_rank(p, fixture_weight) if use_blended else p["points"]

    best = None
    for defenders in range(3, 6):
        for midfielders in range(2, 6):
            forwards = 10 - defenders - midfielders
            if forwards < 1 or forwards > 3:
                continue
            if defenders > len(defs) or midfielders > len(mids) or forwards > len(fwds):
                continue
            for gk in gks:
                for d in combinations(defs, defenders):
                    for m in combinations(mids, midfielders):
                        for f in combinations(fwds, forwards):
                            xi = [gk] + list(d) + list(m) + list(f)
                            score = sum(score_of(p) for p in xi)
                            secondary = sum(p["ppg"] + p["form"] * 0.2 for p in xi)
                            if (best is None or score > best["score"] or
                                    (score == best["score"] and secondary > best["secondary"])):
                                best = {
                                    "players": xi,
                                    "score": score,
                                    "points": sum(p["points"] for p in xi),
                                    "secondary": secondary,
                                    "formation": f"{defenders}-{midfielders}-{forwards}",
                                }
    return best


def captain_choices(xi, use_blended=False, fixture_weight=1.0):
    def key(p):
        base = player_rank(p, fixture_weight) if use_blended else p["points"]
        return (base, p["ppg"], p["form"])
    ranked = sorted(xi, key=key, reverse=True)
    if len(ranked) < 2:
        return None, None
    return ranked[0], ranked[1]


def bench_order(squad, starting_xi):
    starters = {p["id"] for p in starting_xi}
    bench = [p for p in squad if p["id"] not in starters]
    bench.sort(key=lambda p: (p["points"], p["ppg"], p["form"]), reverse=True)
    return bench


# ============================================================
# TRANSFER SUGGESTIONS (for "load my team" mode)
# ============================================================

def suggest_transfers(my_squad, bank, free_transfers, fixture_weight=1.0, max_suggestions=5):
    """Suggest single swaps that improve blended rank per position within budget."""
    my_ids = {p["id"] for p in my_squad}
    suggestions = []
    for pos in ["GK", "DEF", "MID", "FWD"]:
        owned = [p for p in my_squad if p["position"] == pos]
        candidates = sorted(
            [p for p in usable_players if p["position"] == pos and p["id"] not in my_ids],
            key=lambda p: player_rank(p, fixture_weight),
            reverse=True,
        )[:15]
        for out_p in owned:
            for in_p in candidates:
                price_diff = in_p["price"] - out_p["price"]
                if price_diff > bank:
                    continue
                gain = player_rank(in_p, fixture_weight) - player_rank(out_p, fixture_weight)
                if gain > 2:
                    suggestions.append({
                        "out": out_p, "in": in_p, "gain": gain, "cost_diff": price_diff
                    })
    suggestions.sort(key=lambda s: s["gain"], reverse=True)
    return suggestions[:max_suggestions]


with st.spinner("Building recommended £100m team..."):
    fresh_team = build_best_team()


# ============================================================
# HEADER + MODE SELECTOR
# ============================================================

st.title("⚽ FPL Assistant Manager")

if current_event:
    st.caption(
        f"Gameweek {current_gw} (current) | Next: GW{upcoming_gw} | "
        f"Blends actual points, expected points, form and fixture difficulty"
    )
else:
    st.caption("Blends actual points, expected points, form and fixture difficulty")

with st.sidebar:
    st.header("Settings")
    mode = st.radio("Mode", ["Build a fresh £100m team", "Load my FPL team"])
    fixture_weight = st.slider(
        "Fixture difficulty influence", 0.0, 3.0, 1.0, 0.5,
        help="Higher = fixtures matter more relative to past points"
    )
    entry_id = None
    bank = 0.0
    free_transfers = 1
    if mode == "Load my FPL team":
        entry_id = st.text_input("Your FPL Team ID (find it in the URL on the official FPL site)")
        free_transfers = st.number_input("Free transfers available", 0, 5, 1)


# ============================================================
# TABS
# ============================================================

if mode == "Build a fresh £100m team":
    tab_labels = ["🏆 Best £100m Team", "⚽ Starting XI", "📊 Player Rankings", "📅 Fixtures & FDR"]
else:
    tab_labels = ["👤 My Team", "🔁 Transfer Suggestions", "📊 Player Rankings", "📅 Fixtures & FDR"]

tabs = st.tabs(tab_labels)


# ============================================================
# MODE 1: FRESH TEAM BUILD
# ============================================================

if mode == "Build a fresh £100m team":

    with tabs[0]:
        st.header("🏆 Best £100m Team")
        st.write(
            "Built from a blend of **actual FPL points**, **official expected points (ep_next)**, "
            "**form**, and **fixture difficulty** over the next "
            f"{FDR_HORIZON} gameweeks."
        )

        if not fresh_team:
            st.error("I couldn't construct a legal £100m squad.")
        else:
            cost = sum(p["price"] for p in fresh_team)
            points = sum(p["points"] for p in fresh_team)
            remaining = BUDGET - cost

            c1, c2, c3 = st.columns(3)
            c1.metric("Squad Cost", f"£{cost:.1f}m")
            c2.metric("Money Remaining", f"£{remaining:.1f}m")
            c3.metric("Actual Points (season)", f"{points:.0f}")

            st.success("Recommended 15-player squad")

            display = []
            for p in fresh_team:
                flag = ""
                if p["fixtures_next_gw"] == 0:
                    flag = "🚫 BGW"
                elif p["fixtures_next_gw"] >= 2:
                    flag = "⚡ DGW"
                display.append({
                    "Player": p["name"], "Club": p["team"], "Pos": p["position"],
                    "Price": f"£{p['price']:.1f}m", "FPL Points": p["points"],
                    "PPG": round(p["ppg"], 1), "Form": round(p["form"], 1),
                    "EP Next": round(p["ep_next"], 1),
                    "Fixtures (next 3)": p["fixture_str"],
                    "Flag": flag,
                    "Owned": f"{p['ownership']:.1f}%",
                })

            position_order = {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}
            display.sort(key=lambda x: (position_order[x["Pos"]], -x["FPL Points"]))
            st.dataframe(pd.DataFrame(display), use_container_width=True, hide_index=True)
            st.caption("Squad obeys 2 GK / 5 DEF / 5 MID / 3 FWD and max-3-per-club rules.")

    with tabs[1]:
        st.header("⚽ Recommended Starting XI")
        if not fresh_team:
            st.error("No squad available.")
        else:
            use_blended = st.checkbox("Rank XI/captain by blended score (incl. fixtures) instead of pure points", value=True)
            xi = best_starting_xi(fresh_team, use_blended=use_blended, fixture_weight=fixture_weight)
            if not xi:
                st.error("Unable to calculate starting XI.")
            else:
                captain, vice = captain_choices(xi["players"], use_blended=use_blended, fixture_weight=fixture_weight)
                bench = bench_order(fresh_team, xi["players"])

                c1, c2, c3 = st.columns(3)
                c1.metric("Formation", xi["formation"])
                c2.metric("Starting XI Points (season)", f"{xi['points']}")
                c3.metric("Captain", captain["name"] if captain else "-")

                bgw_starters = [p for p in xi["players"] if p["fixtures_next_gw"] == 0]
                if bgw_starters:
                    st.warning(
                        "⚠️ Blank gameweek risk: " +
                        ", ".join(f"{p['name']} ({p['team']})" for p in bgw_starters) +
                        " have no fixture next gameweek."
                    )

                xi_display = []
                for p in xi["players"]:
                    xi_display.append({
                        "Player": p["name"], "Club": p["team"], "Pos": p["position"],
                        "FPL Points": p["points"], "PPG": round(p["ppg"], 1),
                        "Form": round(p["form"], 1), "EP Next": round(p["ep_next"], 1),
                        "Next Fixture": p["fixture_str"].split(",")[0],
                    })
                st.dataframe(pd.DataFrame(xi_display), use_container_width=True, hide_index=True)

                if captain:
                    st.success(f"🧢 CAPTAIN: **{captain['name']}** — {captain['points']} FPL points, next fixture {captain['fixture_str'].split(',')[0]}")
                if vice:
                    st.info(f"🥈 VICE-CAPTAIN: **{vice['name']}** — {vice['points']} FPL points")

                st.subheader("🪑 Bench Order")
                for number, p in enumerate(bench, 1):
                    st.write(f"**{number}. {p['name']}** ({p['position']}) — {p['points']} points")


# ============================================================
# MODE 2: LOAD MY TEAM
# ============================================================

else:
    player_by_id = {p["id"]: p for p in players}

    with tabs[0]:
        st.header("👤 My Team")
        if not entry_id:
            st.info("Enter your FPL Team ID in the sidebar to load your squad.")
        else:
            try:
                info = get_entry_info(entry_id)
                picks_data = get_entry_picks(entry_id, current_gw)
            except Exception as e:
                st.error("Couldn't load that team. Double-check the ID.")
                st.code(str(e))
                picks_data = None

            if picks_data:
                bank = picks_data["entry_history"]["bank"] / 10
                team_value = picks_data["entry_history"]["value"] / 10
                overall_points = picks_data["entry_history"]["total_points"]
                gw_points = picks_data["entry_history"]["points"]

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Team Name", info.get("name", "-"))
                c2.metric("Overall Points", overall_points)
                c3.metric("Team Value", f"£{team_value:.1f}m")
                c4.metric("Bank", f"£{bank:.1f}m")

                my_squad = []
                pick_info = {pk["element"]: pk for pk in picks_data["picks"]}
                for pk in picks_data["picks"]:
                    p = player_by_id.get(pk["element"])
                    if p:
                        my_squad.append(p)

                display = []
                for p in my_squad:
                    pk = pick_info[p["id"]]
                    role = "C" if pk["is_captain"] else ("VC" if pk["is_vice_captain"] else "")
                    flag = "🚫 BGW" if p["fixtures_next_gw"] == 0 else ("⚡ DGW" if p["fixtures_next_gw"] >= 2 else "")
                    display.append({
                        "Player": p["name"], "Club": p["team"], "Pos": p["position"],
                        "Role": role, "Price": f"£{p['price']:.1f}m",
                        "FPL Points": p["points"], "Form": round(p["form"], 1),
                        "EP Next": round(p["ep_next"], 1),
                        "Next Fixture": p["fixture_str"].split(",")[0], "Flag": flag,
                    })
                position_order = {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}
                display.sort(key=lambda x: position_order[x["Pos"]])
                st.dataframe(pd.DataFrame(display), use_container_width=True, hide_index=True)

                st.session_state["my_squad"] = my_squad
                st.session_state["bank"] = bank

    with tabs[1]:
        st.header("🔁 Transfer Suggestions")
        my_squad = st.session_state.get("my_squad")
        bank = st.session_state.get("bank", 0.0)
        if not my_squad:
            st.info("Load your team in the 'My Team' tab first.")
        else:
            suggestions = suggest_transfers(my_squad, bank, free_transfers, fixture_weight)
            if not suggestions:
                st.success("No clear upgrades found — your squad looks well optimised.")
            else:
                st.write(f"Top suggested swaps (bank: £{bank:.1f}m, free transfers: {free_transfers}):")
                for i, s in enumerate(suggestions, 1):
                    cost_note = f"+£{s['cost_diff']:.1f}m" if s["cost_diff"] > 0 else f"£{s['cost_diff']:.1f}m"
                    st.write(
                        f"**{i}. OUT: {s['out']['name']} ({s['out']['team']})** → "
                        f"**IN: {s['in']['name']} ({s['in']['team']})** "
                        f"| cost change: {cost_note} | fixtures: {s['in']['fixture_str']}"
                    )
                st.caption("Each -4 point hit beyond your free transfers should be weighed against the projected gain above.")


# ============================================================
# PLAYER RANKINGS (shared tab)
# ============================================================

with tabs[2]:
    st.header("📊 Player Rankings")
    st.write("Sort by actual points, or switch to blended score to include form, expected points and fixtures.")

    position = st.selectbox("Position", ["ALL", "GK", "DEF", "MID", "FWD"])
    search = st.text_input("Search player or club")
    sort_mode = st.radio("Sort by", ["Actual FPL Points", "Blended Score (incl. fixtures)"], horizontal=True)

    filtered = usable_players.copy()
    if position != "ALL":
        filtered = [p for p in filtered if p["position"] == position]
    if search:
        s = search.lower()
        filtered = [p for p in filtered if s in p["name"].lower() or s in p["full_name"].lower() or s in p["team"].lower()]

    if sort_mode == "Actual FPL Points":
        filtered.sort(key=lambda p: (p["points"], p["ppg"], p["form"]), reverse=True)
    else:
        filtered.sort(key=lambda p: player_rank(p, fixture_weight), reverse=True)

    rows = []
    for p in filtered[:100]:
        rows.append({
            "Player": p["name"], "Club": p["team"], "Pos": p["position"],
            "Price": f"£{p['price']:.1f}m", "FPL Points": p["points"],
            "PPG": round(p["ppg"], 1), "Form": round(p["form"], 1),
            "EP Next": round(p["ep_next"], 1),
            "Blended Score": round(player_rank(p, fixture_weight), 1),
            "Minutes": p["minutes"], "Ownership": f"{p['ownership']:.1f}%",
            "Availability": f"{p['chance']:.0f}%",
            "Fixtures (next 3)": p["fixture_str"],
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ============================================================
# FIXTURES & FDR (shared tab)
# ============================================================

with tabs[3]:
    st.header("📅 Fixture Difficulty by Team")
    st.write(f"Average difficulty (1=easiest, 5=hardest) over the next {FDR_HORIZON} gameweeks, from GW{upcoming_gw}.")

    fdr_rows = []
    for tid, t in teams.items():
        fdr_rows.append({
            "Team": t["short_name"],
            "Avg FDR": round(team_fdr_score(tid), 2),
            "Fixtures": team_fixture_string(tid, n=FDR_HORIZON),
        })
    fdr_df = pd.DataFrame(fdr_rows).sort_values("Avg FDR")
    st.dataframe(fdr_df, use_container_width=True, hide_index=True)

    bgw_teams = [t["short_name"] for tid, t in teams.items() if next_gw_fixture_count(tid, upcoming_gw) == 0]
    dgw_teams = [t["short_name"] for tid, t in teams.items() if next_gw_fixture_count(tid, upcoming_gw) >= 2]
    if bgw_teams:
        st.warning(f"Blank GW{upcoming_gw}: " + ", ".join(bgw_teams))
    if dgw_teams:
        st.success(f"Double GW{upcoming_gw}: " + ", ".join(dgw_teams))


# ============================================================
# RULES FOOTER
# ============================================================

st.divider()
st.caption("Squad rules: £100m • 2 GK • 5 DEF • 5 MID • 3 FWD • maximum 3 players per club.")
st.caption("Starting XI formation must contain 1 GK, at least 3 DEF, at least 2 MID and at least 1 FWD.")
st.caption("Rankings blend actual points, official expected points (ep_next), form and fixture difficulty.")
