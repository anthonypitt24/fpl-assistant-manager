import streamlit as st
import requests
import math
from datetime import datetime, timezone

# ============================================================
# FPL ASSISTANT MANAGER - SINGLE FILE VERSION
# ============================================================
#
# Put this entire file into:
#
#     app.py
#
# The app uses the public Fantasy Premier League API.
#
# Features:
#   - Manager/team import
#   - Squad analysis
#   - Player projections
#   - Fixture analysis
#   - Captain recommendations
#   - Transfer recommendations
#   - Optional -4 transfer hits
#   - Budget raising / squad restructuring
#   - Chip timing
#   - Explanation of recommendations
#
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)

API = "https://fantasy.premierleague.com/api"

# ============================================================
# BASIC HELPERS
# ============================================================

@st.cache_data(ttl=900)
def get_json(url):
    try:
        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0 FPL Assistant Manager"
            }
        )

        response.raise_for_status()
        return response.json()

    except Exception as e:
        return None


@st.cache_data(ttl=900)
def get_bootstrap():
    return get_json(f"{API}/bootstrap-static/")


@st.cache_data(ttl=900)
def get_fixtures():
    return get_json(f"{API}/fixtures/")


@st.cache_data(ttl=900)
def get_manager(manager_id):
    return get_json(f"{API}/entry/{manager_id}/")


@st.cache_data(ttl=900)
def get_manager_history(manager_id):
    return get_json(f"{API}/entry/{manager_id}/history/")


@st.cache_data(ttl=900)
def get_manager_transfers(manager_id):
    return get_json(f"{API}/entry/{manager_id}/transfers/")


@st.cache_data(ttl=600)
def get_manager_picks(manager_id, gw):
    return get_json(
        f"{API}/entry/{manager_id}/event/{gw}/picks/"
    )


# ============================================================
# DATA SETUP
# ============================================================

bootstrap = get_bootstrap()

if not bootstrap:
    st.error(
        "I couldn't connect to the Fantasy Premier League API. "
        "Please try again in a moment."
    )
    st.stop()

players = bootstrap.get("elements", [])
teams = bootstrap.get("teams", [])
events = bootstrap.get("events", [])
element_types = bootstrap.get("element_types", [])

fixtures = get_fixtures() or []

team_map = {
    t["id"]: t
    for t in teams
}

position_map = {
    p["id"]: p["singular_name"]
    for p in element_types
}

player_map = {
    p["id"]: p
    for p in players
}

# ============================================================
# GAMEWEEK INFORMATION
# ============================================================

def get_current_gameweek():

    for event in events:
        if event.get("is_next"):
            return event["id"]

    for event in events:
        if event.get("is_current"):
            return event["id"]

    # Fallback
    completed = [
        e["id"]
        for e in events
        if e.get("finished")
    ]

    if completed:
        return max(completed)

    return 1


CURRENT_GW = get_current_gameweek()

next_event = next(
    (
        e for e in events
        if e["id"] == CURRENT_GW
    ),
    None
)

# ============================================================
# FIXTURE MAP
# ============================================================

fixture_map = {}

for fixture in fixtures:

    home = fixture.get("team_h")
    away = fixture.get("team_a")

    if not home or not away:
        continue

    fixture_map.setdefault(home, []).append({
        "gw": fixture.get("event"),
        "opponent": away,
        "home": True,
        "difficulty": fixture.get("team_h_difficulty", 3),
        "finished": fixture.get("finished", False),
        "started": fixture.get("started", False)
    })

    fixture_map.setdefault(away, []).append({
        "gw": fixture.get("event"),
        "opponent": home,
        "home": False,
        "difficulty": fixture.get("team_a_difficulty", 3),
        "finished": fixture.get("finished", False),
        "started": fixture.get("started", False)
    })


# ============================================================
# FIXTURE SCORE
# ============================================================

def fixture_score(player, number_of_weeks=5):

    team_id = player["team"]

    upcoming = [
        f for f in fixture_map.get(team_id, [])
        if f["gw"]
        and f["gw"] >= CURRENT_GW
        and f["gw"] < CURRENT_GW + number_of_weeks
    ]

    if not upcoming:
        return 3.0

    total = 0

    for f in upcoming:

        difficulty = f.get("difficulty", 3)

        # FPL difficulty generally runs 1-5.
        #
        # Convert it so:
        # 1 = excellent
        # 5 = difficult
        #
        score = 6 - difficulty

        if f["home"]:
            score += 0.25

        total += score

    return total / len(upcoming)


