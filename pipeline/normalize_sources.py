"""
normalize_sources.py
=====================

Первый шаг канонического pipeline. Читает `data/raw/` (только чтение,
ничего там не меняется), применяет ПОДТВЕРЖДЁННЫЕ Audit v1/v2 технические
коррекции (mapId offset, разбор кода турнира, team alias, result-парсинг,
adr/mvp coverage), и сохраняет результат в `data/interim/*.parquet`.

Ничего не придумывает: там, где формула/значение не подтверждено,
проставляется data_quality_flag, raw-значение сохраняется как есть.

Запуск (из корня репозитория):

    python pipeline/normalize_sources.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    INTERIM_DIR,
    MAPID_OFFSET_CORRECTION,
    MIN_EXPECTED_MAPID_JOIN_COVERAGE,
    RATING_STAGE_ORDER,
)
from io_utils import (  # noqa: E402
    load_team_alias_map,
    normalize_team_text,
    parse_rating_code,
    parse_result,
    read_raw_table,
    write_table,
)


def add_rating_code_fields(df: pd.DataFrame, code_col: str) -> pd.DataFrame:
    parsed = df[code_col].apply(parse_rating_code).apply(pd.Series)
    df = pd.concat([df, parsed], axis=1)
    df["rating_stage_order"] = df["rating_stage"].map(RATING_STAGE_ORDER)
    df["rating_code"] = df[code_col].astype(str)
    df["dashboard_filter_code"] = df["tournament_number"].apply(
        lambda n: f"t{int(n)}" if pd.notna(n) else None
    )
    return df


def add_team_alias_fields(df: pd.DataFrame, raw_col: str) -> pd.DataFrame:
    alias_map, pending = load_team_alias_map()
    df["team_raw"] = df[raw_col]
    df["team_normalized_text"] = df[raw_col].apply(normalize_team_text)
    df["canonical_team_name"] = df[raw_col].map(alias_map).fillna(df[raw_col])
    df["team_alias_status"] = df[raw_col].apply(
        lambda v: "pending_review" if v in pending else "resolved"
    )

    def _flags(row):
        flags = []
        if row["team_alias_status"] == "pending_review":
            flags.append("team_alias_pending_review")
        return flags

    df["data_quality_flags"] = df.apply(_flags, axis=1)
    return df


def normalize_elo_changes() -> pd.DataFrame:
    raw = read_raw_table("elo_changes")
    raw = add_rating_code_fields(raw, "tournament")

    # ПОДТВЕРЖДЁННАЯ Audit v2 коррекция: mapId в этой таблице 0-based
    # (range() в notebook), в tournament_results — 1-based. Явная,
    # именованная техническая коррекция, НЕ смысловой переход к другой карте.
    raw["map_id_raw"] = raw["mapId"]
    raw["map_number_canonical"] = raw["mapId"] + MAPID_OFFSET_CORRECTION
    raw["data_quality_flags"] = [[] for _ in range(len(raw))]

    return raw[[
        "id", "tournament_number", "rating_stage", "rating_stage_order", "rating_code",
        "dashboard_filter_code", "map_id_raw", "map_number_canonical", "elo_diff",
        "source_file", "source_row", "data_quality_flags",
    ]]


def normalize_elo_norm() -> pd.DataFrame:
    raw = read_raw_table("elo_norm")
    raw = add_rating_code_fields(raw, "tournament")
    raw["data_quality_flags"] = [[] for _ in range(len(raw))]
    return raw[[
        "id", "tournament_number", "rating_stage", "rating_stage_order", "rating_code",
        "dashboard_filter_code", "elo_diff", "source_file", "source_row", "data_quality_flags",
    ]]


def normalize_tournament_results() -> pd.DataFrame:
    raw = read_raw_table("tournament_results")
    raw = add_rating_code_fields(raw, "tournament")
    raw = add_team_alias_fields(raw, "team")

    raw["map_number_canonical"] = raw["mapId"]  # уже 1-based, коррекция не нужна

    parsed_results = raw.apply(lambda r: parse_result(r["result"], r["w/l"]), axis=1).apply(pd.Series)
    raw = pd.concat([raw, parsed_results], axis=1)

    # adr_tracked: на грейне (tournament_number, rating_stage, map_number) —
    # подтверждено Audit v2: гранулярность отсутствия трекинга — целая карта,
    # не отдельные строки (0 карт с частичным покрытием внутри одной карты).
    map_key_cols = ["tournament_number", "rating_stage", "map_number_canonical"]
    adr_tracked_by_map = raw.groupby(map_key_cols)["adr"].transform(lambda s: bool((s != 0).any()))
    raw["adr_tracked"] = adr_tracked_by_map
    raw["adr"] = raw["adr"].where(raw["adr_tracked"], other=pd.NA)

    # mvp_tracked: подтверждено, что отсутствие трекинга — на уровне целого
    # турнира (см. docs/unresolved_questions.md, artifacts/audit_v2/adr_mvp_coverage.json).
    # 0 внутри протрекованного турнира — реальный результат, НЕ null.
    mvp_tracked_by_tournament = raw.groupby(["tournament_number", "rating_stage"])["mvps"].transform(
        lambda s: bool((s != 0).any())
    )
    raw["mvp_tracked"] = mvp_tracked_by_tournament

    def _flags(row):
        flags = list(row["data_quality_flags"])
        if row["result_parse_status"] != "ok":
            flags.append(f"result_{row['result_parse_status']}")
        if not row["adr_tracked"]:
            flags.append("adr_not_tracked")
        if not row["mvp_tracked"]:
            flags.append("mvp_not_tracked")
        # Найдена как минимум 1 строка в исходнике с kills<0 (см.
        # docs/unresolved_questions.md) — сохраняем как есть, не исправляем,
        # только помечаем.
        if row["kills"] < 0 or row["assists"] < 0 or row["deaths"] < 0:
            flags.append("negative_stat_value_in_raw_source")
        return flags

    raw["data_quality_flags"] = raw.apply(_flags, axis=1)

    cols = [
        "id", "tournament_number", "rating_stage", "rating_stage_order", "rating_code",
        "dashboard_filter_code", "matchId", "map_number_canonical", "map",
        "team_raw", "canonical_team_name", "team_alias_status",
        "date", "rounds", "w/l",
        "kills", "assists", "deaths", "adr", "adr_tracked", "mvps", "mvp_tracked",
        "result_raw", "team_score", "opponent_score", "round_diff", "result_parse_status",
        "source_file", "source_row", "data_quality_flags",
    ]
    return raw[cols].rename(columns={"matchId": "match_id", "w/l": "win_loss"})


def normalize_tournament_score() -> pd.DataFrame:
    raw = read_raw_table("tournament_score")

    # Формат в этой таблице — легаси 'tN' (не 'Nt'), см. docs/rating_semantics.md §1.
    raw = add_rating_code_fields(raw, "tournament")
    raw = add_team_alias_fields(raw, "team")

    return raw[[
        "tournament_number", "rating_code", "dashboard_filter_code",
        "team_raw", "canonical_team_name", "team_alias_status", "place",
        "source_file", "source_row", "data_quality_flags",
    ]]


def normalize_players() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Возвращает (players_wide, player_tournament_elo_long).

    players_wide содержит PII — предназначен только для внутреннего
    pipeline-использования (data/interim не публикуется). Разделение на
    private/public dimension происходит в build_dimensions.py.
    """
    raw = read_raw_table("players")

    records = []
    for _, row in raw.iterrows():
        last_elo = None  # тот же carry-принцип, что в build_elo_diff_table (notebook)
        for n in range(1, 8):
            start_col, calib_col, elo_col, team_col = (
                f"t{n}_elo_start", f"t{n}_elo_calib", f"t{n}_elo", f"t{n}_team",
            )
            if pd.isna(row.get(start_col)):
                continue
            is_new_player = last_elo is None
            flags = []
            elo_start = row.get(start_col)
            # Ожидается ровно 1000 ТОЛЬКО для новых игроков (см. docs/rating_semantics.md §2).
            # У вернувшихся игроков elo_start легитимно отличается от 1000 (нормализованное
            # значение) — это НЕ аномалия, поэтому проверяем именно is_new_player, а не n>1.
            if is_new_player and pd.notna(elo_start) and elo_start != 1000:
                flags.append("unexpected_start_elo")
            records.append({
                "id": row["id"],
                "tournament_number": n,
                "team_raw": row.get(team_col),
                "elo_start": elo_start,
                "elo_calib": row.get(calib_col),
                "elo_final": row.get(elo_col),
                "is_new_player": is_new_player,
                "source_file": "cs_farpost_players.xlsx",
                "source_row": row["source_row"],
                "data_quality_flags": flags,
            })
            current_elo = row.get(elo_col)
            if pd.notna(current_elo):
                last_elo = current_elo
    player_tournament_elo = pd.DataFrame(records)

    return raw, player_tournament_elo


