import streamlit as st
import pandas as pd
import requests
import itertools
import math
from datetime import datetime, timezone

# ============================================================
# FPL ASSISTANT MANAGER - ADVANCED EDITION
# 2026/27
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)

API = "https://fantasy.premierleague.com/api/"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except:
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except:
        return default


@st.cache_data(ttl=1800)
def api_get(endpoint):
    try:
        r = requests.get(
            API + endpoint,
            headers=HEADERS,
            timeout=15
        )
        if r.status_code == 200:
            return r.json()
        return None
    except:
        return None


# ============================================================
# MAIN FPL DATA
# ============================================================

@st.cache_data(ttl=1800)
def get_bootstrap_data():
    return api_get("bootstrap-static/")


@st.cache_data(ttl=600)
def get_fixtures():
    return api_get("fixtures/")


@st.cache_data(ttl=300)
def get_user_picks(team_id, target_gw):
    for gw in range(target_gw, 0, -1):
        data = api_get(
            f"entry/{team_id}/event/{gw}/picks/"
        )
        if data:
            return data, gw

    return None, 0


@st.cache_data(ttl=900)
def get_entry_history(team_id):
    return api_get(
        f"entry/{team_id}/history/"
    )


@st.cache_data(ttl=900)
def get_entry_profile(team_id):
    return api_get(
        f"entry/{team_id}/"
    )


@st.cache_data(ttl=900)
def get_entry_transfers(team_id):
    return api_get(
        f"entry/{team_id}/transfers/"
    )


@st.cache_data(ttl=600)
def get_league_standings(league_id):
    return api_get(
        f"leagues-classic/{league_id}/standings/"
    )


@st.cache_data(ttl=1800)
def get_player_summary(player_id):
    return api_get(
        f"element-summary/{player_id}/"
    )


# ============================================================
# LOAD DATA
# ============================================================

bootstrap = get_bootstrap_data()

if not bootstrap:
    st.error(
        "Unable to load FPL data. Please refresh the page."
    )
    st.stop()

events = bootstrap.get("events", [])
players = bootstrap.get("elements", [])
club_data = bootstrap.get("teams", [])
position_data = bootstrap.get("element_types", [])

elements = {
    p["id"]: p for p in players
}

teams = {
    t["id"]: t["short_name"]
    for t in club_data
}

team_names = {
    t["id"]: t["name"]
    for t in club_data
}

element_types = {
    e["id"]: e["singular_name_short"]
    for e in position_data
}

# ============================================================
# GAMEWEEK DETECTION
# ============================================================

current_event = next(
    (e for e in events if e.get("is_current")),
    None
)

next_event = next(
    (e for e in events if e.get("is_next")),
    None
)

if current_event:
    current_gw = current_event["id"]
else:
    current_gw = 1

if next_event:
    next_gw = next_event["id"]
else:
    next_gw = min(current_gw + 1, 38)


# ============================================================
# FIXTURE DATA
# ============================================================

all_fixtures = get_fixtures() or []


def fixture_difficulty(team_id, opponent_id):
    """
    Approximate fixture difficulty using FPL's own
    difficulty ratings where possible.
    """

    for fixture in all_fixtures:
        if (
            fixture.get("team_h") == team_id
            and fixture.get("team_a") == opponent_id
        ):
            return safe_int(
                fixture.get("team_h_difficulty"),
                3
            )

        if (
            fixture.get("team_a") == team_id
            and fixture.get("team_h") == opponent_id
        ):
            return safe_int(
                fixture.get("team_a_difficulty"),
                3
            )

    return 3


def get_player_fixtures(player):
    """
    Returns upcoming fixtures from the official
    element-summary endpoint.
    """

    data = get_player_summary(player["id"])

    if not data:
        return []

    fixtures = data.get("fixtures", [])

    upcoming = []

    for f in fixtures:
        if f.get("finished"):
            continue

        opponent = f.get("opponent_team")

        if not opponent:
            continue

        upcoming.append({
            "gw": f.get("event"),
            "opponent_id": opponent,
            "opponent": teams.get(
                opponent,
                "?"
            ),
            "home": f.get(
                "is_home",
                f.get("was_home", False)
            ),
            "difficulty": safe_int(
                f.get("difficulty"),
                fixture_difficulty(
                    player["team"],
                    opponent
                )
            )
        })

    return sorted(
        upcoming,
        key=lambda x: (
            x["gw"] if x["gw"] else 99
        )
    )


