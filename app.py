import streamlit as st
import pandas as pd

from data.fpl_api import FPLClient
from models.projections import ProjectionEngine
from models.simulation import MonteCarloEngine
from strategy.team import TeamOptimizer
from strategy.captain import CaptainEngine
from strategy.transfers import TransferEngine
from strategy.rivals import RivalEngine

st.set_page_config(page_title="FPL Assistant Manager", page_icon="⚽", layout="wide")

@st.cache_resource
def get_client():
    return FPLClient()

@st.cache_data(ttl=900)
def get_bootstrap():
    return get_client().bootstrap()

@st.cache_data(ttl=900)
def get_fixtures():
    return get_client().fixtures()

bootstrap = get_bootstrap()
fixtures = get_fixtures()

if not bootstrap:
    st.error("Unable to load official FPL data. Try refreshing.")
    st.stop()

events = bootstrap.get("events", [])
current_event = next((e for e in events if e.get("is_current")), None)
next_event = next((e for e in events if e.get("is_next")), None)
current_gw = current_event["id"] if current_event else 1
next_gw = next_event["id"] if next_event else current_gw + 1

st.sidebar.header("⚙️ Manager settings")
team_id = st.sidebar.number_input("Your FPL Team ID", min_value=1, value=3240706, step=1)
league_id = st.sidebar.number_input("Classic League ID (optional)", min_value=0, value=0, step=1)
horizon = st.sidebar.slider("Planning horizon", 3, 8, 6)
simulations = st.sidebar.select_slider("Monte Carlo simulations", options=[1000, 2500, 5000, 10000], value=5000)

client = get_client()
team_data, loaded_gw = client.picks(team_id, current_gw)

if not team_data:
    st.error("Could not load your FPL squad. Check the Team ID.")
    st.stop()

projection = ProjectionEngine(bootstrap, fixtures)
squad = projection.squad_dataframe(team_data.get("picks", []), next_gw, horizon)

if squad.empty:
    st.error("Your squad could not be constructed from the FPL data.")
    st.stop()

history = team_data.get("entry_history", {})
bank = float(history.get("bank", 0) or 0) / 10
free_transfers = int(history.get("event_transfers", 1) or 1)
team_value = float(history.get("value", 0) or 0) / 10

team = TeamOptimizer(squad).best_xi()
captains = CaptainEngine().rank(team["starting"])

transfer_engine = TransferEngine(
    bootstrap=bootstrap,
    projection=projection,
    squad=squad,
    bank=bank,
    horizon=horizon,
    next_gw=next_gw,
    free_transfers=free_transfers,
)
transfers = transfer_engine.rank()
decision = transfer_engine.decision(transfers)

st.title("⚽ FPL Assistant Manager")
st.caption(f"GW{current_gw} active • Planning for GW{next_gw} • {horizon}-GW model")

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Projected XI", f'{team["projected"]:.1f}')
m2.metric("Formation", team["formation"])
m3.metric("Captain", captains.iloc[0]["Name"])
m4.metric("Best transfer", f'{decision["gain"]:+.1f}')
m5.metric("Bank", f"£{bank:.1f}m")

st.subheader("🧠 Assistant Manager verdict")
v1, v2 = st.columns([1, 2])
with v1:
    st.metric("Decision", decision["label"])
with v2:
    st.info(decision["reason"])

st.divider()
t1, t2, t3, t4, t5 = st.tabs(["📋 XI & Bench", "👑 Captain", "🔄 Transfers", "📈 Projections", "🕵️ Rivals"])

with t1:
    st.subheader(f'Optimal XI — {team["formation"]}')
    st.dataframe(
        team["starting"][["Name","Team","Pos","Price","GW_xP","Horizon_xP","Start_Prob"]],
        hide_index=True, use_container_width=True
    )
    st.subheader("Bench priority")
    st.dataframe(
        team["bench"][["Name","Team","Pos","Price","GW_xP","Horizon_xP","Start_Prob","Bench_Score"]],
        hide_index=True, use_container_width=True
    )

with t2:
    st.subheader("Captain matrix")
    st.dataframe(
        captains[["Name","Team","GW_xP","Start_Prob","Ownership","Captain_Score","Captain_Confidence"]].head(10),
        hide_index=True, use_container_width=True
    )
    st.success(f'👑 Captain: {captains.iloc[0]["Name"]}')
    st.info(f'Vice-captain: {captains.iloc[1]["Name"]}')

    sims = MonteCarloEngine(seed=42).simulate_player(captains.iloc[0], simulations)
    a, b, c = st.columns(3)
    a.metric("Mean simulated points", f'{sims["mean"]:.2f}')
    b.metric("10+ points", f'{sims["p10"]:.0%}')
    c.metric("15+ points", f'{sims["p15"]:.0%}')

with t3:
    st.subheader("Best transfer moves")
    if transfers.empty:
        st.success("No convincing transfer found.")
    else:
        st.dataframe(transfers.head(20), hide_index=True, use_container_width=True)
        best = transfers.iloc[0]
        st.success(
            f'{best["OUT"]} → {best["IN"]}: '
            f'{best["GW_Gain"]:+.2f} GW points, '
            f'{best["Horizon_Gain"]:+.2f} over {horizon} GWs.'
        )

with t4:
    st.subheader("Projected player pool")
    display = squad.sort_values("Horizon_xP", ascending=False)
    st.dataframe(
        display[["Name","Team","Pos","Price","Form","PPG","GW_xP","Horizon_xP","Start_Prob","FDR","Trend"]].head(50),
        hide_index=True, use_container_width=True
    )

with t5:
    st.subheader("Mini-league intelligence")
    if league_id == 0:
        st.info("Enter a Classic League ID in the sidebar.")
    else:
        league = client.league(league_id)
        if not league:
            st.warning("Could not load the league.")
        else:
            report = RivalEngine(client, projection, team_id, league, current_gw).report()
            a,b,c = st.columns(3)
            a.metric("Your rank", f'#{report["rank"]}')
            b.metric("Gap to leader", report["gap"])
            c.metric("Squad overlap", f'{report["overlap"]:.0f}%')
            if not report["threats"].empty:
                st.dataframe(report["threats"], hide_index=True, use_container_width=True)

st.divider()
st.subheader("📆 Fixture planner")
st.dataframe(projection.fixture_matrix(next_gw, horizon), hide_index=True, use_container_width=True)

st.divider()
st.subheader("🚀 Differential watchlist")
market = projection.market_dataframe(next_gw, horizon)
diffs = market[(market["Ownership"] <= 10) & (market["Horizon_xP"] >= market["Horizon_xP"].quantile(.70))]
if diffs.empty:
    st.info("No standout low-owned differentials in the current model.")
else:
    st.dataframe(
        diffs.sort_values("Horizon_xP", ascending=False).head(15),
        hide_index=True, use_container_width=True
    )

st.caption("All projections are estimates, not official FPL predictions.")
