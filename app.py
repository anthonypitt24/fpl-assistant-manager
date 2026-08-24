import streamlit as st
import pandas as pd
import requests
import itertools

# ============================================================
# FPL ASSISTANT MANAGER
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)

API = "https://fantasy.premierleague.com/api/"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ============================================================
# API
# ============================================================

@st.cache_data(ttl=1800)
def get_api(endpoint):
    try:
        r = requests.get(
            API + endpoint,
            headers=HEADERS,
            timeout=20
        )

        if r.status_code == 200:
            return r.json()

    except Exception:
        pass

    return None


@st.cache_data(ttl=1800)
def get_bootstrap():
    return get_api("bootstrap-static/")


@st.cache_data(ttl=600)
def get_fixtures():
    return get_api("fixtures/") or []


@st.cache_data(ttl=600)
def get_picks(team_id, gw):

    for x in range(gw, 0, -1):

        data = get_api(
            f"entry/{team_id}/event/{x}/picks/"
        )

        if data:
            return data, x

    return None, 0


@st.cache_data(ttl=900)
def get_history(team_id):

    return get_api(
        f"entry/{team_id}/history/"
    ) or {}


@st.cache_data(ttl=900)
def get_league(league_id):

    return get_api(
        f"leagues-classic/{league_id}/standings/"
    )


@st.cache_data(ttl=1200)
def get_player_summary(player_id):

    return get_api(
        f"element-summary/{player_id}/"
    ) or {}


# ============================================================
# LOAD DATA
# ============================================================

bootstrap = get_bootstrap()

if not bootstrap:

    st.error(
        "Unable to load FPL data. "
        "Please refresh the page."
    )

    st.stop()


events = bootstrap["events"]
players = bootstrap["elements"]
clubs = bootstrap["teams"]
positions = bootstrap["element_types"]


elements = {
    p["id"]: p
    for p in players
}


teams = {
    t["id"]: t["short_name"]
    for t in clubs
}