# ============================================================
# PLAYER HISTORY
# ============================================================

def get_player_history(player_id):

    data = get_player_summary(player_id)

    if not data:
        return []

    return data.get("history", [])


def historical_metrics(player_id):

    history = get_player_history(player_id)

    if not history:
        return {
            "avg_points": 0,
            "avg_minutes": 0,
            "avg_bonus": 0,
            "avg_bps": 0,
            "avg_xg": 0,
            "avg_xa": 0,
            "avg_dc": 0,
            "starts": 0,
            "appearances": 0,
            "recent_points": 0
        }

    recent = history[-5:]

    appearances = [
        h for h in history
        if safe_int(h.get("minutes")) > 0
    ]

    starts = [
        h for h in history
        if safe_int(h.get("minutes")) >= 60
    ]

    def avg(data, key):
        if not data:
            return 0
        return sum(
            safe_float(x.get(key))
            for x in data
        ) / len(data)

    return {
        "avg_points": avg(
            appearances,
            "total_points"
        ),
        "avg_minutes": avg(
            appearances,
            "minutes"
        ),
        "avg_bonus": avg(
            appearances,
            "bonus"
        ),
        "avg_bps": avg(
            appearances,
            "bps"
        ),
        "avg_xg": avg(
            appearances,
            "expected_goals"
        ),
        "avg_xa": avg(
            appearances,
            "expected_assists"
        ),
        "avg_dc": avg(
            appearances,
            "defensive_contribution"
        ),
        "starts": len(starts),
        "appearances": len(appearances),
        "recent_points": avg(
            recent,
            "total_points"
        )
    }


# ============================================================
# MODEL: PLAYER PROJECTED POINTS
# ============================================================

def calculate_projection(player):

    form = safe_float(
        player.get("form")
    )

    ppg = safe_float(
        player.get("points_per_game")
    )

    total_points = safe_float(
        player.get("total_points")
    )

    xg = safe_float(
        player.get("expected_goals")
    )

    xa = safe_float(
        player.get("expected_assists")
    )

    minutes = safe_float(
        player.get("minutes")
    )

    starts = safe_float(
        player.get("starts")
    )

    clean_sheets = safe_float(
        player.get("clean_sheets")
    )

    bonus = safe_float(
        player.get("bonus")
    )

    bps = safe_float(
        player.get("bps")
    )

    selected = safe_float(
        player.get("selected_by_percent")
    )

    chance = player.get(
        "chance_of_playing_next_round"
    )

    if chance is None:
        chance = 100

    availability = safe_float(
        chance,
        100
    ) / 100

    # --------------------------------------------------------
    # BASE QUALITY
    # --------------------------------------------------------

    base = (
        form * 0.30
        + ppg * 0.20
        + (total_points / max(current_gw, 1)) * 0.10
    )

    # --------------------------------------------------------
    # ATTACKING THREAT
    # --------------------------------------------------------

    attacking = (
        xg * 1.25
        + xa * 0.85
    )

    # --------------------------------------------------------
    # BONUS POTENTIAL
    # --------------------------------------------------------

    bonus_component = (
        min(bonus / max(current_gw, 1), 0.8)
        * 1.4
    )

    bps_component = min(
        bps / 100,
        1.0
    ) * 0.8

    # --------------------------------------------------------
    # MINUTES / STARTING PROBABILITY
    # --------------------------------------------------------

    minutes_component = min(
        minutes / max(current_gw * 90, 1),
        1
    )

    starts_component = min(
        starts / max(current_gw, 1),
        1
    )

    start_probability = (
        minutes_component * 0.65
        + starts_component * 0.35
    )

    # Blend with current FPL availability
    start_probability = (
        start_probability * 0.65
        + availability * 0.35
    )

    # --------------------------------------------------------
    # CLEAN SHEET POTENTIAL
    # --------------------------------------------------------

    cs_rate = (
        clean_sheets / max(current_gw, 1)
    )

    position = element_types.get(
        player["element_type"],
        ""
    )

    if position == "GKP":
        cs_weight = 1.15
    elif position == "DEF":
        cs_weight = 1.05
    elif position == "MID":
        cs_weight = 0.35
    else:
        cs_weight = 0.10

    clean_sheet_component = (
        cs_rate * cs_weight
    )

    # --------------------------------------------------------
    # DEFENSIVE CONTRIBUTION POTENTIAL
    # --------------------------------------------------------

    dc = safe_float(
        player.get(
            "defensive_contribution",
            0
        )
    )

    # Defensive contribution is particularly valuable
    # for defenders and defensive midfielders.
    if position == "DEF":
        dc_component = min(
            dc / max(current_gw, 1),
            1.0
        ) * 1.6
    elif position == "MID":
        dc_component = min(
            dc / max(current_gw, 1),
            1.0
        ) * 1.0
    else:
        dc_component = min(
            dc / max(current_gw, 1),
            1.0
        ) * 0.3

    # --------------------------------------------------------
    # FINAL BASE MODEL
    # --------------------------------------------------------

    projection = (
        base
        + attacking
        + bonus_component
        + bps_component
        + clean_sheet_component
        + dc_component
    )

    projection *= (
        0.65
        + (start_probability * 0.35)
    )

    projection *= availability

    return max(
        round(projection, 2),
        0
    )


