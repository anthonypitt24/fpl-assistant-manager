import streamlit as st
import requests
import pandas as pd
from itertools import combinations
from collections import Counter
from datetime import datetime

# ============================================================
# FPL ASSISTANT MANAGER
# COMPLETE VERSION
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)

# ============================================================
# SETTINGS
# ============================================================

FPL_BASE = "https://fantasy.premierleague.com/api"

REQUEST_TIMEOUT = 15

# ============================================================
# ELITE MANAGERS
#
# IMPORTANT:
# IDs are verified against the actual FPL entry.
# Verification uses the ID first, NOT the team name.
# ============================================================

ELITE_MANAGERS = [
    {
        "name": "Abinav C",
        "entry_id": 175376,
        "hof_rank": 3,
        "aliases": ["Abinav C", "Taken Quickly Origi"]
    },
    {
        "name": "John Walsh",
        "entry_id": 1519295,
        "hof_rank": 5,
        "aliases": ["John Walsh", "Dinho's Disciples"]
    },
    {
        "name": "FPL Harry",
        "entry_id": 1320,
        "hof_rank": 10,
        "aliases": ["FPL Harry", "Harry Daniels", "DANIELS XI"]
    },
    {
        "name": "Keilan Kenny",
        "entry_id": None,
        "hof_rank": 38,
        "aliases": ["Keilan Kenny"]
    },
    {
        "name": "Nick (FPL Spartan)",
        "entry_id": None,
        "hof_rank": 63,
        "aliases": ["Nick", "FPL Spartan"]
    }
]

# ============================================================
# SESSION STATE
# ============================================================

if "bootstrap" not in st.session_state:
    st.session_state.bootstrap = None

if "user_team" not in st.session_state:
    st.session_state.user_team = None

if "elite_data" not in st.session_state:
    st.session_state.elite_data = {}

# ============================================================
# API HELPERS
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def get_json(url):
    try:
        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 FPL Assistant Manager"
            }
        )

        r.raise_for_status()
        return r.json()

    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def get_bootstrap():
    return get_json(f"{FPL_BASE}/bootstrap-static/")


@st.cache_data(ttl=120, show_spinner=False)
def get_entry(entry_id):
    if not entry_id:
        return None

    return get_json(
        f"{FPL_BASE}/entry/{int(entry_id)}/"
    )


@st.cache_data(ttl=120, show_spinner=False)
def get_picks(entry_id, gameweek):
    if not entry_id:
        return None

    return get_json(
        f"{FPL_BASE}/entry/{int(entry_id)}/event/{int(gameweek)}/picks/"
    )


@st.cache_data(ttl=300, show_spinner=False)
def get_fixtures():
    return get_json(
        f"{FPL_BASE}/fixtures/"
    )


# ============================================================
# LOAD BOOTSTRAP
# ============================================================

bootstrap = get_bootstrap()

if not bootstrap:
    st.error(
        "❌ I couldn't connect to the official FPL API right now."
    )
    st.stop()

st.session_state.bootstrap = bootstrap

players = bootstrap.get("elements", [])
teams = bootstrap.get("teams", [])
events = bootstrap.get("events", [])

# ============================================================
# DATA DICTIONARIES
# ============================================================

PLAYER_BY_ID = {
    p["id"]: p
    for p in players
}

TEAM_BY_ID = {
    t["id"]: t
    for t in teams
}

TEAM_NAME_BY_ID = {
    t["id"]: t["name"]
    for t in teams
}

POSITION_NAMES = {
    1: "GK",
    2: "DEF",
    3: "MID",
    4: "FWD"
}

POSITION_LIMITS = {
    "GK": 1,
    "DEF": 5,
    "MID": 5,
    "FWD": 3
}

# ============================================================
# CURRENT GAMEWEEK
# ============================================================

current_event = None

for event in events:
    if event.get("is_current"):
        current_event = event
        break

if current_event is None:
    for event in events:
        if event.get("is_next"):
            current_event = event
            break

if current_event is None and events:
    current_event = events[-1]

CURRENT_GW = (
    current_event.get("id", 1)
    if current_event
    else 1
)

CURRENT_GW_NAME = f"GW{CURRENT_GW}"

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def money(value):
    try:
        return f"£{value / 10:.1f}m"
    except Exception:
        return "—"


def player_name(player):
    return (
        player.get("web_name")
        or f"{player.get('first_name', '')} "
           f"{player.get('second_name', '')}".strip()
    )


def player_position(player):
    return POSITION_NAMES.get(
        player.get("element_type"),
        "?"
    )


