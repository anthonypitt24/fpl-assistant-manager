import streamlit as st
import requests
import pandas as pd
import itertools
import math
from collections import Counter

# ============================================================
# FPL ASSISTANT MANAGER
# Fast version - single file
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)

# ------------------------------------------------------------
# SETTINGS
# ------------------------------------------------------------

FPL_API = "https://fantasy.premierleague.com/api/bootstrap-static/"
FIXTURES_API = "https://fantasy.premierleague.com/api/fixtures/"

BUDGET = 100.0

# How many candidates we keep for optimisation.
# Keeping this relatively small makes the app very fast.
GK_CANDIDATES = 12
DEF_CANDIDATES = 35
MID_CANDIDATES = 40
FWD_CANDIDATES = 25

# Number of states retained during optimisation.
BEAM_WIDTH = 6000


# ------------------------------------------------------------
# LOAD FPL DATA
# ------------------------------------------------------------

@st.cache_data(ttl=900, show_spinner=False)
def load_fpl_data():

    response = requests.get(
        FPL_API,
        timeout=20,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    response.raise_for_status()

    data = response.json()

    fixtures_response = requests.get(
        FIXTURES_API,
        timeout=20,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    fixtures_response.raise_for_status()

    fixtures = fixtures_response.json()

    return data, fixtures


# ------------------------------------------------------------
# BASIC HELPERS
# ------------------------------------------------------------

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except:
        return default


def safe_int(value, default=0):
    try:
        if value is None:
            return default
        return int(value)
    except:
        return default


def position_name(position_id):

    return {
        1: "GK",
        2: "DEF",
        3: "MID",
        4: "FWD"
    }.get(position_id, "UNK")


# ------------------------------------------------------------
# FIXTURE DIFFICULTY
# ------------------------------------------------------------

def calculate_fixture_score(player_team, fixtures, current_gw):

    """
    Produces a simple attacking/defensive fixture score.

    Lower fixture difficulty = better fixture.

    We look ahead several Gameweeks rather than only one.
    """

    future = []

    for fixture in fixtures:

        if fixture.get("finished"):
            continue

        event = fixture.get("event")

        if event is None:
            continue

        if event < current_gw:
            continue

        if event > current_gw + 5:
            continue

        home = fixture.get("team_h")
        away = fixture.get("team_a")

        if player_team not in (home, away):
            continue

        difficulty = (
            fixture.get("team_h_difficulty", 3)
            if player_team == home
            else fixture.get("team_a_difficulty", 3)
        )

        future.append(difficulty)

    if not future:
        return 3.0

    average = sum(future) / len(future)

    # Convert FDR into a positive score.
    # 1 = excellent fixture
    # 5 = difficult fixture
    return max(0.0, 6.0 - average)


# ------------------------------------------------------------
# PLAYER DATA
# ------------------------------------------------------------

def build_players(data, fixtures):

    teams = {
        team["id"]: team
        for team in data["teams"]
    }

    current_gw = safe_int(
        data.get("events", [{}])[0].get("id", 1),
        1
    )

    # Find the current Gameweek.
    for event in data.get("events", []):

        if event.get("is_current"):
            current_gw = event.get("id", current_gw)

    players = []

    for p in data.get("elements", []):

        position = position_name(p.get("element_type"))

        team_id = p.get("team")
        team = teams.get(team_id, {})

        price = safe_float(p.get("now_cost")) / 10

        total_points = safe_float(p.get("total_points"))
        ppg = safe_float(p.get("points_per_game"))
        form = safe_float(p.get("form"))

        minutes = safe_float(p.get("minutes"))

        starts = safe_float(p.get("starts"))

        ownership = safe_float(p.get("selected_by_percent"))

        ep_next = safe_float(p.get("ep_next"))
        ep_this = safe_float(p.get("ep_this"))

        chance = safe_float(
            p.get("chance_of_playing_next_round"),
            100
        )

        if chance is None:
            chance = 100

        if chance == 0:
            availability = 0
        else:
            availability = chance / 100

        fixture_score = calculate_fixture_score(
            team_id,
            fixtures,
            current_gw
        )

        # ----------------------------------------------------
        # PROJECTED POINTS
        # ----------------------------------------------------

        # Current FPL projection if available.
        official_projection = (
            ep_next
            if ep_next > 0
            else ep_this
        )

        # Historical / current season points per game.
        base_ppg = ppg

        # Form.
        form_score = form

        # If there isn't enough current-season information,
        # use points per game as the main starting point.
        if base_ppg <= 0:
            base_ppg = 2.5

        # Combine the available information.
        #
        # Official FPL expected points gets the biggest weight
        # when it is available.
        if official_projection > 0:

            projection = (
                official_projection * 0.50
                + base_ppg * 0.25
                + form_score * 0.10
            )

        else:

            projection = (
                base_ppg * 0.60
                + form_score * 0.15
            )

        # Fixture adjustment.
        #
        # A good fixture should slightly increase the projection.
        fixture_multiplier = 0.90 + (
            fixture_score / 10
        )

        projection *= fixture_multiplier

        # Availability.
        projection *= (
            0.70 + (0.30 * availability)
        )

        # Minutes/start probability.
        #
        # Players who regularly start are safer.
        if minutes > 0:

            start_rate = starts / max(
                1,
                minutes / 90
            )

            start_rate = min(
                1.0,
                max(0.0, start_rate)
            )

        else:

            start_rate = 0.5

        # Don't punish too harshly for limited sample sizes.
        start_factor = 0.80 + (
            0.20 * start_rate
        )

        projection *= start_factor

        # ----------------------------------------------------
        # VALUE SCORE
        # ----------------------------------------------------

        value = projection / max(
            0.1,
            price
        )

        # Slightly reward proven performers.
        history_bonus = min(
            1.0,
            total_points / 1000
        )

        # Reliability.
        reliability = (
            availability * 0.5
            + start_rate * 0.5
        )

        # Final optimisation score.
        optimisation_score = (
            projection * 0.70
            + value * 0.20
            + history_bonus * 0.05
            + reliability * 0.05
        )

        players.append({

            "id": p.get("id"),

            "name": (
                f"{p.get('first_name', '')} "
                f"{p.get('second_name', '')}"
            ).strip(),

            "short_name": p.get("web_name", "Unknown"),

            "position": position,

            "team_id": team_id,

            "team": team.get(
                "name",
                "Unknown"
            ),

            "price": price,

            "total_points": total_points,

            "ppg": ppg,

            "form": form,

            "minutes": minutes,

            "starts": starts,

            "ownership": ownership,

            "projection": round(
                max(0, projection),
                2
            ),

            "value": round(
                max(0, value),
                3
            ),

            "fixture_score": round(
                fixture_score,
                2
            ),

            "availability": availability,

            "start_rate": start_rate,

            "optimisation_score":
                optimisation_score
        })

    return players, current_gw


# ------------------------------------------------------------
# CANDIDATE FILTERING
# ------------------------------------------------------------

def get_candidates(players, position):

    candidates = [
        p for p in players
        if p["position"] == position
        and p["price"] > 0
        and p["availability"] > 0
    ]

    candidates.sort(
        key=lambda x: x["optimisation_score"],
        reverse=True
    )

    limits = {
        "GK": GK_CANDIDATES,
        "DEF": DEF_CANDIDATES,
        "MID": MID_CANDIDATES,
        "FWD": FWD_CANDIDATES
    }

    return candidates[:limits[position]]


# ------------------------------------------------------------
# FAST BEAM SEARCH
# ------------------------------------------------------------

def optimise_squad(players):

    """
    Finds a strong 15-man squad quickly.

    Required:
        2 GK
        5 DEF
        5 MID
        3 FWD

    Constraints:
        £100m
        max 3 players per club
    """

    positions = [
        ("GK", 2),
        ("DEF", 5),
        ("MID", 5),
        ("FWD", 3)
    ]

    candidates_by_position = {
        pos: get_candidates(players, pos)
        for pos, _ in positions
    }

    # State:
    # (
    #   selected_tuple,
    #   cost,
    #   score,
    #   club_counts
    # )

    states = [
        (
            tuple(),
            0.0,
            0.0,
            {}
        )
    ]

    for position, required in positions:

        candidates = candidates_by_position[position]

        # Keep candidates affordable in isolation.
        candidates = [
            p for p in candidates
            if p["price"] <= BUDGET
        ]

        new_states = []

        # For each current state, add combinations
        # of the required players for this position.
        #
        # Candidate lists are intentionally limited.
        for state in states:

            selected, cost, score, clubs = state

            for combo in itertools.combinations(
                candidates,
                required
            ):

                combo_cost = sum(
                    p["price"]
                    for p in combo
                )

                new_cost = cost + combo_cost

                if new_cost > BUDGET:
                    continue

                new_clubs = clubs.copy()

                valid = True

                for p in combo:

                    club = p["team_id"]

                    new_clubs[club] = (
                        new_clubs.get(club, 0) + 1
                    )

                    if new_clubs[club] > 3:
                        valid = False
                        break

                if not valid:
                    continue

                combo_score = sum(
                    p["optimisation_score"]
                    for p in combo
                )

                new_states.append(
                    (
                        selected + combo,
                        new_cost,
                        score + combo_score,
                        new_clubs
                    )
                )

        # Keep only the best states.
        new_states.sort(
            key=lambda x: x[2],
            reverse=True
        )

        states = new_states[:BEAM_WIDTH]

        if not states:
            return []

    # Best state.
    states.sort(
        key=lambda x: x[2],
        reverse=True
    )

    return list(states[0][0])


# ------------------------------------------------------------
# STARTING XI OPTIMISATION
# ------------------------------------------------------------

def best_starting_xi(squad):

    """
    Find the highest projected starting XI.

    Valid FPL formations require:

        1 GK
        minimum 3 DEF
        minimum 2 MID
        minimum 1 FWD
    """

    gks = [
        p for p in squad
        if p["position"] == "GK"
    ]

    defs = [
        p for p in squad
        if p["position"] == "DEF"
    ]

    mids = [
        p for p in squad
        if p["position"] == "MID"
    ]

    fwds = [
        p for p in squad
        if p["position"] == "FWD"
    ]

    best = None

    formations = []

    for defenders in range(3, 6):

        for midfielders in range(2, 6):

            forwards = 10 - defenders - midfielders

            if forwards < 1:
                continue

            if forwards > 3:
                continue

            if defenders > len(defs):
                continue

            if midfielders > len(mids):
                continue

            if forwards > len(fwds):
                continue

            formations.append(
                (
                    defenders,
                    midfielders,
                    forwards
                )
            )

    for d_count, m_count, f_count in formations:

        for gk in gks:

            for d_combo in itertools.combinations(
                defs,
                d_count
            ):

                for m_combo in itertools.combinations(
                    mids,
                    m_count
                ):

                    for f_combo in itertools.combinations(
                        fwds,
                        f_count
                    ):

                        xi = (
                            [gk]
                            + list(d_combo)
                            + list(m_combo)
                            + list(f_combo)
                        )

                        score = sum(
                            p["projection"]
                            for p in xi
                        )

                        if (
                            best is None
                            or score > best["score"]
                        ):

                            best = {
                                "players": xi,
                                "score": score,
                                "formation": (
                                    f"{d_count}-"
                                    f"{m_count}-"
                                    f"{f_count}"
                                )
                            }

    return best


# ------------------------------------------------------------
# CAPTAIN
# ------------------------------------------------------------

def choose_captain(xi):

    sorted_players = sorted(
        xi,
        key=lambda p: (
            p["projection"],
            p["form"],
            p["fixture_score"]
        ),
        reverse=True
    )

    if not sorted_players:
        return None, None

    captain = sorted_players[0]

    vice = (
        sorted_players[1]
        if len(sorted_players) > 1
        else None
    )

    return captain, vice


# ------------------------------------------------------------
# BENCH
# ------------------------------------------------------------

def build_bench(squad, starting_xi):

    starter_ids = {
        p["id"]
        for p in starting_xi
    }

    bench = [
        p for p in squad
        if p["id"] not in starter_ids
    ]

    # Put strongest bench options first,
    # but maintain a useful positional order.
    bench.sort(
        key=lambda p: (
            p["projection"],
            p["start_rate"],
            p["form"]
        ),
        reverse=True
    )

    return bench


# ------------------------------------------------------------
# DISPLAY TABLE
# ------------------------------------------------------------

def player_dataframe(players):

    rows = []

    for p in players:

        rows.append({

            "Player": p["short_name"],

            "Club": p["team"],

            "Pos": p["position"],

            "Price": f"£{p['price']:.1f}m",

            "Projected": round(
                p["projection"],
                1
            ),

            "Form": round(
                p["form"],
                1
            ),

            "PPG": round(
                p["ppg"],
                1
            ),

            "Fixture": round(
                p["fixture_score"],
                1
            ),

            "Value": round(
                p["value"],
                2
            )
        })

    return pd.DataFrame(rows)


# ------------------------------------------------------------
# TRANSFER ENGINE
# ------------------------------------------------------------

def recommend_transfers(
    current_squad,
    players,
    free_transfers=1
):

    if not current_squad:
        return []

    recommendations = []

    # Candidate pool.
    candidates_by_position = {

        "GK": get_candidates(players, "GK"),

        "DEF": get_candidates(players, "DEF"),

        "MID": get_candidates(players, "MID"),

        "FWD": get_candidates(players, "FWD")
    }

    current_ids = {
        p["id"]
        for p in current_squad
    }

    current_clubs = Counter(
        p["team_id"]
        for p in current_squad
    )

    for outgoing in current_squad:

        candidates = candidates_by_position[
            outgoing["position"]
        ]

        for incoming in candidates:

            if incoming["id"] in current_ids:
                continue

            # Buying the incoming player means
            # removing the outgoing player first.
            new_club_count = (
                current_clubs[incoming["team_id"]]
                - (
                    1
                    if incoming["team_id"]
                    == outgoing["team_id"]
                    else 0
                )
                + 1
            )

            if new_club_count > 3:
                continue

            # Projected improvement.
            improvement = (
                incoming["projection"]
                - outgoing["projection"]
            )

            # Cost of transfer.
            hit_cost = 0

            if free_transfers <= 0:
                hit_cost = 4

            net_gain = improvement - hit_cost

            # Only recommend transfers with a
            # meaningful projected benefit.
            if net_gain <= 0.5:
                continue

            recommendations.append({

                "out": outgoing,

                "in": incoming,

                "improvement": improvement,

                "hit_cost": hit_cost,

                "net_gain": net_gain
            })

    recommendations.sort(
        key=lambda x: x["net_gain"],
        reverse=True
    )

    return recommendations[:10]


# ------------------------------------------------------------
# HEADER
# ------------------------------------------------------------

st.title("⚽ FPL Assistant Manager")

st.caption(
    "Fast squad optimisation • Starting XI • Transfers • Captain"
)


# ------------------------------------------------------------
# LOAD
# ------------------------------------------------------------

try:

    with st.spinner("Loading the latest FPL data..."):

        data, fixtures = load_fpl_data()

        players, current_gw = build_players(
            data,
            fixtures
        )

except Exception as e:

    st.error(
        "Could not load the official FPL data."
    )

    st.code(str(e))

    st.stop()


# ------------------------------------------------------------
# GAMEWEEK STATUS
# ------------------------------------------------------------

current_event = None

for event in data.get("events", []):

    if event.get("is_current"):

        current_event = event
        break

if current_event:

    gw_number = current_event.get(
        "id",
        current_gw
    )

    gw_name = current_event.get(
        "name",
        f"Gameweek {gw_number}"
    )

else:

    gw_number = current_gw
    gw_name = f"Gameweek {gw_number}"


st.info(
    f"📅 {gw_name} • "
    f"Using current official FPL data"
)


# ------------------------------------------------------------
# TABS
# ------------------------------------------------------------

tab1, tab2, tab3, tab4 = st.tabs(
    [
        "🏆 Best Team",
        "⚽ Starting XI",
        "🔄 Transfers",
        "📊 Player Data"
    ]
)


# ============================================================
# BEST £100M TEAM
# ============================================================

with tab1:

    st.header("💰 Best £100m Team")

    st.write(
        "This searches for a high-scoring 15-player squad "
        "within the official FPL squad rules."
    )

    with st.spinner(
        "Finding the best £100m squad..."
    ):

        best_squad = optimise_squad(players)

    if not best_squad:

        st.error(
            "I couldn't find a valid squad. "
            "Try refreshing the page."
        )

    else:

        total_cost = sum(
            p["price"]
            for p in best_squad
        )

        total_projection = sum(
            p["projection"]
            for p in best_squad
        )

        remaining = BUDGET - total_cost

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Squad Cost",
            f"£{total_cost:.1f}m"
        )

        col2.metric(
            "Money Remaining",
            f"£{remaining:.1f}m"
        )

        col3.metric(
            "Projected Points",
            f"{total_projection:.1f}"
        )

        st.success(
            "This is the recommended 15-player squad."
        )

        df = player_dataframe(best_squad)

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True
        )

        # Position summaries.

        for position, label in [
            ("GK", "🧤 Goalkeepers"),
            ("DEF", "🛡️ Defenders"),
            ("MID", "🎯 Midfielders"),
            ("FWD", "⚡ Forwards")
        ]:

            st.subheader(label)

            position_players = [
                p for p in best_squad
                if p["position"] == position
            ]

            for p in position_players:

                st.write(
                    f"**{p['short_name']}** "
                    f"({p['team']}) — "
                    f"£{p['price']:.1f}m — "
                    f"projected **{p['projection']:.1f}**"
                )