# ============================================================
# FIXTURE-ADJUSTED PROJECTION
# ============================================================

def fixture_adjusted_projection(player):

    base_projection = calculate_projection(
        player
    )

    fixtures = get_player_fixtures(
        player
    )

    future = [
        f for f in fixtures
        if f["gw"] is not None
        and f["gw"] >= next_gw
        and f["gw"] <= next_gw + 4
    ]

    if not future:
        return base_projection

    adjustment = 0

    for f in future:

        difficulty = f["difficulty"]

        # Easier fixture = positive adjustment
        if difficulty == 1:
            adjustment += 0.22
        elif difficulty == 2:
            adjustment += 0.10
        elif difficulty == 3:
            adjustment += 0
        elif difficulty == 4:
            adjustment -= 0.10
        elif difficulty == 5:
            adjustment -= 0.20

        # Home advantage
        if f["home"]:
            adjustment += 0.04

    avg_adjustment = (
        adjustment / len(future)
    )

    return round(
        base_projection *
        (1 + avg_adjustment),
        2
    )


# ============================================================
# BUILD PLAYER DATAFRAME
# ============================================================

@st.cache_data(ttl=900)
def build_player_dataframe(player_records):

    rows = []

    for p in player_records:

        projection = fixture_adjusted_projection(
            p
        )

        chance = p.get(
            "chance_of_playing_next_round"
        )

        if chance is None:
            chance = 100

        position = element_types.get(
            p["element_type"],
            "?"
        )

        rows.append({

            "ID": p["id"],

            "Name": p["web_name"],

            "Team": teams.get(
                p["team"],
                "?"
            ),

            "Pos": position,

            "Cost": p["now_cost"] / 10,

            "Form": safe_float(
                p.get("form")
            ),

            "PPG": safe_float(
                p.get("points_per_game")
            ),

            "Total": p.get(
                "total_points",
                0
            ),

            "xG": safe_float(
                p.get("expected_goals")
            ),

            "xA": safe_float(
                p.get("expected_assists")
            ),

            "BPS": safe_float(
                p.get("bps")
            ),

            "Bonus": safe_float(
                p.get("bonus")
            ),

            "Def Con": safe_float(
                p.get(
                    "defensive_contribution",
                    0
                )
            ),

            "Ownership": safe_float(
                p.get(
                    "selected_by_percent"
                )
            ),

            "Availability": chance,

            "Projection": projection,

            "Status": p.get(
                "status",
                "a"
            ),

            "News": p.get(
                "news",
                ""
            )
        })

    return pd.DataFrame(rows)


# ============================================================
# USER SETTINGS
# ============================================================

st.title("⚽ FPL Assistant Manager")
st.caption(
    f"Advanced 2026/27 FPL decision engine | "
    f"GW {current_gw} → GW {next_gw}"
)

st.sidebar.header(
    "⚙️ Manager Settings"
)

