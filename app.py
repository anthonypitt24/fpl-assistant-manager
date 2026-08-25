from collections import defaultdict
import re

import pandas as pd
import requests
import streamlit as st

# Optional packages are imported safely so the main FPL app can still show a
# useful error if an optional dependency is missing.
try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except Exception:
    YouTubeTranscriptApi = None


# ============================================================
# FPL ASSISTANT MANAGER
# ============================================================
# Main improvements in this version:
# - Uses current Gemini 3.6 Flash instead of retired Gemini 2.5 Flash.
# - Centralises all Gemini calls so Creator Analysis and AI Assistant
#   use the same reliable model/fallback logic.
# - Safer YouTube transcript extraction, including mobile/shorts URLs.
# - Fixes several edge cases around fixtures, missing player data and
#   incomplete squads.
# - Keeps Best XI limited strictly to the user's current squad.
# - Adds clearer transfer, captain, fixture and chip analysis.
# - Removes the inaccurate "Opta" branding: these are FPL API
#   underlying/expected metrics.
# ============================================================

st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide",
)

API = "https://fantasy.premierleague.com/api"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/139 Safari/537.36"
    )
}

FIXTURE_HORIZON = 5
PROJECTION_WEEKS = 4
MAX_PER_CLUB = 3
TRANSFER_HIT = 4

VALID_FORMATIONS = [
    (3, 4, 3),
    (3, 5, 2),
    (4, 4, 2),
    (4, 3, 3),
    (4, 5, 1),
    (5, 4, 1),
    (5, 3, 2),
    (5, 2, 3),
]

# Gemini 3.6 Flash is the current stable model used by this app.
# You can override it in Streamlit secrets with GEMINI_MODEL if needed.
DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
GEMINI_FALLBACK_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
]

CREATOR_CHANNELS = {
    "FPL Harry": "https://www.youtube.com/@FPLHarry",
    "Let's Talk FPL (Andy)": "https://www.youtube.com/@LetsTalkFPL",
    "FPL Focal (Oscar)": "https://www.youtube.com/@FPLFocal",
    "FPL Mate (Dan)": "https://www.youtube.com/@FPLMate",
    "Planet FPL (James & Suj)": "https://www.youtube.com/@PlanetFPL",
}

ELITE_MANAGERS = {
    "Abinav C": {"entry_id": 175376, "hall_of_fame": 3},
    "John Walsh": {"entry_id": 1519295, "hall_of_fame": 5},
    "FPL Harry": {"entry_id": 1320, "hall_of_fame": 10},
    "Keilan Kenny": {"entry_id": None, "hall_of_fame": 38},
    "Nick (FPL Spartan)": {"entry_id": None, "hall_of_fame": 63},
}


# ============================================================
# API HELPERS
# ============================================================
@st.cache_data(ttl=300, show_spinner=False)
def api_get(url):
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_info(entry_id):
    return api_get(f"{API}/entry/{entry_id}/")


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_picks(entry_id, gameweek):
    return api_get(f"{API}/entry/{entry_id}/event/{gameweek}/picks/")


@st.cache_data(ttl=300, show_spinner=False)
def get_entry_transfers(entry_id):
    return api_get(f"{API}/entry/{entry_id}/transfers/")


@st.cache_data(ttl=300, show_spinner=False)
def get_league(league_id):
    return api_get(f"{API}/leagues-classic/{league_id}/standings/")


@st.cache_data(ttl=600, show_spinner=False)
def get_team_history(entry_id):
    return api_get(f"{API}/entry/{entry_id}/history/")


@st.cache_data(ttl=300, show_spinner=False)
def get_live_gw(gameweek):
    data = api_get(f"{API}/event/{gameweek}/live/")
    return {
        element["id"]: element.get("stats", {}).get("total_points", 0)
        for element in data.get("elements", [])
    }


