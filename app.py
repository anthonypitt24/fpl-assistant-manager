from collections import defaultdict
import itertools
import re
from google import genai
import pandas as pd
import requests
import streamlit as st
from youtube_transcript_api import YouTubeTranscriptApi

# ============================================================
# FPL ASSISTANT MANAGER — ULTIMATE DECISION ENGINE
# ============================================================
st.set_page_config(
    page_title="FPL Assistant Manager",
    page_icon="⚽",
    layout="wide"
)

API = "https://fantasy.premierleague.com/api"
HEADERS = {"User-Agent": "Mozilla/5.0"}
FIXTURE_HORIZON = 5
HIT_PROJECTION_WEEKS = 4
SQUAD_BUDGET = 1000  # tenths of £m, i.e. £100.0m
MAX_PER_CLUB = 3
TRANSFER_HIT = 4

VALID_FORMATIONS = [
    (3, 4, 3), (3, 5, 2), (4, 4, 2), (4, 3, 3),
    (4, 5, 1), (5, 4, 1), (5, 3, 2), (5, 2, 3),
]

CREATOR_CHANNELS = {
    "FPL Harry": "https://www.youtube.com/@FPLHarry",
    "Let's Talk FPL (Andy)": "https://www.youtube.com/@LetsTalkFPL",
    "FPL Focal (Oscar)": "https://www.youtube.com/@FPLFocal",
    "FPL Mate (Dan)": "https://www.youtube.com/@FPLMate",
    "Planet FPL (James & Suj)": "https://www.youtube.com/@PlanetFPL",
}

# ============================================================
# API HELPERS
# ============================================================
@st.cache_data(ttl=300, show_spinner=False)
def api_get(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=300, show_spinner=False)
def get_entry_info(entry_id):
    return api_get(f"{API}/entry/{entry_id}/")

@st.cache_data(ttl=300, show_spinner=False)
def get_entry_picks(entry_id, gameweek):
    return api_get(f"{API}/entry/{entry_id}/event/{gameweek}/picks/")

@st.cache_data(ttl=300, show_spinner=False)
def get_league(league_id):
    return api_get(f"{API}/leagues-classic/{league_id}/standings/")

@st.cache_data(ttl=600, show_spinner=False)
def get_team_history(entry_id):
    return api_get(f"{API}/entry/{entry_id}/history/")

@st.cache_data(ttl=300, show_spinner=False)
def get_live_gw(gameweek):
    data = api_get(f"{API}/event/{gameweek}/live/")
    return {el["id"]: el["stats"]["total_points"] for el in data.get("elements", [])}