my_team_id = st.sidebar.number_input(
    "Your FPL Team ID",
    min_value=1,
    value=3240706,
    step=1
)

league_id = st.sidebar.number_input(
    "Mini-League ID",
    min_value=0,
    value=0,
    step=1
)

planning_horizon = st.sidebar.slider(
    "Planning horizon (Gameweeks)",
    min_value=3,
    max_value=8,
    value=5
)

hit_tolerance = st.sidebar.slider(
    "Maximum transfer hit considered",
    min_value=0,
    max_value=12,
    value=4,
    step=4
)

st.sidebar.caption(
    "A -4 is only recommended when the projected "
    "gain is strong enough to justify it."
)


# ============================================================
# USER DATA
# ============================================================

user_data, loaded_gw = get_user_picks(
    my_team_id,
    current_gw
)

if not user_data:

    st.warning(
        "Could not load your FPL squad. "
        "Check your Team ID."
    )

    st.stop()


df_all = build_player_dataframe(
    players
)

picks = user_data.get(
    "picks",
    []
)

my_ids = {
    p["element"]
    for p in picks
}

df_squad = df_all[
    df_all["ID"].isin(my_ids)
].copy()


# ============================================================
# BANK / TEAM VALUE
# ============================================================

entry_history = user_data.get(
    "entry_history",
    {}
)

bank = safe_float(
    entry_history.get(
        "bank",
        0
    )
) / 10

team_value = safe_float(
    entry_history.get(
        "value",
        0
    )
) / 10


# ============================================================
# CHIP HISTORY
# ============================================================

history = get_entry_history(
    my_team_id
) or {}

used_chips = history.get(
    "chips",
    []
)

used_chip_records = []

for chip in used_chips:

    used_chip_records.append({
        "Chip": chip.get("name"),
        "GW": chip.get("event")
    })


def chip_used(name, first_half=True):

    for chip in used_chips:

        if chip.get("name") != name:
            continue

        gw = safe_int(
            chip.get("event")
        )

        if first_half and gw <= 19:
            return True

        if not first_half and gw >= 20:
            return True

    return False


# ============================================================
# TABS
# ============================================================

(
    tab1,
    tab2,
    tab3,
    tab4,
    tab5,
    tab6
) = st.tabs([
    "🏆 Team Optimiser",
    "🔄 Transfers",
    "🩺 Squad Health",
    "👑 Captain & Chips",
    "🕵️ Rival Spy",
    "📈 Player Explorer"
])


# ============================================================
# TAB 1 - TEAM OPTIMISER
# ============================================================