# ============================================================
# PLAYER PROJECTION
# ============================================================

def safe_float(value, default=0.0):

    try:
        return float(value)
    except:
        return default


def player_projection(player, weeks=5):

    form = safe_float(player.get("form"))
    ppg = safe_float(player.get("points_per_game"))
    total_points = safe_float(player.get("total_points"))

    xg = safe_float(player.get("expected_goals"))
    xa = safe_float(player.get("expected_assists"))

    minutes = safe_float(player.get("minutes"))
    ict = safe_float(player.get("ict_index"))

    fixture = fixture_score(
        player,
        weeks
    )

    # --------------------------------------------------------
    # Base ability
    # --------------------------------------------------------

    base = (
        ppg * 0.35
        + form * 0.30
        + (total_points / max(CURRENT_GW, 1)) * 0.15
    )

    # --------------------------------------------------------
    # Attacking potential
    # --------------------------------------------------------

    attacking = (
        xg * 0.15
        + xa * 0.10
    )

    # --------------------------------------------------------
    # ICT contribution
    # --------------------------------------------------------

    ict_bonus = min(ict / 200, 1.5)

    # --------------------------------------------------------
    # Minutes reliability
    # --------------------------------------------------------

    if minutes >= 1500:
        minutes_bonus = 1.15
    elif minutes >= 1000:
        minutes_bonus = 1.05
    elif minutes >= 500:
        minutes_bonus = 0.90
    else:
        minutes_bonus = 0.70

    # --------------------------------------------------------
    # Fixture multiplier
    # --------------------------------------------------------

    fixture_multiplier = 0.75 + (
        fixture / 5
    ) * 0.25

    projection = (
        (base + attacking + ict_bonus)
        * minutes_bonus
        * fixture_multiplier
    )

    return round(
        max(projection, 0),
        2
    )


# ============================================================
# VALUE / VALUE PER MILLION
# ============================================================

def player_value_score(player):

    price = max(
        safe_float(player.get("now_cost")) / 10,
        0.1
    )

    projection = player_projection(player)

    return projection / price


# ============================================================
# PLAYER STATUS
# ============================================================

def availability_penalty(player):

    status = player.get("status")

    if status == "a":
        return 1.0

    if status == "d":
        chance = player.get("chance_of_playing_next_round")

        if chance is None:
            return 0.65

        return max(
            safe_float(chance) / 100,
            0.25
        )

    if status == "i":
        return 0.25

    if status == "s":
        return 0.35

    if status == "u":
        return 0.50

    return 1.0


# ============================================================
# ADJUSTED PROJECTION
# ============================================================

def adjusted_projection(player, weeks=5):

    return round(
        player_projection(player, weeks)
        * availability_penalty(player),
        2
    )


# ============================================================
# SQUAD VALIDATION
# ============================================================

def count_clubs(squad):

    counts = {}

    for p in squad:
        team_id = p["team"]
        counts[team_id] = counts.get(team_id, 0) + 1

    return counts


def valid_squad(squad):

    if len(squad) != 15:
        return False

    positions = {
        1: 0,
        2: 0,
        3: 0,
        4: 0
    }

    for p in squad:
        positions[p["element_type"]] = (
            positions.get(p["element_type"], 0) + 1
        )

    # Standard FPL squad:
    # 2 GKs
    # 5 DEFs
    # 5 MIDs
    # 3 FWDs

    if positions.get(1) != 2:
        return False

    if positions.get(2) != 5:
        return False

    if positions.get(3) != 5:
        return False

    if positions.get(4) != 3:
        return False

    # Max 3 players per club

    clubs = count_clubs(squad)

    if any(v > 3 for v in clubs.values()):
        return False

    return True


# ============================================================
# TRANSFER VALIDATION
# ============================================================

def can_transfer_in(
    player_in,
    player_out,
    squad,
    bank,
    selling_price
):

    if player_in["id"] == player_out["id"]:
        return False

    purchase_price = (
        safe_float(player_in["now_cost"]) / 10
    )

    if purchase_price > bank + selling_price + 0.01:
        return False

    new_squad = [
        p for p in squad
        if p["id"] != player_out["id"]
    ]

    new_squad.append(player_in)

    return valid_squad(new_squad)


