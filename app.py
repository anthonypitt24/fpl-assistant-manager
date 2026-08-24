import streamlit as st
import requests
import pandas as pd
from collections import defaultdict

# ============================================================
# FPL ASSISTANT MANAGER
# Fast version - no expensive Starting XI optimiser
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)

API = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0"}
BUDGET = 100.0
FDR_HORIZON = 5


# ============================================================
# DATA LOADERS
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def get_bootstrap():

    r = requests.get(
        f"{API}/bootstrap-static/",
        headers=HEADERS,
        timeout=20
    )

    r.raise_for_status()

    return r.json()


@st.cache_data(ttl=900, show_spinner=False)
def get_fixtures():

    r = requests.get(
        f"{API}/fixtures/",
        headers=HEADERS,
        timeout=20
    )

    r.raise_for_status()

    return r.json()


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_picks(entry_id, event_id):

    r = requests.get(
        f"{API}/entry/{entry_id}/event/{event_id}/picks/",
        headers=HEADERS,
        timeout=20
    )

    r.raise_for_status()

    return r.json()


@st.cache_data(ttl=900, show_spinner=False)
def get_entry_info(entry_id):

    r = requests.get(
        f"{API}/entry/{entry_id}/",
        headers=HEADERS,
        timeout=20
    )

    r.raise_for_status()

    return r.json()


# ============================================================
# LOAD DATA
# ============================================================

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

teams = {
    t["id"]: t
    for t in data["teams"]
}

position_names = {
    1: "GK",
    2: "DEF",
    3: "MID",
    4: "FWD"
}

events = data["events"]

current_event = next(
    (
        e for e in events
        if e.get("is_current")
    ),
    None
)

next_event = next(
    (
        e for e in events
        if e.get("is_next")
    ),
    None
)

current_gw = (
    current_event["id"]
    if current_event
    else 1
)

upcoming_gw = (
    next_event["id"]
    if next_event
    else current_gw + 1
)


# ============================================================
# SAFE NUMBER
# ============================================================

def safe_float(value, default=0.0):

    try:

        return (
            float(value)
            if value is not None
            else default
        )

    except (
        ValueError,
        TypeError
    ):

        return default


# ============================================================
# FIXTURE ENGINE
# ============================================================

def build_team_fixture_map(
    fixtures,
    from_gw,
    horizon
):

    fmap = defaultdict(list)

    for fx in fixtures:

        gw = fx.get("event")

        if gw is None:
            continue

        if gw < from_gw:
            continue

        if gw >= from_gw + horizon:
            continue

        fmap[
            fx["team_h"]
        ].append(
            {
                "gw": gw,
                "difficulty":
                    fx["team_h_difficulty"],
                "home": True,
                "opp": fx["team_a"]
            }
        )

        fmap[
            fx["team_a"]
        ].append(
            {
                "gw": gw,
                "difficulty":
                    fx["team_a_difficulty"],
                "home": False,
                "opp": fx["team_h"]
            }
        )

    return fmap


team_fixture_map = build_team_fixture_map(
    fixtures,
    upcoming_gw,
    FDR_HORIZON
)


def team_fdr_score(team_id):

    fx = team_fixture_map.get(
        team_id,
        []
    )

    if not fx:
        return 3.0

    return sum(
        f["difficulty"]
        for f in fx
    ) / len(fx)


def next_gw_fixture_count(
    team_id,
    gw
):

    return sum(
        1
        for f
        in team_fixture_map.get(
            team_id,
            []
        )
        if f["gw"] == gw
    )


def team_fixture_string(
    team_id,
    n=3
):

    fx = sorted(
        team_fixture_map.get(
            team_id,
            []
        ),
        key=lambda f: f["gw"]
    )[:n]

    parts = []

    for f in fx:

        opp = teams.get(
            f["opp"],
            {}
        ).get(
            "short_name",
            "?"
        )

        location = (
            "H"
            if f["home"]
            else "A"
        )

        parts.append(
            f"{opp}({location}) "
            f"[{f['difficulty']}]"
        )

    return (
        ", ".join(parts)
        if parts
        else "No fixtures"
    )


# ============================================================
# BUILD PLAYER DATABASE
# ============================================================

