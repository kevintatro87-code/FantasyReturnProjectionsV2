"""Return-yard fantasy points: lineup and waiver helper for Sleeper leagues."""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

import core

# Defaults used when the page opens. Change these, or edit the fields in the
# app. Anyone who can see this repository can see these two values.
DEFAULT_LEAGUE_ID = "1402446958437634049"
DEFAULT_USERNAME = "tatersarsenal"
DEFAULT_TEAM_NAME = "Dak and Balls"

DATA = Path(__file__).parent / "data"

st.set_page_config(page_title="Return Points", page_icon="🏈", layout="centered")


# ---------------------------------------------------------------- caching
@st.cache_data(ttl=86400, show_spinner=False)
def cached_players():
    return core.load_players()


@st.cache_data(ttl=300, show_spinner=False)
def cached_league(league_id, username, team_label):
    return core.load_league(league_id, username, team_label)


@st.cache_data(show_spinner=False)
def cached_data(stamp):
    return core.load_data(DATA)


@st.cache_data(ttl=900, show_spinner=False)
def cached_model(stamp, league_id, username, team_label, week):
    pbp, sch, id_map, _ = cached_data(stamp)
    league, pid_to_owner, _, _ = cached_league(league_id, username, team_label)
    players = cached_players()
    _, pts_yd, pts_td = core.scoring_rates(league.get("scoring_settings", {}))
    return core.run_model(pbp, sch, id_map, players, core.build_index(players),
                          pid_to_owner, pts_yd, pts_td, week)


@st.cache_data(ttl=900, show_spinner=False)
def cached_offense(season, week, scoring):
    rules = core.find_return_scoring(scoring)
    skip = core.BASE_RETURN_KEYS | set(rules)
    return core.fetch_offense(season, week, scoring, skip)


def read_stamp():
    meta_file = DATA / "meta.json"
    if not meta_file.exists():
        return None
    return meta_file.read_text()


# ---------------------------------------------------------------- settings
qp = st.query_params
st.title("🏈 Return Points")

with st.expander("League settings"):
    league_id = st.text_input("Sleeper league ID", qp.get("league", DEFAULT_LEAGUE_ID))
    username = st.text_input("Sleeper username", qp.get("user", DEFAULT_USERNAME))
    team_label = st.text_input("Your team name (optional)",
                               qp.get("team", DEFAULT_TEAM_NAME))
    include_all = st.checkbox("Consider every free agent, not just returners", False)
    min_ret = st.slider("Minimum projected return points to consider", 0.0, 5.0, 1.0, 0.5)
st.query_params["league"] = league_id
st.query_params["user"] = username
st.query_params["team"] = team_label

stamp = read_stamp()
if stamp is None:
    st.error("No data files yet. In your GitHub repository open the Actions tab, "
             "choose 'Refresh NFL data' and click 'Run workflow'. Then reload this page.")
    st.stop()
meta = json.loads(stamp)

try:
    pbp, sch, id_map, _ = cached_data(stamp)
except Exception as exc:
    st.error(f"Couldn't read the data files: {exc}")
    st.stop()

default_week = core.next_week(sch) or int(meta["through_week"])
col_week, col_refresh = st.columns([3, 1])
with col_week:
    week = int(st.number_input("Week to project", 1, 22, int(default_week)))
with col_refresh:
    st.write("")
    if st.button("Refresh"):
        st.cache_data.clear()
        st.rerun()
st.caption(f"{meta['season']} season · play-by-play through week "
           f"{meta['through_week']} · data built {meta['built']}")

if not league_id.strip() or not username.strip():
    st.info("Enter your Sleeper league ID and username above.")
    st.stop()

try:
    with st.spinner("Loading Sleeper and running the model..."):
        league, pid_to_owner, my_roster, my_label = cached_league(
            league_id.strip(), username.strip(), team_label)
        result = cached_model(stamp, league_id.strip(), username.strip(), team_label, week)
except Exception as exc:
    st.error(f"Couldn't reach Sleeper or run the model: {exc}")
    st.stop()

scoring = league.get("scoring_settings", {})
rules, pts_yd, pts_td = core.scoring_rates(scoring)
if not rules:
    st.warning("No return-yard scoring found in this league's settings, so return "
               "points are using a default of 0.1 per yard.")
proj, stats, bt, cal = result["proj"], result["stats"], result["bt"], result["cal"]
me = my_label or "Your team"

tab_waiver, tab_proj, tab_season, tab_check = st.tabs(
    ["Lineup & waivers", "Projections", "Season so far", "Model check"])