def get_player_price(player):
    return player.get("now_cost", 0) / 10


def get_player_team(player):
    return TEAM_NAME_BY_ID.get(
        player.get("team"),
        "Unknown"
    )


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def normalise_name(name):
    if not name:
        return ""

    return (
        str(name)
        .lower()
        .replace("'", "")
        .replace("-", " ")
        .strip()
    )


# ============================================================
# ENTRY VERIFICATION
# ============================================================

def verify_elite_manager(manager):
    entry_id = manager.get("entry_id")

    if not entry_id:
        return {
            "status": "🟠 ID needed",
            "verified": False,
            "entry": None,
            "reason": "No FPL ID has been entered."
        }

    entry = get_entry(entry_id)

    if not entry:
        return {
            "status": "🔴 API error",
            "verified": False,
            "entry": None,
            "reason": "FPL entry could not be loaded."
        }

    # --------------------------------------------------------
    # ID-FIRST VERIFICATION
    #
    # If the official FPL API successfully returns the entry
    # for the hard-coded ID, the ID is valid.
    #
    # We DO NOT require the FPL team name to equal the creator
    # name.
    # --------------------------------------------------------

    returned_id = safe_int(
        entry.get("id"),
        -1
    )

    if returned_id == int(entry_id):
        return {
            "status": "🟢 Verified",
            "verified": True,
            "entry": entry,
            "reason": "FPL ID confirmed."
        }

    return {
        "status": "⚠️ ID mismatch",
        "verified": False,
        "entry": entry,
        "reason": (
            f"Expected ID {entry_id}, "
            f"but FPL returned {returned_id}."
        )
    }


# ============================================================
# LOAD ELITE MANAGERS
# ============================================================

for manager in ELITE_MANAGERS:

    name = manager["name"]

    st.session_state.elite_data[name] = (
        verify_elite_manager(manager)
    )

# ============================================================
# ENTRY DETAILS
# ============================================================

def get_entry_summary(entry):
    if not entry:
        return {}

    return {
        "id": entry.get("id"),
        "player_name": (
            f"{entry.get('player_first_name', '')} "
            f"{entry.get('player_last_name', '')}"
        ).strip(),
        "team_name": entry.get("name", "Unknown"),
        "overall_points": entry.get("summary_overall_points", 0),
        "overall_rank": entry.get("summary_overall_rank", 0),
        "gw_points": entry.get("summary_event_points", 0),
        "bank": entry.get("last_deadline_bank", 0) / 10,
        "value": entry.get("last_deadline_value", 0) / 10,
        "total_transfers": entry.get("last_deadline_total_transfers", 0),
    }


# ============================================================
# PICKS PROCESSING
# ============================================================

def process_picks(picks_response):
    if not picks_response:
        return []

    result = []

    for pick in picks_response.get("picks", []):

        pid = pick.get("element")

        player = PLAYER_BY_ID.get(pid)

        if not player:
            continue

        result.append({
            "id": pid,
            "name": player_name(player),
            "position": player_position(player),
            "club": get_player_team(player),
            "price": get_player_price(player),
            "multiplier": pick.get("multiplier", 1),
            "is_captain": pick.get("is_captain", False),
            "is_vice": pick.get("is_vice_captain", False),
            "position_in_team": pick.get(
                "position",
                0
            )
        })

    return result


# ============================================================
# TEAM DISPLAY
# ============================================================

def team_dataframe(players_list):

    rows = []

    for p in players_list:

        rows.append({
            "Player": p["name"],
            "Pos": p["position"],
            "Club": p["club"],
            "Price": f"£{p['price']:.1f}m",
            "Captain": "©" if p.get("is_captain") else "",
            "Vice": "V" if p.get("is_vice") else ""
        })

    return pd.DataFrame(rows)


# ============================================================
# BEST XI
# ============================================================

