import streamlit as st
import requests
import itertools
from collections import defaultdict

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)

BASE = "https://fantasy.premierleague.com/api"
TIMEOUT = 20


# ============================================================
# FPL DATA
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def get_json(path):
    response = requests.get(
        f"{BASE}{path}",
        timeout=TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=900, show_spinner=False)
def load_data():

    bootstrap = get_json("/bootstrap-static/")
    fixtures = get_json("/fixtures/")

    teams = {
        team["id"]: team
        for team in bootstrap["teams"]
    }

    element_types = {
        item["id"]: item
        for item in bootstrap["element_types"]
    }

    finished_gameweeks = sum(
        1 for event in bootstrap["events"]
        if event.get("finished")
    )

    players = []

    for player in bootstrap["elements"]:

        position = element_types[
            player["element_type"]
        ]["singular_name_short"]

        team_name = teams[
            player["team"]
        ]["short_name"]

        try:
            ep_next = float(player.get("ep_next") or 0)
        except:
            ep_next = 0.0

        try:
            form = float(player.get("form") or 0)
        except:
            form = 0.0

        try:
            ppg = float(
                player.get("points_per_game") or 0
            )
        except:
            ppg = 0.0

        chance = player.get(
            "chance_of_playing_next_round"
        )

        if chance is None:
            chance = 100
        else:
            chance = float(chance)

        # FPL's own next-gameweek projection is our
        # main projection figure.
        if ep_next > 0:
            projection = ep_next
        else:
            # Fallback for players where FPL has not
            # produced a useful projection yet.
            projection = (
                ppg * 0.60
                + form * 0.30
                + (
                    player["total_points"]
                    / max(1, finished_gameweeks)
                ) * 0.10
            )

        players.append({

            "id": player["id"],

            "name":
                f'{player["first_name"]} '
                f'{player["second_name"]}',

            "pos": position,

            "team_id":
                player["team"],

            "team":
                team_name,

            "price":
                player["now_cost"] / 10,

            "projection":
                projection,

            "ep_next":
                ep_next,

            "form":
                form,

            "ppg":
                ppg,

            "total_points":
                player["total_points"],

            "selected":
                float(
                    player.get(
                        "selected_by_percent"
                    ) or 0
                ),

            "chance":
                chance,

            "status":
                player.get("status", "a"),

            "news":
                player.get("news") or "",

            "transfers_in":
                player.get(
                    "transfers_in_event", 0
                ),

            "transfers_out":
                player.get(
                    "transfers_out_event", 0
                )
        })

    return (
        bootstrap,
        fixtures,
        teams,
        players
    )


# ============================================================
# PLAYER ELIGIBILITY
# ============================================================

def eligible(player):

    return (
        player["status"] in {"a", "d"}
        and player["chance"] > 0
        and player["price"] > 0
    )


def player_score(player):

    return player["projection"]


# ============================================================
# FAST £100M DREAM TEAM OPTIMISER
# ============================================================