players = []

for p in data["elements"]:

    position = position_names[
        p["element_type"]
    ]

    team = teams.get(
        p["team"],
        {}
    )

    price = (
        p["now_cost"] / 10
    )

    total_points = p.get(
        "total_points",
        0
    )

    ppg = safe_float(
        p.get("points_per_game")
    )

    form = safe_float(
        p.get("form")
    )

    minutes = p.get(
        "minutes",
        0
    )

    chance = p.get(
        "chance_of_playing_next_round"
    )

    if chance is None:
        chance = 100

    ep_next = safe_float(
        p.get("ep_next")
    )

    xgi90 = safe_float(
        p.get(
            "expected_goal_involvements_per_90"
        )
    )

    xgc90 = safe_float(
        p.get(
            "expected_goals_conceded_per_90"
        )
    )

    ict = safe_float(
        p.get("ict_index")
    )

    fixtures_next_gw = (
        next_gw_fixture_count(
            p["team"],
            upcoming_gw
        )
    )

    players.append(
        {
            "id":
                p["id"],

            "name":
                p["web_name"],

            "full_name":
                f"{p['first_name']} "
                f"{p['second_name']}",

            "position":
                position,

            "team_id":
                p["team"],

            "team":
                team.get(
                    "short_name",
                    "?"
                ),

            "price":
                price,

            "points":
                total_points,

            "ppg":
                ppg,

            "form":
                form,

            "minutes":
                minutes,

            "chance":
                chance,

            "ownership":
                safe_float(
                    p.get(
                        "selected_by_percent"
                    )
                ),

            "status":
                p.get(
                    "status",
                    "a"
                ),

            "news":
                p.get(
                    "news",
                    ""
                ),

            "ep_next":
                ep_next,

            "xgi90":
                xgi90,

            "xgc90":
                xgc90,

            "ict":
                ict,

            "fdr":
                team_fdr_score(
                    p["team"]
                ),

            "fixtures_next_gw":
                fixtures_next_gw,

            "fixture_str":
                team_fixture_string(
                    p["team"]
                ),

            "transfers_in_event":
                p.get(
                    "transfers_in_event",
                    0
                ),

            "transfers_out_event":
                p.get(
                    "transfers_out_event",
                    0
                ),

            "cost_change_event":
                p.get(
                    "cost_change_event",
                    0
                )
        }
    )


player_by_id = {
    p["id"]: p
    for p in players
}


# ============================================================
# PLAYER AVAILABILITY
# ============================================================

def usable(p):

    if p["status"] not in [
        "a",
        "d"
    ]:
        return False

    if p["chance"] <= 0:
        return False

    if p["price"] <= 0:
        return False

    return True


usable_players = [
    p
    for p in players
    if usable(p)
]


# ============================================================
# MANAGER SCORE
# ============================================================

def manager_score(
    p,
    fixture_weight=1.0
):

    """
    IMPORTANT:

    This is NOT FPL points.

    Actual FPL points remain completely separate.

    Manager Score combines:

    - Actual season points
    - Points per game
    - Recent form
    - Official FPL expected points
    - Fixture difficulty
    - Availability
    - DGW/BGW
    """

    actual_points = p["points"]

    ppg_component = min(
        p["ppg"] * 2,
        15
    )

    form_component = min(
        p["form"],
        10
    )

    availability_component = (
        p["chance"] / 100
    ) * 5

    expected_component = (
        p["ep_next"] * 3
    )

    fixture_component = (
        (3.0 - p["fdr"])
        * 2
        * fixture_weight
    )

    blank_penalty = (
        -50
        if p["fixtures_next_gw"] == 0
        else 0
    )

    double_bonus = (
        8
        if p["fixtures_next_gw"] >= 2
        else 0
    )

    return (
        actual_points
        + ppg_component
        + form_component
        + availability_component
        + expected_component
        + fixture_component
        + blank_penalty
        + double_bonus
    )


# ============================================================
# BEST £100M TEAM BUILDER
# ============================================================

