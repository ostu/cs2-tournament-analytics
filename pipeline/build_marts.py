"""
build_marts.py
================

Строит аналитические витрины (marts) из `data/processed/fact_*`/`dim_*`.
Формулы соответствуют `config/metric_definitions.json`. Ничего не читает из
`data/raw/` напрямую — только уже канонизированные факты/справочники.

Результаты — data/processed/mart_*.parquet:
    - mart_player_summary
    - mart_player_rating_history
    - mart_player_map
    - mart_team_summary
    - mart_team_rating_history
    - mart_team_map
    - mart_team_map_matrix

Запуск (из корня репозитория):

    python pipeline/build_marts.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import DASHBOARD_CONFIG_PATH, EXPECTED_ROSTER_SIZE, PROCESSED_DIR  # noqa: E402
from io_utils import read_table, write_table  # noqa: E402

with DASHBOARD_CONFIG_PATH.open("r", encoding="utf-8") as _fh:
    DASHBOARD_CONFIG = json.load(_fh)
LOW_SAMPLE_THRESHOLD = DASHBOARD_CONFIG["low_sample_threshold"]


def _safe_div(numer, denom):
    if denom in (0, None) or pd.isna(denom):
        return None
    return numer / denom


def _union_flags(series: pd.Series) -> list:
    flags: set[str] = set()
    for item in series:
        if isinstance(item, (list, tuple)):
            flags.update(item)
    return sorted(flags)


# ---------------------------------------------------------------------------
# Player marts
# ---------------------------------------------------------------------------

def build_mart_player_summary(player_map: pd.DataFrame, rating_events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    tfinal = rating_events[rating_events["event_type"] == "tournament_final"]
    nstart = rating_events[rating_events["event_type"] == "normalized_entry"]

    for pid, grp in player_map.groupby("private_player_key"):
        main_tour_rows = grp[grp["tournament_phase"] == "t"]
        latest_tournament = int(main_tour_rows["tournament_number"].max()) if len(main_tour_rows) else None
        latest_team = None
        if latest_tournament is not None:
            lt_rows = main_tour_rows[main_tour_rows["tournament_number"] == latest_tournament]
            latest_team = lt_rows["team_key"].mode().iloc[0] if len(lt_rows) else None

        maps_played = len(grp)
        kills, assists, deaths = int(grp["kills"].sum()), int(grp["assists"].sum()), int(grp["deaths"].sum())
        kd = _safe_div(kills, deaths)
        kr = _safe_div(kills, grp["rounds_played"].sum())
        canonical_maps = grp[["tournament_number", "tournament_phase", "map_number"]].drop_duplicates().shape[0]
        avg_kills_per_map = _safe_div(kills, canonical_maps)

        adr_rows = grp[grp["adr_tracked"]]
        adr = adr_rows["adr"].mean() if len(adr_rows) else None
        adr_maps_available = len(adr_rows)
        adr_coverage = _safe_div(adr_maps_available, maps_played)

        mvp_rows = grp[grp["mvp_tracked"]]
        mvps = int(mvp_rows["mvps"].sum()) if len(mvp_rows) else None
        mvp_maps_available = len(mvp_rows)
        mvp_coverage = _safe_div(mvp_maps_available, maps_played)

        tournaments_played = int(main_tour_rows["tournament_number"].nunique())

        player_tfinal = tfinal[tfinal["private_player_key"] == pid]
        player_nstart = nstart[nstart["private_player_key"] == pid]
        rating_final = None
        rating_start = None
        if latest_tournament is not None:
            rf = player_tfinal[player_tfinal["tournament_number"] == latest_tournament]["rating_after"]
            rating_final = float(rf.iloc[0]) if len(rf) else None
            rs = player_nstart[player_nstart["tournament_number"] == latest_tournament]["rating_after"]
            rating_start = float(rs.iloc[0]) if len(rs) else None
        rating_change = (rating_final - rating_start) if (rating_final is not None and rating_start is not None) else None
        rating_change_percent = _safe_div(rating_change, rating_start) if rating_change is not None else None

        flags = _union_flags(grp["data_quality_flags"])

        rows.append({
            "private_player_key": pid,
            "latest_tournament": latest_tournament,
            "latest_team": latest_team,
            "tournaments_played": tournaments_played,
            "maps_played": maps_played,
            "kills": kills, "assists": assists, "deaths": deaths,
            "kd": kd, "kr": kr,
            "average_kills_per_map": avg_kills_per_map,
            "adr": adr, "adr_maps_available": adr_maps_available, "adr_coverage": adr_coverage,
            "mvps": mvps, "mvp_maps_available": mvp_maps_available, "mvp_coverage": mvp_coverage,
            "rating_start": rating_start, "rating_final": rating_final,
            "rating_change": rating_change, "rating_change_percent": rating_change_percent,
            "data_quality_flags": flags,
        })
    return pd.DataFrame(rows)


def build_mart_player_rating_history(
    rating_events: pd.DataFrame, dim_tournament: pd.DataFrame,
    player_map: pd.DataFrame, dim_map: pd.DataFrame,
) -> pd.DataFrame:
    df = rating_events.merge(
        dim_tournament[["tournament_number", "tournament_label"]], on="tournament_number", how="left"
    )

    # map_name для карточных рейтинговых событий (Nc/Nt по картам) — берётся
    # через существующую связь fact_player_map.map_key -> dim_map, по тому же
    # ключу (игрок, турнир, стадия, номер карты), которым фактически являются
    # rating_stage/map_number_canonical в fact_player_rating_event (см.
    # build_rating_events.py::build_map_level_events). Для событий без карты
    # (a/c_final/n/t_final) остаётся null.
    map_lookup = player_map[["private_player_key", "tournament_number", "tournament_phase", "map_number", "map_key"]].drop_duplicates()
    map_lookup = map_lookup.merge(dim_map[["map_key", "canonical_map_name"]], on="map_key", how="left")
    map_lookup = map_lookup.rename(columns={
        "tournament_phase": "rating_stage",
        "map_number": "map_number_canonical",
        "canonical_map_name": "map_name",
    }).drop(columns=["map_key"])

    df = df.merge(
        map_lookup, on=["private_player_key", "tournament_number", "rating_stage", "map_number_canonical"], how="left"
    )
    return df


def build_mart_player_map(player_map: pd.DataFrame, dim_tournament: pd.DataFrame) -> pd.DataFrame:
    return player_map.merge(
        dim_tournament[["tournament_number", "tournament_label"]], on="tournament_number", how="left"
    )


# ---------------------------------------------------------------------------
# Team marts
# ---------------------------------------------------------------------------
#
# Калибровочные команды (team_type='calibration_team' в dim_team, временные
# составы одного calibration-game) НЕ должны попадать ни в один командный
# mart ниже — см. docs/dashboard_bugfix_v1.md и
# artifacts/validation/dashboard_filter_debug.json (issue #5). Сигнал взят из
# dim_team.team_type (построен в build_dimensions.py на основе реального
# разделения по tournament_phase, не по названию).

def permanent_team_keys(dim_team: pd.DataFrame) -> set:
    return set(dim_team[dim_team["team_type"] == "permanent_tournament_team"]["team_key"])


def resolve_final_roster(player_map: pd.DataFrame) -> pd.DataFrame:
    """Финальный состав постоянной команды на турнире = команда, за которую
    игрок сыграл СВОЮ ПОСЛЕДНЮЮ карту основного турнира (max map_number).

    Нужно, т.к. на реальных данных состав части игроков менялся В ТЕЧЕНИЕ
    одного турнира (16 случаев 'player x tournament' с >1 командой в стадии
    't') — без этого один игрок был бы посчитан сразу в нескольких командных
    рейтингах за тот же турнир."""
    t_only = player_map[player_map["tournament_phase"] == "t"]
    idx = t_only.sort_values("map_number").groupby(["private_player_key", "tournament_number"]).tail(1)
    return idx[["private_player_key", "tournament_number", "team_key"]].reset_index(drop=True)


def team_roster_rating_sum(rating_events: pd.DataFrame, roster: pd.DataFrame, event_type: str) -> pd.DataFrame:
    """СУММА (не среднее) рейтинга указанного event_type по составу команды
    на турнире. Если хотя бы у одного игрока состава нет события этого типа —
    сумма НЕ считается (null), а не тихо занижается частичной суммой —
    см. секцию 4 задания ('не показывать вводящий в заблуждение ноль')."""
    events = rating_events[rating_events["event_type"] == event_type][
        ["private_player_key", "tournament_number", "rating_after"]
    ]
    merged = roster.merge(events, on=["private_player_key", "tournament_number"], how="left")
    grouped = merged.groupby(["team_key", "tournament_number"]).agg(
        rating_sum=("rating_after", "sum"),
        players_with_rating=("rating_after", lambda s: int(s.notna().sum())),
        roster_size=("private_player_key", "nunique"),
    ).reset_index()
    incomplete = grouped["players_with_rating"] < grouped["roster_size"]
    grouped.loc[incomplete, "rating_sum"] = None
    return grouped


def build_mart_team_summary(
    team_map: pd.DataFrame, roster: pd.DataFrame, rating_events: pd.DataFrame,
    tournament_score: pd.DataFrame, dim_team: pd.DataFrame,
) -> pd.DataFrame:
    permanent = permanent_team_keys(dim_team)
    team_map = team_map[team_map["team_key"].isin(permanent)]
    roster = roster[roster["team_key"].isin(permanent)]

    nc = team_roster_rating_sum(rating_events, roster, "calibration_final")
    nt = team_roster_rating_sum(rating_events, roster, "tournament_final")
    roster_counts = roster.groupby(["team_key", "tournament_number"])["private_player_key"].nunique()

    def _lookup(df, team_key, tournament_number):
        row = df[(df["team_key"] == team_key) & (df["tournament_number"] == tournament_number)]
        if not len(row) or pd.isna(row["rating_sum"].iloc[0]):
            return None
        return float(row["rating_sum"].iloc[0])

    def _team_tournament_row(team_key, tournament_number, grp):
        maps_played = len(grp)
        wins = int((grp["win_loss"] == "W").sum())
        losses = int((grp["win_loss"] == "L").sum())
        win_rate = _safe_div(wins, maps_played)
        valid = grp[grp["result_parse_status"] == "ok"]
        rounds_won = int(valid["team_score"].sum()) if len(valid) else None
        rounds_lost = int(valid["opponent_score"].sum()) if len(valid) else None
        round_diff = int(valid["round_diff"].sum()) if len(valid) else None

        rating_start = _lookup(nc, team_key, tournament_number)
        rating_final = _lookup(nt, team_key, tournament_number)
        rating_change = (rating_final - rating_start) if (rating_start is not None and rating_final is not None) else None
        rating_change_percent = _safe_div(rating_change, rating_start) if rating_change is not None else None

        players_count = int(roster_counts.get((team_key, tournament_number), 0))

        place_row = tournament_score[
            (tournament_score["canonical_team_name"] == team_key) & (tournament_score["tournament_number"] == tournament_number)
        ]
        place = int(place_row["place"].iloc[0]) if len(place_row) else None

        flags = _union_flags(grp["data_quality_flags"])
        if place is None:
            flags.append("tournament_place_not_found")
        if rating_start is None or rating_final is None:
            flags.append("incomplete_roster_rating_coverage")
        if players_count != EXPECTED_ROSTER_SIZE:
            flags.append("roster_size_not_five")

        return {
            "team_key": team_key,
            "team_name": team_key,
            "tournament_number": tournament_number,
            "latest_tournament_number": None,
            "is_calibration_team": False,
            "maps_played": maps_played, "wins": wins, "losses": losses, "win_rate": win_rate,
            "rounds_won": rounds_won, "rounds_lost": rounds_lost, "round_diff": round_diff,
            "players_count": players_count,
            "rating_start": rating_start, "rating_final": rating_final,
            "rating_change": rating_change, "rating_change_percent": rating_change_percent,
            "tournament_place": place,
            "data_quality_flags": sorted(set(flags)),
        }

    rows = []
    for (team_key, tournament_number), grp in team_map.groupby(["team_key", "tournament_number"]):
        rows.append(_team_tournament_row(team_key, tournament_number, grp))

    # "Все турниры" — последнее доступное значение постоянной команды, БЕЗ
    # суммирования/усреднения рейтинга между турнирами (см. секцию 3 задания).
    per_tournament = pd.DataFrame(rows)
    all_time_rows = []
    for team_key, grp in per_tournament.groupby("team_key"):
        latest = grp.loc[grp["tournament_number"].idxmax()].to_dict()
        latest = dict(latest)
        latest["latest_tournament_number"] = latest["tournament_number"]
        latest["tournament_number"] = None
        latest["tournament_place"] = None
        latest["data_quality_flags"] = list(latest["data_quality_flags"]) + ["all_time_row_is_latest_tournament_snapshot"]
        all_time_rows.append(latest)
    rows.extend(all_time_rows)

    return pd.DataFrame(rows)


def build_mart_team_rating_comparison(
    rating_events: pd.DataFrame, roster: pd.DataFrame, dim_team: pd.DataFrame
) -> pd.DataFrame:
    """Nc vs Nt для постоянных команд, грейн: команда × турнир. Формула — по
    прямому указанию продукта (не усреднение, как в остальных team-marts):
    team_nc/team_nt = СУММА индивидуальных Nc/Nt игроков финального состава
    турнира. Используется для карточки 'Nc vs Nt' на Overview и может
    использоваться отдельно от mart_team_summary."""
    permanent = permanent_team_keys(dim_team)
    roster = roster[roster["team_key"].isin(permanent)]

    nc = team_roster_rating_sum(rating_events, roster, "calibration_final").rename(
        columns={"rating_sum": "team_nc", "players_with_rating": "nc_players_count"}
    )
    nt = team_roster_rating_sum(rating_events, roster, "tournament_final").rename(
        columns={"rating_sum": "team_nt", "players_with_rating": "nt_players_count"}
    )
    roster_counts = roster.groupby(["team_key", "tournament_number"])["private_player_key"].nunique().rename(
        "roster_players_count"
    ).reset_index()

    merged = roster_counts.merge(
        nc[["team_key", "tournament_number", "team_nc", "nc_players_count"]],
        on=["team_key", "tournament_number"], how="left",
    )
    merged = merged.merge(
        nt[["team_key", "tournament_number", "team_nt", "nt_players_count"]],
        on=["team_key", "tournament_number"], how="left",
    )

    merged["rating_coverage_complete"] = (
        (merged["nc_players_count"] == merged["roster_players_count"])
        & (merged["nt_players_count"] == merged["roster_players_count"])
    )

    def _change(row):
        if pd.isna(row["team_nc"]) or pd.isna(row["team_nt"]):
            return None
        return row["team_nt"] - row["team_nc"]

    def _change_pct(row):
        change = _change(row)
        if change is None:
            return None
        return _safe_div(change, row["team_nc"])

    merged["team_rating_change"] = merged.apply(_change, axis=1)
    merged["team_rating_change_percent"] = merged.apply(_change_pct, axis=1)
    merged["team_name"] = merged["team_key"]
    merged["is_calibration_team"] = False
    def _comparison_flags(row):
        flags = [] if row["rating_coverage_complete"] else ["incomplete_roster_rating_coverage"]
        if row["roster_players_count"] != EXPECTED_ROSTER_SIZE:
            flags.append("roster_size_not_five")
        return flags

    merged["data_quality_flags"] = merged.apply(_comparison_flags, axis=1)
    return merged[[
        "team_key", "team_name", "tournament_number", "is_calibration_team",
        "team_nc", "team_nt", "team_rating_change", "team_rating_change_percent",
        "nc_players_count", "nt_players_count", "roster_players_count", "rating_coverage_complete",
        "data_quality_flags",
    ]]


def build_mart_team_rating_history(rating_events: pd.DataFrame, roster: pd.DataFrame, dim_team: pd.DataFrame) -> pd.DataFrame:
    permanent = permanent_team_keys(dim_team)
    roster = roster[roster["team_key"].isin(permanent)]

    stage_events = rating_events[rating_events["event_type"].isin(
        ["normalized_entry", "calibration_final", "blended_normalized", "tournament_final"]
    )]
    merged = stage_events.merge(roster, on=["private_player_key", "tournament_number"], how="inner")

    agg = merged.groupby(["team_key", "tournament_number", "event_type", "rating_stage", "rating_stage_order"]).agg(
        rating_before_avg=("rating_before", "mean"),
        rating_change_avg=("rating_change", "mean"),
        rating_after_avg=("rating_after", "mean"),
        roster_size=("private_player_key", "nunique"),
    ).reset_index()
    agg["is_calibration_team"] = False
    agg["data_quality_flags"] = [["team_rating_is_roster_average_not_official_formula"] for _ in range(len(agg))]
    return agg


def build_mart_team_map(team_map: pd.DataFrame, dim_tournament: pd.DataFrame, dim_team: pd.DataFrame) -> pd.DataFrame:
    permanent = permanent_team_keys(dim_team)
    team_map = team_map[team_map["team_key"].isin(permanent)]
    return team_map.merge(
        dim_tournament[["tournament_number", "tournament_label"]], on="tournament_number", how="left"
    )


def build_mart_team_map_matrix(team_map: pd.DataFrame, dim_team: pd.DataFrame) -> pd.DataFrame:
    permanent = permanent_team_keys(dim_team)
    team_map = team_map[team_map["team_key"].isin(permanent)]

    rows = []
    for (team_key, map_key), grp in team_map.groupby(["team_key", "map_key"]):
        maps_played = len(grp)
        wins = int((grp["win_loss"] == "W").sum())
        losses = int((grp["win_loss"] == "L").sum())
        win_rate = _safe_div(wins, maps_played)
        valid = grp[grp["result_parse_status"] == "ok"]
        round_diff = int(valid["round_diff"].sum()) if len(valid) else None
        elo_change = grp["elo_change_total"].sum(skipna=True) if grp["elo_change_total"].notna().any() else None

        rows.append({
            "team_key": team_key,
            "map_key": map_key,
            "maps_played": maps_played,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "round_diff": round_diff,
            "elo_change": elo_change,
            "sample_size": maps_played,
            "low_sample_flag": maps_played < LOW_SAMPLE_THRESHOLD,
        })
    return pd.DataFrame(rows)


def main() -> int:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    player_map = read_table(PROCESSED_DIR / "fact_player_map.parquet")
    team_map = read_table(PROCESSED_DIR / "fact_team_map.parquet")
    rating_events = read_table(PROCESSED_DIR / "fact_player_rating_event.parquet")
    dim_tournament = read_table(PROCESSED_DIR / "dim_tournament.parquet")
    dim_team = read_table(PROCESSED_DIR / "dim_team.parquet")
    dim_map = read_table(PROCESSED_DIR / "dim_map.parquet")
    tournament_score = read_table(Path(PROCESSED_DIR).parent / "interim" / "tournament_score_normalized.parquet")

    roster = resolve_final_roster(player_map)

    print("[build_marts] mart_player_summary ...")
    write_table(build_mart_player_summary(player_map, rating_events), PROCESSED_DIR / "mart_player_summary.parquet")

    print("[build_marts] mart_player_rating_history ...")
    write_table(
        build_mart_player_rating_history(rating_events, dim_tournament, player_map, dim_map),
        PROCESSED_DIR / "mart_player_rating_history.parquet",
    )

    print("[build_marts] mart_player_map ...")
    write_table(build_mart_player_map(player_map, dim_tournament), PROCESSED_DIR / "mart_player_map.parquet")

    print("[build_marts] mart_team_summary ...")
    write_table(
        build_mart_team_summary(team_map, roster, rating_events, tournament_score, dim_team),
        PROCESSED_DIR / "mart_team_summary.parquet",
    )

    print("[build_marts] mart_team_rating_comparison ...")
    write_table(
        build_mart_team_rating_comparison(rating_events, roster, dim_team),
        PROCESSED_DIR / "mart_team_rating_comparison.parquet",
    )

    print("[build_marts] mart_team_rating_history ...")
    write_table(build_mart_team_rating_history(rating_events, roster, dim_team), PROCESSED_DIR / "mart_team_rating_history.parquet")

    print("[build_marts] mart_team_map ...")
    write_table(build_mart_team_map(team_map, dim_tournament, dim_team), PROCESSED_DIR / "mart_team_map.parquet")

    print("[build_marts] mart_team_map_matrix ...")
    write_table(build_mart_team_map_matrix(team_map, dim_team), PROCESSED_DIR / "mart_team_map_matrix.parquet")

    print("[build_marts] Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