# ============================================================
# PURE DATA & METRIC HELPERS
# ============================================================
def num(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def availability_factor(player):
    chance = player["chance"]
    if chance >= 90: return 1.0
    if chance >= 75: return 0.85
    if chance >= 50: return 0.60
    if chance > 0: return 0.30
    return 0.0

def fixture_count(fixture_map, team_id, gw):
    return len([f for f in fixture_map.get(team_id, []) if f["gw"] == gw])

def average_fdr(fixture_map, team_id, weeks=None):
    games = fixture_map.get(team_id, [])
    if weeks:
        games = games[:weeks]
    if not games:
        return 3.0
    return sum(f["difficulty"] for f in games) / len(games)

def fixture_text(fixture_map, team_names, team_id, number=5):
    games = sorted(fixture_map.get(team_id, []), key=lambda x: (x["gw"], not x["home"]))[:number]
    output = []
    for f in games:
        opponent = team_names.get(f["opponent"], "?")
        location = "H" if f["home"] else "A"
        output.append(f"GW{f['gw']} {opponent} ({location}) [{f['difficulty']}]")
    return " | ".join(output) if output else "No fixtures"

def price_momentum_flag(player):
    net = player["net_transfers"]
    ownership = max(player["ownership"], 0.1)
    ratio = net / (ownership * 1000)
    if ratio > 0.4: return "📈 Likely rise"
    if ratio < -0.4: return "📉 Likely fall"
    return "— Stable"

# ============================================================
# QUANTITATIVE DECISION MODELS
# ============================================================
def calc_blended_score(player):
    ppg = min(player["ppg"] * 1.5, 10)
    form = min(player["form"] * 1.2, 9)
    expected = min(player["ep_next"] * 2.5, 16)
    fixture = max(0, (3.2 - player["fdr"]) * 3)
    availability = availability_factor(player) * 5
    attacking = min(player["xgi90"] * 8, 12)
    defensive = 0
    if player["position"] in ("GK", "DEF"):
        defensive = max(0, (1.4 - player["xgc90"]) * 4)

    dgw_bonus = 7 if player["next_gw_fixtures"] >= 2 else 0
    bgw_penalty = 8 if player["next_gw_fixtures"] == 0 else 0
    ownership_bonus = 2 if (player["ownership"] < 5 and player["xgi90"] >= 0.25) else 0

    score = (
        ppg + form + expected + fixture + availability +
        attacking + defensive + dgw_bonus + ownership_bonus - bgw_penalty
    )
    return round(score, 2)

def calc_multi_gw_projection(player, fixture_map, weeks=HIT_PROJECTION_WEEKS):
    games = sorted(fixture_map.get(player["team_id"], []), key=lambda x: x["gw"])[:weeks]
    if not games:
        return round(player["ep_next"], 1)
    availability = availability_factor(player)
    base = (player["ep_next"] * 0.55) + (player["ppg"] * 0.20) + (player["xgi90"] * 2.0)
    total = 0.0
    for fixture in games:
        difficulty_multiplier = 1.0 + ((3 - fixture["difficulty"]) * 0.08)
        total += (base * difficulty_multiplier * availability)
    return round(total, 1)

# ============================================================
# DATA LOADER
# ============================================================
@st.cache_data(ttl=900, show_spinner="Loading FPL & Opta data...")
def load_fpl_data():
    bootstrap = api_get(f"{API}/bootstrap-static/")
    fixtures_raw = api_get(f"{API}/fixtures/")

    events = bootstrap.get("events", [])
    raw_players = bootstrap.get("elements", [])
    raw_teams = bootstrap.get("teams", [])

    teams = {t["id"]: t for t in raw_teams}
    team_names = {t["id"]: t.get("short_name", "?") for t in raw_teams}
    positions = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

    current_event = next((e for e in events if e.get("is_current")), None)
    next_event = next((e for e in events if e.get("is_next")), None)
    current_gw = current_event["id"] if current_event else 1
    next_gw = next_event["id"] if next_event else current_gw + 1

    fixture_map = defaultdict(list)
    for fixture in fixtures_raw:
        gw = fixture.get("event")
        if gw is None or gw < next_gw or gw > next_gw + FIXTURE_HORIZON - 1:
            continue
        home, away = fixture.get("team_h"), fixture.get("team_a")
        if home:
            fixture_map[home].append({
                "gw": gw, "home": True, "opponent": away,
                "difficulty": fixture.get("team_h_difficulty", 3)
            })
        if away:
            fixture_map[away].append({
                "gw": gw, "home": False, "opponent": home,
                "difficulty": fixture.get("team_a_difficulty", 3)
            })

    players = []
    for p in raw_players:
        team_id = p["team"]
        chance = p.get("chance_of_playing_next_round")
        if chance is None:
            chance = 100
        transfers_in = p.get("transfers_in_event", 0)
        transfers_out = p.get("transfers_out_event", 0)

        player = {
            "id": p["id"],
            "name": p.get("web_name", "?"),
            "full_name": f"{p.get('first_name', '')} {p.get('second_name', '')}".strip(),
            "position": positions.get(p.get("element_type"), "?"),
            "team_id": team_id,
            "team": team_names.get(team_id, "?"),
            "price": p.get("now_cost", 0) / 10,
            "points": p.get("total_points", 0),
            "ppg": num(p.get("points_per_game")),
            "form": num(p.get("form")),
            "minutes": p.get("minutes", 0),
            "goals": p.get("goals_scored", 0),
            "assists": p.get("assists", 0),
            "clean_sheets": p.get("clean_sheets", 0),
            "bonus": p.get("bonus", 0),
            "bps": p.get("bps", 0),
            "ep_next": num(p.get("ep_next")),
            "ownership": num(p.get("selected_by_percent")),
            "chance": chance,
            "status": p.get("status", "a"),
            "news": p.get("news", ""),
            "xgi90": num(p.get("expected_goal_involvements_per_90")),
            "xgc90": num(p.get("expected_goals_conceded_per_90")),
            "ict": num(p.get("ict_index")),
            "transfers_in": transfers_in,
            "transfers_out": transfers_out,
            "net_transfers": transfers_in - transfers_out,
            "price_change": p.get("cost_change_event", 0),
        }
        player["fdr"] = average_fdr(fixture_map, team_id)
        player["next_gw_fixtures"] = fixture_count(fixture_map, team_id, next_gw)
        player["fixtures"] = fixture_text(fixture_map, team_names, team_id, FIXTURE_HORIZON)
        player["blended"] = calc_blended_score(player)
        player["projection_4gw"] = calc_multi_gw_projection(player, fixture_map)
        players.append(player)

    player_by_id = {p["id"]: p for p in players}

    return {
        "bootstrap": bootstrap, "teams": teams, "team_names": team_names,
        "current_gw": current_gw, "next_gw": next_gw, "fixture_map": dict(fixture_map),
        "players": players, "player_by_id": player_by_id,
    }

try:
    _data = load_fpl_data()
except Exception:
    st.error("⚠️ FPL API temporarily unavailable. Please refresh.")
    st.stop()

teams = _data["teams"]
team_names = _data["team_names"]
current_gw = _data["current_gw"]
next_gw = _data["next_gw"]
fixture_map = _data["fixture_map"]
players = _data["players"]
player_by_id = _data["player_by_id"]

def blended_score(p): return p["blended"]
def multi_gw_projection(p, weeks=HIT_PROJECTION_WEEKS):
    return calc_multi_gw_projection(p, fixture_map, weeks)

def player_status(p):
    if p["status"] != "a": return "🔴 Unavailable"
    if p["chance"] < 50: return "🔴 Major doubt"
    if p["chance"] < 75: return "🟠 Rotation risk"
    if p["next_gw_fixtures"] == 0: return "⚠️ Blank GW"
    if p["next_gw_fixtures"] >= 2: return "⚡ Double GW"
    if p["form"] >= 5: return "🟢 In Form"
    return "🟡 Normal"

def hold_sell(player):
    if player["status"] != "a": return "🔴 SELL / REPLACE"
    if player["chance"] < 75: return "🟠 CONSIDER SELLING"
    if player["next_gw_fixtures"] == 0: return "🟡 MONITOR — BLANK"
    if player["form"] < 2.5 and player["ppg"] < 3 and player["minutes"] > 300: return "🔴 SELL"
    if player["form"] >= 5 or player["ppg"] >= 5: return "🟢 STRONG HOLD"
    return "🟡 MONITOR"

def load_my_team(entry_id):
    data = get_entry_picks(entry_id, current_gw)
    squad = []
    for pick in data.get("picks", []):
        p = player_by_id.get(pick["element"])
        if p:
            cp = p.copy()
            cp["is_captain"] = pick.get("is_captain", False)
            cp["is_vice"] = pick.get("is_vice_captain", False)
            cp["multiplier"] = pick.get("multiplier", 1)
            cp["position_slot"] = pick.get("position", 0)
            squad.append(cp)
    return data, squad

def squad_club_counts(squad, exclude_id=None):
    counts = defaultdict(int)
    for p in squad:
        if p["id"] != exclude_id:
            counts[p["team_id"]] += 1
    return counts

# ============================================================
# TRANSFERS & DECISION ENGINE
# ============================================================
def transfer_suggestions(squad, bank, free_transfers):
    owned_ids = {p["id"] for p in squad}
    club_counts = squad_club_counts(squad)
    suggestions = []
    for outgoing in squad:
        candidates = [
            p for p in players
            if p["position"] == outgoing["position"]
            and p["id"] not in owned_ids
            and p["status"] == "a"
            and p["chance"] > 0
        ]
        candidates.sort(key=blended_score, reverse=True)
        for incoming in candidates[:25]:
            available = bank + outgoing["price"]
            if incoming["price"] > available:
                continue
            projected_count = club_counts[incoming["team_id"]]
            if incoming["team_id"] == outgoing["team_id"]:
                projected_count -= 1
            if projected_count + 1 > MAX_PER_CLUB:
                continue

            projected_gain = multi_gw_projection(incoming) - multi_gw_projection(outgoing)
            hit = 0 if free_transfers > 0 else TRANSFER_HIT
            net_gain = projected_gain - hit

            if free_transfers > 0 and projected_gain < 2:
                continue
            if free_transfers == 0 and projected_gain < 4:
                continue

            suggestions.append({
                "out": outgoing, "in": incoming, "projected_gain": projected_gain,
                "hit": hit, "net_gain": net_gain,
                "cost_difference": incoming["price"] - outgoing["price"]
            })
    suggestions.sort(key=lambda x: x["net_gain"], reverse=True)
    return suggestions[:10]

def transfer_decision(squad, bank, free_transfers):
    suggestions = transfer_suggestions(squad, bank, free_transfers)
    if not suggestions:
        return {"decision": "ROLL", "reason": "No transfer clears the minimum projected improvement threshold.", "suggestions": []}

    best = suggestions[0]
    if free_transfers > 0:
        if best["projected_gain"] >= 4.5:
            decision = "TRANSFER"
            reason = f"{best['in']['name']} projects +{best['projected_gain']:.1f} pts over {HIT_PROJECTION_WEEKS} GWs compared with {best['out']['name']}."
        else:
            decision = "ROLL"
            reason = "Minor upgrade available, but rolling the transfer provides greater tactical flexibility."
    else:
        if best["net_gain"] >= 2.0:
            decision = "TAKE HIT"
            reason = f"Transfer projects +{best['projected_gain']:.1f} pts (+{best['net_gain']:.1f} net after -4 hit)."
        else:
            decision = "ROLL"
            reason = "Best available move does not justify paying a -4 hit penalty."

    return {"decision": decision, "reason": reason, "suggestions": suggestions}

def captain_recommendations(squad):
    available = [p for p in squad if p["chance"] >= 75 and p["status"] == "a" and p["next_gw_fixtures"] > 0]
    available.sort(key=blended_score, reverse=True)
    return available[:5]

def bench_boost_value(squad, entry_id):
    try:
        live_points = get_live_gw(current_gw)
    except Exception:
        return None
    bench = [p for p in squad if p.get("multiplier", 1) == 0]
    rows = []
    total = 0
    for p in bench:
        pts = live_points.get(p["id"], 0)
        total += pts
        rows.append({"Player": p["name"], "GW Points": pts})
    return rows, total

def generate_manager_briefing(squad, bank, free_transfers):
    if not squad: return None
    injuries = [p for p in squad if p["status"] != "a" or p["chance"] < 75]
    blanks = [p for p in squad if p["next_gw_fixtures"] == 0]
    doubles = [p for p in squad if p["next_gw_fixtures"] >= 2]

    caps = captain_recommendations(squad)
    top_cap = caps[0] if caps else None
    vice_cap = caps[1] if len(caps) > 1 else None

    t_dec = transfer_decision(squad, bank, free_transfers)
    squad_avg_fdr = sum(p["fdr"] for p in squad) / len(squad)
    hardest_run = sorted(squad, key=lambda p: p["fdr"], reverse=True)[:2]
    easiest_run = sorted(squad, key=lambda p: p["fdr"])[:2]

    chip_advice = "Hold chips. Squad structure is balanced for standard play."
    if len(blanks) >= 4:
        chip_advice = "⚠️ **Chip Alert (Free Hit / Wildcard):** 4+ players blanking this GW. Strong window to activate Free Hit if bench cannot cover."
    elif len(doubles) >= 4:
        chip_advice = "⚡ **Chip Alert (Bench Boost / Triple Captain):** Multiple DGW assets detected. Prime window for an offensive chip."

    return {
        "injuries": injuries, "blanks": blanks, "doubles": doubles,
        "top_cap": top_cap, "vice_cap": vice_cap, "t_dec": t_dec,
        "squad_avg_fdr": squad_avg_fdr, "hardest_run": hardest_run,
        "easiest_run": easiest_run, "chip_advice": chip_advice,
    }

def best_xi(squad):
    by_pos = defaultdict(list)
    for p in squad: by_pos[p["position"]].append(p)
    for pos in by_pos: by_pos[pos].sort(key=blended_score, reverse=True)
    gks = by_pos.get("GK", [])
    if not gks: return None, 0

    best_formation, best_score, best_lineup = None, -1, None
    for defs, mids, fwds in VALID_FORMATIONS:
        if len(by_pos["DEF"]) < defs or len(by_pos["MID"]) < mids or len(by_pos["FWD"]) < fwds:
            continue
        lineup = [gks[0]] + by_pos["DEF"][:defs] + by_pos["MID"][:mids] + by_pos["FWD"][:fwds]
        score = sum(blended_score(p) for p in lineup)
        if score > best_score:
            best_score, best_formation, best_lineup = score, f"{defs}-{mids}-{fwds}", lineup

    bench = [p for p in squad if p not in (best_lineup or [])]
    return {"formation": best_formation, "lineup": best_lineup, "bench": bench}, best_score

# ============================================================
# YOUTUBE TRANSCRIPT EXTRACTOR
# ============================================================
# ============================================================
# YOUTUBE TRANSCRIPT EXTRACTOR
# ============================================================
def extract_video_id(url_or_id):
  if len(url_or_id) == 11 and " " not in url_or_id:
    return url_or_id
  match = re.search(
      r"(?:v=|\/|youtu\.be\/|embed\/)([0-9A-Za-z_-]{11})", url_or_id
  )
  return match.group(1) if match else None


def fetch_youtube_transcript(video_identifier):
  vid = extract_video_id(video_identifier)
  if not vid:
    return None, "Invalid YouTube URL or Video ID format."
  try:
    if hasattr(YouTubeTranscriptApi, "get_transcript"):
      transcript_data = YouTubeTranscriptApi.get_transcript(vid)
    else:
      ytt = YouTubeTranscriptApi()
      transcript_data = ytt.fetch(vid)

    lines = []
    for snippet in transcript_data:
      if isinstance(snippet, dict):
        lines.append(snippet.get("text", ""))
      else:
        lines.append(getattr(snippet, "text", str(snippet)))

    full_text = " ".join(lines)
    return full_text, None
  except Exception as e:
    return None, f"Could not retrieve transcript: {e}"


# ============================================================
# UI HEADER & SIDEBAR
# ============================================================
st.title("⚽ FPL Assistant Manager")
st.caption(f"GW{current_gw} | Planning for GW{next_gw} | Opta xGI/xGC Engine + Creator Intelligence")

with st.sidebar:
    st.header("⚙️ Manager Settings")
    entry_id = st.text_input("FPL Team ID", value="", help="Found in the URL of your FPL points/team page.")
    league_id = st.text_input("Mini-League ID (optional)", value="")
    free_transfers = st.number_input("Free Transfers Available", min_value=0, max_value=5, value=1)
    st.divider()
    st.caption("FPL Assistant Manager | Data updated per gameweek.")

tabs = st.tabs([
    "📋 Strategy Briefing",
    "👤 My Team",
    "🔄 Transfers",
    "🩺 Hold / Sell",
    "🧢 Captain",
    "📊 Player Rankings",
    "📅 Fixtures",
    "💊 Chips",
    "🕵️ Mini-League",
    "🏆 Best XI",
    "📺 Creator Consensus",
    "💬 AI Assistant",
])

# Load Squad
team_data, my_squad = None, []
if entry_id:
    try:
        team_data, my_squad = load_my_team(int(entry_id))
    except Exception:
        st.error("Couldn't load squad. Confirm your Team ID is correct.")

# ============================================================
# TAB 1: STRATEGY BRIEFING
# ============================================================
with tabs[0]:
    st.header(f"📋 Gameweek {next_gw} Strategy Briefing")
    if not my_squad:
        st.info("Enter your FPL Team ID in the sidebar to generate your strategic briefing.")
    else:
        entry_hist = team_data.get("entry_history", {})
        bank = entry_hist.get("bank", 0) / 10
        briefing = generate_manager_briefing(my_squad, bank, free_transfers)

        # Gameweek Directives
        st.subheader(f"⚡ GW{next_gw} Directives")
        col1, col2, col3 = st.columns(3)
        col1.metric("Transfer Move", briefing["t_dec"]["decision"])
        col2.metric("Captain Armband", briefing["top_cap"]["name"] if briefing["top_cap"] else "N/A")
        col3.metric("Vice-Captain", briefing["vice_cap"]["name"] if briefing["vice_cap"] else "N/A")

        st.markdown(f"**Transfer Assessment:** {briefing['t_dec']['reason']}")

        if briefing["injuries"]:
            st.warning("🚨 **Flagged / Injured:** " + ", ".join([f"{p['name']} ({p['news'] or 'Doubt'})" for p in briefing["injuries"]]))
        if briefing["blanks"]:
            st.error("⚠️ **Blanking Next GW:** " + ", ".join([p["name"] for p in briefing["blanks"]]))
        if briefing["doubles"]:
            st.success("⚡ **Double Gameweek Assets:** " + ", ".join([p["name"] for p in briefing["doubles"]]))

        st.divider()

        # 5-GW Schedule Outlook
        st.subheader("🔭 5-Gameweek Squad Horizon")
        fdr_eval = "🟢 Favourable" if briefing["squad_avg_fdr"] < 2.9 else ("🔴 Difficult" if briefing["squad_avg_fdr"] > 3.2 else "🟡 Balanced")
        st.write(f"**Squad Schedule Rating:** {fdr_eval} (Avg FDR: {briefing['squad_avg_fdr']:.2f})")

        ca, cb = st.columns(2)
        with ca:
            st.markdown("**🟢 Prime Fixture Runs (Hold/Target):**")
            for p in briefing["easiest_run"]:
                st.write(f"• **{p['name']}** ({p['team']}) — Avg FDR: {p['fdr']:.1f} | {p['fixtures']}")
        with cb:
            st.markdown("**🔴 Tough Fixture Runs (Exit Candidates):**")
            for p in briefing["hardest_run"]:
                st.write(f"• **{p['name']}** ({p['team']}) — Avg FDR: {p['fdr']:.1f} | {p['fixtures']}")

        st.divider()
        st.subheader("💊 Chip Deployment")
        st.markdown(briefing["chip_advice"])

# ============================================================
# TAB 2: MY TEAM
# ============================================================
with tabs[1]:
    st.header("👤 My FPL Team")
    if not my_squad:
        st.info("Enter your FPL Team ID in the sidebar.")
    else:
        entry_hist = team_data.get("entry_history", {})
        bank = entry_hist.get("bank", 0) / 10
        team_val = entry_hist.get("value", 0) / 10
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("GW Points", entry_hist.get("points", 0))
        c2.metric("Total Points", entry_hist.get("total_points", 0))
        c3.metric("Team Value", f"£{team_val:.1f}m")
        c4.metric("Bank", f"£{bank:.1f}m")

        st.divider()
        squad_rows = [{
            "Player": p["name"], "Club": p["team"], "Pos": p["position"],
            "Role": "© Captain" if p.get("is_captain") else ("VC" if p.get("is_vice") else ""),
            "Price": f"£{p['price']:.1f}m", "Points": p["points"], "PPG": round(p["ppg"], 1),
            "Form": round(p["form"], 1), "Opta xGI/90": round(p["xgi90"], 2),
            "FDR": round(p["fdr"], 1), "Status": player_status(p)
        } for p in my_squad]
        st.dataframe(pd.DataFrame(squad_rows), use_container_width=True, hide_index=True)

# ============================================================
# TAB 3: TRANSFERS
# ============================================================
with tabs[2]:
    st.header("🔄 Transfer Recommendations")
    if not my_squad:
        st.info("Load your squad first.")
    else:
        entry_hist = team_data.get("entry_history", {})
        bank = entry_hist.get("bank", 0) / 10
        t_eval = transfer_decision(my_squad, bank, free_transfers)

        st.info(f"**Model Recommendation:** {t_eval['decision']} — {t_eval['reason']}")
        st.divider()

        if t_eval["suggestions"]:
            for i, s in enumerate(t_eval["suggestions"][:5], 1):
                out_p, in_p = s["out"], s["in"]
                diff = s["cost_difference"]
                cost_str = f"Costs +£{diff:.1f}m" if diff > 0 else (f"Frees £{abs(diff):.1f}m" if diff < 0 else "Equal Price")
                st.markdown(f"### {i}. {out_p['name']} ➡️ {in_p['name']} ({cost_str})")
                sc1, sc2, sc3 = st.columns(3)
                sc1.metric("Out xGI/90", f"{out_p['xgi90']:.2f}")
                sc2.metric("In xGI/90", f"{in_p['xgi90']:.2f}")
                sc3.metric(f"{HIT_PROJECTION_WEEKS}GW Net Gain", f"+{s['net_gain']:.1f} pts")
                st.write(f"**In Fixtures:** {in_p['fixtures']} | Status: {price_momentum_flag(in_p)}")
                st.divider()

# ============================================================
# TAB 4: HOLD / SELL
# ============================================================
with tabs[3]:
    st.header("🩺 Hold / Sell Diagnostics")
    if not my_squad:
        st.info("Load your squad first.")
    else:
        diag_rows = [{
            "Player": p["name"], "Club": p["team"], "Pos": p["position"],
            "Points": p["points"], "Form": round(p["form"], 1),
            "Opta xGI/90": round(p["xgi90"], 2), "Avg FDR": round(p["fdr"], 1),
            "Price Trend": price_momentum_flag(p), "Action": hold_sell(p)
        } for p in my_squad]
        st.dataframe(pd.DataFrame(diag_rows), use_container_width=True, hide_index=True)

# ============================================================
# TAB 5: CAPTAIN
# ============================================================
with tabs[4]:
    st.header("🧢 Captaincy & Armband Analysis")
    if not my_squad:
        st.info("Load your squad first.")
    else:
        caps = captain_recommendations(my_squad)
        if caps:
            c_top = caps[0]
            vc_top = caps[1] if len(caps) > 1 else None
            ca, cb = st.columns(2)
            with ca:
                st.success(f"👑 **CAPTAIN:** {c_top['name']} ({c_top['team']})")
                st.write(f"Opta xGI/90: **{c_top['xgi90']:.2f}** | Form: **{c_top['form']:.1f}** | PPG: **{c_top['ppg']:.1f}**")
                st.write(f"Fixtures: {c_top['fixtures']}")
            with cb:
                if vc_top:
                    st.info(f"🥈 **VICE-CAPTAIN:** {vc_top['name']} ({vc_top['team']})")
                    st.write(f"Opta xGI/90: **{vc_top['xgi90']:.2f}** | Form: **{vc_top['form']:.1f}**")

# ============================================================
# TAB 6: PLAYER RANKINGS
# ============================================================
with tabs[5]:
    st.header("📊 Player Rankings & Underlying Metrics")
    sort_by = st.radio("Sort By", ["Blended Model Score", "Opta xGI/90", "FPL Points", "Form", "PPG"], horizontal=True)
    pos_filter = st.selectbox("Position", ["ALL", "GK", "DEF", "MID", "FWD"])

    pool = [p for p in players if (pos_filter == "ALL" or p["position"] == pos_filter)]
    if sort_by == "Blended Model Score": pool.sort(key=blended_score, reverse=True)
    elif sort_by == "Opta xGI/90": pool.sort(key=lambda x: x["xgi90"], reverse=True)
    elif sort_by == "FPL Points": pool.sort(key=lambda x: x["points"], reverse=True)
    elif sort_by == "Form": pool.sort(key=lambda x: x["form"], reverse=True)
    elif sort_by == "PPG": pool.sort(key=lambda x: x["ppg"], reverse=True)

    rank_rows = [{
        "Player": p["name"], "Club": p["team"], "Pos": p["position"], "Price": f"£{p['price']:.1f}m",
        "Points": p["points"], "Opta xGI/90": round(p["xgi90"], 2), "Form": round(p["form"], 1),
        "PPG": round(p["ppg"], 1), "Avg FDR": round(p["fdr"], 1), "Score": round(blended_score(p), 1)
    } for p in pool[:75]]
    st.dataframe(pd.DataFrame(rank_rows), use_container_width=True, hide_index=True)

# ============================================================
# TAB 7: FIXTURES (SQUAD + LEAGUE + SWINGS)
# ============================================================
with tabs[6]:
    st.header("📅 Fixture Difficulty & Swings")
    
    # 1. Squad Specific FDR
    if my_squad:
        st.subheader("👤 Your Squad Fixture Difficulty")
        squad_fdr_rows = [{
            "Player": p["name"], "Club": p["team"], "Pos": p["position"],
            "Avg FDR": round(p["fdr"], 2), "Upcoming Schedule": p["fixtures"]
        } for p in my_squad]
        st.dataframe(pd.DataFrame(squad_fdr_rows).sort_values("Avg FDR"), use_container_width=True, hide_index=True)
        st.divider()

    # 2. Fixture Swings (Correct Inversion Logic)
    st.subheader("🔥 Fixture Swings (Next 2 GWs vs Next 5 GWs)")
    improving, worsening = [], []
    for tid, t in teams.items():
        near_fdr = average_fdr(fixture_map, tid, weeks=2)
        later_fdr = average_fdr(fixture_map, tid, weeks=5)
        # Lower FDR = Easier. If later < near -> Getting easier (improving)
        if later_fdr < near_fdr - 0.2:
            improving.append(f"**{team_names[tid]}** — {near_fdr:.1f} ➔ {later_fdr:.1f}")
        elif later_fdr > near_fdr + 0.2:
            worsening.append(f"**{team_names[tid]}** — {near_fdr:.1f} ➔ {later_fdr:.1f}")

    col_s1, col_s2 = st.columns(2)
    with col_s1:
        st.markdown("**🟢 Improving Fixtures (Getting Easier):**")
        for line in improving: st.write(line)
    with col_s2:
        st.markdown("**🔴 Worsening Fixtures (Getting Tougher):**")
        for line in worsening: st.write(line)

# ============================================================
# TAB 8: CHIPS
# ============================================================
with tabs[7]:
    st.header("💊 Chip Strategy & Bench Boost")
    if not my_squad:
        st.info("Load your squad first.")
    else:
        bb = bench_boost_value(my_squad, entry_id)
        if bb:
            rows, total = bb
            st.subheader("🪑 Bench Boost Check")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.metric(f"Bench GW{current_gw} Points", f"+{total} pts")

# ============================================================
# TAB 9: MINI-LEAGUE
# ============================================================
with tabs[8]:
    st.header("🕵️ Mini-League Rival Analysis")
    if not league_id:
        st.info("Enter your Mini-League ID in the sidebar.")
    else:
        try:
            league = get_league(int(league_id))
            standings = league.get("standings", {}).get("results", [])[:15]
            st.dataframe(pd.DataFrame([{
                "Rank": r["rank"], "Manager": r["player_name"],
                "Team": r["entry_name"], "Total Points": r["total"], "GW Points": r["event_total"]
            } for r in standings]), use_container_width=True, hide_index=True)
        except Exception:
            st.error("Couldn't retrieve mini-league.")

# ============================================================
# TAB 10: BEST XI
# ============================================================
with tabs[9]:
    st.header("🏆 Best Starting XI")
    if not my_squad:
        st.info("Load your squad first.")
    elif st.button("Compute Best Starting Lineup"):
        result, score = best_xi(my_squad)
        if result:
            st.success(f"Optimal Formation: **{result['formation']}** (Score: {score:.1f})")
            st.dataframe(pd.DataFrame([{
                "Player": p["name"], "Club": p["team"], "Pos": p["position"],
                "Opta xGI/90": round(p["xgi90"], 2), "Blended Score": round(blended_score(p), 1)
            } for p in result["lineup"]]), use_container_width=True, hide_index=True)

# ============================================================
# TAB 11: CREATOR CONSENSUS & STRESS-TESTING
# ============================================================
with tabs[10]:
    st.header("📺 Creator Intelligence & Video Stress-Testing")
    st.caption("Extracts transcripts from top FPL channels and tests their advice against objective Opta metrics.")

    st.markdown("### 🎙️ Monitored Creator Channels")
    ch_cols = st.columns(5)
    for idx, (cname, curl) in enumerate(CREATOR_CHANNELS.items()):
        ch_cols[idx].markdown(f"**[{cname}]({curl})**")

    st.divider()
    video_input = st.text_input("Paste YouTube Video URL or Video ID to analyze:", placeholder="https://www.youtube.com/watch?v=...")

    if st.button("Analyze Creator Video"):
        if not video_input:
            st.warning("Please paste a valid YouTube video link.")
        elif "GEMINI_API_KEY" not in st.secrets:
            st.error("GEMINI_API_KEY missing from Streamlit secrets.")
        else:
            with st.spinner("Extracting transcript & evaluating against model metrics..."):
                transcript, err = fetch_youtube_transcript(video_input)
                if err:
                    st.error(err)
                else:
                    client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
                    squad_ctx = ", ".join([f"{p['name']} ({p['team']}, xGI90: {p['xgi90']:.2f})" for p in my_squad]) if my_squad else "No squad loaded."
                    top_opta = ", ".join([f"{p['name']} ({p['team']}, xGI90: {p['xgi90']:.2f})" for p in sorted(players, key=lambda x: x['xgi90'], reverse=True)[:10]])

                    prompt = f"""
Transcript of Creator Video:
"{transcript[:4000]}"

Manager's Loaded Squad:
{squad_ctx}

Top League Assets by Opta xGI/90:
{top_opta}

Task:
1. Summarize the creator's key recommendations (transfers, captain picks, players to buy/avoid).
2. Stress-test their advice: Where does it align or clash with underlying Opta metrics (xGI90, xGC90) and fixture FDR?
3. Actionable verdict for the manager: Should they execute this advice or stick to the mathematical model?
"""
                    try:
                        resp = client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=prompt,
                            config={"system_instruction": "You are an objective FPL analyst testing creator advice against Opta data."}
                        )
                        st.subheader("📋 Creator Analysis & Statistical Verdict")
                        st.markdown(resp.text)
                    except Exception as e:
                        st.error(f"Gemini API Error: {e}")