def build_best_team(
    fixture_weight=1.0
):

    def rank(p):

        return manager_score(
            p,
            fixture_weight
        )

    limits = {
        "GK": 10,
        "DEF": 25,
        "MID": 30,
        "FWD": 20
    }

    required = {
        "GK": 2,
        "DEF": 5,
        "MID": 5,
        "FWD": 3
    }

    by_position = {}

    for pos in required:

        pool = [
            p
            for p in usable_players
            if p["position"] == pos
        ]

        pool.sort(
            key=rank,
            reverse=True
        )

        by_position[pos] = (
            pool[:limits[pos]]
        )

    squad = []

    for pos, amount in required.items():

        squad.extend(
            by_position[pos][:amount]
        )

    def club_counts(team):

        counts = {}

        for p in team:

            counts[p["team_id"]] = (
                counts.get(
                    p["team_id"],
                    0
                ) + 1
            )

        return counts

    def total_cost(team):

        return sum(
            p["price"]
            for p in team
        )

    # --------------------------------------------------------
    # Fix maximum 3 players per club
    # --------------------------------------------------------

    repair_attempts = 0

    while repair_attempts < 50:

        repair_attempts += 1

        counts = club_counts(
            squad
        )

        overloaded = [
            club
            for club, count
            in counts.items()
            if count > 3
        ]

        if not overloaded:
            break

        club = overloaded[0]

        candidates = [
            p
            for p in squad
            if p["team_id"] == club
        ]

        remove = min(
            candidates,
            key=rank
        )

        squad.remove(remove)

        replacement = None

        for candidate in by_position[
            remove["position"]
        ]:

            if candidate["id"] in {
                p["id"]
                for p in squad
            }:
                continue

            test_team = (
                squad
                + [candidate]
            )

            test_counts = club_counts(
                test_team
            )

            if all(
                count <= 3
                for count
                in test_counts.values()
            ):

                replacement = candidate
                break

        if replacement:

            squad.append(
                replacement
            )

    # --------------------------------------------------------
    # Fix £100m budget
    # --------------------------------------------------------

    attempts = 0

    while (
        total_cost(squad)
        > BUDGET
        and attempts < 100
    ):

        attempts += 1

        best_move = None

        current_ids = {
            p["id"]
            for p in squad
        }

        for outgoing in squad:

            candidates = by_position[
                outgoing["position"]
            ]

            for incoming in candidates:

                if incoming["id"] in current_ids:
                    continue

                if (
                    incoming["price"]
                    >= outgoing["price"]
                ):
                    continue

                new_team = [
                    p
                    for p in squad
                    if p["id"]
                    != outgoing["id"]
                ]

                new_team.append(
                    incoming
                )

                if total_cost(
                    new_team
                ) > BUDGET:
                    continue

                counts = club_counts(
                    new_team
                )

                if any(
                    count > 3
                    for count
                    in counts.values()
                ):
                    continue

                score_loss = (
                    rank(outgoing)
                    - rank(incoming)
                )

                money_saved = (
                    outgoing["price"]
                    - incoming["price"]
                )

                efficiency = (
                    money_saved * 10
                    - score_loss
                )

                if (
                    best_move is None
                    or efficiency
                    > best_move["efficiency"]
                ):

                    best_move = {
                        "out":
                            outgoing,

                        "in":
                            incoming,

                        "efficiency":
                            efficiency
                    }

        if best_move is None:
            break

        squad.remove(
            best_move["out"]
        )

        squad.append(
            best_move["in"]
        )

    # --------------------------------------------------------
    # Final validation
    # --------------------------------------------------------

    if len(squad) != 15:
        return []

    if total_cost(squad) > BUDGET:
        return []

    counts = club_counts(
        squad
    )

    if any(
        count > 3
        for count
        in counts.values()
    ):
        return []

    for pos, required_count in required.items():

        actual = sum(
            p["position"] == pos
            for p in squad
        )

        if actual != required_count:
            return []

    return squad


# ============================================================
# TRANSFER ENGINE
# ============================================================