with tab1:

    st.subheader(
        "🏆 Optimal Starting XI"
    )

    if len(df_squad) < 15:

        st.warning(
            "Your squad does not contain 15 players."
        )

    else:

        # ----------------------------------------------------
        # CREATE VALID FORMATIONS
        # ----------------------------------------------------

        goalkeepers = list(
            df_squad[
                df_squad["Pos"] == "GKP"
            ].to_dict("records")
        )

        defenders = list(
            df_squad[
                df_squad["Pos"] == "DEF"
            ].to_dict("records")
        )

        midfielders = list(
            df_squad[
                df_squad["Pos"] == "MID"
            ].to_dict("records")
        )

        forwards = list(
            df_squad[
                df_squad["Pos"] == "FWD"
            ].to_dict("records")
        )

        best_team = None
        best_score = -999

        for d_count in range(3, 6):

            for m_count in range(2, 6):

                for f_count in range(1, 4):

                    if (
                        d_count
                        + m_count
                        + f_count
                        != 10
                    ):
                        continue

                    if len(defenders) < d_count:
                        continue

                    if len(midfielders) < m_count:
                        continue

                    if len(forwards) < f_count:
                        continue

                    for gk in goalkeepers:

                        for defs in itertools.combinations(
                            defenders,
                            d_count
                        ):

                            for mids in itertools.combinations(
                                midfielders,
                                m_count
                            ):

                                for fwds in itertools.combinations(
                                    forwards,
                                    f_count
                                ):

                                    lineup = (
                                        [gk]
                                        + list(defs)
                                        + list(mids)
                                        + list(fwds)
                                    )

                                    score = sum(
                                        x["Projection"]
                                        for x in lineup
                                    )

                                    if score > best_score:

                                        best_score = score
                                        best_team = lineup

        if best_team:

            start_ids = {
                p["ID"]
                for p in best_team
            }

            start_df = df_squad[
                df_squad["ID"].isin(
                    start_ids
                )
            ].copy()

            bench_df = df_squad[
                ~df_squad["ID"].isin(
                    start_ids
                )
            ].copy()

            # Bench order:
            # goalkeeper first, then lowest projected outfield
            bench_gk = bench_df[
                bench_df["Pos"] == "GKP"
            ]

            bench_outfield = bench_df[
                bench_df["Pos"] != "GKP"
            ].sort_values(
                "Projection",
                ascending=True
            )

            bench_df = pd.concat([
                bench_gk,
                bench_outfield
            ])

            captain = start_df.sort_values(
                "Projection",
                ascending=False
            ).iloc[0]

            vice = start_df.sort_values(
                "Projection",
                ascending=False
            ).iloc[1]

            c1, c2, c3, c4 = st.columns(4)

            c1.metric(
                "Captain",
                captain["Name"],
                f"{captain['Projection']:.1f} projected"
            )

            c2.metric(
                "Vice Captain",
                vice["Name"],
                f"{vice['Projection']:.1f} projected"
            )

            c3.metric(
                "Starting XI",
                f"{best_score:.1f}",
                "5-GW model"
            )

            c4.metric(
                "Bank",
                f"£{bank:.1f}m"
            )

            st.markdown(
                "### Starting XI"
            )

            st.dataframe(
                start_df[
                    [
                        "Name",
                        "Team",
                        "Pos",
                        "Cost",
                        "Form",
                        "PPG",
                        "xG",
                        "xA",
                        "Projection"
                    ]
                ].sort_values(
                    ["Pos", "Projection"],
                    ascending=[True, False]
                ),
                hide_index=True,
                use_container_width=True
            )

            st.markdown(
                "### Bench Order"
            )

            st.dataframe(
                bench_df[
                    [
                        "Name",
                        "Team",
                        "Pos",
                        "Projection",
                        "Availability"
                    ]
                ],
                hide_index=True,
                use_container_width=True
            )


# ============================================================
# TAB 2 - TRANSFER ENGINE
# ============================================================