# ============================================================
# GENERIC HELPERS
# ============================================================
def num(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def availability_factor(player):
    chance = num(player.get("chance"), 100)
    if chance >= 90:
        return 1.0
    if chance >= 75:
        return 0.85
    if chance >= 50:
        return 0.60
    if chance > 0:
        return 0.30
    return 0.0


def fixture_count(fixture_map, team_id, gw):
    return sum(1 for f in fixture_map.get(team_id, []) if f["gw"] == gw)


def average_fdr(fixture_map, team_id, weeks=None):
    games = sorted(
        fixture_map.get(team_id, []),
        key=lambda x: (x["gw"], not x["home"]),
    )
    if weeks is not None:
        games = games[:weeks]
    if not games:
        return 3.0
    return sum(num(f["difficulty"], 3) for f in games) / len(games)


def fixture_text(fixture_map, team_names, team_id, number=5):
    games = sorted(
        fixture_map.get(team_id, []),
        key=lambda x: (x["gw"], not x["home"]),
    )[:number]

    if not games:
        return "No fixtures"

    output = []
    for fixture in games:
        opponent = team_names.get(fixture["opponent"], "?")
        location = "H" if fixture["home"] else "A"
        output.append(
            f"GW{fixture['gw']} {opponent} ({location}) "
            f"[{fixture['difficulty']}]"
        )
    return " | ".join(output)


def price_momentum_flag(player):
    net = num(player.get("net_transfers"))
    ownership = max(num(player.get("ownership")), 0.1)
    ratio = net / (ownership * 1000)

    if ratio > 0.4:
        return "📈 Likely rise"
    if ratio < -0.4:
        return "📉 Likely fall"
    return "— Stable"


# ============================================================
# FPL UNDERLYING-METRIC DECISION MODEL
# ============================================================
def calc_blended_score(player):
    ppg = min(num(player["ppg"]) * 1.5, 10)
    form = min(num(player["form"]) * 1.2, 9)
    expected = min(num(player["ep_next"]) * 2.5, 16)
    fixture = max(0, (3.2 - num(player["fdr"], 3)) * 3)
    availability = availability_factor(player) * 5

    attacking = min(num(player["xgi90"]) * 8, 12)

    defensive = 0
    if player["position"] in ("GK", "DEF"):
        defensive = max(0, (1.4 - num(player["xgc90"])) * 4)

    dgw_bonus = 7 if safe_int(player["next_gw_fixtures"]) >= 2 else 0
    bgw_penalty = 8 if safe_int(player["next_gw_fixtures"]) == 0 else 0
    ownership_bonus = (
        2
        if num(player["ownership"]) < 5 and num(player["xgi90"]) >= 0.25
        else 0
    )

    score = (
        ppg
        + form
        + expected
        + fixture
        + availability
        + attacking
        + defensive
        + dgw_bonus
        + ownership_bonus
        - bgw_penalty
    )

    return round(score, 2)


def calc_multi_gw_projection(player, fixture_map, weeks=PROJECTION_WEEKS):
    games = sorted(
        fixture_map.get(player["team_id"], []),
        key=lambda x: x["gw"],
    )[:weeks]

    if not games:
        return round(num(player["ep_next"]), 1)

    availability = availability_factor(player)

    base = (
        num(player["ep_next"]) * 0.55
        + num(player["ppg"]) * 0.20
        + num(player["xgi90"]) * 2.0
    )

    total = 0.0

    for fixture in games:
        difficulty = num(fixture["difficulty"], 3)
        difficulty_multiplier = 1.0 + ((3 - difficulty) * 0.08)
        total += base * difficulty_multiplier * availability

    return round(total, 1)


# ============================================================
# DATA LOADER
# ============================================================
@st.cache_data(ttl=900, show_spinner="Loading FPL data...")
def load_fpl_data():
    bootstrap = api_get(f"{API}/bootstrap-static/")
    fixtures_raw = api_get(f"{API}/fixtures/")

    events = bootstrap.get("events", [])
    raw_players = bootstrap.get("elements", [])
    raw_teams = bootstrap.get("teams", [])

    teams = {t["id"]: t for t in raw_teams}
    team_names = {
        t["id"]: t.get("short_name", "?")
        for t in raw_teams
    }

    positions = {
        1: "GK",
        2: "DEF",
        3: "MID",
        4: "FWD",
    }

    current_event = next(
        (event for event in events if event.get("is_current")),
        None,
    )
    next_event = next(
        (event for event in events if event.get("is_next")),
        None,
    )

    if current_event:
        current_gw = safe_int(current_event.get("id"), 1)
    elif next_event:
        current_gw = max(safe_int(next_event.get("id"), 1) - 1, 1)
    else:
        current_gw = 1

    if next_event:
        next_gw = safe_int(next_event.get("id"), current_gw + 1)
    else:
        next_gw = current_gw + 1

    fixture_map = defaultdict(list)

    for fixture in fixtures_raw:
        gw = fixture.get("event")

        if gw is None:
            continue

        if gw < next_gw or gw > next_gw + FIXTURE_HORIZON - 1:
            continue

        home = fixture.get("team_h")
        away = fixture.get("team_a")

        if home:
            fixture_map[home].append(
                {
                    "gw": safe_int(gw),
                    "home": True,
                    "opponent": away,
                    "difficulty": safe_int(
                        fixture.get("team_h_difficulty"),
                        3,
                    ),
                }
            )

        if away:
            fixture_map[away].append(
                {
                    "gw": safe_int(gw),
                    "home": False,
                    "opponent": home,
                    "difficulty": safe_int(
                        fixture.get("team_a_difficulty"),
                        3,
                    ),
                }
            )

    players = []

    for raw in raw_players:
        team_id = raw.get("team")

        chance = raw.get("chance_of_playing_next_round")
        if chance is None:
            chance = 100

        transfers_in = safe_int(raw.get("transfers_in_event"))
        transfers_out = safe_int(raw.get("transfers_out_event"))

        player = {
            "id": raw.get("id"),
            "name": raw.get("web_name", "?"),
            "full_name": (
                f"{raw.get('first_name', '')} "
                f"{raw.get('second_name', '')}"
            ).strip(),
            "position": positions.get(raw.get("element_type"), "?"),
            "team_id": team_id,
            "team": team_names.get(team_id, "?"),
            "price": num(raw.get("now_cost")) / 10,
            "points": safe_int(raw.get("total_points")),
            "ppg": num(raw.get("points_per_game")),
            "form": num(raw.get("form")),
            "minutes": safe_int(raw.get("minutes")),
            "goals": safe_int(raw.get("goals_scored")),
            "assists": safe_int(raw.get("assists")),
            "clean_sheets": safe_int(raw.get("clean_sheets")),
            "bonus": safe_int(raw.get("bonus")),
            "bps": safe_int(raw.get("bps")),
            "ep_next": num(raw.get("ep_next")),
            "ownership": num(raw.get("selected_by_percent")),
            "chance": num(chance, 100),
            "status": raw.get("status", "a"),
            "news": raw.get("news", ""),
            "xgi90": num(
                raw.get("expected_goal_involvements_per_90")
            ),
            "xgc90": num(
                raw.get("expected_goals_conceded_per_90")
            ),
            "ict": num(raw.get("ict_index")),
            "transfers_in": transfers_in,
            "transfers_out": transfers_out,
            "net_transfers": transfers_in - transfers_out,
            "price_change": safe_int(raw.get("cost_change_event")),
        }

        player["fdr"] = average_fdr(fixture_map, team_id)
        player["next_gw_fixtures"] = fixture_count(
            fixture_map,
            team_id,
            next_gw,
        )
        player["fixtures"] = fixture_text(
            fixture_map,
            team_names,
            team_id,
            FIXTURE_HORIZON,
        )
        player["blended"] = calc_blended_score(player)
        player["projection_4gw"] = calc_multi_gw_projection(
            player,
            fixture_map,
        )

        players.append(player)

    player_by_id = {
        p["id"]: p
        for p in players
        if p.get("id") is not None
    }

    return {
        "bootstrap": bootstrap,
        "teams": teams,
        "team_names": team_names,
        "current_gw": current_gw,
        "next_gw": next_gw,
        "fixture_map": dict(fixture_map),
        "players": players,
        "player_by_id": player_by_id,
    }


# ============================================================
# LOAD GLOBAL FPL DATA
# ============================================================
try:
    data = load_fpl_data()
except Exception as exc:
    st.error(
        "⚠️ The official FPL API could not be loaded right now."
    )
    st.caption(f"Technical detail: {exc}")
    st.stop()

teams = data["teams"]
team_names = data["team_names"]
current_gw = data["current_gw"]
next_gw = data["next_gw"]
fixture_map = data["fixture_map"]
players = data["players"]
player_by_id = data["player_by_id"]


def blended_score(player):
    return player["blended"]


def multi_gw_projection(player, weeks=PROJECTION_WEEKS):
    return calc_multi_gw_projection(
        player,
        fixture_map,
        weeks,
    )


def player_status(player):
    if player["status"] != "a":
        return "🔴 Unavailable"
    if player["chance"] < 50:
        return "🔴 Major doubt"
    if player["chance"] < 75:
        return "🟠 Rotation risk"
    if player["next_gw_fixtures"] == 0:
        return "⚠️ Blank GW"
    if player["next_gw_fixtures"] >= 2:
        return "⚡ Double GW"
    if player["form"] >= 5:
        return "🟢 In Form"
    return "🟡 Normal"


def hold_sell(player):
    if player["status"] != "a":
        return "🔴 SELL / REPLACE"

    if player["chance"] < 50:
        return "🔴 SELL / REPLACE"

    if player["chance"] < 75:
        return "🟠 CONSIDER SELLING"

    if player["next_gw_fixtures"] == 0:
        return "🟡 MONITOR — BLANK"

    if (
        player["form"] < 2.5
        and player["ppg"] < 3
        and player["minutes"] > 300
    ):
        return "🔴 SELL"

    if player["form"] >= 5 or player["ppg"] >= 5:
        return "🟢 STRONG HOLD"

    return "🟡 MONITOR"


# ============================================================
# MY TEAM
# ============================================================
def load_my_team(entry_id):
    data = get_entry_picks(entry_id, current_gw)
    squad = []

    for pick in data.get("picks", []):
        player = player_by_id.get(pick.get("element"))

        if not player:
            continue

        copy_player = player.copy()
        copy_player["is_captain"] = bool(
            pick.get("is_captain", False)
        )
        copy_player["is_vice"] = bool(
            pick.get("is_vice_captain", False)
        )
        copy_player["multiplier"] = safe_int(
            pick.get("multiplier"),
            1,
        )
        copy_player["position_slot"] = safe_int(
            pick.get("position"),
            0,
        )

        squad.append(copy_player)

    return data, squad


def squad_club_counts(squad, exclude_id=None):
    counts = defaultdict(int)

    for player in squad:
        if player["id"] == exclude_id:
            continue
        counts[player["team_id"]] += 1

    return counts


# ============================================================
# TRANSFER ENGINE
# ============================================================
def transfer_suggestions(squad, bank, free_transfers):
    owned_ids = {p["id"] for p in squad}
    club_counts = squad_club_counts(squad)
    suggestions = []

    for outgoing in squad:
        candidates = [
            player
            for player in players
            if player["position"] == outgoing["position"]
            and player["id"] not in owned_ids
            and player["status"] == "a"
            and player["chance"] > 0
        ]

        candidates.sort(
            key=blended_score,
            reverse=True,
        )

        for incoming in candidates[:40]:
            available = bank + outgoing["price"]

            if incoming["price"] > available:
                continue

            projected_count = club_counts[incoming["team_id"]]

            if incoming["team_id"] == outgoing["team_id"]:
                projected_count -= 1

            if projected_count + 1 > MAX_PER_CLUB:
                continue

            projected_gain = (
                multi_gw_projection(incoming)
                - multi_gw_projection(outgoing)
            )

            hit = (
                0
                if free_transfers > 0
                else TRANSFER_HIT
            )

            net_gain = projected_gain - hit

            if free_transfers > 0 and projected_gain < 2:
                continue

            if free_transfers == 0 and projected_gain < 4:
                continue

            suggestions.append(
                {
                    "out": outgoing,
                    "in": incoming,
                    "projected_gain": projected_gain,
                    "hit": hit,
                    "net_gain": net_gain,
                    "cost_difference": (
                        incoming["price"]
                        - outgoing["price"]
                    ),
                }
            )

    suggestions.sort(
        key=lambda item: item["net_gain"],
        reverse=True,
    )

    return suggestions[:10]


def transfer_decision(squad, bank, free_transfers):
    suggestions = transfer_suggestions(
        squad,
        bank,
        free_transfers,
    )

    if not suggestions:
        return {
            "decision": "ROLL",
            "reason": (
                "No available transfer clears the model's "
                "minimum projected improvement threshold."
            ),
            "suggestions": [],
        }

    best = suggestions[0]

    if free_transfers > 0:
        if best["projected_gain"] >= 4.5:
            decision = "TRANSFER"
            reason = (
                f"{best['in']['name']} projects "
                f"+{best['projected_gain']:.1f} points over "
                f"{PROJECTION_WEEKS} GWs compared with "
                f"{best['out']['name']}."
            )
        else:
            decision = "ROLL"
            reason = (
                "A minor upgrade exists, but rolling the transfer "
                "provides greater flexibility."
            )
    else:
        if best["net_gain"] >= 2:
            decision = "TAKE HIT"
            reason = (
                f"Projected improvement: "
                f"+{best['projected_gain']:.1f} points "
                f"(+{best['net_gain']:.1f} net after -4)."
            )
        else:
            decision = "ROLL"
            reason = (
                "The best available move does not justify "
                "paying the -4 hit."
            )

    return {
        "decision": decision,
        "reason": reason,
        "suggestions": suggestions,
    }


# ============================================================
# CAPTAIN / CHIPS / BEST XI
# ============================================================
def captain_recommendations(squad):
    available = [
        player
        for player in squad
        if player["chance"] >= 75
        and player["status"] == "a"
        and player["next_gw_fixtures"] > 0
    ]

    # Captaincy gets a slight boost from expected output and fixtures.
    def captain_score(player):
        return (
            blended_score(player)
            + num(player["ep_next"]) * 1.5
            + max(0, 3 - num(player["fdr"], 3)) * 1.5
            + (4 if player["next_gw_fixtures"] >= 2 else 0)
        )

    available.sort(
        key=captain_score,
        reverse=True,
    )

    return available[:5]


def bench_boost_value(squad):
    try:
        live_points = get_live_gw(current_gw)
    except Exception:
        return None

    bench = [
        player
        for player in squad
        if player.get("multiplier", 1) == 0
    ]

    rows = []
    total = 0

    for player in bench:
        points = live_points.get(player["id"], 0)
        total += points

        rows.append(
            {
                "Player": player["name"],
                "GW Points": points,
            }
        )

    return rows, total


def best_xi(squad):
    if len(squad) < 11:
        return None, 0

    by_pos = defaultdict(list)

    for player in squad:
        by_pos[player["position"]].append(player)

    for position in by_pos:
        by_pos[position].sort(
            key=blended_score,
            reverse=True,
        )

    gks = by_pos.get("GK", [])
    defs = by_pos.get("DEF", [])
    mids = by_pos.get("MID", [])
    fwds = by_pos.get("FWD", [])

    if not gks or not defs or not mids or not fwds:
        return None, 0

    best_formation = None
    best_score = float("-inf")
    best_lineup = None

    for def_count, mid_count, fwd_count in VALID_FORMATIONS:
        if len(defs) < def_count:
            continue
        if len(mids) < mid_count:
            continue
        if len(fwds) < fwd_count:
            continue

        lineup = (
            [gks[0]]
            + defs[:def_count]
            + mids[:mid_count]
            + fwds[:fwd_count]
        )

        score = sum(
            blended_score(player)
            for player in lineup
        )

        if score > best_score:
            best_score = score
            best_formation = (
                f"{def_count}-{mid_count}-{fwd_count}"
            )
            best_lineup = lineup

    if not best_lineup:
        return None, 0

    bench = [
        player
        for player in squad
        if player["id"]
        not in {p["id"] for p in best_lineup}
    ]

    return (
        {
            "formation": best_formation,
            "lineup": best_lineup,
            "bench": bench,
        },
        best_score,
    )


# ============================================================
# STRATEGY BRIEFING
# ============================================================
def generate_manager_briefing(
    squad,
    bank,
    free_transfers,
):
    if not squad:
        return None

    injuries = [
        player
        for player in squad
        if player["status"] != "a"
        or player["chance"] < 75
    ]

    blanks = [
        player
        for player in squad
        if player["next_gw_fixtures"] == 0
    ]

    doubles = [
        player
        for player in squad
        if player["next_gw_fixtures"] >= 2
    ]

    caps = captain_recommendations(squad)
    top_cap = caps[0] if caps else None
    vice_cap = caps[1] if len(caps) > 1 else None

    transfer_eval = transfer_decision(
        squad,
        bank,
        free_transfers,
    )

    squad_avg_fdr = (
        sum(player["fdr"] for player in squad)
        / len(squad)
    )

    hardest_run = sorted(
        squad,
        key=lambda player: player["fdr"],
        reverse=True,
    )[:2]

    easiest_run = sorted(
        squad,
        key=lambda player: player["fdr"],
    )[:2]

    chip_advice = (
        "Hold chips. Squad structure looks suitable for normal play."
    )

    if len(blanks) >= 4:
        chip_advice = (
            "⚠️ Chip alert: 4+ squad players blank next GW. "
            "Consider whether a Free Hit or restructuring is justified."
        )
    elif len(doubles) >= 4:
        chip_advice = (
            "⚡ Double-gameweek alert: 4+ squad players have "
            "multiple fixtures. This is a potential Bench Boost/"
            "Triple Captain window."
        )

    return {
        "injuries": injuries,
        "blanks": blanks,
        "doubles": doubles,
        "top_cap": top_cap,
        "vice_cap": vice_cap,
        "t_dec": transfer_eval,
        "squad_avg_fdr": squad_avg_fdr,
        "hardest_run": hardest_run,
        "easiest_run": easiest_run,
        "chip_advice": chip_advice,
    }


# ============================================================
# YOUTUBE
# ============================================================
def extract_video_id(value):
    value = (value or "").strip()

    if re.fullmatch(r"[0-9A-Za-z_-]{11}", value):
        return value

    patterns = [
        r"(?:v=)([0-9A-Za-z_-]{11})",
        r"(?:youtu\.be/)([0-9A-Za-z_-]{11})",
        r"(?:youtube\.com/embed/)([0-9A-Za-z_-]{11})",
        r"(?:youtube\.com/shorts/)([0-9A-Za-z_-]{11})",
        r"(?:youtube\.com/live/)([0-9A-Za-z_-]{11})",
    ]

    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return match.group(1)

    return None


def fetch_youtube_transcript(video_identifier):
    if YouTubeTranscriptApi is None:
        return (
            None,
            "youtube-transcript-api is not installed. "
            "Add it to requirements.txt and redeploy.",
        )

    video_id = extract_video_id(video_identifier)

    if not video_id:
        return (
            None,
            "Invalid YouTube URL or Video ID.",
        )

    try:
        api = YouTubeTranscriptApi()

        # Current youtube-transcript-api versions use fetch().
        if hasattr(api, "fetch"):
            transcript = api.fetch(video_id)

            lines = []
            for snippet in transcript:
                if isinstance(snippet, dict):
                    lines.append(str(snippet.get("text", "")))
                else:
                    lines.append(
                        str(getattr(snippet, "text", snippet))
                    )

            text = " ".join(
                line.strip()
                for line in lines
                if line and line.strip()
            )

            if text:
                return text, None

        # Compatibility fallback for older versions.
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            transcript = (
                YouTubeTranscriptApi.get_transcript(video_id)
            )

            text = " ".join(
                str(item.get("text", ""))
                for item in transcript
                if item.get("text")
            )

            if text:
                return text, None

        return (
            None,
            "No transcript was available for this video.",
        )

    except Exception as exc:
        return (
            None,
            f"Could not retrieve transcript: {exc}",
        )


# ============================================================
# GEMINI
# ============================================================
def get_gemini_key():
    try:
        return st.secrets.get("GEMINI_API_KEY")
    except Exception:
        return None


def get_gemini_models():
    configured = None

    try:
        configured = st.secrets.get(
            "GEMINI_MODEL",
            DEFAULT_GEMINI_MODEL,
        )
    except Exception:
        configured = DEFAULT_GEMINI_MODEL

    models = [configured] + GEMINI_FALLBACK_MODELS

    # Remove duplicates while keeping order.
    return list(dict.fromkeys(models))


def gemini_generate(prompt, system_instruction):
    api_key = get_gemini_key()

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing from Streamlit Secrets."
        )

    if genai is None or types is None:
        raise RuntimeError(
            "google-genai is not installed. "
            "Add google-genai to requirements.txt."
        )

    client = genai.Client(api_key=api_key)
    errors = []

    for model_name in get_gemini_models():
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    max_output_tokens=2500,
                ),
            )

            text = getattr(response, "text", None)

            if text:
                return text, model_name

            errors.append(
                f"{model_name}: empty response"
            )

        except Exception as exc:
            errors.append(
                f"{model_name}: {exc}"
            )

    raise RuntimeError(
        "Gemini could not generate a response.\n\n"
        + "\n".join(errors)
    )