def suggest_transfers(
    my_squad,
    bank,
    free_transfers,
    fixture_weight=1.0,
    max_suggestions=10
):

    """
    Transfer system.

    Calculates:

    - Player improvement
    - Price difference
    - Whether a free transfer is available
    - Whether a -4 hit is required
    - Expected gain
    - Net gain after hit
    """

    my_ids = {
        p["id"]
        for p in my_squad
    }

    suggestions = []

    for out_player in my_squad:

        position = (
            out_player["position"]
        )

        candidates = [
            p
            for p in usable_players
            if (
                p["position"]
                == position
                and p["id"]
                not in my_ids
            )
        ]

        candidates.sort(
            key=lambda p:
            manager_score(
                p,
                fixture_weight
            ),
            reverse=True
        )

        # Only investigate the strongest
        # realistic candidates.
        candidates = candidates[:20]

        for in_player in candidates:

            price_difference = (
                in_player["price"]
                - out_player["price"]
            )

            if price_difference > bank:
                continue

            improvement = (
                manager_score(
                    in_player,
                    fixture_weight
                )
                -
                manager_score(
                    out_player,
                    fixture_weight
                )
            )

            if improvement <= 2:
                continue

            # ------------------------------------------------
            # Is this free?
            # ------------------------------------------------

            if free_transfers >= 1:

                hit = 0

            else:

                hit = 4

            net_gain = (
                improvement
                - hit
            )

            # ------------------------------------------------
            # Break-even logic
            # ------------------------------------------------

            if hit == 4:

                break_even = 4

                worth_hit = (
                    improvement > 4
                )

            else:

                break_even = 0
                worth_hit = True

            suggestions.append(
                {
                    "out":
                        out_player,

                    "in":
                        in_player,

                    "improvement":
                        improvement,

                    "price_difference":
                        price_difference,

                    "hit":
                        hit,

                    "net_gain":
                        net_gain,

                    "break_even":
                        break_even,

                    "worth_hit":
                        worth_hit
                }
            )

    # Sort by actual useful gain,
    # not simply highest player score.

    suggestions.sort(
        key=lambda s:
        s["net_gain"],
        reverse=True
    )

    return suggestions[
        :max_suggestions
    ]


# ============================================================
# BUILD FRESH TEAM
# ============================================================

with st.spinner(
    "Building recommended £100m team..."
):

    fresh_team = build_best_team()


# ============================================================
# HEADER
# ============================================================

st.title(
    "⚽ FPL Assistant Manager"
)

if current_event:

    st.caption(
        f"GW{current_gw} | "
        f"Next: GW{upcoming_gw} | "
        f"Actual points + Manager Score + fixtures"
    )

else:

    st.caption(
        "Actual points + Manager Score + fixtures"
    )


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header(
        "⚙️ Settings"
    )

    mode = st.radio(
        "Mode",
        [
            "Build a fresh £100m team",
            "Load my FPL team"
        ]
    )

    fixture_weight = st.slider(
        "Fixture influence",
        0.0,
        3.0,
        1.0,
        0.5,
        help=(
            "Higher values make upcoming "
            "fixtures more important."
        )
    )

    entry_id = None

    free_transfers = 1

    if mode == "Load my FPL team":

        entry_id = st.text_input(
            "Your FPL Team ID",
            help=(
                "This is the number in your "
                "FPL team URL."
            )
        )

        free_transfers = st.number_input(
            "Free transfers available",
            min_value=0,
            max_value=5,
            value=1
        )


# ============================================================
# FRESH TEAM MODE
# ============================================================

