"""
build_dimensions.py
====================

Строит справочные (dimension) таблицы из `data/interim/` +
`data/private/player_aliases.csv`. Читает только уже нормализованные данные
(результат `normalize_sources.py`) — не трогает `data/raw/`.

Результаты:
    - data/processed/dim_tournament.parquet
    - data/processed/dim_rating_stage.parquet
    - data/processed/dim_team.parquet
    - data/processed/dim_map.parquet
    - data/processed/dim_player_public.parquet   (public_player_id, public_nickname — БЕЗ private key)
    - data/private/dim_player_private.parquet    (реальные идентификаторы — только в data/private/)

Запуск (из корня репозитория):

    python pipeline/build_dimensions.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    INTERIM_DIR,
    PRIVATE_DIR,
    PROCESSED_DIR,
    RATING_STAGE_INFO,
    RATING_STAGE_ORDER,
)
from io_utils import load_private_alias_bridge, read_table, write_table  # noqa: E402


def build_dim_rating_stage() -> pd.DataFrame:
    rows = []
    for stage, order in RATING_STAGE_ORDER.items():
        info = RATING_STAGE_INFO[stage]
        rows.append({
            "rating_stage": stage,
            "stage_name": info["stage_name"],
            "stage_order": order,
            "stage_description": info["description"],
        })
    return pd.DataFrame(rows).sort_values("stage_order").reset_index(drop=True)


def build_dim_tournament() -> pd.DataFrame:
    results = read_table(INTERIM_DIR / "tournament_results_normalized.parquet")
    elo_norm = read_table(INTERIM_DIR / "elo_norm_normalized.parquet")

    rows = []
    for n in sorted(results["tournament_number"].dropna().unique()):
        n = int(n)
        t_rows = results[results["tournament_number"] == n]
        stages_in_results = set(t_rows["rating_stage"].unique())
        stages_in_norm = set(elo_norm[elo_norm["tournament_number"] == n]["rating_stage"].unique())
        stages_present = stages_in_results | stages_in_norm

        rows.append({
            "tournament_key": f"T{n}",
            "tournament_number": n,
            "tournament_label": f"Турнир {n}",
            "tournament_start_date": t_rows["date"].min() if len(t_rows) else None,
            "tournament_end_date": t_rows["date"].max() if len(t_rows) else None,
            "player_count": int(t_rows["id"].nunique()),
            "team_count": int(t_rows["canonical_team_name"].nunique()),
            "maps_count": int(t_rows[["rating_stage", "map_number_canonical"]].drop_duplicates().shape[0]),
            "rating_stages_present": sorted(stages_present, key=lambda s: RATING_STAGE_ORDER.get(s, 999)),
            "rating_cycle_available": stages_present == set(RATING_STAGE_ORDER.keys()),
            "adr_tracked_any": bool(t_rows["adr_tracked"].any()),
            "mvp_tracked_any": bool(t_rows["mvp_tracked"].any()),
            "source_coverage_flags": (
                (["adr_not_tracked_in_tournament"] if not t_rows["adr_tracked"].any() else [])
                + (["mvp_not_tracked_in_tournament"] if not t_rows["mvp_tracked"].any() else [])
            ),
        })
    return pd.DataFrame(rows)


def classify_team_types(results: pd.DataFrame, score: pd.DataFrame, players_wide: pd.DataFrame) -> dict:
    """Разделяет команды на постоянные турнирные и калибровочные (временные
    составы calibration games) на основе РЕАЛЬНОГО сигнала в данных, а не по
    названию: `tournament_results_normalized.rating_stage` показывает, что
    КАЖДАЯ команда встречается ИСКЛЮЧИТЕЛЬНО в стадии 'c' ИЛИ исключительно в
    стадии 't' — пересечения на реальных данных нет (0 из 66 команд).
    `cs_farpost_tournament_score.xlsx` и `players.t{n}_team` структурно
    описывают только основной турнир (формат 'tN', см. docs/rating_semantics.md
    §1) — присутствие там (для команд, отсутствующих в tournament_results,
    например неутверждённые alias-варианты) тоже означает permanent-команду.
    Если команда не находится ни в одном из этих сигналов — 'unknown' с флагом,
    не угадываем."""
    phase_sets = results.groupby("canonical_team_name")["rating_stage"].apply(lambda s: set(s.dropna().unique()))

    score_teams = set(score["canonical_team_name"].dropna().unique())
    team_cols = [c for c in players_wide.columns if re.fullmatch(r"t\d+_team", c)]
    players_wide_teams = set()
    for c in team_cols:
        players_wide_teams.update(players_wide[c].dropna().unique().tolist())

    def classify(name: str) -> tuple[str, list[str]]:
        stages = phase_sets.get(name, set())
        if stages == {"t"}:
            return "permanent_tournament_team", []
        if stages == {"c"}:
            return "calibration_team", []
        if stages:  # непустое пересечение {'c','t'} — на текущих данных не встречается
            return "unknown", ["team_type_ambiguous_both_phases"]
        if name in score_teams or name in players_wide_teams:
            return "permanent_tournament_team", ["team_type_inferred_from_score_or_roster_only"]
        return "unknown", ["team_type_no_signal_found"]

    return {name: classify(name) for name in phase_sets.index.tolist()
            + [t for t in (score_teams | players_wide_teams) if t not in phase_sets.index]}


def build_dim_team() -> pd.DataFrame:
    results = read_table(INTERIM_DIR / "tournament_results_normalized.parquet")
    score = read_table(INTERIM_DIR / "tournament_score_normalized.parquet")
    players_wide = read_table(INTERIM_DIR / "players_wide_normalized.parquet")

    team_types = classify_team_types(results, score, players_wide)

    frames = []
    for df, tnum_col in [(results, "tournament_number"), (score, "tournament_number")]:
        sub = df[["canonical_team_name", "team_raw", "team_alias_status", tnum_col]].rename(
            columns={tnum_col: "tournament_number"}
        )
        frames.append(sub)
    combined = pd.concat(frames, ignore_index=True)

    rows = []
    for canonical, grp in combined.groupby("canonical_team_name"):
        pending = bool((grp["team_alias_status"] == "pending_review").any())
        team_type, type_flags = team_types.get(canonical, ("unknown", ["team_type_no_signal_found"]))
        flags = type_flags[:]
        if pending:
            flags.append("team_alias_pending_review")
        rows.append({
            "team_key": canonical,
            "canonical_team_name": canonical,
            "display_team_name": canonical,
            "team_type": team_type,
            "alias_status": "pending_review" if pending else "resolved",
            "raw_name_variants": sorted(grp["team_raw"].dropna().unique().tolist()),
            "first_tournament": int(grp["tournament_number"].min()),
            "last_tournament": int(grp["tournament_number"].max()),
            "data_quality_flags": sorted(set(flags)),
        })
    return pd.DataFrame(rows).sort_values("canonical_team_name").reset_index(drop=True)


def build_dim_map() -> pd.DataFrame:
    results = read_table(INTERIM_DIR / "tournament_results_normalized.parquet")
    maps = sorted(results["map"].dropna().unique())
    latest_tournament = int(results["tournament_number"].max())
    latest_maps = set(
        results[(results["tournament_number"] == latest_tournament) & (results["rating_stage"] == "t")]["map"].unique()
    )
    rows = []
    for i, m in enumerate(maps, start=1):
        rows.append({
            "map_key": m,
            "canonical_map_name": m,
            "raw_map_name": m,
            "map_order": i,
            "active_flag": m in latest_maps,
        })
    return pd.DataFrame(rows)


def build_dim_player_public_and_private() -> tuple[pd.DataFrame, pd.DataFrame]:
    players_wide = read_table(INTERIM_DIR / "players_wide_normalized.parquet")
    bridge = load_private_alias_bridge()

    private_cols = [
        "id", "player_id", "steam_name", "steam_account", "fio", "city", "department",
        "discord_name", "faceit_name", "faceit_link", "another_names",
    ]
    dim_private = players_wide[private_cols].rename(columns={"id": "private_player_key"})
    dim_private = dim_private.merge(
        bridge.rename(columns={"private_player_key": "private_player_key"}),
        on="private_player_key", how="left",
    )
    dim_private["mapping_provenance"] = "data/private/player_aliases.csv"

    dim_public = bridge[["public_player_id", "public_nickname"]].drop_duplicates().reset_index(drop=True)

    return dim_public, dim_private


def main() -> int:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)

    print("[build_dimensions] dim_rating_stage ...")
    write_table(build_dim_rating_stage(), PROCESSED_DIR / "dim_rating_stage.parquet")

    print("[build_dimensions] dim_tournament ...")
    write_table(build_dim_tournament(), PROCESSED_DIR / "dim_tournament.parquet")

    print("[build_dimensions] dim_team ...")
    write_table(build_dim_team(), PROCESSED_DIR / "dim_team.parquet")

    print("[build_dimensions] dim_map ...")
    write_table(build_dim_map(), PROCESSED_DIR / "dim_map.parquet")

    print("[build_dimensions] dim_player_public / dim_player_private ...")
    dim_public, dim_private = build_dim_player_public_and_private()
    write_table(dim_public, PROCESSED_DIR / "dim_player_public.parquet")
    write_table(dim_private, PRIVATE_DIR / "dim_player_private.parquet")

    print("[build_dimensions] Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