# ============================================================
# ELITE MANAGERS — FULL TRACKER ENABLED
# ============================================================
# ============================================================
# ELITE MANAGER TRACKER
# ============================================================
def load_elite_manager(name, entry_id):
    entry_id = safe_int(entry_id, 0)
    if not entry_id:
        return {"name": name, "status": "MISSING", "error": "Team ID not configured."}
    try:
        info = get_entry_info(entry_id)
        picks_data = get_entry_picks(entry_id, current_gw)
        picks = picks_data.get("picks", [])
        if not picks and next_gw != current_gw:
            picks_data = get_entry_picks(entry_id, next_gw)
            picks = picks_data.get("picks", [])
        squad, captain, vice = [], None, None
        for pick in picks:
            player = player_by_id.get(pick.get("element"))
            if not player:
                continue
            squad.append(player.copy())
            if pick.get("is_captain"): captain = player["name"]
            if pick.get("is_vice_captain"): vice = player["name"]
        transfers = []
        try:
            for tr in get_entry_transfers(entry_id):
                if tr.get("event") == current_gw:
                    out_p = player_by_id.get(tr.get("element_out"))
                    in_p = player_by_id.get(tr.get("element_in"))
                    transfers.append({
                        "out": out_p["name"] if out_p else str(tr.get("element_out")),
                        "in": in_p["name"] if in_p else str(tr.get("element_in")),
                        "cost": tr.get("event_cost", 0),
                    })
        except Exception:
            pass
        history = get_team_history(entry_id)
        latest = (history.get("current") or [{}])[-1]
        return {
            "name": name, "entry_id": entry_id, "status": "OK",
            "entry_name": info.get("name", ""),
            "manager_name": f"{info.get('player_first_name', '')} {info.get('player_last_name', '')}".strip(),
            "overall_rank": info.get("summary_overall_rank", "—"),
            "total_points": info.get("summary_overall_points", "—"),
            "gw_points": latest.get("points", "—"),
            "squad": squad, "captain": captain, "vice": vice, "transfers": transfers,
        }
    except Exception as exc:
        return {"name": name, "entry_id": entry_id, "status": "ERROR", "error": str(exc), "squad": [], "transfers": []}


