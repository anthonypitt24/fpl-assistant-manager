import streamlit as st
import pandas as pd
import requests

# ============================================================
# FPL ASSISTANT MANAGER
# Single-file version
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide",
)

API = "https://fantasy.premierleague.com/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 FPL Assistant Manager",
    "Accept": "application/json",
}


# ============================================================
# API FUNCTIONS
# ============================================================

@st.cache_data(ttl=1800, show_spinner=False)
def get_json(endpoint):
    try:
        response = requests.get(
            f"{API}/{endpoint}",
            headers=HEADERS,
            timeout=15,
        )

        if response.status_code == 200:
            return response.json()

    except requests.RequestException:
        pass

    return None


@st.cache_data(ttl=300, show_spinner=False)
def get_user_picks(team_id, target_gw):

    for gw in range(target_gw, 0, -1):

        data = get_json(
            f"entry/{team_id}/event/{gw}/picks/"
        )

        if data:
            return data, gw

    return None, 0


@st.cache_data(ttl=300, show_spinner=False)
def get_league(league_id):

    return get_json(
        f"leagues-classic/{league_id}/standings/"
        "?page_standings=1&page_new_entries=1&phase=1"
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_fixtures():

    return get_json("fixtures/")


# ============================================================
# LOAD FPL DATA
# ============================================================

bootstrap = get_json("bootstrap-static/")
fixtures = get_fixtures()

if not bootstrap:

    st.error(
        "Unable to load FPL data. "
        "Please refresh the page in a few seconds."
    )

    st.stop()


events = bootstrap.get("events", [])
elements_raw = bootstrap.get("elements", [])
teams_raw = bootstrap.get("teams", [])
element_types_raw = bootstrap.get("element_types", [])


elements = {
    player["id"]: player
    for player in elements_raw
}


teams = {
    team["id"]: team
    for team in teams_raw
}


team_names = {
    team["id"]: team["short_name"]
    for team in teams_raw
}


positions = {
    position["id"]: position["singular_name_short"]
    for position in element_types_raw
}


event_map = {
    event["id"]: event
    for event in events
}


current_gw = next(
    (
        event["id"]
        for event in events
        if event.get("is_current")
    ),
    None,
)


next_gw = next(
    (
        event["id"]
        for event in events
        if event.get("is_next")
    ),
    None,
)


# Pre-season fallback
if current_gw is None:

    next_gw = next_gw or 1

    current_gw = max(
        1,
        next_gw - 1
    )


if next_gw is None:

    next_gw = min(
        current_gw + 1,
        len(events)
    )


# ============================================================
# GENERAL HELPERS
# ============================================================

def number(player, key, default=0.0):

    try:
        return float(
            player.get(key, default) or default
        )

    except (TypeError, ValueError):

        return float(default)


def availability(player):

    chance = player.get(
        "chance_of_playing_next_round"
    )

    if chance is None:

        if player.get("status") == "a":
            return 1.0

        return 0.0

    return max(
        0.0,
        min(
            1.0,
            float(chance) / 100
        )
    )


# ============================================================
# FIXTURE ENGINE
# ============================================================

def fixture_rating(team_id, gw, horizon=1):

    """
    FPL difficulty:

    1 = very easy
    2 = easy
    3 = average
    4 = difficult
    5 = very difficult
    """

    if not fixtures:
        return 3.0

    ratings = []

    end_gw = min(
        gw + horizon,
        len(events) + 1
    )

    for fixture in fixtures:

        event = fixture.get("event")

        if event not in range(gw, end_gw):
            continue

        if fixture.get("team_h") == team_id:

            ratings.append(
                number(
                    fixture,
                    "team_h_difficulty",
                    3
                )
            )

        elif fixture.get("team_a") == team_id:

            ratings.append(
                number(
                    fixture,
                    "team_a_difficulty",
                    3
                )
            )

    if not ratings:
        return 3.0

    return sum(ratings) / len(ratings)


def fixture_multiplier(team_id, gw):

    difficulty = fixture_rating(
        team_id,
        gw,
        1
    )

    multiplier = (
        1.10 -
        ((difficulty - 3.0) * 0.10)
    )

    return max(
        0.82,
        min(
            1.20,
            multiplier
        )
    )


def fixture_text(team_id, gw):

    if not fixtures:
        return "Unavailable"

    upcoming = []

    for fixture in fixtures:

        if fixture.get("event") != gw:
            continue

        if fixture.get("team_h") == team_id:

            opponent = team_names.get(
                fixture.get("team_a"),
                "?"
            )

            upcoming.append(
                f"{opponent} (H)"
            )

        elif fixture.get("team_a") == team_id:

            opponent = team_names.get(
                fixture.get("team_h"),
                "?"
            )

            upcoming.append(
                f"{opponent} (A)"
            )

    if not upcoming:
        return "No fixture"

    return ", ".join(upcoming)


# ============================================================
# PLAYER PROJECTION ENGINE
# ============================================================

def player_projection(player):

    """
    Blended projection model.

    Uses:

    50% FPL ep_next
    16% form
    10% PPG
    expected goal involvement
    ICT
    minutes / starts
    availability
    fixture difficulty
    """

    ep_next = number(
        player,
        "ep_next"
    )

    form = number(
        player,
        "form"
    )

    ppg = number(
        player,
        "points_per_game"
    )

    minutes = number(
        player,
        "minutes"
    )

    starts = number(
        player,
        "starts"
    )

    xgi = number(
        player,
        "expected_goal_involvements"
    )

    xgi90 = number(
        player,
        "expected_goal_involvements_per_90"
    )

    ict = number(
        player,
        "ict_index"
    )

    # Base projection
    projection = (
        ep_next * 0.50
        + form * 0.16
        + ppg * 0.10
    )

    # Expected attacking involvement
    if xgi90 > 0:

        projection += min(
            1.5,
            xgi90 * 0.45
        )

    elif xgi > 0:

        projection += min(
            1.0,
            xgi * 0.06
        )

    # ICT contribution
    projection += min(
        0.60,
        ict / 100
    )

    # Minutes reliability
    if minutes >= 900:

        projection += 0.25

    elif minutes >= 450:

        projection += 0.10

    elif minutes < 180:

        projection -= 0.35

    # Starting reliability
    if starts > 0 and minutes > 0:

        expected_starts = minutes / 90

        start_rate = min(
            1.0,
            starts / max(
                1.0,
                expected_starts
            )
        )

        projection += (
            start_rate - 0.70
        ) * 0.60

    # Availability
    projection *= availability(player)

    # Fixture
    projection *= fixture_multiplier(
        player["team"],
        next_gw
    )

    return round(
        max(
            0.0,
            projection
        ),
        2
    )


# ============================================================
# PLAYER DATABASE
# ============================================================

def build_player_dataframe():

    rows = []

    for player in elements_raw:

        chance = player.get(
            "chance_of_playing_next_round"
        )

        if chance is None:

            chance_percent = (
                100
                if player.get("status") == "a"
                else 0
            )

        else:

            chance_percent = chance

        xp = player_projection(player)

        rows.append({

            "ID":
                player["id"],

            "Name":
                player["web_name"],

            "Team":
                team_names.get(
                    player["team"],
                    "?"
                ),

            "Team ID":
                player["team"],

            "Pos":
                positions.get(
                    player["element_type"],
                    "?"
                ),

            "Pos ID":
                player["element_type"],

            "Cost":
                number(
                    player,
                    "now_cost"
                ) / 10,

            "Form":
                number(
                    player,
                    "form"
                ),

            "PPG":
                number(
                    player,
                    "points_per_game"
                ),

            "xP":
                xp,

            "FPL xP":
                number(
                    player,
                    "ep_next"
                ),

            "xGI":
                number(
                    player,
                    "expected_goal_involvements"
                ),

            "ICT":
                number(
                    player,
                    "ict_index"
                ),

            "Minutes":
                number(
                    player,
                    "minutes"
                ),

            "Starts":
                number(
                    player,
                    "starts"
                ),

            "Owned %":
                number(
                    player,
                    "selected_by_percent"
                ),

            "Transfers In":
                number(
                    player,
                    "transfers_in_event"
                ),

            "Transfers Out":
                number(
                    player,
                    "transfers_out_event"
                ),

            "Status":
                player.get(
                    "status",
                    ""
                ),

            "Chance":
                chance_percent,

        })

    return pd.DataFrame(rows)


df_all = build_player_dataframe()


# ============================================================
# HOLD / SELL ENGINE
# ============================================================

def player_verdict(row):

    if (
        row["Status"] != "a"
        or row["Chance"] < 75
    ):

        return "🔴 SELL / REPLACE"

    if (
        row["xP"] < 2.5
        and row["Form"] < 3
    ):

        return "🔴 SELL"

    if row["xP"] >= 5.5:

        return "🟢 STRONG HOLD"

    if row["xP"] >= 4.2:

        return "🟢 HOLD"

    return "🟡 MONITOR"


# ============================================================
# SQUAD BUILDER
# ============================================================

def build_squad_dataframe(picks):

    rows = []

    for pick in picks:

        player = elements.get(
            pick["element"]
        )

        if not player:
            continue

        matching = df_all[
            df_all["ID"]
            == player["id"]
        ]

        if matching.empty:
            continue

        row = matching.iloc[0].to_dict()

        row["Position"] = pick.get(
            "position"
        )

        row["Multiplier"] = pick.get(
            "multiplier",
            1
        )

        row["Captain"] = pick.get(
            "is_captain",
            False
        )

        row["Vice"] = pick.get(
            "is_vice_captain",
            False
        )

        row["Verdict"] = player_verdict(
            pd.Series(row)
        )

        rows.append(row)

    return pd.DataFrame(rows)


# ============================================================
# BEST XI ENGINE
# ============================================================

def best_starting_xi(squad):

    gks = squad[
        squad["Pos"] == "GKP"
    ].sort_values(
        "xP",
        ascending=False
    )

    defs = squad[
        squad["Pos"] == "DEF"
    ].sort_values(
        "xP",
        ascending=False
    )

    mids = squad[
        squad["Pos"] == "MID"
    ].sort_values(
        "xP",
        ascending=False
    )

    fwds = squad[
        squad["Pos"] == "FWD"
    ].sort_values(
        "xP",
        ascending=False
    )

    best = None
    best_score = -999

    # Legal formations
    for defenders in range(3, 6):

        for midfielders in range(2, 6):

            forwards = (
                10
                - defenders
                - midfielders
            )

            if forwards < 1:
                continue

            if forwards > 3:
                continue

            if len(defs) < defenders:
                continue

            if len(mids) < midfielders:
                continue

            if len(fwds) < forwards:
                continue

            eleven = pd.concat(
                [
                    gks.head(1),
                    defs.head(defenders),
                    mids.head(midfielders),
                    fwds.head(forwards),
                ]
            )

            if len(eleven) != 11:
                continue

            score = eleven["xP"].sum()

            if score > best_score:

                best_score = score
                best = eleven

    if best is None:
        return None, None

    bench = squad[
        ~squad["ID"].isin(
            set(best["ID"])
        )
    ].sort_values(
        ["Pos ID", "xP"],
        ascending=[True, False]
    )

    return best, bench


# ============================================================
# TRANSFER ENGINE
# ============================================================

def transfer_candidates(
    squad,
    bank,
    limit=15
):

    squad_ids = set(
        squad["ID"]
    )

    results = []

    for _, outgoing in squad.sort_values(
        "xP"
    ).iterrows():

        budget = (
            outgoing["Cost"]
            + bank
        )

        candidates = df_all[
            (~df_all["ID"].isin(squad_ids))
            &
            (df_all["Pos"] == outgoing["Pos"])
            &
            (df_all["Cost"] <= budget)
            &
            (df_all["Status"] == "a")
            &
            (df_all["Chance"] >= 75)
        ].copy()

        for _, incoming in candidates.iterrows():

            # Maximum three players per club
            current_count = int(
                (
                    squad["Team ID"]
                    == incoming["Team ID"]
                ).sum()
            )

            if (
                current_count >= 3
                and incoming["Team ID"]
                != outgoing["Team ID"]
            ):
                continue

            gain = (
                incoming["xP"]
                - outgoing["xP"]
            )

            if gain <= 0:
                continue

            incoming_fixture = fixture_multiplier(
                int(incoming["Team ID"]),
                next_gw
            )

            outgoing_fixture = fixture_multiplier(
                int(outgoing["Team ID"]),
                next_gw
            )

            fixture_edge = (
                incoming_fixture
                - outgoing_fixture
            )

            transfer_score = (
                gain
                + fixture_edge * 1.5
            )

            results.append({

                "OUT":
                    outgoing["Name"],

                "IN":
                    incoming["Name"],

                "Pos":
                    outgoing["Pos"],

                "Out Cost":
                    outgoing["Cost"],

                "In Cost":
                    incoming["Cost"],

                "Gain":
                    round(
                        gain,
                        2
                    ),

                "Fixture Edge":
                    round(
                        fixture_edge,
                        2
                    ),

                "Transfer Score":
                    round(
                        transfer_score,
                        2
                    ),

                "New Team":
                    incoming["Team"],

                "New xP":
                    incoming["xP"],

            })

    if not results:

        return pd.DataFrame()

    return (
        pd.DataFrame(results)
        .sort_values(
            [
                "Transfer Score",
                "Gain"
            ],
            ascending=False
        )
        .drop_duplicates(
            subset=[
                "OUT",
                "IN"
            ]
        )
        .head(limit)
    )


# ============================================================
# DIFFERENTIAL ENGINE
# ============================================================

def ownership_label(ownership):

    if ownership < 5:
        return "🔥 Huge Differential"

    if ownership < 15:
        return "🟠 Differential"

    if ownership < 30:
        return "🟡 Moderate"

    return "🟢 Template"


# ============================================================
# SIDEBAR
# ============================================================

st.title(
    "⚽ FPL Assistant Manager"
)

st.caption(
    f"GW {current_gw} active • "
    f"Planning for GW {next_gw}"
)

st.sidebar.header(
    "Manager Settings"
)

team_id = st.sidebar.number_input(
    "Your FPL Team ID",
    min_value=1,
    value=3240706,
    step=1,
)

league_id = st.sidebar.number_input(
    "Mini-League ID",
    min_value=0,
    value=0,
    step=1,
)

free_transfers = st.sidebar.number_input(
    "Free Transfers",
    min_value=1,
    max_value=5,
    value=1,
    step=1,
)

st.sidebar.caption(
    "Your Team ID is your FPL entry ID."
)

st.sidebar.divider()

st.sidebar.caption(
    "Projection model combines FPL's own "
    "projection with form, PPG, expected "
    "involvement, ICT, minutes, availability "
    "and fixture difficulty."
)


# ============================================================
# LOAD USER
# ============================================================

user_data, loaded_gw = get_user_picks(
    team_id,
    current_gw
)

if not user_data:

    st.warning(
        "Could not load your FPL team. "
        "Check your Team ID."
    )

    st.stop()


squad = build_squad_dataframe(
    user_data.get(
        "picks",
        []
    )
)


entry_history = user_data.get(
    "entry_history",
    {}
)


bank = (
    number(
        entry_history,
        "bank"
    )
    / 10
)


last_points = entry_history.get(
    "points",
    0
)


total_points = entry_history.get(
    "total_points",
    0
)


# ============================================================
# TABS
# ============================================================

tabs = st.tabs(
    [
        "🏟️ Team Optimiser",
        "🔄 Transfers",
        "👑 Captain",
        "🩺 Squad Health",
        "🕵️ Rival Spy",
        "📊 Player Explorer",
    ]
)


# ============================================================
# TEAM OPTIMISER
# ============================================================

with tabs[0]:

    st.subheader(
        "🏟️ Best Starting XI"
    )

    starting, bench = best_starting_xi(
        squad
    )

    if starting is None:

        st.error(
            "Unable to create a legal starting XI."
        )

    else:

        formation = (
            f"{sum(starting['Pos'] == 'DEF')}-"
            f"{sum(starting['Pos'] == 'MID')}-"
            f"{sum(starting['Pos'] == 'FWD')}"
        )

        col1, col2, col3, col4 = st.columns(4)

        col1.metric(
            "Projected XI",
            f"{starting['xP'].sum():.1f}"
        )

        col2.metric(
            "Formation",
            formation
        )

        col3.metric(
            "Bank",
            f"£{bank:.1f}m"
        )

        col4.metric(
            "Last GW",
            f"{last_points} pts"
        )

        st.markdown(
            "### Starting XI"
        )

        display = starting[
            [
                "Name",
                "Team",
                "Pos",
                "Cost",
                "xP",
                "FPL xP",
                "Form",
                "Chance",
            ]
        ].copy()

        display = display.rename(
            columns={
                "Cost": "£m",
                "Chance": "Chance %",
            }
        )

        st.dataframe(
            display,
            hide_index=True,
            use_container_width=True
        )

        st.markdown(
            "### 🪑 Bench Order"
        )

        bench_display = bench[
            [
                "Name",
                "Team",
                "Pos",
                "Cost",
                "xP",
                "Form",
                "Chance",
            ]
        ].rename(
            columns={
                "Cost": "£m",
                "Chance": "Chance %",
            }
        )

        st.dataframe(
            bench_display,
            hide_index=True,
            use_container_width=True
        )

        st.markdown(
            "### 📅 Fixture Watch"
        )

        fixture_rows = []

        for _, player in squad.iterrows():

            fixture_rows.append({

                "Player":
                    player["Name"],

                "Team":
                    player["Team"],

                "GW Fixture":
                    fixture_text(
                        int(player["Team ID"]),
                        next_gw
                    ),

                "Difficulty":
                    round(
                        fixture_rating(
                            int(player["Team ID"]),
                            next_gw
                        ),
                        1
                    ),

                "xP":
                    player["xP"],

            })

        st.dataframe(
            pd.DataFrame(
                fixture_rows
            ).sort_values(
                "xP",
                ascending=False
            ),
            hide_index=True,
            use_container_width=True
        )


# ============================================================
# TRANSFERS
# ============================================================

with tabs[1]:

    st.subheader(
        "🔄 Transfer Planner"
    )

    st.write(
        f"**Bank:** £{bank:.1f}m  •  "
        f"**Free transfers:** {free_transfers}"
    )

    transfer_df = transfer_candidates(
        squad,
        bank
    )

    if transfer_df.empty:

        st.success(
            "No obvious one-for-one upgrades "
            "found within your budget."
        )

    else:

        st.dataframe(
            transfer_df,
            hide_index=True,
            use_container_width=True
        )

        best = transfer_df.iloc[0]

        st.success(
            f"### 🎯 Best Transfer: "
            f"{best['OUT']} → {best['IN']}\n\n"
            f"Projected gain: "
            f"**+{best['Gain']} points**"
        )

        st.caption(
            "Transfer Score considers projected "
            "points and fixture improvement."
        )

    st.markdown(
        "### ⚠️ Weakest Players"
    )

    weakest = squad.sort_values(
        "xP"
    ).head(5)

    st.dataframe(
        weakest[
            [
                "Name",
                "Team",
                "Pos",
                "Cost",
                "xP",
                "Form",
                "Verdict",
            ]
        ],
        hide_index=True,
        use_container_width=True
    )


# ============================================================
# CAPTAIN
# ============================================================

with tabs[2]:

    st.subheader(
        "👑 Captain Matrix"
    )

    if starting is None:

        st.warning(
            "Starting XI unavailable."
        )

    else:

        captain_rows = []

        for _, player in starting.iterrows():

            difficulty = fixture_rating(
                int(player["Team ID"]),
                next_gw
            )

            fixture_bonus = max(
                0,
                3 - difficulty
            ) * 0.04

            position_bonus = (
                1.08
                if player["Pos"]
                in ["MID", "FWD"]
                else 1.0
            )

            captain_score = (
                player["xP"]
                * position_bonus
                * (1 + fixture_bonus)
            )

            captain_rows.append({

                "Player":
                    player["Name"],

                "Team":
                    player["Team"],

                "xP":
                    player["xP"],

                "FPL xP":
                    player["FPL xP"],

                "Form":
                    player["Form"],

                "Fixture":
                    fixture_text(
                        int(player["Team ID"]),
                        next_gw
                    ),

                "Difficulty":
                    round(
                        difficulty,
                        1
                    ),

                "Own %":
                    player["Owned %"],

                "Captain Score":
                    round(
                        captain_score,
                        2
                    ),

            })

        captain_df = pd.DataFrame(
            captain_rows
        ).sort_values(
            "Captain Score",
            ascending=False
        )

        st.dataframe(
            captain_df,
            hide_index=True,
            use_container_width=True
        )

        if len(captain_df) >= 2:

            c1, c2, c3 = st.columns(3)

            c1.metric(
                "👑 Captain",
                captain_df.iloc[0]["Player"]
            )

            c2.metric(
                "Vice Captain",
                captain_df.iloc[1]["Player"]
            )

            c3.metric(
                "Captain xP",
                f"{captain_df.iloc[0]['xP']:.2f}"
            )


# ============================================================
# SQUAD HEALTH
# ============================================================

with tabs[3]:

    st.subheader(
        "🩺 Squad Health"
    )

    health = squad.copy()

    health["Verdict"] = health.apply(
        player_verdict,
        axis=1
    )

    health_display = health[
        [
            "Name",
            "Team",
            "Pos",
            "Cost",
            "xP",
            "FPL xP",
            "Form",
            "PPG",
            "Chance",
            "Minutes",
            "Verdict",
        ]
    ].rename(
        columns={
            "Cost": "£m",
            "Chance": "Chance %",
        }
    )

    st.dataframe(
        health_display,
        hide_index=True,
        use_container_width=True
    )

    flagged = health[
        health["Verdict"].isin(
            [
                "🔴 SELL",
                "🔴 SELL / REPLACE",
            ]
        )
    ]

    if len(flagged):

        st.warning(
            "Priority concerns: "
            + ", ".join(
                flagged["Name"].tolist()
            )
        )

    else:

        st.success(
            "No major squad-health problems detected."
        )

    st.markdown(
        "### 📉 Weakest Five"
    )

    st.dataframe(
        health.sort_values(
            "xP"
        )[
            [
                "Name",
                "Team",
                "Pos",
                "xP",
                "Form",
                "PPG",
                "Verdict",
            ]
        ].head(5),
        hide_index=True,
        use_container_width=True
    )


# ============================================================
# RIVAL SPY
# ============================================================

with tabs[4]:

    st.subheader(
        "🕵️ Mini-League Rival Spy"
    )

    if league_id == 0:

        st.info(
            "Enter your Mini-League ID "
            "in the sidebar."
        )

    else:

        league = get_league(
            league_id
        )

        if not league:

            st.error(
                "Could not load the league."
            )

        else:

            standings = (
                league
                .get("standings", {})
                .get("results", [])
            )

            league_name = (
                league
                .get("league", {})
                .get(
                    "name",
                    "Mini-League"
                )
            )

            st.markdown(
                f"### {league_name}"
            )

            me = next(
                (
                    row
                    for row in standings
                    if row.get("entry")
                    == team_id
                ),
                None
            )

            leader = (
                standings[0]
                if standings
                else None
            )

            if me and leader:

                gap = max(
                    0,
                    leader["total"]
                    - me["total"]
                )

                r1, r2, r3 = st.columns(3)

                r1.metric(
                    "Your Rank",
                    f"#{me['rank']}"
                )

                r2.metric(
                    "Leader",
                    leader["player_name"]
                )

                r3.metric(
                    "Gap to 1st",
                    f"{gap} pts"
                )

                leader_id = leader[
                    "entry"
                ]

                leader_data, _ = get_user_picks(
                    leader_id,
                    current_gw
                )

                if leader_data:

                    leader_squad = (
                        build_squad_dataframe(
                            leader_data.get(
                                "picks",
                                []
                            )
                        )
                    )

                    my_ids = set(
                        squad["ID"]
                    )

                    leader_ids = set(
                        leader_squad["ID"]
                    )

                    shared = (
                        my_ids
                        & leader_ids
                    )

                    my_differentials = (
                        my_ids
                        - leader_ids
                    )

                    leader_differentials = (
                        leader_ids
                        - my_ids
                    )

                    overlap = (
                        len(shared)
                        / 15
                        * 100
                    )

                    st.metric(
                        "Squad Overlap",
                        f"{overlap:.0f}%",
                        f"{len(shared)}/15 shared"
                    )

                    left, right = st.columns(2)

                    with left:

                        st.markdown(
                            "**Your Differentials**"
                        )

                        st.dataframe(
                            squad[
                                squad["ID"].isin(
                                    my_differentials
                                )
                            ][
                                [
                                    "Name",
                                    "Team",
                                    "Pos",
                                    "xP",
                                    "Owned %",
                                ]
                            ],
                            hide_index=True,
                            use_container_width=True
                        )

                    with right:

                        st.markdown(
                            "**Leader Differentials**"
                        )

                        st.dataframe(
                            leader_squad[
                                leader_squad["ID"].isin(
                                    leader_differentials
                                )
                            ][
                                [
                                    "Name",
                                    "Team",
                                    "Pos",
                                    "xP",
                                    "Owned %",
                                ]
                            ],
                            hide_index=True,
                            use_container_width=True
                        )

                    # Catch-up targets
                    st.markdown(
                        "### 🎯 Catch-Up Targets"
                    )

                    targets = df_all[
                        (~df_all["ID"].isin(
                            leader_ids
                        ))
                        &
                        (df_all["Status"] == "a")
                        &
                        (df_all["Chance"] >= 75)
                    ].copy()

                    targets[
                        "Differential"
                    ] = targets[
                        "Owned %"
                    ].apply(
                        ownership_label
                    )

                    targets[
                        "Catch-Up Score"
                    ] = (
                        targets["xP"]
                        +
                        (
                            targets["xP"]
                            - targets["PPG"]
                        ).clip(
                            lower=0
                        ) * 0.25
                        +
                        targets["Owned %"].apply(
                            lambda value:
                            max(
                                0,
                                20 - value
                            ) / 20
                        )
                    )

                    targets = targets.sort_values(
                        "Catch-Up Score",
                        ascending=False
                    )

                    st.dataframe(
                        targets[
                            [
                                "Name",
                                "Team",
                                "Pos",
                                "Cost",
                                "xP",
                                "Form",
                                "Owned %",
                                "Differential",
                                "Catch-Up Score",
                            ]
                        ].head(10).rename(
                            columns={
                                "Cost": "£m"
                            }
                        ),
                        hide_index=True,
                        use_container_width=True
                    )

            else:

                st.info(
                    "Your team wasn't found "
                    "on the returned standings page."
                )


# ============================================================
# PLAYER EXPLORER
# ============================================================

with tabs[5]:

    st.subheader(
        "📊 Player Explorer"
    )

    c1, c2, c3 = st.columns(3)

    with c1:

        selected_position = st.selectbox(
            "Position",
            [
                "ALL",
                "GKP",
                "DEF",
                "MID",
                "FWD",
            ]
        )

    with c2:

        max_price = st.slider(
            "Maximum price (£m)",
            3.5,
            15.0,
            15.0,
            0.1
        )

    with c3:

        sort_by = st.selectbox(
            "Sort by",
            [
                "xP",
                "FPL xP",
                "Form",
                "PPG",
                "Owned %",
                "xGI",
            ]
        )

    explorer = df_all.copy()

    if selected_position != "ALL":

        explorer = explorer[
            explorer["Pos"]
            == selected_position
        ]

    explorer = explorer[
        explorer["Cost"]
        <= max_price
    ]

    explorer = explorer[
        explorer["Status"]
        == "a"
    ]

    explorer["Value"] = (
        explorer["xP"]
        /
        explorer["Cost"].clip(
            lower=4.0
        )
    ).round(2)

    explorer["Fixture"] = (
        explorer["Team ID"]
        .apply(
            lambda team:
            fixture_text(
                int(team),
                next_gw
            )
        )
    )

    explorer = explorer.sort_values(
        sort_by,
        ascending=False
    )

    explorer_display = explorer[
        [
            "Name",
            "Team",
            "Pos",
            "Cost",
            "xP",
            "FPL xP",
            "Form",
            "PPG",
            "xGI",
            "Value",
            "Owned %",
            "Fixture",
        ]
    ].head(50).rename(
        columns={
            "Cost": "£m"
        }
    )

    st.dataframe(
        explorer_display,
        hide_index=True,
        use_container_width=True
    )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "⚽ FPL Assistant Manager • "
    "Projection engine is decision support, "
    "not a guarantee of future points. "
    "Always check official FPL news before the deadline."
)