def owner_filter(df, key):
    choice = st.radio("Show", ["All", "Free agents", "My team"], horizontal=True, key=key)
    if choice == "Free agents":
        return df[df.owner == "FA"]
    if choice == "My team":
        return df[df.owner == me]
    return df


# ---------------------------------------------------------------- tab 1
with tab_waiver:
    if proj.empty:
        st.info(f"No projections for week {week}.")
    elif my_roster is None:
        st.warning("Couldn't find your roster. Check the username in League settings.")
    else:
        with st.spinner("Getting offensive projections..."):
            off, src = cached_offense(meta["season"], week, scoring)
        wa = core.waiver_analysis(league, my_roster, cached_players(), pid_to_owner,
                                  off, proj, include_all, min_ret)
        st.subheader("Waiver moves")
        table = wa["table"]
        if table.empty:
            st.success("No free agent would improve your lineup this week.")
        else:
            show = table.sort_values("gain", ascending=False).head(10).rename(columns={
                "add": "Add", "pos": "Pos", "team": "Team", "off": "Offense",
                "ret": "Returns", "gain": "Gain", "gain_from_ret": "From returns",
                "drop": "Drop"}).round(1)
            st.dataframe(show, hide_index=True)
            st.caption("Gain = lineup points added versus your current best lineup. "
                       "'Drop' is your lowest-projected bench player; it ignores "
                       "his value beyond this week. Check injuries and the player's "
                       "return role before acting.")

        st.subheader(f"Your best lineup: {wa['base_total']:.1f} pts")
        lineup = pd.DataFrame([{"Slot": s, "Player": c["name"], "Pos": c["pos"],
                                "Offense": c["off"], "Returns": c["ret"],
                                "Total": c["total"]} for s, c in wa["picks"]])
        st.dataframe(lineup.round(1), hide_index=True)
        st.caption(f"Offensive points: {src}.")

# ---------------------------------------------------------------- tab 2
with tab_proj:
    if proj.empty:
        st.info(f"No projections for week {week}.")
    else:
        st.caption(f"Week {week}. adj_pts scales the raw projection by {cal:.2f}, "
                   "which is how optimistic it has been in past weeks.")
        view = owner_filter(proj, "proj_filter")
        cols = ["name", "team", "opp", "owner", "kr_exp", "pr_exp", "proj_yds", "adj_pts"]
        st.dataframe(view.sort_values("adj_pts", ascending=False)
                     .head(30)[cols].round(1), hide_index=True)

# ---------------------------------------------------------------- tab 3
with tab_season:
    st.caption("Return points scored so far this season under your league's rules.")
    view = owner_filter(stats, "season_filter")
    min_att = st.slider("Minimum returns", 0, 40, 10)
    view = view[view.ret_att >= min_att]
    cols = ["name", "team", "owner", "ret_att", "ret_yds", "ret_tds", "ret_pts",
            "pts_per_game"]
    st.dataframe(view.sort_values("ret_pts", ascending=False).head(40)[cols].round(1),
                 hide_index=True)
    if result["unmatched"]:
        with st.expander(f"{len(result['unmatched'])} returners couldn't be matched to Sleeper"):
            st.dataframe(pd.DataFrame(result["unmatched"],
                                      columns=["Name", "Return yds", "Why"]),
                         hide_index=True)

# ---------------------------------------------------------------- tab 4
with tab_check:
    if not bt:
        st.info("Not enough completed weeks yet to check the model.")
    else:
        st.caption(f"Each past week (weeks {bt['weeks']}, {bt['n']} player-games) was "
                   "projected using only the weeks before it, then compared with what "
                   "happened. 'Season average' just uses each player's average so far.")
        a, b = st.columns(2)
        a.metric("Average miss (model)", f"{bt['mae_model']:.2f} pts")
        b.metric("Average miss (season avg)", f"{bt['mae_base']:.2f} pts")
        a.metric("Correlation (model)", f"{bt['corr_model']:.2f}")
        b.metric("Correlation (season avg)", f"{bt['corr_base']:.2f}")
        st.caption(f"Projected {bt['mean_proj']:.2f} pts per player-game on average "
                   f"versus {bt['mean_actual']:.2f} actual, so projections are scaled "
                   f"by {cal:.2f}.")
    st.caption(f"Receiving-team columns detected: kickoffs = {result['conv']['kr']}, "
               f"punts = {result['conv']['pr']}.")