def elite_consensus(elite_rows):
    valid = [r for r in elite_rows if r.get("status") == "OK" and r.get("squad")]
    total = len(valid)
    if not total: return [], [], [], valid
    counts, caps, trans = defaultdict(int), defaultdict(int), defaultdict(int)
    for row in valid:
        for p in {p["id"]: p for p in row["squad"]}.values(): counts[p["id"]] += 1
        if row.get("captain"): caps[row["captain"]] += 1
        for t in row.get("transfers", []): trans[(t["out"], t["in"])] += 1
    players_rows=[]
    for pid,n in sorted(counts.items(), key=lambda x:(-x[1], player_by_id.get(x[0],{}).get("name",""))):
        p=player_by_id.get(pid)
        if p: players_rows.append({"Player":p["name"],"Club":p["team"],"Pos":p["position"],"Elite":f"{n}/{total}","Elite %":round(100*n/total),"Model Score":round(blended_score(p),1),"FDR":round(p["fdr"],1)})
    cap_rows=[{"Captain":k,"Managers":f"{v}/{total}","%":round(100*v/total)} for k,v in sorted(caps.items(),key=lambda x:-x[1])]
    tr_rows=[{"Out":a,"In":b,"Managers":n} for (a,b),n in sorted(trans.items(),key=lambda x:-x[1])]
    return players_rows, cap_rows, tr_rows, valid