# ============================================================
# TRANSFER PROJECTION
# ============================================================

def transfer_gain(
    player_in,
    player_out,
    weeks=5
):

    incoming = adjusted_projection(
        player_in,
        weeks
    )

    outgoing = adjusted_projection(
        player_out,
        weeks
    )

    return round(
        incoming - outgoing,
        2
    )


# ============================================================
# BEST TRANSFERS
# ============================================================

def find_transfer_options(
    squad,
    bank,
    max_results=15
):

    options = []

    squad_ids = {
        p["id"]
        for p in squad
    }

    for player_out in squad:

        position = player_out["element_type"]

        selling_price = safe_float(
            player_out.get(
                "_selling_price",
                player_out.get("now_cost", 0)
            )
        ) / 10

        for player_in in players:

            if player_in["id"] in squad_ids:
                continue

            if player_in["element_type"] != position:
                continue

            if player_in.get("status") == "u":
                continue

            if not can_transfer_in(
                player_in,
                player_out,
                squad,
                bank,
                selling_price
            ):
                continue

            gain = transfer_gain(
                player_in,
                player_out,
                5
            )

            if gain <= 0:
                continue

            options.append({
                "out": player_out,
                "in": player_in,
                "gain": gain,
                "cost": (
                    safe_float(
                        player_in["now_cost"]
                    ) / 10
                    - selling_price
                )
            })

    options.sort(
        key=lambda x: x["gain"],
        reverse=True
    )

    return options[:max_results]


# ============================================================
# HIT ANALYSIS
# ============================================================

def analyse_hit(option):

    gain = option["gain"]

    # A -4 should normally not be recommended
    # unless the expected gain is meaningfully above 4.
    #
    # We use a safety margin because projections
    # are uncertain.

    net_gain = gain - 4

    if net_gain >= 2:
        verdict = "STRONG -4"
    elif net_gain >= 0:
        verdict = "POSSIBLE -4"
    else:
        verdict = "NO HIT"

    return {
        "gross_gain": gain,
        "hit_cost": 4,
        "net_gain": round(net_gain, 2),
        "verdict": verdict
    }


# ============================================================
# BUDGET RAISING
# ============================================================

def find_budget_upgrades(
    squad,
    bank,
    max_results=10
):

    options = []

    for player_out in squad:

        position = player_out["element_type"]

        selling_price = safe_float(
            player_out.get(
                "_selling_price",
                player_out.get("now_cost", 0)
            )
        ) / 10

        current_projection = adjusted_projection(
            player_out
        )

        cheaper_replacements = []

        for player_in in players:

            if player_in["id"] == player_out["id"]:
                continue

            if player_in["element_type"] != position:
                continue

            if player_in.get("status") == "u":
                continue

            price = safe_float(
                player_in["now_cost"]
            ) / 10

            if price >= selling_price:
                continue

            projection = adjusted_projection(
                player_in
            )

            # Only consider reasonably viable replacements.
            if projection < current_projection * 0.55:
                continue

            cheaper_replacements.append({
                "player": player_in,
                "saving": selling_price - price,
                "projection": projection
            })

        cheaper_replacements.sort(
            key=lambda x: (
                x["saving"] * 0.5
                + x["projection"] * 0.5
            ),
            reverse=True
        )

        for replacement in cheaper_replacements[:3]:

            saving = replacement["saving"]

            if saving <= 0.1:
                continue

            options.append({
                "out": player_out,
                "replacement": replacement["player"],
                "saving": saving,
                "lost_projection": round(
                    current_projection
                    - replacement["projection"],
                    2
                )
            })

    options.sort(
        key=lambda x: (
            x["saving"]
            - x["lost_projection"] * 0.5
        ),
        reverse=True
    )

    return options[:max_results]


# ============================================================
# CAPTAIN RECOMMENDATION
# ============================================================

def captain_score(player):

    projection = adjusted_projection(
        player,
        1
    )

    fixture = fixture_score(
        player,
        1
    )

    minutes = safe_float(
        player.get("minutes")
    )

    reliability = min(
        minutes / 1000,
        1.2
    )

    return (
        projection
        * (0.8 + fixture / 10)
        * reliability
    )