def fast_best_squad(players, budget=100.0):

    """
    Finds a very strong legal FPL 15-player squad
    without brute-forcing every possible squad.

    Rules:

    2 GKs
    5 DEFs
    5 MIDs
    3 FWDs
    £100m maximum
    Maximum 3 players from one club

    Uses a beam-search optimiser so it remains fast.
    """

    quotas = {
        "GKP": 2,
        "DEF": 5,
        "MID": 5,
        "FWD": 3
    }

    # Only keep the strongest projected players at
    # each position. This dramatically reduces the
    # search space.
    candidate_limits = {
        "GKP": 12,
        "DEF": 25,
        "MID": 30,
        "FWD": 22
    }

    players_by_position = defaultdict(list)

    for player in players:

        if eligible(player):

            players_by_position[
                player["pos"]
            ].append(player)

    for position in quotas:

        players_by_position[position].sort(
            key=lambda p: (
                p["projection"],
                p["total_points"]
            ),
            reverse=True
        )

        players_by_position[position] = (
            players_by_position[position]
            [:candidate_limits[position]]
        )

    # Convert £m to tenths of a million.
    budget_t = int(
        round(budget * 10)
    )

    # Clubs used by the players.
    clubs = sorted(
        {
            p["team_id"]
            for p in players
        }
    )

    club_index = {
        club: index
        for index, club in enumerate(clubs)
    }

    # State:
    #
    # spent
    # position counts
    # club counts
    # player IDs
    #
    # value = projected points

    states = {
        (
            0,
            (0, 0, 0, 0),
            (0,) * len(clubs),
            ()
        ): 0.0
    }

    # This is the important part:
    # instead of checking millions of complete
    # squads, we keep only the strongest partial
    # squads after each player selection.

    beam_size = 6000

    selection_order = [
        ("GKP", 2),
        ("DEF", 5),
        ("MID", 5),
        ("FWD", 3)
    ]

    for position, quantity in selection_order:

        for _ in range(quantity):

            new_states = {}

            pool = players_by_position[position]

            for (
                spent,
                position_counts,
                club_counts,
                selected_ids
            ), current_points in states.items():

                already_selected = set(
                    selected_ids
                )

                for player in pool:

                    if player["id"] in already_selected:
                        continue

                    cost = int(
                        round(
                            player["price"] * 10
                        )
                    )

                    new_spent = (
                        spent + cost
                    )

                    if new_spent > budget_t:
                        continue

                    club_id = player["team_id"]

                    club_number = (
                        club_index[club_id]
                    )

                    # Maximum 3 players from
                    # one Premier League club.
                    if (
                        club_counts[
                            club_number
                        ] >= 3
                    ):
                        continue

                    new_position_counts = list(
                        position_counts
                    )

                    position_number = [
                        "GKP",
                        "DEF",
                        "MID",
                        "FWD"
                    ].index(position)

                    new_position_counts[
                        position_number
                    ] += 1

                    new_club_counts = list(
                        club_counts
                    )

                    new_club_counts[
                        club_number
                    ] += 1

                    state_key = (
                        new_spent,
                        tuple(
                            new_position_counts
                        ),
                        tuple(
                            new_club_counts
                        )
                    )

                    new_points = (
                        current_points
                        + player_score(player)
                    )

                    previous = new_states.get(
                        state_key
                    )

                    if (
                        previous is None
                        or new_points > previous[0]
                    ):

                        new_states[
                            state_key
                        ] = (
                            new_points,
                            selected_ids
                            + (player["id"],)
                        )

            # Keep only the strongest partial squads.
            ranked = sorted(
                new_states.items(),
                key=lambda item:
                    item[1][0],
                reverse=True
            )[:beam_size]

            states = {}

            for (
                spent,
                position_counts,
                club_counts
            ), (
                points,
                selected_ids
            ) in ranked:

                states[
                    (
                        spent,
                        position_counts,
                        club_counts,
                        selected_ids
                    )
                ] = points

            if not states:

                return [], 0.0, 0.0

    # Best final squad.
    best_state = max(
        states.items(),
        key=lambda item: item[1]
    )

    best_key, best_points = best_state

    (
        spent,
        position_counts,
        club_counts,
        selected_ids
    ) = best_key

    player_lookup = {
        player["id"]: player
        for player in players
    }

    squad = [
        player_lookup[player_id]
        for player_id in selected_ids
    ]

    return (
        squad,
        best_points,
        spent / 10
    )


# ============================================================
# LOAD LIVE DATA
# ============================================================

try:

    (
        bootstrap,
        fixtures,
        teams,
        players
    ) = load_data()

except Exception as error:

    st.error(
        "I couldn't load the live FPL data."
    )

    st.code(str(error))

    st.stop()


# ============================================================
# GAMEWEEK STATUS
# ============================================================

current_gameweek = next(
    (
        event
        for event in bootstrap["events"]
        if event.get("is_current")
    ),
    None
)

next_gameweek = next(
    (
        event
        for event in bootstrap["events"]
        if event.get("is_next")
    ),
    None
)

finished_gameweeks = sum(
    1
    for event in bootstrap["events"]
    if event.get("finished")
)


# ============================================================
# HEADER
# ============================================================

st.title(
    "⚽ FPL Assistant Manager"
)

if current_gameweek:

    status_text = (
        f"Gameweek "
        f"{current_gameweek['id']}"
        f" active"
    )

else:

    status_text = "FPL data loaded"