def render_elite_tracker():
    st.header("🏆 Elite Manager Tracker")
    st.caption("Five proven FPL Hall of Fame managers, their live squads/captains/transfers, and consensus compared with your team.")
    st.info("The group is Abinav C, John Walsh, FPL Harry, Keilan Kenny and Nick (FPL Spartan), matching Fantasy Football Scout's 21 Aug 2026 Hall of Fame team-reveal group.")
    with st.expander("⚙️ Manager Team IDs", expanded=False):
        st.caption("Verified IDs are pre-filled. The two blank entries are deliberately not guessed; enter their current FPL Team IDs when known.")
        ids={}
        for name,meta in ELITE_MANAGERS.items():
            default="" if meta["entry_id"] is None else str(meta["entry_id"])
            ids[name]=st.text_input(f"{name} — HOF #{meta['hall_of_fame']}", value=default, key=f"elite_{name}")
    with st.spinner("Following elite managers..."):
        rows=[load_elite_manager(name,ids[name]) for name in ELITE_MANAGERS]
    overview=[]
    for r in rows:
        overview.append({"Manager":r["name"],"HOF Rank":ELITE_MANAGERS[r["name"]]["hall_of_fame"],"Status":"🟢 Connected" if r.get("status")=="OK" else "🟠 ID needed", "GW Points":r.get("gw_points","—"),"Overall Rank":r.get("overall_rank","—"),"Captain":r.get("captain","—"),"Transfers":len(r.get("transfers",[]))})
    st.dataframe(pd.DataFrame(overview),use_container_width=True,hide_index=True)
    consensus,caps,trans,valid=elite_consensus(rows)
    if not valid:
        st.warning("No elite teams are connected yet. Enter the missing Team IDs above.")
        return
    a,b,c=st.columns(3); a.metric("Managers Connected",f"{len(valid)}/5"); b.metric("Captain Leader",caps[0]["Captain"] if caps else "—"); c.metric("Captain Consensus",caps[0]["Managers"] if caps else "—")
    st.subheader("🔥 Most-Owned Elite Players")
    st.dataframe(pd.DataFrame(consensus[:30]),use_container_width=True,hide_index=True)
    c1,c2=st.columns(2)
    with c1:
        st.markdown("### 🧢 Captain Consensus"); st.dataframe(pd.DataFrame(caps),use_container_width=True,hide_index=True) if caps else st.info("No captain data yet.")
    with c2:
        st.markdown("### 🔄 Elite Transfers"); st.dataframe(pd.DataFrame(trans),use_container_width=True,hide_index=True) if trans else st.info("No current-GW transfers recorded.")
    st.divider(); st.subheader("🆚 Elite Managers vs Your Team")
    if not my_squad: st.info("Load your FPL Team ID in the sidebar to compare.")
    else:
        owned={p["id"] for p in my_squad}; comp=[]; threshold=max(2,(len(valid)+1)//2)
        for r in consensus:
            n=int(r["Elite"].split('/')[0])
            if n<threshold: continue
            p=next((x for x in players if x["name"]==r["Player"]),None)
            if not p: continue
            comp.append({"Player":p["name"],"Elite":r["Elite"],"You Own":"✅ Yes" if p["id"] in owned else "❌ No","Model Score":r["Model Score"],"FDR":r["FDR"],"Verdict":"🟢 Elite + Model target" if p["id"] not in owned and r["Model Score"]>=60 else "🟡 Elite pick — review" if p["id"] not in owned else "✅ Already owned"})
        st.dataframe(pd.DataFrame(comp),use_container_width=True,hide_index=True) if comp else st.info("No strong consensus differences found.")
    st.subheader("👤 Individual Elite Squads")
    for r in valid:
        with st.expander(f"{r['name']} — {r.get('entry_name','')}"):
            st.dataframe(pd.DataFrame([{"Player":p["name"],"Club":p["team"],"Pos":p["position"],"Price":f"£{p['price']:.1f}m","Captain":"👑" if p["name"]==r.get("captain") else "","Model Score":round(blended_score(p),1)} for p in r["squad"]]),use_container_width=True,hide_index=True)
            if r.get("transfers"): st.write("**Current GW transfers:** "+", ".join(f"{t['out']} → {t['in']}" for t in r["transfers"]))

# ============================================================
# UI HEADER / SIDEBAR
# ============================================================
st.title("⚽ FPL Assistant Manager")
st.caption(
    f"GW{current_gw} → GW{next_gw} | "
    "FPL API data + underlying metrics + decision engine"
)

with st.sidebar:
    st.header("⚙️ Manager Settings")

    entry_id = st.text_input(
        "FPL Team ID",
        value="",
        help=(
            "Found in the URL of your FPL team/points page."
        ),
    )

    league_id = st.text_input(
        "Mini-League ID (optional)",
        value="",
    )

    free_transfers = st.number_input(
        "Free Transfers Available",
        min_value=0,
        max_value=5,
        value=1,
        step=1,
    )

    st.divider()
    st.caption(
        "Data is cached briefly to keep the app fast "
        "and reduce API requests."
    )


tabs = st.tabs(
    [
        "📋 Strategy",
        "👤 My Team",
        "🔄 Transfers",
        "🩺 Hold / Sell",
        "🧢 Captain",
        "📊 Rankings",
        "📅 Fixtures",
        "💊 Chips",
        "🕵️ Mini-League",
        "🏆 Best XI",
        "🏆 Elite Managers",
        "📺 Creator AI",
        "💬 AI Assistant",
    ]
)


# ============================================================
# LOAD USER SQUAD
# ============================================================
team_data = None
my_squad = []

if entry_id.strip():
    try:
        team_data, my_squad = load_my_team(
            safe_int(entry_id.strip())
        )
    except Exception as exc:
        st.error(
            "Couldn't load your squad. Check the Team ID."
        )
        st.caption(f"Technical detail: {exc}")


# ============================================================
# TAB 1 — STRATEGY
# ============================================================
with tabs[0]:
    st.header(
        f"📋 Gameweek {next_gw} Strategy Briefing"
    )

    if not my_squad:
        st.info(
            "Enter your FPL Team ID in the sidebar."
        )
    else:
        entry_hist = team_data.get(
            "entry_history",
            {},
        )

        bank = num(entry_hist.get("bank")) / 10

        briefing = generate_manager_briefing(
            my_squad,
            bank,
            free_transfers,
        )

        st.subheader(
            f"⚡ GW{next_gw} Directives"
        )

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Transfer",
            briefing["t_dec"]["decision"],
        )

        col2.metric(
            "Captain",
            (
                briefing["top_cap"]["name"]
                if briefing["top_cap"]
                else "N/A"
            ),
        )

        col3.metric(
            "Vice-Captain",
            (
                briefing["vice_cap"]["name"]
                if briefing["vice_cap"]
                else "N/A"
            ),
        )

        st.markdown(
            f"**Transfer Assessment:** "
            f"{briefing['t_dec']['reason']}"
        )

        if briefing["injuries"]:
            st.warning(
                "🚨 **Flagged / Injured:** "
                + ", ".join(
                    f"{p['name']} "
                    f"({p['news'] or 'Doubt'})"
                    for p in briefing["injuries"]
                )
            )

        if briefing["blanks"]:
            st.error(
                "⚠️ **Blanking Next GW:** "
                + ", ".join(
                    p["name"]
                    for p in briefing["blanks"]
                )
            )

        if briefing["doubles"]:
            st.success(
                "⚡ **Double GW Assets:** "
                + ", ".join(
                    p["name"]
                    for p in briefing["doubles"]
                )
            )

        st.divider()
        st.subheader("🔭 5-Gameweek Squad Horizon")

        avg_fdr = briefing["squad_avg_fdr"]

        if avg_fdr < 2.9:
            fdr_eval = "🟢 Favourable"
        elif avg_fdr > 3.2:
            fdr_eval = "🔴 Difficult"
        else:
            fdr_eval = "🟡 Balanced"

        st.write(
            f"**Schedule Rating:** {fdr_eval} "
            f"(Avg FDR: {avg_fdr:.2f})"
        )

        ca, cb = st.columns(2)

        with ca:
            st.markdown(
                "**🟢 Best Fixture Runs:**"
            )
            for player in briefing["easiest_run"]:
                st.write(
                    f"• **{player['name']}** "
                    f"({player['team']}) — "
                    f"FDR {player['fdr']:.1f} | "
                    f"{player['fixtures']}"
                )

        with cb:
            st.markdown(
                "**🔴 Toughest Fixture Runs:**"
            )
            for player in briefing["hardest_run"]:
                st.write(
                    f"• **{player['name']}** "
                    f"({player['team']}) — "
                    f"FDR {player['fdr']:.1f} | "
                    f"{player['fixtures']}"
                )

        st.divider()
        st.subheader("💊 Chip Deployment")
        st.markdown(briefing["chip_advice"])