def calculate_best_11(team_players):
    """
    Selects the best XI ONLY from the user's current squad.

    This preserves the working Best 11 feature.
    """

    if not team_players:
        return []

    # Sort by recent/expected FPL value where available.
    # Current form is used as the primary simple signal.
    sorted_players = sorted(
        team_players,
        key=lambda p: (
            PLAYER_BY_ID.get(
                p["id"],
                {}
            ).get("form", 0)
        ),
        reverse=True
    )

    # Need exactly:
    # 1 GK
    # 3-5 DEF
    # 2-5 MID
    # 1-3 FWD

    gks = [
        p for p in sorted_players
        if p["position"] == "GK"
    ]

    defs = [
        p for p in sorted_players
        if p["position"] == "DEF"
    ]

    mids = [
        p for p in sorted_players
        if p["position"] == "MID"
    ]

    fwds = [
        p for p in sorted_players
        if p["position"] == "FWD"
    ]

    if not gks:
        return []

    best = []

    # Best goalkeeper
    best.append(gks[0])

    # --------------------------------------------------------
    # Try all legal formations and choose highest form total.
    # --------------------------------------------------------

    candidates = []

    for def_count in range(3, 6):

        for mid_count in range(2, 6):

            fwd_count = 11 - 1 - def_count - mid_count

            if fwd_count < 1 or fwd_count > 3:
                continue

            if (
                len(defs) < def_count
                or len(mids) < mid_count
                or len(fwds) < fwd_count
            ):
                continue

            chosen_defs = defs[:def_count]
            chosen_mids = mids[:mid_count]
            chosen_fwds = fwds[:fwd_count]

            xi = (
                [gks[0]]
                + chosen_defs
                + chosen_mids
                + chosen_fwds
            )

            score = 0

            for p in xi:

                player = PLAYER_BY_ID.get(
                    p["id"],
                    {}
                )

                score += float(
                    player.get("form", 0) or 0
                )

                score += (
                    float(
                        player.get(
                            "points_per_game",
                            0
                        ) or 0
                    ) * 0.5
                )

            candidates.append(
                (score, xi)
            )

    if candidates:

        candidates.sort(
            key=lambda x: x[0],
            reverse=True
        )

        return candidates[0][1]

    return best


# ============================================================
# ELITE CONSENSUS
# ============================================================

def calculate_consensus(elite_teams):
    counter = Counter()

    player_lookup = {}

    for manager_name, players_list in elite_teams.items():

        for p in players_list:

            pid = p["id"]

            counter[pid] += 1
            player_lookup[pid] = p

    rows = []

    total_managers = len(elite_teams)

    for pid, count in counter.most_common():

        p = player_lookup[pid]

        rows.append({
            "Player": p["name"],
            "Pos": p["position"],
            "Club": p["club"],
            "Managers": count,
            "Consensus": f"{count}/{total_managers}",
            "Price": f"£{p['price']:.1f}m"
        })

    return pd.DataFrame(rows)


# ============================================================
# MAIN HEADER
# ============================================================

st.title("⚽ FPL Assistant Manager")

st.caption(
    f"2026/27 season • Current Gameweek: {CURRENT_GW}"
)

# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header("⚙️ Your FPL Team")

entry_input = st.sidebar.text_input(
    "Enter your FPL Team ID",
    placeholder="e.g. 1234567"
)

load_team = st.sidebar.button(
    "🔄 Load My Team",
    use_container_width=True
)

if load_team:

    try:
        entry_id = int(entry_input)

        entry = get_entry(entry_id)

        if entry:

            st.session_state.user_team = {
                "entry_id": entry_id,
                "entry": entry
            }

            st.sidebar.success(
                "Team loaded successfully."
            )

        else:

            st.sidebar.error(
                "Couldn't find that FPL team ID."
            )

    except ValueError:

        st.sidebar.error(
            "Please enter a valid numeric FPL ID."
        )


# ============================================================
# TABS
# ============================================================

tabs = st.tabs([
    "🏠 Dashboard",
    "⭐ Elite Managers",
    "👥 My Team",
    "📋 Best 11",
    "🔄 Transfers",
    "📊 Player Data"
])

# ============================================================
# DASHBOARD
# ============================================================

with tabs[0]:

    st.subheader("📊 FPL Dashboard")

    if not st.session_state.user_team:

        st.info(
            "Enter your FPL Team ID in the sidebar to load your team."
        )

        st.markdown(
            """
            ### What this app does

            - 📊 Analyse your FPL team
            - ⭐ Follow elite FPL managers
            - 👥 Compare elite squads
            - 📋 Pick the best XI from your own squad
            - 🔥 Find popular elite-manager players
            - 🔄 Help identify transfer options
            - 📈 Show player statistics
            """
        )

    else:

        entry = st.session_state.user_team["entry"]

        summary = get_entry_summary(entry)

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Overall Rank",
            f"{summary['overall_rank']:,}"
            if summary["overall_rank"]
            else "—"
        )

        c2.metric(
            "Total Points",
            f"{summary['overall_points']:,}"
        )

        c3.metric(
            f"GW{CURRENT_GW}",
            summary["gw_points"]
        )

        c4.metric(
            "Team Value",
            f"£{summary['value']:.1f}m"
        )

        st.divider()

        st.write(
            f"### {summary['team_name']}"
        )

        st.write(
            f"Manager: **{summary['player_name']}**"
        )

        st.write(
            f"Transfers made: **{summary['total_transfers']}**"
        )