# ============================================================
# STARTING XI
# ============================================================

with tab2:

    st.header("⚽ Best Starting XI")

    if not best_squad:

        st.warning(
            "Build the £100m squad first."
        )

    else:

        with st.spinner(
            "Finding the best starting XI..."
        ):

            best_xi = best_starting_xi(
                best_squad
            )

        if not best_xi:

            st.error(
                "Could not calculate a starting XI."
            )

        else:

            captain, vice = choose_captain(
                best_xi["players"]
            )

            bench = build_bench(
                best_squad,
                best_xi["players"]
            )

            col1, col2, col3 = st.columns(3)

            col1.metric(
                "Formation",
                best_xi["formation"]
            )

            col2.metric(
                "Projected XI",
                f"{best_xi['score']:.1f}"
            )

            if captain:

                col3.metric(
                    "Captain",
                    captain["short_name"]
                )

            st.subheader(
                f"⭐ Recommended {best_xi['formation']}"
            )

            xi_df = player_dataframe(
                best_xi["players"]
            )

            st.dataframe(
                xi_df,
                use_container_width=True,
                hide_index=True
            )

            if captain:

                st.success(
                    f"🧢 Captain: **{captain['short_name']}** "
                    f"({captain['projection']:.1f} projected)"
                )

            if vice:

                st.info(
                    f"🥈 Vice-Captain: "
                    f"**{vice['short_name']}** "
                    f"({vice['projection']:.1f} projected)"
                )

            st.subheader("🪑 Bench")

            for index, player in enumerate(
                bench,
                start=1
            ):

                st.write(
                    f"**{index}. {player['short_name']}** "
                    f"({player['position']}) — "
                    f"{player['projection']:.1f} projected"
                )