# ============================================================
# TAB 2 — MY TEAM
# ============================================================
with tabs[1]:
    st.header("👤 My FPL Team")

    if not my_squad:
        st.info(
            "Enter your FPL Team ID in the sidebar."
        )
    else:
        entry_hist = team_data.get(
            "entry_history",
            {},
        )

        bank = num(entry_hist.get("bank")) / 10
        team_value = num(
            entry_hist.get("value")
        ) / 10

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "GW Points",
            entry_hist.get("points", 0),
        )
        c2.metric(
            "Total Points",
            entry_hist.get("total_points", 0),
        )
        c3.metric(
            "Team Value",
            f"£{team_value:.1f}m",
        )
        c4.metric(
            "Bank",
            f"£{bank:.1f}m",
        )

        st.divider()

        squad_rows = [
            {
                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Role": (
                    "© Captain"
                    if p.get("is_captain")
                    else (
                        "VC"
                        if p.get("is_vice")
                        else ""
                    )
                ),
                "Price": f"£{p['price']:.1f}m",
                "Points": p["points"],
                "PPG": round(p["ppg"], 1),
                "Form": round(p["form"], 1),
                "xGI/90": round(p["xgi90"], 2),
                "FDR": round(p["fdr"], 1),
                "Status": player_status(p),
            }
            for p in my_squad
        ]

        st.dataframe(
            pd.DataFrame(squad_rows),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# TAB 3 — TRANSFERS
# ============================================================
with tabs[2]:
    st.header("🔄 Transfer Recommendations")

    if not my_squad:
        st.info("Load your squad first.")
    else:
        entry_hist = team_data.get(
            "entry_history",
            {},
        )

        bank = num(entry_hist.get("bank")) / 10

        transfer_eval = transfer_decision(
            my_squad,
            bank,
            free_transfers,
        )

        st.info(
            f"**Model Recommendation:** "
            f"{transfer_eval['decision']} — "
            f"{transfer_eval['reason']}"
        )

        if transfer_eval["suggestions"]:
            st.divider()

            for index, suggestion in enumerate(
                transfer_eval["suggestions"][:5],
                1,
            ):
                outgoing = suggestion["out"]
                incoming = suggestion["in"]

                difference = suggestion[
                    "cost_difference"
                ]

                if difference > 0:
                    cost_string = (
                        f"Costs +£{difference:.1f}m"
                    )
                elif difference < 0:
                    cost_string = (
                        f"Frees £{abs(difference):.1f}m"
                    )
                else:
                    cost_string = "Equal Price"

                st.markdown(
                    f"### {index}. "
                    f"{outgoing['name']} ➡️ "
                    f"{incoming['name']} "
                    f"({cost_string})"
                )

                sc1, sc2, sc3 = st.columns(3)

                sc1.metric(
                    "Out xGI/90",
                    f"{outgoing['xgi90']:.2f}",
                )
                sc2.metric(
                    "In xGI/90",
                    f"{incoming['xgi90']:.2f}",
                )
                sc3.metric(
                    f"{PROJECTION_WEEKS}GW Net Gain",
                    f"{suggestion['net_gain']:+.1f}",
                )

                st.write(
                    f"**Incoming fixtures:** "
                    f"{incoming['fixtures']}"
                )
                st.write(
                    f"**Price trend:** "
                    f"{price_momentum_flag(incoming)}"
                )

                st.divider()
        else:
            st.warning(
                "No transfer currently meets the model threshold."
            )


# ============================================================
# TAB 4 — HOLD / SELL
# ============================================================
with tabs[3]:
    st.header("🩺 Hold / Sell Diagnostics")

    if not my_squad:
        st.info("Load your squad first.")
    else:
        rows = [
            {
                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Points": p["points"],
                "Form": round(p["form"], 1),
                "xGI/90": round(p["xgi90"], 2),
                "Avg FDR": round(p["fdr"], 1),
                "Price Trend": price_momentum_flag(p),
                "Action": hold_sell(p),
            }
            for p in my_squad
        ]

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# TAB 5 — CAPTAIN
# ============================================================
with tabs[4]:
    st.header("🧢 Captaincy Analysis")

    if not my_squad:
        st.info("Load your squad first.")
    else:
        captains = captain_recommendations(
            my_squad
        )

        if not captains:
            st.warning(
                "No suitable captain candidates found."
            )
        else:
            captain = captains[0]
            vice = (
                captains[1]
                if len(captains) > 1
                else None
            )

            ca, cb = st.columns(2)

            with ca:
                st.success(
                    f"👑 **CAPTAIN:** "
                    f"{captain['name']} "
                    f"({captain['team']})"
                )

                st.write(
                    f"xGI/90: **{captain['xgi90']:.2f}**  \n"
                    f"Form: **{captain['form']:.1f}**  \n"
                    f"PPG: **{captain['ppg']:.1f}**  \n"
                    f"Next GW fixtures: "
                    f"**{captain['next_gw_fixtures']}**"
                )

                st.write(
                    f"Fixtures: {captain['fixtures']}"
                )

            with cb:
                if vice:
                    st.info(
                        f"🥈 **VICE-CAPTAIN:** "
                        f"{vice['name']} "
                        f"({vice['team']})"
                    )

                    st.write(
                        f"xGI/90: **{vice['xgi90']:.2f}**  \n"
                        f"Form: **{vice['form']:.1f}**  \n"
                        f"PPG: **{vice['ppg']:.1f}**"
                    )

            st.divider()
            st.subheader("Top Captain Candidates")

            captain_rows = [
                {
                    "Rank": i,
                    "Player": p["name"],
                    "Club": p["team"],
                    "xGI/90": round(p["xgi90"], 2),
                    "Form": round(p["form"], 1),
                    "PPG": round(p["ppg"], 1),
                    "FDR": round(p["fdr"], 1),
                }
                for i, p in enumerate(captains, 1)
            ]

            st.dataframe(
                pd.DataFrame(captain_rows),
                use_container_width=True,
                hide_index=True,
            )


# ============================================================
# TAB 6 — PLAYER RANKINGS
# ============================================================
with tabs[5]:
    st.header("📊 Player Rankings")

    sort_by = st.radio(
        "Sort By",
        [
            "Blended Model Score",
            "xGI/90",
            "FPL Points",
            "Form",
            "PPG",
            "4-GW Projection",
        ],
        horizontal=True,
    )

    position_filter = st.selectbox(
        "Position",
        ["ALL", "GK", "DEF", "MID", "FWD"],
    )

    pool = [
        player
        for player in players
        if (
            position_filter == "ALL"
            or player["position"] == position_filter
        )
    ]

    if sort_by == "Blended Model Score":
        pool.sort(
            key=blended_score,
            reverse=True,
        )
    elif sort_by == "xGI/90":
        pool.sort(
            key=lambda x: x["xgi90"],
            reverse=True,
        )
    elif sort_by == "FPL Points":
        pool.sort(
            key=lambda x: x["points"],
            reverse=True,
        )
    elif sort_by == "Form":
        pool.sort(
            key=lambda x: x["form"],
            reverse=True,
        )
    elif sort_by == "PPG":
        pool.sort(
            key=lambda x: x["ppg"],
            reverse=True,
        )
    else:
        pool.sort(
            key=lambda x: x["projection_4gw"],
            reverse=True,
        )

    rows = [
        {
            "Player": p["name"],
            "Club": p["team"],
            "Pos": p["position"],
            "Price": f"£{p['price']:.1f}m",
            "Points": p["points"],
            "xGI/90": round(p["xgi90"], 2),
            "Form": round(p["form"], 1),
            "PPG": round(p["ppg"], 1),
            "FDR": round(p["fdr"], 1),
            "4GW Projection": round(
                p["projection_4gw"],
                1,
            ),
            "Score": round(
                blended_score(p),
                1,
            ),
        }
        for p in pool[:75]
    ]

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# TAB 7 — FIXTURES
# ============================================================
with tabs[6]:
    st.header("📅 Fixture Difficulty & Swings")

    if my_squad:
        st.subheader(
            "👤 Your Squad Fixture Difficulty"
        )

        rows = [
            {
                "Player": p["name"],
                "Club": p["team"],
                "Pos": p["position"],
                "Avg FDR": round(p["fdr"], 2),
                "Upcoming Schedule": p["fixtures"],
            }
            for p in my_squad
        ]

        st.dataframe(
            pd.DataFrame(rows).sort_values(
                "Avg FDR"
            ),
            use_container_width=True,
            hide_index=True,
        )

        st.divider()

    st.subheader(
        "🔥 Fixture Swings: Next 2 vs Next 5 GWs"
    )

    improving = []
    worsening = []

    for team_id in teams:
        near_fdr = average_fdr(
            fixture_map,
            team_id,
            weeks=2,
        )
        later_fdr = average_fdr(
            fixture_map,
            team_id,
            weeks=5,
        )

        if later_fdr < near_fdr - 0.2:
            improving.append(
                (
                    team_names.get(team_id, "?"),
                    near_fdr,
                    later_fdr,
                )
            )
        elif later_fdr > near_fdr + 0.2:
            worsening.append(
                (
                    team_names.get(team_id, "?"),
                    near_fdr,
                    later_fdr,
                )
            )

    improving.sort(key=lambda x: x[2])
    worsening.sort(key=lambda x: x[2], reverse=True)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            "**🟢 Getting Easier:**"
        )

        if improving:
            for name, near, later in improving:
                st.write(
                    f"**{name}** — "
                    f"{near:.1f} ➜ {later:.1f}"
                )
        else:
            st.write("No major improvements detected.")

    with col2:
        st.markdown(
            "**🔴 Getting Tougher:**"
        )

        if worsening:
            for name, near, later in worsening:
                st.write(
                    f"**{name}** — "
                    f"{near:.1f} ➜ {later:.1f}"
                )
        else:
            st.write("No major worsening detected.")


