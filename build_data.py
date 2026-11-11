"""Downloads NFL data and writes the small files the app reads.

Run by the GitHub Action every morning (see .github/workflows/refresh.yml),
or by hand:  python build_data.py [season]

Output (in ./data):
  plays.csv.gz   kickoff and punt plays only (a few thousand rows)
  schedule.csv   regular-season schedule with scores
  id_map.csv     nflverse player id -> Sleeper player id
  meta.json      season, latest week in the data, build date
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

OUT = Path(__file__).parent / "data"

PLAY_COLS = [
    "season", "week", "play_type", "posteam", "defteam", "return_yards",
    "return_touchdown", "kickoff_returner_player_id",
    "kickoff_returner_player_name", "punt_returner_player_id",
    "punt_returner_player_name",
]
SCHEDULE_COLS = ["season", "game_type", "week", "away_team", "home_team",
                 "away_score", "home_score"]


def current_season():
    today = dt.date.today()
    return today.year if today.month >= 3 else today.year - 1


def main():
    import nflreadpy as nfl

    season = int(sys.argv[1]) if len(sys.argv) > 1 else current_season()
    print(f"Building data for the {season} season...")

    pbp = nfl.load_pbp([season]).to_pandas()
    missing = [c for c in PLAY_COLS if c not in pbp.columns]
    if missing:
        sys.exit(f"Play-by-play is missing expected columns: {missing}")
    plays = pbp[pbp.play_type.isin(["kickoff", "punt"])][PLAY_COLS]
    if plays.empty:
        print("No kickoff/punt plays yet for this season; keeping existing data.")
        return

    sch = nfl.load_schedules([season]).to_pandas()
    if "game_type" in sch.columns:
        sch = sch[sch.game_type == "REG"]
    sch = sch[[c for c in SCHEDULE_COLS if c in sch.columns]]

    ids = nfl.load_ff_playerids().to_pandas()[["gsis_id", "sleeper_id"]].dropna()
    ids["sleeper_id"] = [str(s).split(".")[0] for s in ids.sleeper_id]

    OUT.mkdir(exist_ok=True)
    plays.to_csv(OUT / "plays.csv.gz", index=False)
    sch.to_csv(OUT / "schedule.csv", index=False)
    ids.to_csv(OUT / "id_map.csv", index=False)
    meta = {"season": season, "through_week": int(plays.week.max()),
            "built": dt.date.today().isoformat(),
            "plays": len(plays), "ids": len(ids)}
    (OUT / "meta.json").write_text(json.dumps(meta))
    print(f"Wrote {meta}")


if __name__ == "__main__":
    main()