# ============================================================
# ELITE MANAGERS
# ============================================================

with tabs[1]:

    st.subheader("🏆 Elite Manager Overview")

    st.write(
        "These are the five Hall of Fame managers being tracked."
    )

    overview_rows = []

    for manager in ELITE_MANAGERS:

        name = manager["name"]
        result = st.session_state.elite_data.get(
            name,
            {}
        )

        entry = result.get("entry")

        if entry:

            summary = get_entry_summary(entry)

            team_name = summary["team_name"]
            gw_points = summary["gw_points"]
            rank = summary["overall_rank"]

        else:

            team_name = "—"
            gw_points = "—"
            rank = "—"

        overview_rows.append({
            "Manager": name,
            "HOF Rank": manager["hof_rank"],
            "FPL ID": (
                manager["entry_id"]
                if manager["entry_id"]
                else "—"
            ),
            "Status": result.get(
                "status",
                "—"
            ),
            "FPL Team": team_name,
            "GW Points": gw_points,
            "Overall Rank": rank
        })

    overview_df = pd.DataFrame(
        overview_rows
    )

    st.dataframe(
        overview_df,
        use_container_width=True,
        hide_index=True
    )

    verified_count = sum(
        1
        for x in st.session_state.elite_data.values()
        if x.get("verified")
    )

    st.metric(
        "Verified Managers",
        f"{verified_count}/5"
    )

    st.divider()

    st.subheader(
        "🔎 ID Verification"
    )

    st.caption(
        "Verification is based on the official FPL entry ID. "
        "The manager's FPL team name does not need to match their "
        "creator/real name."
    )

    verification_rows = []

    for manager in ELITE_MANAGERS:

        result = st.session_state.elite_data.get(
            manager["name"],
            {}
        )

        entry = result.get("entry")

        verification_rows.append({
            "Manager": manager["name"],
            "Expected ID": (
                manager["entry_id"]
                if manager["entry_id"]
                else "Not entered"
            ),
            "FPL Name": (
                get_entry_summary(entry)["player_name"]
                if entry
                else "—"
            ),
            "FPL Team": (
                get_entry_summary(entry)["team_name"]
                if entry
                else "—"
            ),
            "Status": result.get(
                "status",
                "—"
            )
        })

    st.dataframe(
        pd.DataFrame(verification_rows),
        use_container_width=True,
        hide_index=True
    )

    st.divider()

    # --------------------------------------------------------
    # LOAD ELITE SQUADS
    # --------------------------------------------------------

    elite_teams = {}

    for manager in ELITE_MANAGERS:

        name = manager["name"]
        entry_id = manager["entry_id"]

        if not entry_id:
            continue

        result = st.session_state.elite_data.get(
            name,
            {}
        )

        if not result.get("verified"):
            continue

        picks = get_picks(
            entry_id,
            CURRENT_GW
        )

        players_list = process_picks(
            picks
        )

        if players_list:
            elite_teams[name] = players_list

    if elite_teams:

        st.subheader(
            "🔥 Elite Manager Squads"
        )

        for manager_name, players_list in elite_teams.items():

            with st.expander(
                f"⭐ {manager_name}"
            ):

                st.dataframe(
                    team_dataframe(
                        players_list
                    ),
                    use_container_width=True,
                    hide_index=True
                )

        st.divider()

        st.subheader(
            "🔥 Top Consensus Players"
        )

        consensus_df = calculate_consensus(
            elite_teams
        )

        if not consensus_df.empty:

            st.dataframe(
                consensus_df.head(15),
                use_container_width=True,
                hide_index=True
            )

            top_player = consensus_df.iloc[0]["Player"]

            st.metric(
                "Top Consensus Player",
                top_player
            )

    else:

        st.warning(
            "No verified elite-manager squads could be loaded yet."
        )


# ============================================================
# MY TEAM
# ============================================================

with tabs[2]:

    st.subheader("👥 My Current Team")

    if not st.session_state.user_team:

        st.info(
            "Load your FPL team first."
        )

    else:

        entry_id = st.session_state.user_team[
            "entry_id"
        ]

        picks = get_picks(
            entry_id,
            CURRENT_GW
        )

        my_players = process_picks(
            picks
        )

        if not my_players:

            st.warning(
                "Your squad could not be loaded."
            )

        else:

            st.dataframe(
                team_dataframe(
                    my_players
                ),
                use_container_width=True,
                hide_index=True
            )

            total_value = sum(
                p["price"]
                for p in my_players
            )

            st.metric(
                "Squad Value",
                f"£{total_value:.1f}m"
            )


