"""
build_fact_player_map.py
==========================

Грейн: один игрок на одной сыгранной карте.
Результат: data/processed/fact_player_map.parquet

Запуск (из корня репозитория):

    python pipeline/build_fact_player_map.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import EXPECTED_ROSTER_SIZE, INTERIM_DIR, PROCESSED_DIR  # noqa: E402
from io_utils import read_table, write_table  # noqa: E402


def build_fact_player_map() -> pd.DataFrame:
    results = read_table(INTERIM_DIR / "tournament_results_normalized.parquet")
    elo_changes = read_table(INTERIM_DIR / "elo_changes_normalized.parquet")

    map_key_cols = ["id", "tournament_number", "rating_stage", "map_number_canonical"]
    df = results.merge(
        elo_changes[map_key_cols[:-1] + ["map_number_canonical", "elo_diff"]],
        on=map_key_cols,
        how="left",
        suffixes=("", "_elo"),
    )
    df = df.rename(columns={"elo_diff": "elo_change"})

    # opponent_team_key: на каждой карте должно быть ровно 2 канонические команды
    team_map_key = ["tournament_number", "rating_stage", "map_number_canonical"]
    teams_per_map = df.groupby(team_map_key)["canonical_team_name"].agg(lambda s: sorted(set(s)))

    def _opponent(row):
        teams = teams_per_map.get((row["tournament_number"], row["rating_stage"], row["map_number_canonical"]), [])
        others = [t for t in teams if t != row["canonical_team_name"]]
        if len(teams) == 2 and len(others) == 1:
            return others[0]
        return None

    df["opponent_team_key"] = df.apply(_opponent, axis=1)
    unexpected_team_count = teams_per_map[teams_per_map.apply(len) != 2]

    # roster size check (грейн команда×карта)
    roster_size = df.groupby(team_map_key + ["canonical_team_name"]).size().rename("roster_size")
    df = df.merge(roster_size.reset_index(), on=team_map_key + ["canonical_team_name"], how="left")

    def _extra_flags(row):
        flags = list(row["data_quality_flags"])
        if row["roster_size"] != EXPECTED_ROSTER_SIZE:
            flags.append("non_standard_roster_size")
        key = (row["tournament_number"], row["rating_stage"], row["map_number_canonical"])
        if key in unexpected_team_count.index:
            flags.append("unexpected_team_count_on_map")
        if pd.isna(row["elo_change"]):
            flags.append("missing_elo_change_for_map")
        return flags

    df["data_quality_flags"] = df.apply(_extra_flags, axis=1)

    out = pd.DataFrame({
        "private_player_key": df["id"],
        "tournament_number": df["tournament_number"],
        "tournament_phase": df["rating_stage"],
        "tournament_code_raw": df["rating_code"],
        "match_id": df["match_id"],
        "map_number": df["map_number_canonical"],
        "map_key": df["map"],
        "team_key": df["canonical_team_name"],
        "opponent_team_key": df["opponent_team_key"],
        "win_loss": df["win_loss"],
        "rounds_played": df["rounds"],
        "kills": df["kills"],
        "assists": df["assists"],
        "deaths": df["deaths"],
        "adr": df["adr"],
        "adr_tracked": df["adr_tracked"],
        "mvps": df["mvps"],
        "mvp_tracked": df["mvp_tracked"],
        "elo_change": df["elo_change"],
        "result_raw": df["result_raw"],
        "team_score": df["team_score"],
        "opponent_score": df["opponent_score"],
        "round_diff": df["round_diff"],
        "result_parse_status": df["result_parse_status"],
        "source_row": df["source_row"],
        "data_quality_flags": df["data_quality_flags"],
    })
    return out, unexpected_team_count


def main() -> int:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    print("[build_fact_player_map] строю fact_player_map ...")
    fact, unexpected_team_count = build_fact_player_map()

    dup_key = ["private_player_key", "tournament_number", "tournament_phase", "map_number"]
    n_dupes = fact.duplicated(subset=dup_key).sum()
    print(f"[build_fact_player_map] дублей по ключу (игрок,турнир,стадия,карта): {n_dupes}")

    non_standard = (fact["data_quality_flags"].apply(lambda fl: "non_standard_roster_size" in fl)).sum()
    print(f"[build_fact_player_map] строк с нестандартным размером состава: {non_standard}")
    if len(unexpected_team_count):
        print(f"[build_fact_player_map] ПРЕДУПРЕЖДЕНИЕ: карт с числом команд != 2: {len(unexpected_team_count)}")

    write_table(fact, PROCESSED_DIR / "fact_player_map.parquet")
    print(f"[build_fact_player_map] Готово. Строк: {len(fact)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