# ============================================================
# TAB 8 — CHIPS
# ============================================================
with tabs[7]:
    st.header("💊 Chip Strategy")

    if not my_squad:
        st.info("Load your squad first.")
    else:
        briefing = generate_manager_briefing(
            my_squad,
            num(
                team_data.get(
                    "entry_history",
                    {},
                ).get("bank")
            ) / 10,
            free_transfers,
        )

        st.markdown(
            f"**Current recommendation:** "
            f"{briefing['chip_advice']}"
        )

        bb = bench_boost_value(my_squad)

        if bb:
            rows, total = bb

            st.subheader(
                f"🪑 Current GW{current_gw} Bench Boost Check"
            )

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )

            st.metric(
                "Current Bench Points",
                f"{total} pts",
            )
        else:
            st.info(
                "Live bench points are unavailable right now."
            )


# ============================================================
# TAB 9 — MINI-LEAGUE
# ============================================================
with tabs[8]:
    st.header("🕵️ Mini-League")

    if not league_id.strip():
        st.info(
            "Enter your Mini-League ID in the sidebar."
        )
    else:
        try:
            league = get_league(
                safe_int(league_id.strip())
            )

            standings = (
                league.get("standings", {})
                .get("results", [])
            )[:15]

            rows = [
                {
                    "Rank": row.get("rank"),
                    "Manager": row.get("player_name"),
                    "Team": row.get("entry_name"),
                    "Total Points": row.get("total"),
                    "GW Points": row.get("event_total"),
                }
                for row in standings
            ]

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True,
            )

        except Exception as exc:
            st.error(
                "Couldn't retrieve mini-league."
            )
            st.caption(f"Technical detail: {exc}")