if next_gameweek:

    status_text += (
        f" | Planning for GW "
        f"{next_gameweek['id']}"
    )

st.caption(
    status_text
    + f" | {len(players)} players loaded"
    + f" | {finished_gameweeks} GW(s) completed"
)


# ============================================================
# REFRESH BUTTON
# ============================================================

if st.button(
    "🔄 Refresh FPL data"
):

    st.cache_data.clear()

    st.rerun()


# ============================================================
# TABS
# ============================================================

(
    planner_tab,
    dream_tab,
    transfer_tab,
    scoring_tab
) = st.tabs(
    [
        "📋 Player Planner",
        "💰 £100m Dream Team",
        "🔄 Transfers",
        "📚 FPL Scoring"
    ]
)


# ============================================================
# PLAYER PLANNER
# ============================================================

with planner_tab:

    st.subheader(
        "Player Planner"
    )

    col1, col2, col3 = st.columns(3)

    with col1:

        position_filter = st.selectbox(
            "Position",
            [
                "All",
                "GKP",
                "DEF",
                "MID",
                "FWD"
            ]
        )

    with col2:

        maximum_price = st.number_input(
            "Maximum price (£m)",
            min_value=3.5,
            max_value=15.0,
            value=15.0,
            step=0.1
        )

    with col3:

        minimum_projection = st.number_input(
            "Minimum projected points",
            min_value=0.0,
            max_value=20.0,
            value=0.0,
            step=0.5
        )

    filtered_players = []

    for player in players:

        if (
            position_filter != "All"
            and player["pos"]
            != position_filter
        ):
            continue

        if (
            player["price"]
            > maximum_price
        ):
            continue

        if (
            player["projection"]
            < minimum_projection
        ):
            continue

        filtered_players.append(
            player
        )

    filtered_players.sort(
        key=lambda p:
            p["projection"],
        reverse=True
    )

    rows = []

    for player in filtered_players[:100]:

        rows.append(
            {
                "Player":
                    player["name"],

                "Pos":
                    player["pos"],

                "Club":
                    player["team"],

                "Price":
                    f"£{player['price']:.1f}m",

                "Projected":
                    round(
                        player["projection"],
                        1
                    ),

                "PPG":
                    round(
                        player["ppg"],
                        1
                    ),

                "Form":
                    round(
                        player["form"],
                        1
                    ),

                "Total":
                    player["total_points"],

                "Start chance":
                    f"{player['chance']:.0f}%"
            }
        )

    st.dataframe(
        rows,
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# DREAM TEAM
# ============================================================

with dream_tab:

    st.subheader(
        "💰 £100m Best Possible Team"
    )

    st.write(
        "This searches for the strongest projected "
        "15-player squad without brute-forcing "
        "every possible combination."
    )

    st.info(
        "Rules: £100m maximum • "
        "2 GKs • 5 DEFs • 5 MIDs • 3 FWDs • "
        "maximum 3 players from one club."
    )

    if st.button(
        "🚀 Find best £100m squad",
        type="primary"
    ):

        with st.spinner(
            "Finding the best £100m squad..."
        ):

            (
                dream_squad,
                total_projection,
                total_cost
            ) = fast_best_squad(
                players,
                100.0
            )

        if not dream_squad:

            st.error(
                "No legal £100m squad was found."
            )

        else:

            st.session_state[
                "dream_squad"
            ] = dream_squad

            st.session_state[
                "dream_total"
            ] = total_projection

            st.session_state[
                "dream_cost"
            ] = total_cost

    if (
        "dream_squad"
        in st.session_state
    ):

        dream_squad = (
            st.session_state[
                "dream_squad"
            ]
        )

        total_projection = (
            st.session_state[
                "dream_total"
            ]
        )

        total_cost = (
            st.session_state[
                "dream_cost"
            ]
        )

        col1, col2 = st.columns(2)

        with col1:

            st.metric(
                "Projected squad points",
                f"{total_projection:.1f}"
            )

        with col2:

            st.metric(
                "Squad cost",
                f"£{total_cost:.1f}m"
            )

        rows = []

        position_order = [
            "GKP",
            "DEF",
            "MID",
            "FWD"
        ]

        dream_squad.sort(
            key=lambda player:
                position_order.index(
                    player["pos"]
                )
        )

        for player in dream_squad:

            rows.append(
                {
                    "Player":
                        player["name"],

                    "Pos":
                        player["pos"],

                    "Club":
                        player["team"],

                    "Price":
                        f"£{player['price']:.1f}m",

                    "Projected":
                        round(
                            player["projection"],
                            1
                        ),

                    "Total":
                        player["total_points"]
                }
            )

        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True
        )

        club_counts = defaultdict(int)

        for player in dream_squad:

            club_counts[
                player["team"]
            ] += 1

        st.caption(
            "Club limits: "
            + " • ".join(
                f"{club} {count}"
                for club, count
                in sorted(
                    club_counts.items()
                )
            )
        )

        st.success(
            "Done — the fast optimiser has "
            "finished searching the squad."
        )

    else:

        st.warning(
            "Press the button above to "
            "build the £100m squad."
        )