# ============================================================
# TAB 12: AI ASSISTANT (PIN 2325)
# ============================================================
with tabs[11]:
    st.header("💬 FPL AI Assistant")
    if "GEMINI_API_KEY" not in st.secrets:
        st.warning("GEMINI_API_KEY not found in Streamlit Secrets.")
    else:
        pin = st.text_input("Enter Manager PIN to unlock Assistant", type="password", placeholder="Enter 4-digit PIN")
        if pin != "2325":
            if pin: st.error("Incorrect PIN.")
            else: st.info("🔒 Enter Manager PIN to activate the AI Assistant.")
        else:
            st.success("🔓 Assistant unlocked.")
            client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

            if "messages" not in st.session_state:
                st.session_state.messages = []

            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])

            if user_prompt := st.chat_input("Ask about transfers, captaincy, or form..."):
                st.session_state.messages.append({"role": "user", "content": user_prompt})
                with st.chat_message("user"):
                    st.markdown(user_prompt)

                with st.chat_message("assistant"):
                    with st.spinner("Analyzing live squad & Opta context..."):
                        try:
                            squad_context = "\n".join([
                                f"- {p['name']} ({p['team']}, {p['position']}): £{p['price']}m | Form: {p['form']} | Opta xGI/90: {p['xgi90']:.2f} | Fixtures: {p['fixtures']}"
                                for p in my_squad
                            ]) if my_squad else "No squad loaded."

                            payload = f"""
Current Gameweek: GW{current_gw} (Planning for GW{next_gw})

Manager's Squad:
{squad_context}

User Question: {user_prompt}
"""
                            response = client.models.generate_content(
                                model="gemini-2.5-flash",
                                contents=payload,
                                config={"system_instruction": "You are an elite FPL strategist. Base advice on squad data and Opta metrics."}
                            )
                            st.markdown(response.text)
                            st.session_state.messages.append({"role": "assistant", "content": response.text})
                        except Exception as e:
                            st.error(f"Error calling Gemini: {e}")

# ============================================================
# FOOTER
# ============================================================
st.divider()
st.caption("⚽ FPL Assistant Manager — Data sources: Official FPL API, Opta Event Statistics.")
