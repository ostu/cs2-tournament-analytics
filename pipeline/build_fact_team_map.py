"""
build_fact_team_map.py
========================

Грейн: одна команда на одной сыгранной карте. Считается ПРЯМО из
`fact_player_map` (агрегация по составу) — round_diff берётся из уже
распарсенного счёта, НЕ через `SUM(PP)/5` (см. docs/powerbi_logic_audit.md
§4 — легаси-формула ломается на составах не из 5 игроков).

Результат: data/processed/fact_team_map.parquet

Запуск (из корня репозитория):

    python pipeline/build_fact_team_map.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PROCESSED_DIR  # noqa: E402
from io_utils import read_table, write_table  # noqa: E402

GROUP_KEY = ["tournament_number", "tournament_phase", "map_number", "team_key"]


def build_fact_team_map() -> tuple[pd.DataFrame, dict]:
    player_map = read_table(PROCESSED_DIR / "fact_player_map.parquet")

    def _first_consistent(series: pd.Series):
        vals = series.dropna().unique()
        return vals[0] if len(vals) else None

    def _is_consistent(series: pd.Series) -> bool:
        return series.dropna().nunique() <= 1

    grouped = player_map.groupby(GROUP_KEY, dropna=False)

    rows = []
    inconsistent_score_groups = 0
    for key, grp in grouped:
        tournament_number, tournament_phase, map_number, team_key = key
        score_consistent = _is_consistent(grp["team_score"]) and _is_consistent(grp["opponent_score"])
        if not score_consistent:
            inconsistent_score_groups += 1

        flags: list[str] = []
        if not score_consistent:
            flags.append("inconsistent_score_within_team_map")

        rows.append({
            "tournament_number": tournament_number,
            "tournament_phase": tournament_phase,
            "match_id": _first_consistent(grp["match_id"]),
            "map_number": map_number,
            "map_key": _first_consistent(grp["map_key"]),
            "team_key": team_key,
            "opponent_team_key": _first_consistent(grp["opponent_team_key"]),
            "team_score": _first_consistent(grp["team_score"]),
            "opponent_score": _first_consistent(grp["opponent_score"]),
            "round_diff": _first_consistent(grp["round_diff"]),
            "win_loss": _first_consistent(grp["win_loss"]),
            "players_count": int(len(grp)),
            "kills_total": int(grp["kills"].sum()),
            "deaths_total": int(grp["deaths"].sum()),
            "elo_change_total": grp["elo_change"].sum(skipna=True) if grp["elo_change"].notna().any() else None,
            "result_parse_status": _first_consistent(grp["result_parse_status"]),
            "data_quality_flags": flags,
        })

    fact = pd.DataFrame(rows)

    # --- reconciliation: две стороны карты должны быть зеркальны ---
    recon_stats = {"total_maps": 0, "maps_with_2_teams": 0, "mirror_ok": 0, "mirror_mismatch": []}
    map_key_cols = ["tournament_number", "tournament_phase", "map_number"]
    for key, grp in fact.groupby(map_key_cols):
        recon_stats["total_maps"] += 1
        if len(grp) != 2:
            continue
        recon_stats["maps_with_2_teams"] += 1
        a, b = grp.iloc[0], grp.iloc[1]

        wl_opposite = {a["win_loss"], b["win_loss"]} == {"W", "L"}
        same_match = a["match_id"] == b["match_id"]
        mirrored_scores = (
            pd.isna(a["round_diff"]) or pd.isna(b["round_diff"]) or (a["round_diff"] == -b["round_diff"])
        )
        opponents_correct = a["opponent_team_key"] == b["team_key"] and b["opponent_team_key"] == a["team_key"]

        if wl_opposite and same_match and mirrored_scores and opponents_correct:
            recon_stats["mirror_ok"] += 1
        else:
            recon_stats["mirror_mismatch"].append({
                "tournament_number": key[0], "tournament_phase": key[1], "map_number": key[2],
                "wl_opposite": wl_opposite, "same_match": same_match,
                "mirrored_scores": mirrored_scores, "opponents_correct": opponents_correct,
            })

    return fact, recon_stats


def main() -> int:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    print("[build_fact_team_map] строю fact_team_map ...")
    fact, recon_stats = build_fact_team_map()

    n_dupes = fact.duplicated(subset=GROUP_KEY).sum()
    print(f"[build_fact_team_map] дублей по гранулярному ключу: {n_dupes}")
    print(f"[build_fact_team_map] reconciliation: {recon_stats['maps_with_2_teams']}/{recon_stats['total_maps']} "
          f"карт с ровно 2 командами, зеркальность OK: {recon_stats['mirror_ok']}, "
          f"несовпадений: {len(recon_stats['mirror_mismatch'])}")

    write_table(fact, PROCESSED_DIR / "fact_team_map.parquet")
    print(f"[build_fact_team_map] Готово. Строк: {len(fact)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