# ============================================================
# BEST 11
# ============================================================

with tabs[3]:

    st.subheader("📋 Best 11")

    st.caption(
        "This selects players ONLY from your current FPL squad."
    )

    if not st.session_state.user_team:

        st.info(
            "Load your FPL team first."
        )

    else:

        entry_id = st.session_state.user_team[
            "entry_id"
        ]

        picks = get_picks(
            entry_id,
            CURRENT_GW
        )

        my_players = process_picks(
            picks
        )

        best_11 = calculate_best_11(
            my_players
        )

        if len(best_11) == 11:

            st.success(
                "Best XI calculated from your current squad."
            )

            st.dataframe(
                team_dataframe(
                    best_11
                ),
                use_container_width=True,
                hide_index=True
            )

            formation = (
                f"{sum(1 for p in best_11 if p['position'] == 'DEF')}-"
                f"{sum(1 for p in best_11 if p['position'] == 'MID')}-"
                f"{sum(1 for p in best_11 if p['position'] == 'FWD')}"
            )

            st.metric(
                "Recommended Formation",
                formation
            )

        else:

            st.warning(
                "I couldn't create a complete legal XI from the loaded squad."
            )


# ============================================================
# TRANSFERS
# ============================================================

with tabs[4]:

    st.subheader("🔄 Transfer Ideas")

    if not st.session_state.user_team:

        st.info(
            "Load your FPL team first."
        )

    else:

        entry_id = st.session_state.user_team[
            "entry_id"
        ]

        picks = get_picks(
            entry_id,
            CURRENT_GW
        )

        my_players = process_picks(
            picks
        )

        current_ids = {
            p["id"]
            for p in my_players
        }

        st.write(
            "Potential players to consider based on current FPL statistics."
        )

        candidate_rows = []

        for player in players:

            if player["id"] in current_ids:
                continue

            if player.get("minutes", 0) <= 0:
                continue

            candidate_rows.append({
                "Player": player_name(player),
                "Pos": player_position(player),
                "Club": get_player_team(player),
                "Price": get_player_price(player),
                "Form": float(
                    player.get("form", 0) or 0
                ),
                "PPG": float(
                    player.get("points_per_game", 0) or 0
                ),
                "Total": player.get(
                    "total_points",
                    0
                )
            })

        candidate_df = pd.DataFrame(
            candidate_rows
        )

        if not candidate_df.empty:

            candidate_df = candidate_df.sort_values(
                ["Form", "PPG", "Total"],
                ascending=False
            )

            candidate_df["Price"] = candidate_df[
                "Price"
            ].map(
                lambda x: f"£{x:.1f}m"
            )

            st.dataframe(
                candidate_df.head(30),
                use_container_width=True,
                hide_index=True
            )


# ============================================================
# PLAYER DATA
# ============================================================

with tabs[5]:

    st.subheader("📊 Player Data")

    search = st.text_input(
        "Search for a player"
    )

    filtered_players = players

    if search:

        search_lower = search.lower()

        filtered_players = [
            p for p in players
            if search_lower in player_name(p).lower()
        ]

    rows = []

    for player in filtered_players:

        rows.append({
            "Player": player_name(player),
            "Pos": player_position(player),
            "Club": get_player_team(player),
            "Price": f"£{get_player_price(player):.1f}m",
            "Total Points": player.get(
                "total_points",
                0
            ),
            "Form": player.get(
                "form",
                0
            ),
            "PPG": player.get(
                "points_per_game",
                0
            ),
            "Selected %": player.get(
                "selected_by_percent",
                0
            ),
            "Minutes": player.get(
                "minutes",
                0
            ),
            "Goals": player.get(
                "goals_scored",
                0
            ),
            "Assists": player.get(
                "assists",
                0
            )
        })

    player_df = pd.DataFrame(
        rows
    )

    if not player_df.empty:

        st.dataframe(
            player_df.sort_values(
                "Total Points",
                ascending=False
            ).head(100),
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "FPL Assistant Manager • Data supplied by the official "
    "Fantasy Premier League API"
)

st.caption(
    f"Last loaded: {datetime.now().strftime('%d/%m/%Y %H:%M')}"
)