# ============================================================
# TRANSFERS
# ============================================================

with tab3:

    st.header("🔄 Transfer Recommendations")

    st.write(
        "The transfer engine looks for upgrades based on "
        "projected points, fixtures, form and value."
    )

    st.caption(
        "If you have used all your free transfers, "
        "the calculation can account for the normal "
        "-4 point transfer cost."
    )

    # We don't know the user's actual FPL squad unless
    # they add it manually.
    st.info(
        "To make personalised transfer recommendations, "
        "enter the players currently in your squad below."
    )

    names = [
        p["short_name"]
        for p in players
    ]

    selected_names = st.multiselect(
        "Your current 15 players",
        names,
        max_selections=15
    )

    free_transfers = st.number_input(
        "Free transfers available",
        min_value=0,
        max_value=5,
        value=1,
        step=1
    )

    if selected_names:

        current_squad = [
            p for p in players
            if p["short_name"]
            in selected_names
        ]

        if len(current_squad) < 15:

            st.warning(
                f"You've selected {len(current_squad)} "
                f"players. Select all 15 for the most accurate "
                f"recommendations."
            )

        recommendations = recommend_transfers(
            current_squad,
            players,
            free_transfers
        )

        if not recommendations:

            st.success(
                "No obvious profitable transfer found "
                "from the current data."
            )

        else:

            st.subheader(
                "Best potential transfers"
            )

            for rec in recommendations:

                outgoing = rec["out"]
                incoming = rec["in"]

                hit_text = (
                    "FREE"
                    if rec["hit_cost"] == 0
                    else "-4"
                )

                st.markdown(
                    f"""
### 🔄 {outgoing['short_name']} → {incoming['short_name']}

**Sell:** {outgoing['team']} • £{outgoing['price']:.1f}m  
**Buy:** {incoming['team']} • £{incoming['price']:.1f}m

Projected improvement:
**+{rec['improvement']:.1f} points**

Transfer cost:
**{hit_text}**

Estimated net benefit:
**+{rec['net_gain']:.1f} points**
"""
                )


# ============================================================
# PLAYER DATA
# ============================================================

with tab4:

    st.header("📊 Player Data")

    st.write(
        "Players are ranked using current FPL information "
        "including projected points, form, previous/current "
        "points-per-game, fixtures, availability and value."
    )

    search = st.text_input(
        "Search for a player"
    )

    filtered = players

    if search:

        filtered = [
            p for p in players
            if search.lower()
            in p["name"].lower()
            or search.lower()
            in p["team"].lower()
        ]

    filtered.sort(
        key=lambda p:
        p["optimisation_score"],
        reverse=True
    )

    st.dataframe(
        player_dataframe(filtered[:100]),
        use_container_width=True,
        hide_index=True
    )


# ------------------------------------------------------------
# FOOTER
# ------------------------------------------------------------

st.divider()

st.caption(
    "FPL Assistant Manager uses official Fantasy Premier League "
    "data. Projections are estimates, not guarantees."
)

st.caption(
    "Data refreshes automatically and is cached temporarily "
    "to keep the app fast."
)
