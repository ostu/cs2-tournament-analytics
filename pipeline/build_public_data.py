"""
build_public_data.py
======================

Строит публичный слой `data/public/*.json` — единственный источник данных
для `dashboard.html`. Собирается ТОЛЬКО из marts (`data/processed/mart_*`) +
`data/private/player_aliases.csv` (используется только чтобы ПОДМЕНИТЬ
`private_player_key` на `public_player_id`/`public_nickname` — сам мостик
никогда не копируется в вывод).

После генерации автоматически прогоняет `pipeline/check_publication_leaks.py`
на `data/public/` — сборка считается неуспешной, если найдена утечка.

Запуск (из корня репозитория):

    python pipeline/build_public_data.py
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PROCESSED_DIR, PUBLIC_DIR, ROOT_DIR  # noqa: E402
from io_utils import load_private_alias_bridge, read_table, write_json  # noqa: E402
from check_publication_leaks import collect_forbidden_tokens  # noqa: E402

# Поля, которые НИКОГДА не должны попасть в публичный вывод, даже случайно.
FORBIDDEN_PUBLIC_COLUMNS = {
    "private_player_key", "id", "player_id", "steam_name", "steam_account",
    "fio", "city", "department", "discord_name", "faceit_name", "faceit_link",
    "another_names", "source_file", "source_row",
}

MIN_TEAM_TOKEN_LEN = 3


def build_team_redaction_map() -> dict[str, str]:
    """Некоторые исторические названия команд буквально содержат реальный
    private-идентификатор игрока как подстроку (например, команда
    'AutostorySawyer' содержит id/steam_name игрока 'sawyer'/'Sawyer') —
    публикация такого имени команды деанонимизирует конкретного игрока через
    состав команды, даже хотя названия команд в целом разрешены к публикации
    (см. docs/privacy_and_publication.md). Такие имена заменяются на
    стабильный редактированный плейсхолдер; сопоставление детерминировано
    (по отсортированному списку команд), не хранит обратимой связи с
    исходным именем."""
    dim_team = read_table(PROCESSED_DIR / "dim_team.parquet")
    teams = sorted(dim_team["canonical_team_name"].unique().tolist())
    tokens = [t for t in collect_forbidden_tokens() if len(t) >= MIN_TEAM_TOKEN_LEN]

    redaction_map = {}
    redacted_count = 0
    for team in teams:
        team_lower = team.lower()
        hit = next((t for t in tokens if t.lower() in team_lower), None)
        if hit is not None:
            redacted_count += 1
            redaction_map[team] = f"Команда (имя скрыто #{redacted_count})"
        else:
            redaction_map[team] = team

    if redacted_count:
        print(f"[build_public_data] ПРЕДУПРЕЖДЕНИЕ: {redacted_count} название(й) команды "
              f"содержит приватный идентификатор игрока как подстроку — заменено на "
              f"плейсхолдер в публичном выводе (см. docs/privacy_and_publication.md).")
    return redaction_map


def _apply_team_redaction(df: pd.DataFrame, cols: list[str], redaction_map: dict[str, str]) -> pd.DataFrame:
    df = df.copy()
    for col in cols:
        if col in df.columns:
            df[col] = df[col].map(lambda v: redaction_map.get(v, v) if pd.notna(v) else v)
    return df


def _drop_forbidden(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in df.columns if c not in FORBIDDEN_PUBLIC_COLUMNS]
    return df[cols]


def _attach_public_identity(df: pd.DataFrame, bridge: pd.DataFrame) -> pd.DataFrame:
    merged = df.merge(bridge[["private_player_key", "public_player_id", "public_nickname"]],
                       on="private_player_key", how="left")
    return _drop_forbidden(merged)


def _clean_nan(value):
    """json.dump сериализует float('nan') как невалидный для браузера литерал
    NaN (не перехватывается default=), поэтому чистим рекурсивно здесь, а не
    надеемся на DataFrame.where (ненадёжно для Arrow-backed/list-колонок)."""
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (list, tuple)):
        return [_clean_nan(v) for v in value]
    return value


def _records(df: pd.DataFrame) -> list[dict]:
    records = df.to_dict(orient="records")
    return [{k: _clean_nan(v) for k, v in rec.items()} for rec in records]


def build_players_json(bridge: pd.DataFrame, team_redaction: dict[str, str]) -> list[dict]:
    df = read_table(PROCESSED_DIR / "mart_player_summary.parquet")
    df = _apply_team_redaction(df, ["latest_team"], team_redaction)
    df = _attach_public_identity(df, bridge)
    return _records(df)


def build_player_rating_history_json(bridge: pd.DataFrame) -> list[dict]:
    df = read_table(PROCESSED_DIR / "mart_player_rating_history.parquet")
    df = _attach_public_identity(df, bridge)
    return _records(df)


def build_player_maps_json(bridge: pd.DataFrame, team_redaction: dict[str, str]) -> list[dict]:
    df = read_table(PROCESSED_DIR / "mart_player_map.parquet")
    df = _apply_team_redaction(df, ["team_key", "opponent_team_key"], team_redaction)
    df = df.rename(columns={"team_key": "team_id", "opponent_team_key": "opponent_team_id"})
    df = _attach_public_identity(df, bridge)
    return _records(df)


def build_teams_json(team_redaction: dict[str, str]) -> list[dict]:
    df = read_table(PROCESSED_DIR / "mart_team_summary.parquet")
    df = _apply_team_redaction(df, ["team_key", "team_name"], team_redaction)
    df = _drop_forbidden(df).rename(columns={"team_key": "team_id"})
    return _records(df)


def build_team_rating_history_json(team_redaction: dict[str, str]) -> list[dict]:
    df = read_table(PROCESSED_DIR / "mart_team_rating_history.parquet")
    df = _apply_team_redaction(df, ["team_key"], team_redaction)
    df = _drop_forbidden(df).rename(columns={"team_key": "team_id"})
    return _records(df)


def build_team_maps_json(team_redaction: dict[str, str]) -> list[dict]:
    df = read_table(PROCESSED_DIR / "mart_team_map.parquet")
    df = _apply_team_redaction(df, ["team_key", "opponent_team_key"], team_redaction)
    df = _drop_forbidden(df).rename(columns={"team_key": "team_id", "opponent_team_key": "opponent_team_id"})
    return _records(df)


def build_team_map_matrix_json(team_redaction: dict[str, str]) -> list[dict]:
    df = read_table(PROCESSED_DIR / "mart_team_map_matrix.parquet")
    df = _apply_team_redaction(df, ["team_key"], team_redaction)
    df = _drop_forbidden(df).rename(columns={"team_key": "team_id"})
    return _records(df)


def build_team_rating_comparison_json(team_redaction: dict[str, str]) -> list[dict]:
    df = read_table(PROCESSED_DIR / "mart_team_rating_comparison.parquet")
    df = _apply_team_redaction(df, ["team_key", "team_name"], team_redaction)
    df = _drop_forbidden(df).rename(columns={"team_key": "team_id"})
    return _records(df)


def build_metadata_json(bridge: pd.DataFrame, team_redaction: dict[str, str]) -> dict:
    dim_tournament = read_table(PROCESSED_DIR / "dim_tournament.parquet")
    dim_team = read_table(PROCESSED_DIR / "dim_team.parquet")
    dim_map = read_table(PROCESSED_DIR / "dim_map.parquet")
    player_summary = read_table(PROCESSED_DIR / "mart_player_summary.parquet")
    team_map = read_table(PROCESSED_DIR / "mart_team_map.parquet")

    # Калибровочные команды (issue #5, dashboard_filter_debug.json) не должны
    # попадать ни в фильтр, ни в счётчик команд публичного отчёта.
    permanent_teams_df = dim_team[dim_team["team_type"] == "permanent_tournament_team"]

    last_updated = dim_tournament["tournament_end_date"].max()
    tournaments = dim_tournament[["tournament_number", "tournament_label"]].sort_values("tournament_number")
    teams = sorted({team_redaction.get(t, t) for t in permanent_teams_df["canonical_team_name"].unique().tolist()})
    maps_ = dim_map[["canonical_map_name", "map_order"]].sort_values("map_order")

    public_players_count = player_summary["private_player_key"].nunique()

    # teams_by_tournament: какие постоянные команды реально играли в каждом
    # турнире — источник для каскадного фильтра "турнир → команда" на фронте
    # (без этого JS не может сузить список команд без похода в team_maps.json).
    teams_by_tournament = {}
    for tnum, grp in team_map.groupby("tournament_number"):
        key = str(int(tnum))
        names = sorted({team_redaction.get(t, t) for t in grp["team_key"].unique().tolist()})
        teams_by_tournament[key] = names

    return {
        "report_title": "CS2 Tournament Report",
        "data_as_of": last_updated.isoformat() if pd.notna(last_updated) else None,
        "tournament_count": int(dim_tournament.shape[0]),
        "public_player_count": int(public_players_count),
        "team_count": int(len(teams)),
        "map_count": int(dim_map.shape[0]),
        "tournaments": tournaments.to_dict(orient="records"),
        "teams": teams,
        "teams_by_tournament": teams_by_tournament,
        "maps": maps_["canonical_map_name"].tolist(),
        "players": bridge[["public_player_id", "public_nickname"]].sort_values("public_nickname").to_dict(orient="records"),
    }


def run_leak_check() -> int:
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT_DIR / "pipeline" / "check_publication_leaks.py"), str(PUBLIC_DIR)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def main() -> int:
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    bridge = load_private_alias_bridge()
    team_redaction = build_team_redaction_map()

    print("[build_public_data] players.json ...")
    write_json(build_players_json(bridge, team_redaction), PUBLIC_DIR / "players.json")

    print("[build_public_data] player_rating_history.json ...")
    write_json(build_player_rating_history_json(bridge), PUBLIC_DIR / "player_rating_history.json")

    print("[build_public_data] player_maps.json ...")
    write_json(build_player_maps_json(bridge, team_redaction), PUBLIC_DIR / "player_maps.json")

    print("[build_public_data] teams.json ...")
    write_json(build_teams_json(team_redaction), PUBLIC_DIR / "teams.json")

    print("[build_public_data] team_rating_history.json ...")
    write_json(build_team_rating_history_json(team_redaction), PUBLIC_DIR / "team_rating_history.json")

    print("[build_public_data] team_maps.json ...")
    write_json(build_team_maps_json(team_redaction), PUBLIC_DIR / "team_maps.json")

    print("[build_public_data] team_map_matrix.json ...")
    write_json(build_team_map_matrix_json(team_redaction), PUBLIC_DIR / "team_map_matrix.json")

    print("[build_public_data] team_rating_comparison.json ...")
    write_json(build_team_rating_comparison_json(team_redaction), PUBLIC_DIR / "team_rating_comparison.json")

    print("[build_public_data] metadata.json ...")
    write_json(build_metadata_json(bridge, team_redaction), PUBLIC_DIR / "metadata.json")

    print("[build_public_data] запускаю check_publication_leaks.py на data/public/ ...")
    leak_code = run_leak_check()
    if leak_code != 0:
        print("[build_public_data] ОШИБКА: leak checker нашёл совпадения — сборка публичных данных ОТМЕНЕНА как неуспешная.",
              file=sys.stderr)
        return leak_code

    print("[build_public_data] Готово, утечек не найдено.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