if mode == "Build a fresh £100m team":

    tabs = st.tabs(
        [
            "🏆 Best £100m Team",
            "📊 Player Rankings",
            "📅 Fixtures & FDR"
        ]
    )

    # --------------------------------------------------------
    # BEST TEAM
    # --------------------------------------------------------

    with tabs[0]:

        st.header(
            "🏆 Best £100m Squad"
        )

        st.write(
            "The recommendation uses actual FPL points "
            "as the foundation, then considers form, "
            "PPG, official expected points and fixtures."
        )

        if not fresh_team:

            st.error(
                "I couldn't construct a legal £100m squad."
            )

        else:

            cost = sum(
                p["price"]
                for p in fresh_team
            )

            actual_points = sum(
                p["points"]
                for p in fresh_team
            )

            manager_total = sum(
                manager_score(
                    p,
                    fixture_weight
                )
                for p in fresh_team
            )

            remaining = (
                BUDGET
                - cost
            )

            c1, c2, c3, c4 = st.columns(4)

            c1.metric(
                "Squad Cost",
                f"£{cost:.1f}m"
            )

            c2.metric(
                "Money Left",
                f"£{remaining:.1f}m"
            )

            c3.metric(
                "Actual Points",
                f"{actual_points}"
            )

            c4.metric(
                "Manager Score",
                f"{manager_total:.1f}"
            )

            st.success(
                "Recommended 15-player squad"
            )

            display = []

            for p in fresh_team:

                flag = ""

                if (
                    p["fixtures_next_gw"]
                    == 0
                ):

                    flag = "🚫 BGW"

                elif (
                    p["fixtures_next_gw"]
                    >= 2
                ):

                    flag = "⚡ DGW"

                display.append(
                    {
                        "Player":
                            p["name"],

                        "Club":
                            p["team"],

                        "Pos":
                            p["position"],

                        "Price":
                            f"£{p['price']:.1f}m",

                        "Actual Points":
                            p["points"],

                        "Manager Score":
                            round(
                                manager_score(
                                    p,
                                    fixture_weight
                                ),
                                1
                            ),

                        "PPG":
                            round(
                                p["ppg"],
                                1
                            ),

                        "Form":
                            round(
                                p["form"],
                                1
                            ),

                        "EP Next":
                            round(
                                p["ep_next"],
                                1
                            ),

                        "Fixtures":
                            p["fixture_str"],

                        "Flag":
                            flag,

                        "Ownership":
                            f"{p['ownership']:.1f}%"
                    }
                )

            position_order = {
                "GK": 1,
                "DEF": 2,
                "MID": 3,
                "FWD": 4
            }

            display.sort(
                key=lambda x:
                (
                    position_order[
                        x["Pos"]
                    ],
                    -x["Actual Points"]
                )
            )

            st.dataframe(
                pd.DataFrame(display),
                use_container_width=True,
                hide_index=True
            )

            st.info(
                "💡 Actual Points tells you what a player "
                "has already done. Manager Score is a separate "
                "decision-making score and is NOT FPL points."
            )

    # --------------------------------------------------------
    # PLAYER RANKINGS
    # --------------------------------------------------------

    with tabs[1]:

        st.header(
            "📊 Player Rankings"
        )

        position = st.selectbox(
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

        sort_mode = st.radio(
            "Rank by",
            [
                "Actual FPL Points",
                "Manager Score"
            ],
            horizontal=True
        )

        filtered = (
            usable_players.copy()
        )

        if position != "ALL":

            filtered = [
                p
                for p in filtered
                if p["position"]
                == position
            ]

        if search:

            search_text = (
                search.lower()
            )

            filtered = [
                p
                for p in filtered
                if (
                    search_text
                    in p["name"].lower()
                    or
                    search_text
                    in p["full_name"].lower()
                    or
                    search_text
                    in p["team"].lower()
                )
            ]

        if sort_mode == "Actual FPL Points":

            filtered.sort(
                key=lambda p:
                (
                    p["points"],
                    p["ppg"],
                    p["form"]
                ),
                reverse=True
            )

        else:

            filtered.sort(
                key=lambda p:
                manager_score(
                    p,
                    fixture_weight
                ),
                reverse=True
            )

        rows = []

        for p in filtered[:100]:

            rows.append(
                {
                    "Player":
                        p["name"],

                    "Club":
                        p["team"],

                    "Pos":
                        p["position"],

                    "Price":
                        f"£{p['price']:.1f}m",

                    "Actual Points":
                        p["points"],

                    "Manager Score":
                        round(
                            manager_score(
                                p,
                                fixture_weight
                            ),
                            1
                        ),

                    "PPG":
                        round(
                            p["ppg"],
                            1
                        ),

                    "Form":
                        round(
                            p["form"],
                            1
                        ),

                    "EP Next":
                        round(
                            p["ep_next"],
                            1
                        ),

                    "Minutes":
                        p["minutes"],

                    "Ownership":
                        f"{p['ownership']:.1f}%",

                    "Availability":
                        f"{p['chance']:.0f}%",

                    "Fixtures":
                        p["fixture_str"]
                }
            )

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True
        )

    # --------------------------------------------------------
    # FIXTURES
    # --------------------------------------------------------

    with tabs[2]:

        st.header(
            "📅 Fixtures & FDR"
        )

        st.write(
            f"Average difficulty over the next "
            f"{FDR_HORIZON} gameweeks."
        )

        fdr_rows = []

        for tid, team in teams.items():

            fdr_rows.append(
                {
                    "Team":
                        team["short_name"],

                    "Average FDR":
                        round(
                            team_fdr_score(tid),
                            2
                        ),

                    "Fixtures":
                        team_fixture_string(
                            tid,
                            n=FDR_HORIZON
                        )
                }
            )

        fdr_df = pd.DataFrame(
            fdr_rows
        ).sort_values(
            "Average FDR"
        )

        st.dataframe(
            fdr_df,
            use_container_width=True,
            hide_index=True
        )

        bgw_teams = [
            team["short_name"]
            for tid, team
            in teams.items()
            if next_gw_fixture_count(
                tid,
                upcoming_gw
            ) == 0
        ]

        dgw_teams = [
            team["short_name"]
            for tid, team
            in teams.items()
            if next_gw_fixture_count(
                tid,
                upcoming_gw
            ) >= 2
        ]

        if bgw_teams:

            st.warning(
                f"🚫 Blank GW{upcoming_gw}: "
                + ", ".join(bgw_teams)
            )

        if dgw_teams:

            st.success(
                f"⚡ Double GW{upcoming_gw}: "
                + ", ".join(dgw_teams)
            )