with tab2:

    st.subheader(
        "🔄 Transfer Decision Engine"
    )

    st.caption(
        f"Looking approximately {planning_horizon} "
        "Gameweeks ahead."
    )

    squad_records = df_squad.to_dict(
        "records"
    )

    candidates = []

    for outgoing in squad_records:

        outgoing_id = outgoing["ID"]

        position = outgoing["Pos"]

        selling_price = outgoing["Cost"]

        available_budget = (
            bank
            + selling_price
        )

        for incoming in players:

            if incoming["id"] in my_ids:
                continue

            incoming_position = element_types.get(
                incoming["element_type"]
            )

            if incoming_position != position:
                continue

            incoming_cost = (
                incoming["now_cost"]
                / 10
            )

            if incoming_cost > available_budget:
                continue

            if incoming.get("status") not in [
                "a",
                "d"
            ]:
                continue

            incoming_projection = (
                fixture_adjusted_projection(
                    incoming
                )
            )

            outgoing_projection = (
                outgoing["Projection"]
            )

            gain = (
                incoming_projection
                - outgoing_projection
            )

            # Normal transfer
            net_gain_0 = gain

            # -4 transfer
            net_gain_4 = gain - 4

            # Availability penalty
            chance = incoming.get(
                "chance_of_playing_next_round"
            )

            if chance is None:
                chance = 100

            risk = (
                1
                - (
                    safe_float(chance)
                    / 100
                )
            )

            risk_penalty = (
                risk * 2
            )

            decision_score = (
                gain
                - risk_penalty
            )

            candidates.append({

                "OUT": outgoing["Name"],

                "IN": incoming[
                    "web_name"
                ],

                "Pos": position,

                "Sell £m": selling_price,

                "Buy £m": incoming_cost,

                "Bank After": round(
                    available_budget
                    - incoming_cost,
                    1
                ),

                "Current xP": round(
                    outgoing_projection,
                    2
                ),

                "New xP": round(
                    incoming_projection,
                    2
                ),

                "5-GW Gain": round(
                    gain,
                    2
                ),

                "Gain after -4": round(
                    net_gain_4,
                    2
                ),

                "Risk": round(
                    risk * 100,
                    1
                ),

                "Decision Score": round(
                    decision_score,
                    2
                )
            })

    if candidates:

        transfer_df = pd.DataFrame(
            candidates
        )

        transfer_df = transfer_df.sort_values(
            "Decision Score",
            ascending=False
        )

        # ----------------------------------------------------
        # FREE TRANSFER
        # ----------------------------------------------------

        st.markdown(
            "### 🟢 Best Transfers — No Hit"
        )

        no_hit = transfer_df[
            transfer_df["5-GW Gain"] > 0
        ].head(10)

        if len(no_hit):

            st.dataframe(
                no_hit,
                hide_index=True,
                use_container_width=True
            )

        else:

            st.info(
                "No clear positive transfer found."
            )

        # ----------------------------------------------------
        # -4 HIT
        # ----------------------------------------------------

        st.markdown(
            "### 🟠 Transfers Worth Considering for -4"
        )

        hit_df = transfer_df[
            transfer_df["Gain after -4"] >= 1
        ].head(10)

        if len(hit_df):

            st.dataframe(
                hit_df,
                hide_index=True,
                use_container_width=True
            )

            st.caption(
                "The model only shows a -4 when the "
                "projected gain over the planning horizon "
                "comfortably covers the hit."
            )

        else:

            st.info(
                "No -4 transfer currently looks "
                "strong enough."
            )

        # ----------------------------------------------------
        # FUNDING MOVES
        # ----------------------------------------------------

        st.markdown(
            "### 💰 Sell-to-Fund Opportunities"
        )

        funding = transfer_df[
            (
                transfer_df["Buy £m"]
                > transfer_df["Sell £m"]
            )
            &
            (
                transfer_df["5-GW Gain"]
                >= 3
            )
        ].head(10)

        if len(funding):

            st.dataframe(
                funding,
                hide_index=True,
                use_container_width=True
            )

            st.caption(
                "These moves deliberately spend more than "
                "the outgoing player's current price by "
                "using your bank. They can be useful when "
                "upgrading one position is worth sacrificing "
                "money elsewhere."
            )

        else:

            st.info(
                "No obvious premium upgrade needs funding "
                "right now."
            )


# ============================================================
# TAB 3 - SQUAD HEALTH
# ============================================================

with tab3:

    st.subheader(
        "🩺 Squad Health"
    )

    health_rows = []

    for _, row in df_squad.iterrows():

        availability = row["Availability"]

        if row["Status"] in [
            "i",
            "s",
            "u"
        ]:

            verdict = "🔴 SELL / REPLACE"

        elif availability < 75:

            verdict = "🟠 MINUTES RISK"

        elif row["Projection"] < 3:

            verdict = "🔴 WEAK"

        elif row["Projection"] < 4.5:

            verdict = "🟡 MONITOR"

        elif row["Projection"] >= 6:

            verdict = "🟢 STRONG HOLD"

        else:

            verdict = "🟢 HOLD"

        health_rows.append({

            "Player": row["Name"],

            "Team": row["Team"],

            "Pos": row["Pos"],

            "Price": row["Cost"],

            "Form": row["Form"],

            "PPG": row["PPG"],

            "Projection": row[
                "Projection"
            ],

            "Availability": availability,

            "Ownership": row[
                "Ownership"
            ],

            "Verdict": verdict,

            "News": row["News"]
        })

    health_df = pd.DataFrame(
        health_rows
    )

    st.dataframe(
        health_df,
        hide_index=True,
        use_container_width=True
    )

    st.markdown(
        "### 🚨 Players Needing Attention"
    )

    danger = health_df[
        health_df["Verdict"].isin([
            "🔴 SELL / REPLACE",
            "🔴 WEAK",
            "🟠 MINUTES RISK"
        ])
    ]

    if len(danger):

        st.dataframe(
            danger,
            hide_index=True,
            use_container_width=True
        )

    else:

        st.success(
            "No major squad problems detected."
        )