# ============================================================
# TRANSFERS
# ============================================================

with transfer_tab:

    st.subheader(
        "🔄 Transfer Planner"
    )

    st.write(
        "Enter your FPL Team ID to load your "
        "FPL manager information."
    )

    team_id = st.number_input(
        "FPL Team ID",
        min_value=1,
        value=1,
        step=1
    )

    if st.button(
        "Analyse my team"
    ):

        try:

            entry = get_json(
                f"/entry/{team_id}/"
            )

            history = get_json(
                f"/entry/{team_id}/history/"
            )

            st.session_state[
                "entry"
            ] = entry

            st.session_state[
                "history"
            ] = history

            st.success(
                "FPL team loaded."
            )

        except Exception as error:

            st.error(
                "Couldn't find that FPL team ID."
            )

            st.code(
                str(error)
            )

    if (
        "entry"
        in st.session_state
    ):

        entry = (
            st.session_state[
                "entry"
            ]
        )

        st.write(
            f"**{entry.get('name', 'Your team')}**"
        )

        rank = entry.get(
            "summary_overall_rank"
        )

        if rank:

            st.write(
                f"Overall rank: "
                f"{rank:,}"
            )

        st.info(
            "Transfer logic: the assistant "
            "should consider the expected points "
            "gain from a move, the player's price, "
            "fixture outlook and whether selling an "
            "expensive player frees money for a "
            "better combination elsewhere."
        )

        st.write(
            "A -4 hit should only be recommended "
            "when the expected gain from the move "
            "is strong enough to justify losing "
            "four points."
        )


# ============================================================
# FPL SCORING
# ============================================================

with scoring_tab:

    st.subheader(
        "📚 FPL Scoring"
    )

    st.write(
        "The current model follows the current "
        "FPL scoring system."
    )

    scoring = [

        (
            "Playing up to 60 minutes",
            "+1"
        ),

        (
            "Playing 60 minutes or more",
            "+2"
        ),

        (
            "Goal — goalkeeper",
            "+10"
        ),

        (
            "Goal — defender",
            "+6"
        ),

        (
            "Goal — midfielder",
            "+5"
        ),

        (
            "Goal — forward",
            "+4"
        ),

        (
            "Assist",
            "+3"
        ),

        (
            "Clean sheet — goalkeeper/defender",
            "+4"
        ),

        (
            "Clean sheet — midfielder",
            "+1"
        ),

        (
            "Every 3 goalkeeper saves",
            "+1"
        ),

        (
            "Penalty save",
            "+5"
        ),

        (
            "Defensive contributions — defender",
            "+2"
        ),

        (
            "Defensive contributions — midfielder/forward",
            "+2"
        ),

        (
            "Penalty miss",
            "-2"
        ),

        (
            "Bonus",
            "+1 to +3"
        ),

        (
            "Every 2 goals conceded — GK/DEF",
            "-1"
        ),

        (
            "Yellow card",
            "-1"
        ),

        (
            "Red card",
            "-3"
        ),

        (
            "Own goal",
            "-2"
        )
    ]

    st.table(
        {
            "Event":
                [item[0] for item in scoring],

            "Points":
                [item[1] for item in scoring]
        }
    )

    st.caption(
        "The 60-minute appearance threshold, "
        "defensive-contribution points and "
        "Bonus Points System are included in "
        "the current FPL rules."
    )