def get_captain_options(squad):

    candidates = []

    for player in squad:

        score = captain_score(
            player
        )

        candidates.append({
            "player": player,
            "score": score,
            "projection": adjusted_projection(
                player,
                1
            ),
            "fixture": fixture_score(
                player,
                1
            )
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return candidates


# ============================================================
# CHIP ANALYSIS
# ============================================================

def fixture_count(team_id, gw):

    return len([
        f
        for f in fixture_map.get(team_id, [])
        if f["gw"] == gw
        and not f["finished"]
    ])


def gameweek_fixture_summary(gw):

    summary = {}

    for team in teams:

        summary[team["id"]] = {
            "name": team["name"],
            "fixtures": fixture_count(
                team["id"],
                gw
            )
        }

    return summary


def chip_analysis():

    results = {
        "Wildcard": [],
        "Free Hit": [],
        "Bench Boost": [],
        "Triple Captain": []
    }

    future_gws = [
        e["id"]
        for e in events
        if e["id"] >= CURRENT_GW
        and not e.get("finished")
    ][:12]

    # --------------------------------------------------------
    # Find double / blank gameweeks
    # --------------------------------------------------------

    doubles = []
    blanks = []

    for gw in future_gws:

        counts = {}

        for fixture in fixtures:

            if fixture.get("event") != gw:
                continue

            h = fixture.get("team_h")
            a = fixture.get("team_a")

            if h:
                counts[h] = counts.get(h, 0) + 1

            if a:
                counts[a] = counts.get(a, 0) + 1

        if any(
            count >= 2
            for count in counts.values()
        ):
            doubles.append(gw)

        if len(counts) <= 16:
            blanks.append(gw)

    # --------------------------------------------------------
    # Wildcard
    # --------------------------------------------------------

    for gw in future_gws:

        fixtures_in_window = []

        for team_id in team_map:

            team_fixtures = [
                f
                for f in fixture_map.get(
                    team_id,
                    []
                )
                if f["gw"]
                and gw <= f["gw"] < gw + 5
                and not f["finished"]
            ]

            fixtures_in_window.extend(
                team_fixtures
            )

        if not fixtures_in_window:
            continue

        average_difficulty = sum(
            f["difficulty"]
            for f in fixtures_in_window
        ) / len(fixtures_in_window)

        score = 6 - average_difficulty

        if gw in doubles:
            score += 1.0

        if gw in blanks:
            score += 0.5

        results["Wildcard"].append({
            "gw": gw,
            "score": score
        })

    # --------------------------------------------------------
    # Free Hit
    # --------------------------------------------------------

    for gw in future_gws:

        score = 0

        if gw in blanks:
            score += 4

        if gw in doubles:
            score += 2

        results["Free Hit"].append({
            "gw": gw,
            "score": score
        })

    # --------------------------------------------------------
    # Bench Boost
    # --------------------------------------------------------

    for gw in future_gws:

        count = 0
        double_players = 0

        for player in players:

            team_id = player["team"]

            games = [
                f
                for f in fixture_map.get(
                    team_id,
                    []
                )
                if f["gw"] == gw
                and not f["finished"]
            ]

            if games:
                count += 1

            if len(games) >= 2:
                double_players += 1

        score = (
            count * 0.25
            + double_players * 2
        )

        results["Bench Boost"].append({
            "gw": gw,
            "score": score
        })

    # --------------------------------------------------------
    # Triple Captain
    # --------------------------------------------------------

    for gw in future_gws:

        best_score = 0

        for player in players:

            games = [
                f
                for f in fixture_map.get(
                    player["team"],
                    []
                )
                if f["gw"] == gw
                and not f["finished"]
            ]

            if not games:
                continue

            projection = adjusted_projection(
                player,
                1
            )

            if len(games) >= 2:
                projection *= 1.35

            best_score = max(
                best_score,
                projection
            )

        results["Triple Captain"].append({
            "gw": gw,
            "score": best_score
        })

    return results


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title("⚽ FPL Assistant")

manager_id = st.sidebar.text_input(
    "FPL Team ID",
    placeholder="e.g. 1234567"
)

st.sidebar.markdown(
    """
Enter your **FPL Team ID**.

You can find it in the URL when viewing
your Fantasy Premier League team.
"""
)

weeks = st.sidebar.slider(
    "Projection window",
    min_value=1,
    max_value=8,
    value=5
)

allow_hits = st.sidebar.checkbox(
    "Allow -4 transfer recommendations",
    value=True
)


# ============================================================
# HOME SCREEN
# ============================================================

st.title("⚽ FPL Assistant Manager")

st.caption(
    "Data-driven squad, transfer and chip analysis"
)

if not manager_id:

    st.info(
        "Enter your FPL Team ID on the left to load your squad."
    )

    st.markdown(
        """
### What this assistant analyses

**Transfers**
- Best free transfer
- Whether a -4 is worthwhile
- Selling players to raise funds
- Longer-term transfer value

**Captain**
- Best captain
- Alternative captain
- Fixture strength
- Form and minutes

**Chips**
- Wildcard timing
- Free Hit timing
- Bench Boost timing
- Triple Captain timing

**Important:** the assistant is deliberately willing to recommend
a -4 when the projected benefit is strong enough. It will also
recommend a downgrade when sacrificing one position can materially
improve another.
"""
    )

    st.stop()


# ============================================================
# LOAD MANAGER
# ============================================================

try:
    manager_id_int = int(manager_id)
except:

    st.error(
        "Please enter a numeric FPL Team ID."
    )

    st.stop()


manager = get_manager(
    manager_id_int
)

if not manager:

    st.error(
        "I couldn't find that FPL team. "
        "Check the Team ID and try again."
    )

    st.stop()


# ============================================================
# GET CURRENT PICKS
# ============================================================

picks_data = get_manager_picks(
    manager_id_int,
    CURRENT_GW
)

if not picks_data:

    # Try previous GW if current isn't available
    fallback_gw = max(
        1,
        CURRENT_GW - 1
    )

    picks_data = get_manager_picks(
        manager_id_int,
        fallback_gw
    )

if not picks_data:

    st.warning(
        "Your current squad could not be loaded yet. "
        "This can happen around a Gameweek transition."
    )

    st.stop()


# ============================================================
# BUILD SQUAD
# ============================================================

pick_list = picks_data.get(
    "picks",
    []
)

squad = []

for pick in pick_list:

    player = player_map.get(
        pick.get("element")
    )

    if not player:
        continue

    player_copy = dict(player)

    player_copy["_selling_price"] = (
        pick.get(
            "selling_price",
            player.get("now_cost", 0)
        )
    )

    player_copy["_multiplier"] = pick.get(
        "multiplier",
        0
    )

    player_copy["_is_captain"] = pick.get(
        "is_captain",
        False
    )

    player_copy["_is_vice"] = pick.get(
        "is_vice_captain",
        False
    )

    player_copy["_position"] = pick.get(
        "position",
        0
    )

    squad.append(
        player_copy
    )


# ============================================================
# BANK
# ============================================================

entry_history = get_manager_history(
    manager_id_int
)

bank = 0

if entry_history:

    current_history = entry_history.get(
        "current",
        []
    )

    if current_history:

        latest = current_history[-1]

        # This is only a fallback.
        # FPL history doesn't always expose exact
        # current bank information.

        bank = safe_float(
            latest.get("bank", 0)
        ) / 10


# ============================================================
# HEADER
# ============================================================

manager_name = (
    manager.get("player_first_name", "")
    + " "
    + manager.get("player_last_name", "")
).strip()

st.subheader(
    f"Welcome, {manager_name or 'Manager'}"
)

col1, col2, col3, col4 = st.columns(4)

col1.metric(
    "Gameweek",
    CURRENT_GW
)

col2.metric(
    "Overall Rank",
    f"{manager.get('summary_overall_rank', 0):,}"
)

col3.metric(
    "Total Points",
    f"{manager.get('summary_overall_points', 0):,}"
)

col4.metric(
    "Team Value",
    f"£{manager.get('last_deadline_value', 0) / 10:.1f}m"
)


# ============================================================
# TABS
# ============================================================

(
    tab_squad,
    tab_transfers,
    tab_captain,
    tab_chips,
    tab_players
) = st.tabs(
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

    st.header("Your Squad")

    squad_rows = []

    for p in squad:

        team_name = team_map.get(
            p["team"],
            {}
        ).get(
            "short_name",
            "?"
        )

        projection = adjusted_projection(
            p,
            weeks
        )

        squad_rows.append({
            "Player": p["web_name"],
            "Position": position_map.get(
                p["element_type"],
                "?"
            ),
            "Club": team_name,
            "Price": f"£{p['now_cost'] / 10:.1f}m",
            "Form": p.get("form", 0),
            "PPG": p.get("points_per_game", 0),
            f"{weeks}GW Projection": projection,
            "Status": p.get("status", "")
        })

    squad_rows.sort(
        key=lambda x: x[f"{weeks}GW Projection"],
        reverse=True
    )

    st.dataframe(
        squad_rows,
        use_container_width=True,
        hide_index=True
    )

    st.subheader("Squad Strength")

    total_projection = sum(
        adjusted_projection(
            p,
            weeks
        )
        for p in squad
    )

    st.metric(
        f"Projected points - next {weeks} GWs",
        round(total_projection, 1)
    )


# ============================================================
# TRANSFER TAB
# ============================================================

with tab_transfers:

    st.header("Transfer Planner")

    st.write(
        "The assistant looks beyond the next Gameweek and "
        "tries to find moves that improve your squad over "
        f"the next {weeks} Gameweeks."
    )

    transfer_options = find_transfer_options(
        squad,
        bank,
        20
    )

    if not transfer_options:

        st.info(
            "No significant transfer upgrades were found."
        )

    else:

        st.subheader(
            "Best transfer opportunities"
        )

        for index, option in enumerate(
            transfer_options[:10],
            start=1
        ):

            player_out = option["out"]
            player_in = option["in"]

            gain = option["gain"]

            cost = option["cost"]

            hit_info = analyse_hit(
                option
            )

            st.markdown(
                f"### {index}. "
                f"{player_out['web_name']} → "
                f"{player_in['web_name']}"
            )

            c1, c2, c3, c4 = st.columns(4)

            c1.metric(
                "Projected gain",
                f"+{gain:.1f}"
            )

            c2.metric(
                "Transfer cost",
                (
                    "Free"
                    if cost <= 0
                    else f"£{cost:.1f}m"
                )
            )

            c3.metric(
                "After -4",
                f"{hit_info['net_gain']:+.1f}"
            )

            c4.metric(
                "Verdict",
                (
                    "TAKE HIT"
                    if (
                        allow_hits
                        and hit_info["verdict"]
                        == "STRONG -4"
                    )
                    else "FREE TRANSFER"
                )
            )

            out_team = team_map.get(
                player_out["team"],
                {}
            ).get(
                "name",
                ""
            )

            in_team = team_map.get(
                player_in["team"],
                {}
            ).get(
                "name",
                ""
            )

            st.write(
                f"**Sell:** {player_out['web_name']} "
                f"({out_team}) — "
                f"projection {adjusted_projection(player_out, weeks):.1f}"
            )

            st.write(
                f"**Buy:** {player_in['web_name']} "
                f"({in_team}) — "
                f"projection {adjusted_projection(player_in, weeks):.1f}"
            )

            if (
                allow_hits
                and hit_info["verdict"]
                == "STRONG -4"
            ):

                st.success(
                    f"**-4 is justified:** "
                    f"estimated gross gain is "
                    f"+{gain:.1f}, leaving roughly "
                    f"+{hit_info['net_gain']:.1f} after the hit."
                )

            elif (
                allow_hits
                and hit_info["verdict"]
                == "POSSIBLE -4"
            ):

                st.warning(
                    "A -4 is possible, but the margin is "
                    "not large enough to make it a strong recommendation."
                )

            else:

                st.info(
                    "This is better treated as a free transfer. "
                    "The projected improvement does not comfortably "
                    "justify spending 4 points."
                )

            st.divider()


# ============================================================
# BUDGET / DOWNGRADE SECTION
# ============================================================

with tab_transfers:

    st.header("💰 Budget-Raising Options")

    st.write(
        "Sometimes the best move isn't simply replacing a player. "
        "The assistant can suggest downgrading one position so "
        "the extra money can be used to upgrade another."
    )

    budget_options = find_budget_upgrades(
        squad,
        bank,
        10
    )

    if not budget_options:

        st.info(
            "No useful budget-raising moves were identified."
        )

    else:

        for option in budget_options:

            out = option["out"]
            replacement = option["replacement"]

            st.markdown(
                f"**{out['web_name']} → "
                f"{replacement['web_name']}**"
            )

            st.write(
                f"Save approximately "
                f"**£{option['saving']:.1f}m**. "
                f"Projected sacrifice: "
                f"{option['lost_projection']:.1f} points."
            )


# ============================================================
# CAPTAIN TAB
# ============================================================

with tab_captain:

    st.header("Captain Recommendation")

    captain_options = get_captain_options(
        squad
    )

    if captain_options:

        best = captain_options[0]

        st.success(
            f"### Captain: {best['player']['web_name']}"
        )

        st.write(
            f"Projected next-GW score: "
            f"**{best['projection']:.1f}**"
        )

        st.write(
            f"Fixture score: "
            f"**{best['fixture']:.1f}/5**"
        )

        if len(captain_options) > 1:

            st.subheader(
                "Captain alternatives"
            )

            captain_rows = []

            for item in captain_options[:5]:

                captain_rows.append({
                    "Player": item["player"]["web_name"],
                    "Projection": item["projection"],
                    "Fixture": round(
                        item["fixture"],
                        2
                    ),
                    "Captain Score": round(
                        item["score"],
                        2
                    )
                })

            st.dataframe(
                captain_rows,
                use_container_width=True,
                hide_index=True
            )


# ============================================================
# CHIP TAB
# ============================================================

with tab_chips:

    st.header("🎯 Chip Planner")

    st.write(
        "Chip recommendations look for fixture swings, "
        "Double Gameweeks and Blank Gameweeks."
    )

    chips = chip_analysis()

    for chip_name, data in chips.items():

        st.subheader(
            chip_name
        )

        if not data:

            st.info(
                "Not enough fixture information yet."
            )

            continue

        ranked = sorted(
            data,
            key=lambda x: x["score"],
            reverse=True
        )

        best = ranked[0]

        if best["score"] <= 0:

            st.write(
                "No strong opportunity detected yet."
            )

        else:

            st.success(
                f"Best current window: "
                f"**Gameweek {best['gw']}**"
            )

        rows = []

        for item in ranked[:5]:

            rows.append({
                "Gameweek": item["gw"],
                "Opportunity Score": round(
                    item["score"],
                    2
                )
            })

        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# PLAYER TAB
# ============================================================

with tab_players:

    st.header("📊 Player Rankings")

    ranking = []

    for p in players:

        if p.get("status") == "u":
            continue

        projection = adjusted_projection(
            p,
            weeks
        )

        price = (
            safe_float(
                p.get("now_cost")
            ) / 10
        )

        if price <= 0:
            continue

        ranking.append({
            "Player": p["web_name"],
            "Position": position_map.get(
                p["element_type"],
                "?"
            ),
            "Price": f"£{price:.1f}m",
            "Form": p.get("form", 0),
            "PPG": p.get("points_per_game", 0),
            "Projection": projection,
            "Value": round(
                projection / price,
                2
            )
        })

    ranking.sort(
        key=lambda x: x["Projection"],
        reverse=True
    )

    st.subheader(
        f"Best projected players - next {weeks} GWs"
    )

    st.dataframe(
        ranking[:50],
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# CHIP / TRANSFER SUMMARY
# ============================================================

st.divider()

st.subheader("🤖 Assistant Summary")

if transfer_options:

    best_transfer = transfer_options[0]

    out = best_transfer["out"]
    incoming = best_transfer["in"]

    hit = analyse_hit(
        best_transfer
    )

    st.write(
        f"**Best transfer:** "
        f"{out['web_name']} → "
        f"{incoming['web_name']} "
        f"(projected improvement "
        f"+{best_transfer['gain']:.1f} over the analysis window)."
    )

    if (
        allow_hits
        and hit["verdict"] == "STRONG -4"
    ):

        st.write(
            "The model considers the -4 potentially worthwhile "
            "because the projected improvement is large enough "
            "to recover the hit."
        )

    else:

        st.write(
            "The model does not currently see a strong reason "
            "to spend 4 points on the move."
        )

else:

    st.write(
        "No major transfer opportunity currently stands out."
    )

st.caption(
    "Projections are estimates, not guarantees. "
    "Use the recommendations alongside team news, injuries, "
    "rotation risk and your own judgement."
)