# ============================================================
# TAB 4 - CAPTAIN + CHIP PLANNER
# ============================================================

with tab4:

    st.subheader(
        "👑 Captain Matrix"
    )

    if "start_df" in locals():

        captain_candidates = start_df.sort_values(
            "Projection",
            ascending=False
        ).head(6)

        st.dataframe(
            captain_candidates[
                [
                    "Name",
                    "Team",
                    "Pos",
                    "Projection",
                    "Form",
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

    chips = [
        ("Wildcard", "wildcard"),
        ("Free Hit", "freehit"),
        ("Bench Boost", "bboost"),
        ("Triple Captain", "3xc")
    ]

    chip_rows = []

    for display, code in chips:

        first = chip_used(
            code,
            True
        )

        second = chip_used(
            code,
            False
        )

        chip_rows.append({

            "Chip": display,

            "1st Half": (
                "USED"
                if first
                else "AVAILABLE"
            ),

            "2nd Half": (
                "USED"
                if second
                else "AVAILABLE"
            )
        })

    chip_df = pd.DataFrame(
        chip_rows
    )

    st.dataframe(
        chip_df,
        hide_index=True,
        use_container_width=True
    )

    # --------------------------------------------------------
    # BLANK / DOUBLE GAMEWEEK DETECTION
    # --------------------------------------------------------

    st.markdown(
        "### 📅 Fixture Opportunity Scan"
    )

    fixture_summary = []

    for gw in range(
        next_gw,
        min(next_gw + 8, 39)
    ):

        gw_fixtures = [
            f for f in all_fixtures
            if f.get("event") == gw
        ]

        appearances = {}

        for f in gw_fixtures:

            home = f.get("team_h")
            away = f.get("team_a")

            appearances[home] = (
                appearances.get(home, 0)
                + 1
            )

            appearances[away] = (
                appearances.get(away, 0)
                + 1
            )

        doubles = [
            teams.get(t, "?")
            for t, count in appearances.items()
            if count >= 2
        ]

        teams_with_fixtures = len(
            appearances
        )

        fixture_summary.append({

            "GW": gw,

            "Teams with fixture":
                teams_with_fixtures,

            "Blank teams":
                max(
                    20
                    - teams_with_fixtures,
                    0
                ),

            "Double Gameweek teams":
                ", ".join(doubles)
                if doubles
                else "-"
        })

    st.dataframe(
        pd.DataFrame(
            fixture_summary
        ),
        hide_index=True,
        use_container_width=True
    )

    st.info(
        "Chip decisions are strategic recommendations, "
        "not guarantees. Double Gameweeks can favour "
        "Bench Boost and Triple Captain, while Blank "
        "Gameweeks can favour Free Hit."
    )


# ============================================================
# TAB 5 - RIVAL SPY
# ============================================================

with tab5:

    st.subheader(
        "🕵️ Mini-League Rival Spy"
    )

    if league_id == 0:

        st.info(
            "Enter your Mini-League ID in the sidebar."
        )

    else:

        league = get_league_standings(
            league_id
        )

        if not league:

            st.error(
                "Could not load the mini-league."
            )

        else:

            standings = (
                league
                .get("standings", {})
                .get("results", [])
            )

            if not standings:

                st.warning(
                    "No league standings available."
                )

            else:

                leader = standings[0]

                my_entry = next(
                    (
                        x for x in standings
                        if x.get("entry")
                        == my_team_id
                    ),
                    None
                )

                if my_entry:

                    gap = (
                        leader["total"]
                        - my_entry["total"]
                    )

                    r1, r2, r3 = st.columns(3)

                    r1.metric(
                        "Your Rank",
                        f"#{my_entry['rank']}"
                    )

                    r2.metric(
                        "Leader",
                        leader[
                            "player_name"
                        ]
                    )

                    r3.metric(
                        "Points Behind",
                        gap
                    )

                leader_id = leader[
                    "entry"
                ]

                leader_picks, _ = get_user_picks(
                    leader_id,
                    current_gw
                )

                if leader_picks:

                    leader_ids = {
                        p["element"]
                        for p in
                        leader_picks.get(
                            "picks",
                            []
                        )
                    }

                    shared = (
                        my_ids
                        &
                        leader_ids
                    )

                    my_diffs = (
                        my_ids
                        -
                        leader_ids
                    )

                    leader_diffs = (
                        leader_ids
                        -
                        my_ids
                    )

                    overlap = round(
                        len(shared)
                        / 15
                        * 100,
                        1
                    )

                    st.markdown(
                        f"### Squad overlap: "
                        f"**{overlap}%**"
                    )

                    c1, c2 = st.columns(2)

                    with c1:

                        st.markdown(
                            "### Your Differentials"
                        )

                        st.dataframe(
                            df_squad[
                                df_squad["ID"]
                                .isin(
                                    my_diffs
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

                        leader_df = df_all[
                            df_all["ID"]
                            .isin(
                                leader_diffs
                            )
                        ]

                        st.markdown(
                            "### Leader's Differentials"
                        )

                        st.dataframe(
                            leader_df[
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

                    st.markdown(
                        "### 🎯 Catch-Up Targets"
                    )

                    catchup = df_all[
                        (
                            ~df_all["ID"].isin(
                                my_ids
                            )
                        )
                        &
                        (
                            ~df_all["ID"].isin(
                                leader_ids
                            )
                        )
                        &
                        (
                            df_all[
                                "Availability"
                            ] >= 75
                        )
                    ].sort_values(
                        "Projection",
                        ascending=False
                    ).head(10)

                    st.dataframe(
                        catchup[
                            [
                                "Name",
                                "Team",
                                "Pos",
                                "Cost",
                                "Projection",
                                "Ownership",
                                "Form",
                                "xG",
                                "xA"
                            ]
                        ],
                        hide_index=True,
                        use_container_width=True
                    )


# ============================================================
# TAB 6 - PLAYER EXPLORER
# ============================================================

with tab6:

    st.subheader(
        "📈 Player Explorer"
    )

    selected_name = st.selectbox(
        "Choose a player",
        sorted(
            df_all["Name"].unique()
        )
    )

    selected_row = df_all[
        df_all["Name"]
        == selected_name
    ].iloc[0]

    player = elements[
        selected_row["ID"]
    ]

    fixtures = get_player_fixtures(
        player
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Projected",
        f"{selected_row['Projection']:.1f}"
    )

    c2.metric(
        "Form",
        f"{selected_row['Form']:.1f}"
    )

    c3.metric(
        "xG",
        f"{selected_row['xG']:.2f}"
    )

    c4.metric(
        "xA",
        f"{selected_row['xA']:.2f}"
    )

    st.markdown(
        "### Upcoming Fixtures"
    )

    fixture_rows = []

    for f in fixtures:

        if f["gw"] is None:
            continue

        if f["gw"] < next_gw:
            continue

        if f["gw"] > (
            next_gw
            + planning_horizon
            - 1
        ):
            continue

        fixture_rows.append({

            "GW": f["gw"],

            "Opponent": f["opponent"],

            "Home":
                "Yes"
                if f["home"]
                else "No",

            "Difficulty":
                f["difficulty"]
        })

    if fixture_rows:

        st.dataframe(
            pd.DataFrame(
                fixture_rows
            ),
            hide_index=True,
            use_container_width=True
        )

    metrics = historical_metrics(
        selected_row["ID"]
    )

    st.markdown(
        "### Historical Performance"
    )

    h1, h2, h3, h4, h5 = st.columns(5)

    h1.metric(
        "Avg Points",
        f"{metrics['avg_points']:.1f}"
    )

    h2.metric(
        "Avg Minutes",
        f"{metrics['avg_minutes']:.0f}"
    )

    h3.metric(
        "Avg Bonus",
        f"{metrics['avg_bonus']:.2f}"
    )

    h4.metric(
        "Avg BPS",
        f"{metrics['avg_bps']:.1f}"
    )

    h5.metric(
        "Avg Def Con",
        f"{metrics['avg_dc']:.1f}"
    )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "FPL Assistant Manager | "
    "Uses official FPL API data. "
    "Projected points are a model estimate, "
    "not an official FPL prediction."
)

st.caption(
    f"Data refreshed automatically | "
    f"Planning GW {next_gw}"
        ) 