# ============================================================
# MY TEAM MODE
# ============================================================

else:

    tabs = st.tabs(
        [
            "👤 My Team",
            "🔁 Transfer Suggestions",
            "📊 Player Rankings",
            "📅 Fixtures & FDR"
        ]
    )

    # --------------------------------------------------------
    # MY TEAM
    # --------------------------------------------------------

    with tabs[0]:

        st.header(
            "👤 My FPL Team"
        )

        if not entry_id:

            st.info(
                "Enter your FPL Team ID in the sidebar."
            )

        else:

            try:

                info = get_entry_info(
                    entry_id
                )

                picks_data = get_entry_picks(
                    entry_id,
                    current_gw
                )

            except Exception as e:

                st.error(
                    "Couldn't load that team."
                )

                st.code(
                    str(e)
                )

                picks_data = None

            if picks_data:

                bank = (
                    picks_data[
                        "entry_history"
                    ]["bank"]
                    / 10
                )

                team_value = (
                    picks_data[
                        "entry_history"
                    ]["value"]
                    / 10
                )

                overall_points = (
                    picks_data[
                        "entry_history"
                    ]["total_points"]
                )

                gw_points = (
                    picks_data[
                        "entry_history"
                    ]["points"]
                )

                c1, c2, c3, c4 = st.columns(4)

                c1.metric(
                    "Team",
                    info.get(
                        "name",
                        "-"
                    )
                )

                c2.metric(
                    "Overall Points",
                    overall_points
                )

                c3.metric(
                    "Team Value",
                    f"£{team_value:.1f}m"
                )

                c4.metric(
                    "Bank",
                    f"£{bank:.1f}m"
                )

                my_squad = []

                pick_info = {
                    pk["element"]:
                    pk
                    for pk
                    in picks_data["picks"]
                }

                for pk in picks_data["picks"]:

                    p = player_by_id.get(
                        pk["element"]
                    )

                    if p:

                        my_squad.append(p)

                display = []

                for p in my_squad:

                    pk = pick_info[
                        p["id"]
                    ]

                    role = ""

                    if pk[
                        "is_captain"
                    ]:

                        role = "C"

                    elif pk[
                        "is_vice_captain"
                    ]:

                        role = "VC"

                    flag = ""

                    if (
                        p["fixtures_next_gw"]
                        == 0
                    ):

                        flag = "🚫 BGW"

                    elif (
                        p["fixtures_next_gw"]
                        >= 2
                    ):

                        flag = "⚡ DGW"

                    display.append(
                        {
                            "Player":
                                p 