def main() -> int:
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)

    print("[normalize_sources] elo_changes ...")
    elo_changes = normalize_elo_changes()
    write_table(elo_changes, INTERIM_DIR / "elo_changes_normalized.parquet")

    print("[normalize_sources] elo_norm ...")
    elo_norm = normalize_elo_norm()
    write_table(elo_norm, INTERIM_DIR / "elo_norm_normalized.parquet")

    print("[normalize_sources] tournament_results ...")
    tournament_results = normalize_tournament_results()
    write_table(tournament_results, INTERIM_DIR / "tournament_results_normalized.parquet")

    print("[normalize_sources] tournament_score ...")
    tournament_score = normalize_tournament_score()
    write_table(tournament_score, INTERIM_DIR / "tournament_score_normalized.parquet")

    print("[normalize_sources] players ...")
    players_wide, player_tournament_elo = normalize_players()
    write_table(players_wide, INTERIM_DIR / "players_wide_normalized.parquet")
    write_table(player_tournament_elo, INTERIM_DIR / "player_tournament_elo.parquet")

    # --- быстрая самопроверка join-coverage mapId (полная — в validate_pipeline.py) ---
    merged = tournament_results.merge(
        elo_changes[["id", "tournament_number", "rating_stage", "map_number_canonical", "elo_diff"]],
        on=["id", "tournament_number", "rating_stage", "map_number_canonical"],
        how="left",
    )
    coverage = merged["elo_diff"].notna().mean()
    print(f"[normalize_sources] mapId-join coverage после коррекции: {coverage:.4%} "
          f"(подтверждённый порог: {MIN_EXPECTED_MAPID_JOIN_COVERAGE:.2%})")
    if coverage < MIN_EXPECTED_MAPID_JOIN_COVERAGE:
        print("[normalize_sources] ПРЕДУПРЕЖДЕНИЕ: coverage ниже подтверждённого Audit v2 уровня!")

    print("[normalize_sources] Готово. Файлы в data/interim/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