position_names = {
    p["id"]: p["singular_name_short"]
    for p in positions
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


fixtures = get_fixtures()


# ============================================================
# HELPERS
# ============================================================

def number(value, default=0.0):

    try:

        if value in (None, ""):
            return default

        return float(value)

    except Exception:

        return default


# ============================================================
# FIXTURE MAP
# ============================================================

fixture_map = {}


for fixture in fixtures:

    gw = fixture.get("event")

    if gw is None:
        continue


    home = fixture.get("team_h")
    away = fixture.get("team_a")


    if home:

        fixture_map.setdefault(
            (home, gw),
            []
        ).append(
            {
                "opponent": away,
                "home": True,
                "difficulty":
                    fixture.get(
                        "team_h_difficulty",
                        3
                    )
            }
        )


    if away:

        fixture_map.setdefault(
            (away, gw),
            []
        ).append(
            {
                "opponent": home,
                "home": False,
                "difficulty":
                    fixture.get(
                        "team_a_difficulty",
                        3
                    )
            }
        )


def upcoming_fixtures(
    team_id,
    start_gw,
    horizon=5
):

    output = []

    for gw in range(
        start_gw,
        min(39, start_gw + horizon)
    ):

        for fixture in fixture_map.get(
            (team_id, gw),
            []
        ):

            output.append(
                (
                    gw,
                    fixture
                )
            )

    return output


# ============================================================
# FIXTURE DIFFICULTY
# ============================================================

def fixture_factor(
    team_id,
    horizon=5
):

    fixtures_list = upcoming_fixtures(
        team_id,
        next_gw,
        horizon
    )


    if not fixtures_list:

        return 1.0


    adjustment = 0


    for _, fixture in fixtures_list:

        difficulty = fixture[
            "difficulty"
        ]


        adjustment += {
            1: 0.075,
            2: 0.035,
            3: 0,
            4: -0.035,
            5: -0.075
        }.get(
            difficulty,
            0
        )


        if fixture["home"]:

            adjustment += 0.012


    factor = (
        1
        +
        adjustment /
        len(fixtures_list)
    )


    return max(
        0.80,
        min(1.20, factor)
    )


# ============================================================
# PLAYER HISTORY
# ============================================================

def player_history(player_id):

    data = get_player_summary(
        player_id
    )

    return data.get(
        "history",
        []
    )


@st.cache_data(ttl=1200)
def historical_stats(player_id):

    history = player_history(
        player_id
    )


    played = [
        x
        for x in history
        if number(
            x.get("minutes")
        ) > 0
    ]


    def average(rows, key):

        values = [
            number(x.get(key))
            for x in rows
        ]

        if not values:
            return 0

        return (
            sum(values)
            /
            len(values)
        )


    recent = played[-5:]
    previous = played[-10:-5]


    trend = (
        average(
            recent,
            "total_points"
        )
        -
        average(
            previous,
            "total_points"
        )
    )


    xg_trend = (
        average(
            recent,
            "expected_goals"
        )
        -
        average(
            previous,
            "expected_goals"
        )
    )


    xa_trend = (
        average(
            recent,
            "expected_assists"
        )
        -
        average(
            previous,
            "expected_assists"
        )
    )


    return {

        "last3":
            average(
                history[-3:],
                "total_points"
            ),

        "last5":
            average(
                history[-5:],
                "total_points"
            ),

        "last10":
            average(
                history[-10:],
                "total_points"
            ),

        "average_minutes":
            average(
                played,
                "minutes"
            ),

        "trend":
            trend,

        "xg_trend":
            xg_trend,

        "xa_trend":
            xa_trend,

        "bonus":
            average(
                played,
                "bonus"
            ),

        "bps":
            average(
                played,
                "bps"
            ),

        "defensive_contribution":
            average(
                played,
                "defensive_contribution"
            )
    }


# ============================================================
# PROJECTED POINTS
# ============================================================

def projected_points(
    player,
    horizon=5
):

    form = number(
        player.get("form")
    )

    ppg = number(
        player.get("points_per_game")
    )

    total_points = number(
        player.get("total_points")
    )

    expected_goals = number(
        player.get("expected_goals")
    )

    expected_assists = number(
        player.get("expected_assists")
    )

    bonus = number(
        player.get("bonus")
    )

    bps = number(
        player.get("bps")
    )

    clean_sheets = number(
        player.get("clean_sheets")
    )

    defensive_contribution = number(
        player.get(
            "defensive_contribution"
        )
    )


    chance = player.get(
        "chance_of_playing_next_round"
    )


    if chance is None:

        availability = 1.0

    else:

        availability = (
            number(chance)
            / 100
        )


    season_ppg = (
        total_points
        /
        max(current_gw, 1)
    )


    base = (
        form * 0.34
        +
        ppg * 0.24
        +
        season_ppg * 0.12
    )


    attacking = (
        expected_goals * 0.72
        +
        expected_assists * 0.48
    )


    bonus_component = min(
        bonus /
        max(current_gw, 1),
        1
    ) * 0.65


    bps_component = min(
        bps /
        max(current_gw * 100, 1),
        1
    ) * 0.55


    clean_sheet_component = (
        clean_sheets
        /
        max(current_gw, 1)
    ) * 0.55


    position = position_names[
        player["element_type"]
    ]


    if position == "DEF":

        defensive_component = min(
            defensive_contribution /
            max(current_gw, 1),
            1
        ) * 0.75

        clean_sheet_component *= 1.10


    elif position == "GKP":

        defensive_component = min(
            defensive_contribution /
            max(current_gw, 1),
            1
        ) * 0.30

        clean_sheet_component *= 1.25


    elif position == "MID":

        defensive_component = min(
            defensive_contribution /
            max(current_gw, 1),
            1
        ) * 0.45

        clean_sheet_component *= 0.35


    else:

        defensive_component = min(
            defensive_contribution /
            max(current_gw, 1),
            1
        ) * 0.15

        clean_sheet_component *= 0.08


    minutes = number(
        player.get("minutes")
    )

    starts = number(
        player.get("starts")
    )


    minutes_factor = (
        0.60
        +
        0.40
        *
        (
            (
                min(
                    minutes /
                    max(
                        current_gw * 90,
                        1
                    ),
                    1
                )
                * 0.65
            )
            +
            (
                min(
                    starts /
                    max(
                        current_gw,
                        1
                    ),
                    1
                )
                * 0.35
            )
        )
    )


    raw = (

        base
        +
        attacking
        +
        bonus_component
        +
        bps_component
        +
        clean_sheet_component
        +
        defensive_component

    )


    result = (
        raw
        *
        minutes_factor
        *
        availability
        *
        fixture_factor(
            player["team"],
            horizon
        )
    )


    return round(
        max(0, result),
        2
    )


# ============================================================
# PLAYER DATABASE
# ============================================================

@st.cache_data(ttl=900)
def build_player_database(horizon):

    rows = []


    for player in players:

        chance = player.get(
            "chance_of_playing_next_round"
        )


        availability = (
            100
            if chance is None
            else number(chance)
        )


        rows.append({

            "ID":
                player["id"],

            "Name":
                player["web_name"],

            "Team":
                teams.get(
                    player["team"],
                    "?"
                ),

            "Team ID":
                player["team"],

            "Pos":
                position_names[
                    player["element_type"]
                ],

            "Pos ID":
                player["element_type"],

            "Price":
                player["now_cost"] / 10,

            "Form":
                number(
                    player.get("form")
                ),

            "PPG":
                number(
                    player.get(
                        "points_per_game"
                    )
                ),

            "Total":
                player.get(
                    "total_points",
                    0
                ),

            "xG":
                number(
                    player.get(
                        "expected_goals"
                    )
                ),

            "xA":
                number(
                    player.get(
                        "expected_assists"
                    )
                ),

            "BPS":
                player.get(
                    "bps",
                    0
                ),

            "Bonus":
                player.get(
                    "bonus",
                    0
                ),

            "DefCon":
                number(
                    player.get(
                        "defensive_contribution"
                    )
                ),

            "Ownership":
                number(
                    player.get(
                        "selected_by_percent"
                    )
                ),

            "Availability":
                availability,

            "Projection":
                projected_points(
                    player,
                    horizon
                ),

            "Status":
                player.get(
                    "status",
                    "a"
                ),

            "News":
                player.get(
                    "news",
                    ""
                )
        })


    return pd.DataFrame(rows)


# ============================================================
# DREAM TEAM OPTIMISER
# ============================================================

def build_best_100m_team(
    df,
    budget=100.0
):

    available = df[
        (df["Status"] == "a")
        &
        (df["Availability"] >= 75)
    ].copy()


    available = available[
        available["Price"] <= budget
    ]


    gks = available[
        available["Pos"] == "GKP"
    ].sort_values(
        "Projection",
        ascending=False
    )


    defs = available[
        available["Pos"] == "DEF"
    ].sort_values(
        "Projection",
        ascending=False
    )


    mids = available[
        available["Pos"] == "MID"
    ].sort_values(
        "Projection",
        ascending=False
    )


    fwds = available[
        available["Pos"] == "FWD"
    ].sort_values(
        "Projection",
        ascending=False
    )


    best_team = None
    best_score = -999999


    # Search the most promising players.
    # This keeps Streamlit fast while still
    # exploring a very large number of squads.

    gks = gks.head(10)
    defs = defs.head(35)
    mids = mids.head(35)
    fwds = fwds.head(25)


    for gk_combo in itertools.combinations(
        gks.to_dict("records"),
        2
    ):

        gk_cost = sum(
            p["Price"]
            for p in gk_combo
        )


        gk_score = sum(
            p["Projection"]
            for p in gk_combo
        )


        for def_combo in itertools.combinations(
            defs.to_dict("records"),
            5
        ):

            def_team_counts = {}

            valid = True

            for p in def_combo:

                club = p["Team ID"]

                def_team_counts[club] = (
                    def_team_counts.get(
                        club,
                        0
                    ) + 1
                )

                if (
                    def_team_counts[club]
                    > 3
                ):
                    valid = False
                    break


            if not valid:
                continue


            def_cost = sum(
                p["Price"]
                for p in def_combo
            )


            def_score = sum(
                p["Projection"]
                for p in def_combo
            )


            for mid_combo in itertools.combinations(
                mids.to_dict("records"),
                5
            ):

                team_counts = (
                    def_team_counts.copy()
                )

                valid = True


                for p in mid_combo:

                    club = p["Team ID"]

                    team_counts[club] = (
                        team_counts.get(
                            club,
                            0
                        ) + 1
                    )

                    if (
                        team_counts[club]
                        > 3
                    ):

                        valid = False
                        break


                if not valid:
                    continue


                mid_cost = sum(
                    p["Price"]
                    for p in mid_combo
                )


                for fwd_combo in itertools.combinations(
                    fwds.to_dict("records"),
                    3
                ):

                    counts = (
                        team_counts.copy()
                    )

                    valid = True


                    for p in fwd_combo:

                        club = p["Team ID"]

                        counts[club] = (
                            counts.get(
                                club,
                                0
                            ) + 1
                        )

                        if counts[club] > 3:

                            valid = False
                            break


                    if not valid:
                        continue


                    cost = (
                        gk_cost
                        +
                        def_cost
                        +
                        mid_cost
                        +
                        sum(
                            p["Price"]
                            for p in fwd_combo
                        )
                    )


                    if cost > budget:

                        continue


                    score = (
                        gk_score
                        +
                        def_score
                        +
                        def_score * 0
                        +
                        sum(
                            p["Projection"]
                            for p in mid_combo
                        )
                        +
                        sum(
                            p["Projection"]
                            for p in fwd_combo
                        )
                    )


                    if score > best_score:

                        best_score = score

                        best_team = (
                            list(gk_combo)
                            +
                            list(def_combo)
                            +
                            list(mid_combo)
                            +
                            list(fwd_combo)
                        )


    return (
        best_team,
        best_score
    )


# ============================================================
# SIDEBAR
# ============================================================

st.title(
    "⚽ FPL Assistant Manager"
)


st.caption(
    f"Gameweek {current_gw} active | "
    f"Planning for GW {next_gw}"
)


st.sidebar.header(
    "Manager Settings"
)


team_id = st.sidebar.number_input(
    "Your FPL Team ID",
    min_value=1,
    value=3240706,
    step=1
)


league_id = st.sidebar.number_input(
    "Mini-League ID (optional)",
    min_value=0,
    value=0,
    step=1
)


horizon = st.sidebar.slider(
    "Planning horizon",
    3,
    8,
    5
)


hit_size = st.sidebar.selectbox(
    "Transfer hit to evaluate",
    [0, 4, 8],
    index=1
)


# ============================================================
# LOAD PLAYER DATA
# ============================================================

df = build_player_database(
    horizon
)


user_data, loaded_gw = get_picks(
    team_id,
    current_gw
)


if not user_data:

    st.error(
        "Couldn't load your FPL team. "
        "Check your Team ID."
    )

    st.stop()


my_ids = {
    pick["element"]
    for pick in user_data["picks"]
}


squad = df[
    df["ID"].isin(my_ids)
].copy()


bank = (
    number(
        user_data
        .get("entry_history", {})
        .get("bank", 0)
    )
    / 10
)


# ============================================================
# TABS
# ============================================================

tabs = st.tabs(
    [
        "📋 My Team",
        "💰 £100m Dream Team",
        "🔄 Transfers",
        "🩺 Squad Health",
        "👑 Captain & Chips",
        "🕵️ Rival Spy",
        "📈 Player Explorer",
        "📚 Season Trends"
    ]
)


# ============================================================
# MY TEAM
# ============================================================

with tabs[0]:

    st.subheader(
        "📋 Best Starting XI"
    )


    gks = squad[
        squad["Pos"] == "GKP"
    ].sort_values(
        "Projection",
        ascending=False
    )


    outfield = squad[
        squad["Pos"] != "GKP"
    ].sort_values(
        "Projection",
        ascending=False
    )


    best_start = []


    if not gks.empty:

        best_start.append(
            gks.iloc[0]
        )


    defenders = outfield[
        outfield["Pos"] == "DEF"
    ].head(5)


    midfielders = outfield[
        outfield["Pos"] == "MID"
    ].head(5)


    forwards = outfield[
        outfield["Pos"] == "FWD"
    ].head(3)


    # Build a simple high-projection XI.
    # Then choose the best legal formation.

    best_xi = None
    best_xi_score = -999


    for d, m, f in [
        (3,4,3),
        (3,5,2),
        (4,3,3),
        (4,4,2),
        (4,5,1),
        (5,3,2),
        (5,4,1)
    ]:

        if (
            len(defenders) >= d
            and len(midfielders) >= m
            and len(forwards) >= f
            and not gks.empty
        ):

            players_xi = (
                [gks.iloc[0]]
                +
                [
                    defenders.iloc[i]
                    for i in range(d)
                ]
                +
                [
                    midfielders.iloc[i]
                    for i in range(m)
                ]
                +
                [
                    forwards.iloc[i]
                    for i in range(f)
                ]
            )


            score = sum(
                p["Projection"]
                for p in players_xi
            )


            if score > best_xi_score:

                best_xi_score = score

                best_xi = players_xi


    if best_xi:

        start_df = pd.DataFrame(
            best_xi
        )


        captain = start_df.sort_values(
            "Projection",
            ascending=False
        ).iloc[0]


        vice = start_df.sort_values(
            "Projection",
            ascending=False
        ).iloc[1]


        a,b,c,d = st.columns(4)


        a.metric(
            "Captain",
            captain["Name"],
            f'{captain["Projection"]:.1f} xP'
        )


        b.metric(
            "Vice Captain",
            vice["Name"],
            f'{vice["Projection"]:.1f} xP'
        )


        c.metric(
            "Starting XI xP",
            f"{best_xi_score:.1f}"
        )


        d.metric(
            "Bank",
            f"£{bank:.1f}m"
        )


        st.dataframe(
            start_df[
                [
                    "Name",
                    "Team",
                    "Pos",
                    "Price",
                    "Form",
                    "PPG",
                    "Projection"
                ]
            ],
            hide_index=True,
            use_container_width=True
        )


# ============================================================
# £100M DREAM TEAM
# ============================================================

with tabs[1]:

    st.subheader(
        "💰 £100m Best Possible Team"
    )


    st.write(
        "If you had £100m to build a completely "
        "new squad right now, this attempts to "
        "find the 15-player squad with the highest "
        "projected points."
    )


    st.info(
        "Rules used: £100m maximum, "
        "2 GKs, 5 DEFs, 5 MIDs, 3 FWDs, "
        "maximum 3 players from one club."
    )


    with st.spinner(
        "Searching for the best £100m squad..."
    ):

        dream_team, dream_score = (
            build_best_100m_team(
                df,
                100.0
            )
        )


    if dream_team:

        dream_df = pd.DataFrame(
            dream_team
        )


        total_cost = dream_df[
            "Price"
        ].sum()


        total_projection = dream_df[
            "Projection"
        ].sum()


        a,b,c = st.columns(3)


        a.metric(
            "Squad cost",
            f"£{total_cost:.1f}m"
        )


        b.metric(
            "Money remaining",
            f"£{100-total_cost:.1f}m"
        )


        c.metric(
            "15-player projection",
            f"{total_projection:.1f}"
        )


        st.markdown(
            "### 🏆 Theoretical Best 15"
        )


        st.dataframe(
            dream_df[
                [
                    "Name",
                    "Team",
                    "Pos",
                    "Price",
                    "Form",
                    "PPG",
                    "Projection",
                    "Ownership"
                ]
            ].sort_values(
                ["Pos", "Projection"],
                ascending=[True, False]
            ),
            hide_index=True,
            use_container_width=True
        )


        st.divider()


        st.markdown(
            "### ⭐ Best Starting XI"
        )


        dream_gks = dream_df[
            dream_df["Pos"] == "GKP"
        ].sort_values(
            "Projection",
            ascending=False
        )


        dream_defs = dream_df[
            dream_df["Pos"] == "DEF"
        ].sort_values(
            "Projection",
            ascending=False
        )


        dream_mids = dream_df[
            dream_df["Pos"] == "MID"
        ].sort_values(
            "Projection",
            ascending=False
        )


        dream_fwds = dream_df[
            dream_df["Pos"] == "FWD"
        ].sort_values(
            "Projection",
            ascending=False
        )


        best_dream_xi = None
        best_dream_score = -999


        for d,m,f in [
            (3,4,3),
            (3,5,2),
            (4,3,3),
            (4,4,2),
            (4,5,1),
            (5,3,2),
            (5,4,1)
        ]:

            if (
                len(dream_defs) >= d
                and len(dream_mids) >= m
                and len(dream_fwds) >= f
            ):

                xi = (
                    [dream_gks.iloc[0]]
                    +
                    list(
                        dream_defs.iloc[:d].to_dict(
                            "records"
                        )
                    )
                    +
                    list(
                        dream_mids.iloc[:m].to_dict(
                            "records"
                        )
                    )
                    +
                    list(
                        dream_fwds.iloc[:f].to_dict(
                            "records"
                        )
                    )
                )


                score = sum(
                    p["Projection"]
                    for p in xi
                )


                if score > best_dream_score:

                    best_dream_score = score
                    best_dream_xi = xi


        if best_dream_xi:

            xi_df = pd.DataFrame(
                best_dream_xi
            )


            captain = xi_df.sort_values(
                "Projection",
                ascending=False
            ).iloc[0]


            vice = xi_df.sort_values(
                "Projection",
                ascending=False
            ).iloc[1]


            a,b,c = st.columns(3)


            a.metric(
                "Captain",
                captain["Name"],
                f'{captain["Projection"]:.1f} xP'
            )


            b.metric(
                "Vice",
                vice["Name"],
                f'{vice["Projection"]:.1f} xP'
            )


            c.metric(
                "Starting XI",
                f"{best_dream_score:.1f} xP"
            )


            st.dataframe(
                xi_df[
                    [
                        "Name",
                        "Team",
                        "Pos",
                        "Price",
                        "Projection",
                        "Ownership"
                    ]
                ],
                hide_index=True,
                use_container_width=True
            )


# ============================================================
# TRANSFERS
# ============================================================

with tabs[2]:

    st.subheader(
        "🔄 Transfer Decision Engine"
    )


    transfer_rows = []


    for _, outgoing in squad.iterrows():

        budget = (
            bank
            +
            outgoing["Price"]
        )


        candidates = df[
            (df["Pos"] == outgoing["Pos"])
            &
            (~df["ID"].isin(my_ids))
            &
            (df["Price"] <= budget)
            &
            (df["Status"] == "a")
        ]


        for _, incoming in candidates.iterrows():

            gain = (
                incoming["Projection"]
                -
                outgoing["Projection"]
            )


            after_hit = (
                gain
                -
                hit_size
            )


            transfer_rows.append({

                "OUT":
                    outgoing["Name"],

                "IN":
                    incoming["Name"],

                "Position":
                    outgoing["Pos"],

                "Sell Price":
                    outgoing["Price"],

                "Buy Price":
                    incoming["Price"],

                "Bank After":
                    round(
                        budget
                        -
                        incoming["Price"],
                        1
                    ),

                "Current xP":
                    outgoing["Projection"],

                "New xP":
                    incoming["Projection"],

                "5-GW Gain":
                    round(
                        gain,
                        2
                    ),

                f"After -{hit_size}":
                    round(
                        after_hit,
                        2
                    ),

                "Availability":
                    incoming[
                        "Availability"
                    ],

                "Ownership":
                    incoming[
                        "Ownership"
                    ]
            })


    transfer_df = pd.DataFrame(
        transfer_rows
    )


    if not transfer_df.empty:

        st.markdown(
            "### 🟢 Best Transfers"
        )


        st.dataframe(
            transfer_df
            .sort_values(
                "5-GW Gain",
                ascending=False
            )
            .head(15),
            hide_index=True,
            use_container_width=True
        )


        st.markdown(
            f"### 🟠 Transfers after "
            f"-{hit_size}"
        )


        st.dataframe(
            transfer_df
            .sort_values(
                f"After -{hit_size}",
                ascending=False
            )
            .head(15),
            hide_index=True,
            use_container_width=True
        )


        st.markdown(
            "### 💰 Sell-to-Fund Upgrades"
        )


        fund = transfer_df[
            (
                transfer_df["Buy Price"]
                >
                transfer_df["Sell Price"]
            )
            &
            (
                transfer_df["5-GW Gain"]
                >= 2
            )
        ]


        if fund.empty:

            st.info(
                "No obvious premium upgrade "
                "worth funding right now."
            )

        else:

            st.dataframe(
                fund.sort_values(
                    "5-GW Gain",
                    ascending=False
                ).head(10),
                hide_index=True,
                use_container_width=True
            )


# ============================================================
# SQUAD HEALTH
# ============================================================

with tabs[3]:

    st.subheader(
        "🩺 Squad Health"
    )


    health = []


    for _, player in squad.iterrows():

        if player["Status"] in [
            "i",
            "s",
            "u"
        ]:

            verdict = (
                "🔴 SELL / REPLACE"
            )


        elif player[
            "Availability"
        ] < 75:

            verdict = (
                "🟠 MINUTES / INJURY RISK"
            )


        elif player[
            "Projection"
        ] >= 6:

            verdict = (
                "🟢 STRONG HOLD"
            )


        elif player[
            "Projection"
        ] < 3:

            verdict = (
                "🔴 WEAK"
            )


        else:

            verdict = (
                "🟡 MONITOR"
            )


        health.append({

            "Player":
                player["Name"],

            "Team":
                player["Team"],

            "Pos":
                player["Pos"],

            "Price":
                player["Price"],

            "Form":
                player["Form"],

            "PPG":
                player["PPG"],

            "Projection":
                player["Projection"],

            "Availability":
                player["Availability"],

            "Verdict":
                verdict,

            "News":
                player["News"]
        })


    st.dataframe(
        pd.DataFrame(health),
        hide_index=True,
        use_container_width=True
    )


# ============================================================
# CAPTAIN & CHIPS
# ============================================================

with tabs[4]:

    st.subheader(
        "👑 Captain Matrix"
    )


    if best_xi:

        captain_df = pd.DataFrame(
            best_xi
        ).sort_values(
            "Projection",
            ascending=False
        )


        st.dataframe(
            captain_df[
                [
                    "Name",
                    "Team",
                    "Pos",
                    "Projection",
                    "Form",
                    "PPG",
                    "xG",
                    "xA",
                    "Ownership"
                ]
            ],
            hide_index=True,
            use_container_width=True
        )


    st.divider()


    st.subheader(
        "🎯 Chip Planner"
    )


    history = get_history(
        team_id
    )


    chips_used = history.get(
        "chips",
        []
    )


    def chip_used(
        chip_name,
        first_half
    ):

        for chip in chips_used:

            if chip.get(
                "name"
            ) != chip_name:

                continue


            gw = int(
                chip.get(
                    "event",
                    0
                )
                or 0
            )


            if first_half and gw <= 19:

                return True


            if not first_half and gw >= 20:

                return True


        return False


    chip_rows = []


    for label, code in [
        (
            "Wildcard",
            "wildcard"
        ),
        (
            "Free Hit",
            "freehit"
        ),
        (
            "Bench Boost",
            "bboost"
        ),
        (
            "Triple Captain",
            "3xc"
        )
    ]:

        chip_rows.append({

            "Chip":
                label,

            "GW1-19":
                (
                    "USED"
                    if chip_used(
                        code,
                        True
                    )
                    else
                    "AVAILABLE"
                ),

            "GW20-38":
                (
                    "USED"
                    if chip_used(
                        code,
                        False
                    )
                    else
                    "AVAILABLE"
                )
        })


    st.dataframe(
        pd.DataFrame(chip_rows),
        hide_index=True,
        use_container_width=True
    )


# ============================================================
# RIVAL SPY
# ============================================================

with tabs[5]:

    st.subheader(
        "🕵️ Mini-League Rival Spy"
    )


    if league_id == 0:

        st.info(
            "Enter your Mini-League ID "
            "in the sidebar."
        )


    else:

        league_data = get_league(
            league_id
        )


        if not league_data:

            st.error(
                "Could not load the league."
            )


        else:

            standings = (
                league_data
                .get(
                    "standings",
                    {}
                )
                .get(
                    "results",
                    []
                )
            )


            if standings:

                leader = standings[0]


                mine = next(
                    (
                        x
                        for x in standings
                        if x.get(
                            "entry"
                        )
                        == team_id
                    ),
                    None
                )


                if mine:

                    a,b,c = st.columns(3)


                    a.metric(
                        "Your Rank",
                        f'#{mine["rank"]}'
                    )


                    b.metric(
                        "Leader",
                        leader[
                            "player_name"
                        ]
                    )


                    c.metric(
                        "Points Behind",
                        leader[
                            "total"
                        ]
                        -
                        mine[
                            "total"
                        ]
                    )


                leader_data, _ = get_picks(
                    leader["entry"],
                    current_gw
                )


                if leader_data:

                    leader_ids = {
                        x["element"]
                        for x in
                        leader_data["picks"]
                    }


                    shared = (
                        my_ids
                        &
                        leader_ids
                    )


                    st.metric(
                        "Squad Similarity",
                        f"{len(shared)}/15"
                    )


                    c1,c2 = st.columns(2)


                    with c1:

                        st.markdown(
                            "### Your Differentials"
                        )


                        st.dataframe(
                            squad[
                                squad["ID"].isin(
                                    my_ids
                                    -
                                    leader_ids
                                )
                            ][
                                [
                                    "Name",
                                    "Team",
                                    "Pos",
                                    "Projection",
                                    "Ownership"
                                ]
                            ],
                            hide_index=True,
                            use_container_width=True
                        )


                    with c2:

                        st.markdown(
                            "### Leader Differentials"
                        )


                        st.dataframe(
                            df[
                                df["ID"].isin(
                                    leader_ids
                                    -
                                    my_ids
                                )
                            ][
                                [
                                    "Name",
                                    "Team",
                                    "Pos",
                                    "Projection",
                                    "Ownership"
                                ]
                            ],
                            hide_index=True,
                            use_container_width=True
                        )


# ============================================================
# PLAYER EXPLORER
# ============================================================

with tabs[6]:

    st.subheader(
        "📈 Player Explorer"
    )


    player_name = st.selectbox(
        "Choose a player",
        sorted(
            df["Name"].unique()
        )
    )


    player = df[
        df["Name"]
        ==
        player_name
    ].iloc[0]


    a,b,c,d = st.columns(4)


    a.metric(
        "Projected Points",
        f'{player["Projection"]:.1f}'
    )


    b.metric(
        "Form",
        f'{player["Form"]:.1f}'
    )


    c.metric(
        "xG",
        f'{player["xG"]:.2f}'
    )


    d.metric(
        "xA",
        f'{player["xA"]:.2f}'
    )


    player_hist = player_history(
        int(player["ID"])
    )


    if player_hist:

        chart = pd.DataFrame({

            "GW":
                [
                    x.get(
                        "round"
                    )
                    for x in player_hist
                ],

            "Points":
                [
                    x.get(
                        "total_points",
                        0
                    )
                    for x in player_hist
                ]
        }).set_index(
            "GW"
        )


        st.line_chart(
            chart
        )


        hist_df = pd.DataFrame(
            player_hist
        )


        columns = [
            "round",
            "minutes",
            "total_points",
            "goals_scored",
            "assists",
            "clean_sheets",
            "bonus",
            "bps",
            "expected_goals",
            "expected_assists",
            "defensive_contribution"
        ]


        columns = [
            c
            for c in columns
            if c in hist_df.columns
        ]


        st.dataframe(
            hist_df[columns],
            hide_index=True,
            use_container_width=True
        )


# ============================================================
# SEASON TRENDS
# ============================================================

with tabs[7]:

    st.subheader(
        "📚 Season Trends"
    )


    st.write(
        "The app uses the historical Gameweek "
        "data supplied by FPL. As more Gameweeks "
        "are completed, the trend analysis has "
        "more information to work with."
    )


    if current_gw <= 3:

        st.warning(
            "Early season: trend signals are "
            "based on a small sample."
        )


    trend_rows = []


    for _, player in squad.iterrows():

        h = historical_stats(
            int(player["ID"])
        )


        trend_rows.append({

            "Player":
                player["Name"],

            "Last 3":
                round(
                    h["last3"],
                    2
                ),

            "Last 5":
                round(
                    h["last5"],
                    2
                ),

            "Last 10":
                round(
                    h["last10"],
                    2
                ),

            "Trend":
                round(
                    h["trend"],
                    2
                ),

            "xG Trend":
                round(
                    h["xg_trend"],
                    3
                ),

            "xA Trend":
                round(
                    h["xa_trend"],
                    3
                ),

            "Average Minutes":
                round(
                    h[
                        "average_minutes"
                    ],
                    1
                ),

            "Projection":
                player[
                    "Projection"
                ]
        })


    trend_df = pd.DataFrame(
        trend_rows
    )


    st.dataframe(
        trend_df.sort_values(
            "Projection",
            ascending=False
        ),
        hide_index=True,
        use_container_width=True
    )


    st.markdown(
        "### 🔥 Rising Players"
    )


    rising = trend_df[
        (
            trend_df["Trend"]
            >=
            0.75
        )
        |
        (
            trend_df["xG Trend"]
            >
            0.03
        )
        |
        (
            trend_df["xA Trend"]
            >
            0.03
        )
    ]


    if rising.empty:

        st.info(
            "No strong rising trends detected."
        )

    else:

        st.dataframe(
            rising.sort_values(
                "Trend",
                ascending=False
            ),
            hide_index=True,
            use_container_width=True
        )


    st.markdown(
        "### 📉 Falling Players"
    )


    falling = trend_df[
        (
            trend_df["Trend"]
            <=
            -0.75
        )
        |
        (
            trend_df["xG Trend"]
            <
            -0.03
        )
        |
        (
            trend_df["xA Trend"]
            <
            -0.03
        )
    ]


    if falling.empty:

        st.success(
            "No major negative trends detected."
        )

    else:

        st.dataframe(
            falling.sort_values(
                "Trend"
            ),
            hide_index=True,
            use_container_width=True
        )


# ============================================================
# FOOTER
# ============================================================

st.divider()


st.caption(
    f"FPL Assistant Manager | "
    f"GW {current_gw} | "
    f"Next GW {next_gw}"
)


st.caption(
    "Projected points are this app's "
    "statistical model, not an official "
    "FPL prediction."
            ) 
