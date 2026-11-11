"""Return-yard fantasy projections: data loading, projection model, waiver math.

No UI code lives here, so the same logic can be reused from a script, a
notebook, or the Streamlit app (app.py).
"""
import json
import re
from pathlib import Path

import pandas as pd
import requests

SLEEPER = "https://api.sleeper.app/v1"

TEAM_FIX = {"LA": "LAR", "JAC": "JAX", "WSH": "WAS", "ARZ": "ARI", "BLT": "BAL",
            "CLV": "CLE", "HST": "HOU", "SD": "LAC", "STL": "LAR", "OAK": "LV"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
PLAYER_FIELDS = ("first_name", "last_name", "full_name", "position", "team",
                 "injury_status", "fantasy_positions")


def get_json(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def fix_team(t):
    return TEAM_FIX.get(t, t) if isinstance(t, str) else t


def norm_last(s):
    toks = re.sub(r"[^a-z ]", "", (s or "").lower()).split()
    while toks and toks[-1] in SUFFIXES:
        toks.pop()
    return "".join(toks)


def key_nflverse(name):
    # nflverse short names look like "J.Noel"
    if not isinstance(name, str) or "." not in name:
        return None
    ini, last = name.split(".", 1)
    return ini.strip().lower()[:1] + norm_last(last)


def key_sleeper(p):
    return (p.get("first_name") or "")[:1].lower() + norm_last(p.get("last_name"))


def find_return_scoring(scoring):
    found = {}
    for k, v in scoring.items():
        kl = k.lower()
        if kl.startswith(("kr_", "pr_")) or "ret" in kl or kl == "st_td":
            if v:
                found[k] = v
    return found


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data(data_dir):
    """Read the slim files written by build_data.py."""
    data_dir = Path(data_dir)
    pbp = pd.read_csv(data_dir / "plays.csv.gz")
    sch = pd.read_csv(data_dir / "schedule.csv")
    ids = pd.read_csv(data_dir / "id_map.csv", dtype=str)
    id_map = dict(zip(ids.gsis_id, ids.sleeper_id))
    meta = json.loads((data_dir / "meta.json").read_text())
    return pbp, sch, id_map, meta


def load_players():
    """Sleeper's player list, trimmed to the fields we use. Sleeper asks apps
    to fetch this at most about once a day, so callers should cache it."""
    raw = get_json(f"{SLEEPER}/players/nfl")
    return {pid: {k: p.get(k) for k in PLAYER_FIELDS} for pid, p in raw.items()}


def build_index(players):
    index = {}
    for pid, p in players.items():
        if p.get("team") and p.get("last_name"):
            index.setdefault(key_sleeper(p), []).append((pid, p))
    return index


def load_league(league_id, username, team_label=""):
    """League settings, who owns each player, and the user's own roster."""
    league = get_json(f"{SLEEPER}/league/{league_id}")
    users = get_json(f"{SLEEPER}/league/{league_id}/users")
    rosters = get_json(f"{SLEEPER}/league/{league_id}/rosters")

    me = next((u for u in users
               if u.get("display_name", "").lower() == username.strip().lower()), None)
    my_label = team_label.strip()
    if me and not my_label:
        my_label = (me.get("metadata") or {}).get("team_name") or me["display_name"]

    owner_label = {}
    for u in users:
        if me and u["user_id"] == me["user_id"]:
            owner_label[u["user_id"]] = my_label
        else:
            owner_label[u["user_id"]] = ((u.get("metadata") or {}).get("team_name")
                                         or u.get("display_name"))
    pid_to_owner, my_roster = {}, None
    for r in rosters:
        label = owner_label.get(r.get("owner_id"), f"Roster {r['roster_id']}")
        for pid in r.get("players") or []:
            pid_to_owner[pid] = label
        if me and r.get("owner_id") == me["user_id"]:
            my_roster = r
    return league, pid_to_owner, my_roster, my_label


def scoring_rates(scoring):
    """Return-related scoring rules: (rules, points per yard, points per TD)."""
    rules = find_return_scoring(scoring)
    pts_yd = next((v for k, v in rules.items() if k.endswith("_yd")), 0.1)
    pts_td = next((v for k, v in rules.items() if k.endswith("_td")), 0)
    return rules, pts_yd, pts_td


def build_return_stats(pbp):
    kr = pbp[(pbp.play_type == "kickoff") & pbp.kickoff_returner_player_id.notna()]
    pr = pbp[(pbp.play_type == "punt") & pbp.punt_returner_player_id.notna()]

    def own_teams(df, id_col):
        # A returner's own team shows up in nearly all of his plays (in either
        # posteam or defteam); opponents only show up in one or two games.
        res = {}
        for pid, sub in df.groupby(id_col):
            counts = pd.concat([sub.posteam, sub.defteam]).dropna().value_counts()
            keep = {t for t, c in counts.items() if c / len(sub) >= 0.3}
            keep.add(counts.index[0])
            res[pid] = keep
        return res

    def agg(df, id_col, name_col, prefix):
        g = df.groupby(id_col).agg(
            name=(name_col, "last"),
            att=("return_yards", "size"),
            yds=("return_yards", "sum"),
            tds=("return_touchdown", "sum"),
            games=("week", "nunique"),
        )
        own = own_teams(df, id_col)
        g["teams"] = [own[i] for i in g.index]
        return g.rename(columns={c: f"{prefix}_{c}" for c in
                                 ["att", "yds", "tds", "games"]})

    k = agg(kr, "kickoff_returner_player_id", "kickoff_returner_player_name", "kr")
    p = agg(pr, "punt_returner_player_id", "punt_returner_player_name", "pr")
    out = k.join(p, how="outer", lsuffix="_k", rsuffix="_p")

    out["name"] = out["name_k"].fillna(out["name_p"])
    out["teams"] = [
        (a if isinstance(a, set) else set()) | (b if isinstance(b, set) else set())
        for a, b in zip(out["teams_k"], out["teams_p"])
    ]
    out = out.drop(columns=["name_k", "name_p", "teams_k", "teams_p"])
    num = [c for c in out.columns if c not in ("name", "teams")]
    out[num] = out[num].fillna(0)
    out["ret_att"] = out.kr_att + out.pr_att
    out["ret_yds"] = out.kr_yds + out.pr_yds
    out["ret_tds"] = out.kr_tds + out.pr_tds
    out["games_returning"] = out[["kr_games", "pr_games"]].max(axis=1)
    return out


def match_players(stats, pid_to_owner, index, players, id_map):
    names, teams, owners, matched, sids = [], [], [], [], []
    unmatched = []
    for gsis, row in stats.iterrows():
        sid = id_map.get(str(gsis))
        if sid and sid in players:
            p = players[sid]
            names.append(p.get("full_name") or row["name"])
            teams.append(p.get("team") or "none")
            owners.append(pid_to_owner.get(sid, "FA"))
            matched.append(True)
            sids.append(sid)
            continue
        k = key_nflverse(row["name"])
        row_teams = {fix_team(t) for t in row["teams"]}
        cands = [(pid, p) for pid, p in index.get(k, [])
                 if fix_team(p["team"]) in row_teams]
        if len(cands) > 1:
            # Tiebreak: prefer offensive skill players over everyone else
            skill = [c for c in cands if c[1].get("position") in ("RB", "WR", "TE", "QB")]
            if len(skill) == 1:
                cands = skill
        if len(cands) == 1:
            pid, p = cands[0]
            names.append(p.get("full_name") or row["name"])
            teams.append(p["team"])
            owners.append(pid_to_owner.get(pid, "FA"))
            matched.append(True)
            sids.append(pid)
        else:
            names.append(row["name"])
            teams.append("?")
            owners.append("?")
            matched.append(False)
            sids.append(None)
            if len(cands) > 1:
                why = "ambiguous: " + "; ".join(
                    f"{p.get('full_name')} {p.get('position')} {p.get('team')}"
                    for _, p in cands)
            else:
                why = "no match"
            unmatched.append((row["name"], row["ret_yds"], why))
    stats = stats.copy()
    stats["name"], stats["team"], stats["owner"] = names, teams, owners
    stats["sid"] = sids
    return stats, unmatched


# ---------------------------------------------------------------------------
# Projection layer
# ---------------------------------------------------------------------------
N_ROLE = 4        # recent games used to decide who holds the returner role
N_TEAM = 6        # recent games used for a team's return opportunity rate
K_TEAM = 2        # games of league-average rate blended into team rates
K_YPR = {"kr": 25, "pr": 30}  # returns of league-average yards blended in
K_TD = 150        # returns of league-average TD rate blended in
OPP_SHRINK = 0.5  # fraction of the opponent adjustment that gets applied


def build_plays(pbp):
    """One row per kickoff/punt, with the receiving and kicking team worked out.

    nflverse's posteam/defteam can mean either side on special teams plays, so
    the convention is detected from the data: for plays with a returner, his
    own team is the one that appears in nearly all of his plays.
    """
    df = pbp[pbp.play_type.isin(["kickoff", "punt"])].copy()
    df["posteam"] = df.posteam.map(fix_team)
    df["defteam"] = df.defteam.map(fix_team)
    df["kind"] = df.play_type.map({"kickoff": "kr", "punt": "pr"})
    df["rid"] = df.kickoff_returner_player_id.where(
        df.kind == "kr", df.punt_returner_player_id)
    df["rname"] = df.kickoff_returner_player_name.where(
        df.kind == "kr", df.punt_returner_player_name)

    ret = df[df.rid.notna()]
    both = pd.concat([
        ret[["rid", "posteam"]].rename(columns={"posteam": "t"}),
        ret[["rid", "defteam"]].rename(columns={"defteam": "t"}),
    ]).dropna()
    top = both.groupby("rid").t.agg(lambda s: s.value_counts().index[0])
    ret = ret.assign(top=ret.rid.map(top))
    conv = {}
    for kind in ("kr", "pr"):
        sub = ret[ret.kind == kind]
        frac = (sub.posteam == sub.top).mean() if len(sub) else 1.0
        conv[kind] = "posteam" if frac >= 0.5 else "defteam"

    df["recv"] = [p if conv[k] == "posteam" else d
                  for p, d, k in zip(df.posteam, df.defteam, df.kind)]
    df["kick"] = [d if conv[k] == "posteam" else p
                  for p, d, k in zip(df.posteam, df.defteam, df.kind)]
    df["yds"] = df.return_yards.fillna(0)
    df["td"] = df.return_touchdown.fillna(0)
    return df, conv


def team_games(sch):
    """One row per completed team-game: week, team, opponent, points scored."""
    s = sch.dropna(subset=["home_score", "away_score"])
    home = pd.DataFrame({"week": s.week, "team": s.home_team.map(fix_team),
                         "opp": s.away_team.map(fix_team), "pts": s.home_score})
    away = pd.DataFrame({"week": s.week, "team": s.away_team.map(fix_team),
                         "opp": s.home_team.map(fix_team), "pts": s.away_score})
    return pd.concat([home, away], ignore_index=True)


def project_week(plays, games, sch, week, pts_yd, pts_td):
    """Project return points for every active returner in `week`.

    Only data from weeks before `week` is used, so the same function drives
    both the live projection and the backtest.
    """
    prior_games = games[games.week < week]
    if prior_games.empty:
        return pd.DataFrame()
    played = plays[plays.week < week]
    ret = played[played.rid.notna()]
    if ret.empty:
        return pd.DataFrame()

    n_games = prior_games.groupby("team").size()
    pts_sum = prior_games.groupby("team").pts.sum()
    league_ppg = prior_games.pts.mean()
    team_weeks = {t: sorted(g.week) for t, g in prior_games.groupby("team")}

    punts_by_team = played[played.kind == "pr"].groupby("kick").size()
    league_punts_pg = punts_by_team.sum() / max(len(prior_games), 1)

    lg_rate, lg_ypr, lg_tdr = {}, {}, {}
    for k in ("kr", "pr"):
        r = ret[ret.kind == k]
        lg_rate[k] = len(r) / max(len(prior_games), 1)
        lg_ypr[k] = r.yds.sum() / max(len(r), 1)
        lg_tdr[k] = r.td.sum() / max(len(r), 1)

    wk = sch[sch.week == week]
    opp_of = {}
    for h, a in zip(wk.home_team.map(fix_team), wk.away_team.map(fix_team)):
        opp_of[h], opp_of[a] = a, h

    ordered = ret.sort_values("week")
    last_team = ordered.groupby("rid").recv.last()
    names = ordered.groupby("rid").rname.last()

    rows = []
    for rid, team in last_team.items():
        opp = opp_of.get(team)
        if opp is None:  # bye week, or team not on the schedule
            continue
        opp_n = n_games.get(opp, 0)
        opp_ppg = (pts_sum.get(opp, 0) + 2 * league_ppg) / (opp_n + 2)
        f_k = 1 + OPP_SHRINK * (opp_ppg / league_ppg - 1) if league_ppg else 1
        opp_punts = (punts_by_team.get(opp, 0) + 2 * league_punts_pg) / (opp_n + 2)
        f_p = (1 + OPP_SHRINK * (opp_punts / league_punts_pg - 1)
               if league_punts_pg else 1)

        t_weeks = team_weeks.get(team, [])
        rate_weeks, role_weeks = t_weeks[-N_TEAM:], t_weeks[-N_ROLE:]
        mine_all = ret[ret.rid == rid]
        out = {"rid": rid, "name": names[rid], "team": team, "opp": opp}
        yds = tds = 0.0
        for kind, f in (("kr", f_k), ("pr", f_p)):
            team_ret = ret[(ret.kind == kind) & (ret.recv == team)]
            n_recent = team_ret.week.isin(rate_weeks).sum()
            rate = (n_recent + K_TEAM * lg_rate[kind]) / (len(rate_weeks) + K_TEAM)
            role = team_ret[team_ret.week.isin(role_weeks)]
            share = (role.rid == rid).sum() / len(role) if len(role) else 0.0
            mine = mine_all[mine_all.kind == kind]
            n_i = len(mine)
            ypr = (mine.yds.sum() + K_YPR[kind] * lg_ypr[kind]) / (n_i + K_YPR[kind])
            tdr = (mine.td.sum() + K_TD * lg_tdr[kind]) / (n_i + K_TD)
            exp_ret = rate * f * share
            out[f"{kind}_exp"] = exp_ret
            out[f"{kind}_share"] = share
            yds += exp_ret * ypr
            tds += exp_ret * tdr
        out["proj_yds"] = yds
        out["proj_td"] = tds
        out["proj_pts"] = yds * pts_yd + tds * pts_td
        out["base_pts"] = ((mine_all.yds.sum() * pts_yd + mine_all.td.sum() * pts_td)
                           / max(n_games.get(team, 1), 1))
        rows.append(out)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df[df.proj_pts > 0].set_index("rid")


def backtest(plays, games, sch, pts_yd, pts_td, first_week=5):
    """Compare projections with what actually happened, week by week."""
    counts = games.groupby("week").size()
    done = [w for w, n in counts.items() if n >= 20 and w >= first_week]
    ret = plays[plays.rid.notna()]
    actual = (ret.assign(pts=ret.yds * pts_yd + ret.td * pts_td)
              .groupby(["week", "rid"]).pts.sum())
    recs = []
    for w in done:
        proj = project_week(plays, games, sch, w, pts_yd, pts_td)
        if proj.empty:
            continue
        for rid, row in proj.iterrows():
            recs.append({"week": w, "proj": row.proj_pts, "base": row.base_pts,
                         "actual": actual.get((w, rid), 0.0)})
    if not recs:
        return None
    b = pd.DataFrame(recs)
    return {
        "n": len(b), "weeks": f"{min(done)}-{max(done)}",
        "mae_model": (b.proj - b.actual).abs().mean(),
        "mae_base": (b.base - b.actual).abs().mean(),
        "corr_model": b.proj.corr(b.actual),
        "corr_base": b.base.corr(b.actual),
        "mean_proj": b.proj.mean(), "mean_actual": b.actual.mean(),
    }


def next_week(sch):
    """First week that still has an unplayed game."""
    open_games = sch[sch.home_score.isna() | sch.away_score.isna()]
    return int(open_games.week.min()) if len(open_games) else None


# ---------------------------------------------------------------------------
# Waiver comparison
# ---------------------------------------------------------------------------
BASE_RETURN_KEYS = {"kr_yd", "pr_yd", "kr_td", "pr_td", "st_td", "kr", "pr",
                    "ret_yd", "ret_td"}
BAD_INJURY = {"Out", "IR", "PUP", "Sus", "COV", "DNR", "NA"}

SLOT_ELIG = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"},
    "DEF": {"DEF"}, "DL": {"DL"}, "LB": {"LB"}, "DB": {"DB"},
    "REC_FLEX": {"WR", "TE"}, "WRRB_FLEX": {"RB", "WR"},
    "FLEX": {"RB", "WR", "TE"}, "IDP_FLEX": {"DL", "LB", "DB"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
}
# Narrow slots are filled first, then flex slots take the best of what is left.
FILL_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF", "DL", "LB", "DB",
              "REC_FLEX", "WRRB_FLEX", "FLEX", "IDP_FLEX", "SUPER_FLEX"]


def score_stats(stats, scoring, skip):
    """Fantasy points from a Sleeper stat line under this league's scoring."""
    return sum(v * scoring[k] for k, v in stats.items()
               if k in scoring and k not in skip and isinstance(v, (int, float)))


def fetch_sleeper_stats(kind, season, week):
    """kind is 'projections' or 'stats'. Returns {player_id: stat dict}."""
    url = (f"https://api.sleeper.com/{kind}/nfl/{season}/{week}"
           "?season_type=regular")
    data = get_json(url)
    items = (data.items() if isinstance(data, dict)
             else [(d.get("player_id"), d) for d in data if isinstance(d, dict)])
    out = {}
    for pid, d in items:
        if not pid or not isinstance(d, dict):
            continue
        st = d["stats"] if isinstance(d.get("stats"), dict) else d
        out[str(pid)] = st
    return out


def fetch_offense(season, week, scoring, skip):
    """Offensive points per player: Sleeper's projection for `week`, falling
    back to each player's season-to-date average."""
    try:
        table = fetch_sleeper_stats("projections", season, week)
        pts = {pid: score_stats(st, scoring, skip) for pid, st in table.items()}
        if len(pts) > 100:
            return pts, f"Sleeper's week {week} projections"
    except Exception:
        pass
    history = {}
    for w in range(1, week):
        try:
            table = fetch_sleeper_stats("stats", season, w)
        except Exception:
            continue
        for pid, st in table.items():
            history.setdefault(pid, []).append(score_stats(st, scoring, skip))
    pts = {pid: sum(v) / len(v) for pid, v in history.items() if v}
    return pts, f"season-to-date averages from Sleeper stats (weeks 1-{week - 1})"


def make_cand(pid, players, off, ret):
    p = players.get(pid) or {}
    pos = p.get("position") or (p.get("fantasy_positions") or [None])[0]
    o, r = off.get(pid, 0.0), ret.get(pid, 0.0)
    name = p.get("full_name") or (f"{pid} DEF" if pos == "DEF" else pid)
    return {"pid": pid, "name": name, "pos": pos, "team": p.get("team"),
            "off": o, "ret": r, "total": o + r}


def best_lineup(slots, cands):
    order = sorted((s for s in slots if s in SLOT_ELIG), key=FILL_ORDER.index)
    used, total, picks = set(), 0.0, []
    for s in order:
        pool = [c for c in cands if c["pid"] not in used and c["pos"] in SLOT_ELIG[s]]
        if pool:
            c = max(pool, key=lambda c: c["total"])
            used.add(c["pid"])
            total += c["total"]
            picks.append((s, c))
    return total, picks, used


def waiver_report(slots, my_cands, fa_cands, open_spot):
    base_total, base_picks, _ = best_lineup(slots, my_cands)
    rows = []
    for f in fa_cands:
        t, _, used = best_lineup(slots, my_cands + [f])
        gain = t - base_total
        if gain <= 0.01:
            continue
        t_off, _, _ = best_lineup(slots, my_cands + [dict(f, total=f["off"])])
        if open_spot:
            drop = "(open roster spot)"
        else:
            bench = [c for c in my_cands if c["pid"] not in used]
            drop = min(bench, key=lambda c: c["total"])["name"] if bench else "?"
        rows.append({"add": f["name"], "pos": f["pos"], "team": f["team"],
                     "off": f["off"], "ret": f["ret"], "gain": gain,
                     "gain_from_ret": gain - (t_off - base_total), "drop": drop})
    return base_total, base_picks, pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------
def run_model(pbp, sch, id_map, players, index, pid_to_owner,
              pts_yd, pts_td, week, do_backtest=True):
    """Season-to-date return stats plus a projection for `week`."""
    stats = build_return_stats(pbp)
    stats, unmatched = match_players(stats, pid_to_owner, index, players, id_map)
    stats["ret_pts"] = stats.ret_yds * pts_yd + stats.ret_tds * pts_td
    stats["pts_per_game"] = stats.ret_pts / stats.games_returning.clip(lower=1)

    plays, conv = build_plays(pbp)
    games = team_games(sch)
    bt = backtest(plays, games, sch, pts_yd, pts_td) if do_backtest else None
    cal = 1.0
    if bt and bt["mean_proj"] > 0:
        cal = min(max(bt["mean_actual"] / bt["mean_proj"], 0.5), 1.2)

    proj = project_week(plays, games, sch, week, pts_yd, pts_td)
    if not proj.empty:
        proj = proj.join(stats[["name", "team", "owner", "sid"]], rsuffix="_s")
        proj["name"] = proj["name_s"].fillna(proj["name"])
        proj["team"] = proj["team_s"].fillna(proj["team"])
        proj["owner"] = proj["owner"].fillna("?")
        proj["adj_pts"] = proj.proj_pts * cal
    return {"stats": stats, "proj": proj, "bt": bt, "cal": cal, "conv": conv,
            "unmatched": unmatched, "week": week}


def waiver_analysis(league, my_roster, players, pid_to_owner, off, proj,
                    include_all=False, min_ret=1.0):
    """Best lineup now, and which free agents would improve it."""
    slots = league.get("roster_positions") or []
    inactive = set(my_roster.get("reserve") or []) | set(my_roster.get("taxi") or [])
    my_ids = [p for p in (my_roster.get("players") or []) if p not in inactive]
    ret = {sid: v for sid, v in zip(proj.sid, proj.adj_pts) if sid}
    my_cands = [make_cand(p, players, off, ret) for p in my_ids]
    open_spot = len(my_ids) < len(slots)

    fantasy_pos = {q for e in SLOT_ELIG.values() for q in e}
    fa_cands = []
    for pid, p in players.items():
        if pid in pid_to_owner or not p.get("team"):
            continue
        if p.get("injury_status") in BAD_INJURY or p.get("position") not in fantasy_pos:
            continue
        c = make_cand(pid, players, off, ret)
        if include_all or c["ret"] >= min_ret:
            fa_cands.append(c)

    base_total, picks, table = waiver_report(slots, my_cands, fa_cands, open_spot)
    return {"slots": slots, "base_total": base_total, "picks": picks,
            "table": table, "open_spot": open_spot}