# ============================================================
# TAB 10 — BEST XI
# ============================================================
with tabs[9]:
    st.header("🏆 Best Starting XI")

    st.caption(
        "This ONLY selects from players currently in your "
        "loaded 15-man squad."
    )

    if not my_squad:
        st.info("Load your squad first.")
    elif st.button(
        "Compute Best Starting Lineup",
        key="best_xi_button",
    ):
        result, score = best_xi(my_squad)

        if result:
            st.success(
                f"Optimal Formation: "
                f"**{result['formation']}** "
                f"(Model Score: {score:.1f})"
            )

            lineup_rows = [
                {
                    "Player": p["name"],
                    "Club": p["team"],
                    "Pos": p["position"],
                    "xGI/90": round(
                        p["xgi90"],
                        2,
                    ),
                    "Blended Score": round(
                        blended_score(p),
                        1,
                    ),
                }
                for p in result["lineup"]
            ]

            st.dataframe(
                pd.DataFrame(lineup_rows),
                use_container_width=True,
                hide_index=True,
            )

            st.subheader("🪑 Bench")

            bench_rows = [
                {
                    "Player": p["name"],
                    "Club": p["team"],
                    "Pos": p["position"],
                    "Score": round(
                        blended_score(p),
                        1,
                    ),
                }
                for p in result["bench"]
            ]

            st.dataframe(
                pd.DataFrame(bench_rows),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.error(
                "The squad could not produce a valid formation."
            )


# ============================================================
# TAB 11 — ELITE MANAGERS
# ============================================================
with tabs[10]:
    render_elite_tracker()


# ============================================================
# TAB 12 — CREATOR AI
# ============================================================
with tabs[11]:
    st.header(
        "📺 Creator Intelligence & AI Stress Test"
    )

    st.caption(
        "Paste a YouTube video and the app will compare "
        "the creator's recommendations with your squad, "
        "FPL underlying metrics and fixtures."
    )

    st.markdown("### 🎙️ Monitored Creator Channels")

    channel_columns = st.columns(5)

    for index, (name, url) in enumerate(
        CREATOR_CHANNELS.items()
    ):
        channel_columns[index].markdown(
            f"**[{name}]({url})**"
        )

    st.divider()

    video_input = st.text_input(
        "YouTube Video URL or ID",
        placeholder=(
            "https://www.youtube.com/watch?v=XXXXXXXXXXX"
        ),
        key="creator_video_url",
    )

    if st.button(
        "Analyze Creator Video",
        key="analyze_creator_button",
    ):
        if not video_input.strip():
            st.warning(
                "Paste a YouTube video URL or ID first."
            )
        elif not get_gemini_key():
            st.error(
                "GEMINI_API_KEY is missing from "
                "Streamlit Secrets."
            )
        else:
            with st.spinner(
                "Extracting transcript and analysing..."
            ):
                transcript, transcript_error = (
                    fetch_youtube_transcript(
                        video_input
                    )
                )

                if transcript_error:
                    st.error(transcript_error)
                else:
                    squad_context = (
                        ", ".join(
                            f"{p['name']} "
                            f"({p['team']}, "
                            f"xGI/90: {p['xgi90']:.2f}, "
                            f"FDR: {p['fdr']:.1f})"
                            for p in my_squad
                        )
                        if my_squad
                        else "No squad loaded."
                    )

                    top_assets = ", ".join(
                        f"{p['name']} "
                        f"({p['team']}, "
                        f"xGI/90: {p['xgi90']:.2f}, "
                        f"FDR: {p['fdr']:.1f})"
                        for p in sorted(
                            players,
                            key=lambda x: x["xgi90"],
                            reverse=True,
                        )[:15]
                    )

                    prompt = f"""
You are analysing an FPL creator video.

Creator transcript:
{transcript[:12000]}

Manager squad:
{squad_context}

Top league assets by xGI/90:
{top_assets}

Current gameweek: GW{current_gw}
Planning for: GW{next_gw}

Tasks:
1. Summarise the creator's key FPL recommendations.
2. Identify transfers, captain picks, holds and avoids.
3. Compare those recommendations against the supplied FPL
   underlying metrics and fixture difficulty.
4. Highlight where the creator's reasoning is strong.
5. Highlight where it conflicts with the data.
6. Give a practical verdict specifically for this manager.
7. Do not invent statistics that are not supplied.
8. Clearly separate creator opinion from the app's data.

Keep the answer structured and concise.
"""

                    system_instruction = (
                        "You are an elite but objective FPL analyst. "
                        "Use the supplied data rather than inventing "
                        "facts. Creator opinion should be stress-tested, "
                        "not automatically followed."
                    )

                    try:
                        result, model_used = (
                            gemini_generate(
                                prompt,
                                system_instruction,
                            )
                        )

                        st.success(
                            f"Analysis completed using "
                            f"{model_used}."
                        )

                        st.subheader(
                            "📋 Creator Analysis"
                        )
                        st.markdown(result)

                    except Exception as exc:
                        st.error(
                            "Gemini analysis failed."
                        )
                        st.code(str(exc))


# ============================================================
# TAB 13 — AI ASSISTANT
# ============================================================
with tabs[12]:
    st.header("💬 FPL AI Assistant")

    if not get_gemini_key():
        st.warning(
            "GEMINI_API_KEY not found in Streamlit Secrets."
        )
    else:
        try:
            assistant_pin = st.secrets.get(
                "AI_ASSISTANT_PIN",
                "2325",
            )
        except Exception:
            assistant_pin = "2325"

        pin = st.text_input(
            "Enter Manager PIN to unlock Assistant",
            type="password",
            placeholder="Enter PIN",
            key="assistant_pin",
        )

        if pin != str(assistant_pin):
            if pin:
                st.error("Incorrect PIN.")
            else:
                st.info(
                    "🔒 Enter Manager PIN to activate "
                    "the AI Assistant."
                )
        else:
            st.success(
                "🔓 Assistant unlocked."
            )

            if "messages" not in st.session_state:
                st.session_state.messages = []

            for message in st.session_state.messages:
                with st.chat_message(
                    message["role"]
                ):
                    st.markdown(
                        message["content"]
                    )

            user_prompt = st.chat_input(
                "Ask about transfers, captaincy, fixtures..."
            )

            if user_prompt:
                st.session_state.messages.append(
                    {
                        "role": "user",
                        "content": user_prompt,
                    }
                )

                with st.chat_message("user"):
                    st.markdown(user_prompt)

                with st.chat_message("assistant"):
                    with st.spinner(
                        "Analysing your squad..."
                    ):
                        squad_context = (
                            "\n".join(
                                f"- {p['name']} "
                                f"({p['team']}, {p['position']}): "
                                f"£{p['price']:.1f}m | "
                                f"Form {p['form']:.1f} | "
                                f"PPG {p['ppg']:.1f} | "
                                f"xGI/90 {p['xgi90']:.2f} | "
                                f"xGC/90 {p['xgc90']:.2f} | "
                                f"FDR {p['fdr']:.1f} | "
                                f"Fixtures {p['fixtures']}"
                                for p in my_squad
                            )
                            if my_squad
                            else "No squad loaded."
                        )

                        bank = 0.0
                        if team_data:
                            bank = (
                                num(
                                    team_data.get(
                                        "entry_history",
                                        {},
                                    ).get("bank")
                                )
                                / 10
                            )

                        transfer_eval = (
                            transfer_decision(
                                my_squad,
                                bank,
                                free_transfers,
                            )
                            if my_squad
                            else None
                        )

                        transfer_summary = (
                            transfer_eval["reason"]
                            if transfer_eval
                            else "No transfer analysis available."
                        )

                        prompt = f"""
Current FPL gameweek: GW{current_gw}
Planning for: GW{next_gw}
Free transfers: {free_transfers}
Bank: £{bank:.1f}m

Manager's squad:
{squad_context}

Model transfer assessment:
{transfer_summary}

User question:
{user_prompt}

Answer as an elite FPL strategist.

Rules:
- Prioritise the manager's actual squad.
- Use the supplied FPL data.
- Consider fixtures, form, xGI/90, xGC/90,
  availability and projected output.
- Do not invent statistics.
- If the data is insufficient, say so.
- Give a clear recommendation where possible.
"""

                        try:
                            answer, model_used = (
                                gemini_generate(
                                    prompt,
                                    (
                                        "You are an elite FPL strategist. "
                                        "Give practical, data-led advice "
                                        "for the manager's actual squad. "
                                        "Do not invent facts."
                                    ),
                                )
                            )

                            st.markdown(answer)

                            st.caption(
                                f"Model: {model_used}"
                            )

                            st.session_state.messages.append(
                                {
                                    "role": "assistant",
                                    "content": answer,
                                }
                            )

                        except Exception as exc:
                            st.error(
                                "AI Assistant failed."
                            )
                            st.code(str(exc))


# ============================================================
# FOOTER
# ============================================================
st.divider()
st.caption(
    "⚽ FPL Assistant Manager — Official FPL API + "
    "FPL underlying metrics + decision engine."
)
