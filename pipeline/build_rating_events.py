"""
build_rating_events.py
========================

Строит `data/processed/fact_player_rating_event.parquet` — грейн "одно
рейтинговое событие одного игрока". Формулы взяты буквально из notebook /
authoritative-описания пользователя (см. docs/rating_semantics.md) — ничего
не придумывается: там, где база значения не подтверждена (стадия `Nn`,
источник `elo_norm`), событие строится по ПОДТВЕРЖДЁННОЙ формуле
(`calculate_elo_final`), а не по необъяснённому сырому `elo_diff`, и это
явно помечается data_quality_flag.

Запуск (из корня репозитория):

    python pipeline/build_rating_events.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import FORMULA_VERSIONS, INTERIM_DIR, PROCESSED_DIR  # noqa: E402
from io_utils import read_table, write_table  # noqa: E402

EPS = 1.0  # допуск на округление при сверке "как есть" значений с реконструкцией


def _base_frames():
    player_elo = read_table(INTERIM_DIR / "player_tournament_elo.parquet")
    elo_changes = read_table(INTERIM_DIR / "elo_changes_normalized.parquet")
    elo_norm = read_table(INTERIM_DIR / "elo_norm_normalized.parquet")
    return player_elo, elo_changes, elo_norm


def build_normalized_entry_events(player_elo: pd.DataFrame) -> pd.DataFrame:
    """Na — см. docs/rating_semantics.md §2. rating_change/rating_before/
    rating_after — буквально формула build_elo_diff_table (notebook)."""
    df = player_elo.copy()

    # last_elo (предыдущий финал, если возвращается) уже неявно закодирован в
    # is_new_player; вычислим сам rating_before заново той же carry-логикой.
    df = df.sort_values(["id", "tournament_number"]).reset_index(drop=True)
    last_elo_map: dict[str, float | None] = {}
    rating_before_list = []
    for _, r in df.iterrows():
        pid = r["id"]
        rating_before_list.append(last_elo_map.get(pid))
        if pd.notna(r["elo_final"]):
            last_elo_map[pid] = r["elo_final"]
    df["rating_before"] = rating_before_list

    df["rating_change"] = pd.NA
    df.loc[df["is_new_player"], "rating_change"] = df.loc[df["is_new_player"], "elo_start"]
    df.loc[~df["is_new_player"], "rating_change"] = (
        df.loc[~df["is_new_player"], "elo_start"] - df.loc[~df["is_new_player"], "rating_before"]
    )
    df["rating_after"] = df["elo_start"]

    # normalization_factor (см. docs/rating_semantics.md §2) — агрегатная
    # величина на уровне турнира, применяется ко всем ВЕРНУВШИМСЯ игрокам.
    factors = {}
    for n, grp in df[~df["is_new_player"]].groupby("tournament_number"):
        actual_total = grp["rating_before"].sum()
        target_total = len(grp) * 1000
        factors[n] = (target_total / actual_total) if actual_total else None
    df["normalization_factor"] = df.apply(
        lambda r: None if r["is_new_player"] else factors.get(r["tournament_number"]), axis=1
    )

    out = pd.DataFrame({
        "private_player_key": df["id"],
        "tournament_number": df["tournament_number"],
        "rating_stage": "a",
        "rating_stage_order": 10,
        "rating_code": df["tournament_number"].astype(int).astype(str) + "a",
        "event_type": "normalized_entry",
        "event_sequence": 0,
        "match_id": None,
        "map_id_raw": None,
        "map_number_canonical": None,
        "rating_before": df["rating_before"],
        "rating_change": df["rating_change"],
        "rating_after": df["rating_after"],
        "normalization_factor": df["normalization_factor"],
        "source_type": "notebook_formula",
        "source_file": df["source_file"],
        "source_row": df["source_row"],
        "formula_version": FORMULA_VERSIONS["na_normalized_entry"],
        "data_quality_flags": df["data_quality_flags"],
    })
    return out


def build_previous_tournament_final_events(player_elo: pd.DataFrame) -> pd.DataFrame:
    """Опорное событие: финальный рейтинг предыдущего турнира, на который
    ссылается normalized_entry ТЕКУЩЕГО турнира. Не новая величина — то же
    число, что rating_after предыдущего tournament_final, повторно
    выставленное в контексте следующего цикла для удобства построения
    таймлайна (см. docs/data_model.md)."""
    df = player_elo.sort_values(["id", "tournament_number"]).reset_index(drop=True)
    rows = []
    last_elo_map: dict[str, tuple[float, int]] = {}
    for _, r in df.iterrows():
        pid = r["id"]
        if pid in last_elo_map and not r["is_new_player"]:
            prev_value, prev_tnum = last_elo_map[pid]
            rows.append({
                "private_player_key": pid,
                "tournament_number": r["tournament_number"],
                "rating_stage": None,
                "rating_stage_order": 0,
                "rating_code": None,
                "event_type": "previous_tournament_final",
                "event_sequence": -1,
                "match_id": None,
                "map_id_raw": None,
                "map_number_canonical": None,
                "rating_before": None,
                "rating_change": None,
                "rating_after": prev_value,
                "normalization_factor": None,
                "source_type": "derived_reference",
                "source_file": r["source_file"],
                "source_row": r["source_row"],
                "formula_version": "previous_tournament_final_v1_reference",
                "data_quality_flags": [f"references_tournament_{prev_tnum}_final"],
            })
        if pd.notna(r["elo_final"]):
            last_elo_map[pid] = (r["elo_final"], r["tournament_number"])
    return pd.DataFrame(rows)


def build_map_level_events(
    elo_changes: pd.DataFrame,
    player_elo: pd.DataFrame,
    stage: str,
    base_col: str,
    event_type: str,
) -> pd.DataFrame:
    """Nc/Nt по картам — идентичная механика: cumsum elo_diff от базы
    (elo_start для калибровки, elo_calib для основного турнира)."""
    stage_rows = elo_changes[elo_changes["rating_stage"] == stage].copy()
    if stage_rows.empty:
        return pd.DataFrame()

    stage_rows = stage_rows.sort_values(["id", "tournament_number", "map_number_canonical"])
    base = player_elo[["id", "tournament_number", base_col]].rename(columns={base_col: "base_rating"})
    stage_rows = stage_rows.merge(base, on=["id", "tournament_number"], how="left")

    stage_rows["cum_diff"] = stage_rows.groupby(["id", "tournament_number"])["elo_diff"].cumsum()
    stage_rows["rating_after"] = stage_rows["base_rating"] + stage_rows["cum_diff"]
    stage_rows["rating_before"] = stage_rows["rating_after"] - stage_rows["elo_diff"]

    out = pd.DataFrame({
        "private_player_key": stage_rows["id"],
        "tournament_number": stage_rows["tournament_number"],
        "rating_stage": stage,
        "rating_stage_order": stage_rows["rating_stage_order"],
        "rating_code": stage_rows["rating_code"],
        "event_type": event_type,
        "event_sequence": stage_rows["map_number_canonical"],
        "match_id": None,
        "map_id_raw": stage_rows["map_id_raw"],
        "map_number_canonical": stage_rows["map_number_canonical"],
        "rating_before": stage_rows["rating_before"],
        "rating_change": stage_rows["elo_diff"],
        "rating_after": stage_rows["rating_after"],
        "normalization_factor": None,
        "source_type": "notebook_formula",
        "source_file": stage_rows["source_file"],
        "source_row": stage_rows["source_row"],
        "formula_version": FORMULA_VERSIONS["nc_calibration" if stage == "c" else "nt_tournament_final"],
        "data_quality_flags": [[] for _ in range(len(stage_rows))],
    })
    return out


def build_stage_final_events(
    player_elo: pd.DataFrame,
    map_events: pd.DataFrame,
    before_col: str,
    after_col: str,
    stage: str,
    event_type: str,
) -> pd.DataFrame:
    df = player_elo.dropna(subset=[after_col]).copy()

    reconstructed = pd.Series(dtype=float)
    if not map_events.empty:
        last_idx = map_events.groupby(["private_player_key", "tournament_number"])["event_sequence"].idxmax()
        reconstructed = map_events.loc[last_idx].set_index(["private_player_key", "tournament_number"])["rating_after"]

    rows = []
    for _, r in df.iterrows():
        key = (r["id"], r["tournament_number"])
        flags = list(r["data_quality_flags"]) if isinstance(r["data_quality_flags"], (list, tuple)) else []
        recon_val = reconstructed.get(key)
        if recon_val is None:
            flags.append(f"no_{stage}_map_events_this_tournament")
        elif abs(recon_val - r[after_col]) > EPS:
            flags.append(f"{stage}_final_reconstruction_mismatch")

        rows.append({
            "private_player_key": r["id"],
            "tournament_number": r["tournament_number"],
            "rating_stage": stage,
            "rating_stage_order": {"c": 20, "t": 40}[stage],
            "rating_code": f"{int(r['tournament_number'])}{stage}",
            "event_type": event_type,
            "event_sequence": 9000,
            "match_id": None,
            "map_id_raw": None,
            "map_number_canonical": None,
            "rating_before": r[before_col],
            "rating_change": r[after_col] - r[before_col] if pd.notna(r[before_col]) else None,
            "rating_after": r[after_col],
            "normalization_factor": None,
            "source_type": "raw_source_column",
            "source_file": r["source_file"],
            "source_row": r["source_row"],
            "formula_version": FORMULA_VERSIONS["nc_calibration" if stage == "c" else "nt_tournament_final"],
            "data_quality_flags": flags,
        })
    return pd.DataFrame(rows)


def calculate_elo_final(row, t5_col: str, t6_col: str, base_col: str):
    """Буквально calculate_elo_final из cs_farpost_tournament_calib.ipynb —
    см. docs/rating_semantics.md §4. Веса 0.3/0.5/0.2, 0.7/0.3, 0.6/0.4."""
    t5, t6, base = row.get(t5_col), row.get(t6_col), row[base_col]
    t5_valid = pd.notna(t5) and t5 != 0
    t6_valid = pd.notna(t6) and t6 != 0
    if t5_valid and t6_valid:
        return 0.3 * t5 + 0.5 * t6 + 0.2 * base
    if t6_valid:
        return 0.7 * t6 + 0.3 * base
    if t5_valid:
        return 0.6 * t5 + 0.4 * base
    return base


def build_blended_normalized_events(player_elo: pd.DataFrame, elo_norm: pd.DataFrame) -> pd.DataFrame:
    """Nn — см. docs/rating_semantics.md §4. Формула блендинга подтверждена
    точно (notebook + authoritative-описание пользователя). База для
    СЫРОГО elo_diff в исходном elo_norm.xlsx (стадия 'n') НЕ подтверждена
    (docs/unresolved_questions.md §1.1) — поэтому rating_after считается по
    подтверждённой формуле calculate_elo_final, а не по этому сырому столбцу;
    расхождение с сырым значением помечается флагом, не скрывается."""
    n_tournaments = sorted(elo_norm[elo_norm["rating_stage"] == "n"]["tournament_number"].dropna().unique())
    if not n_tournaments:
        return pd.DataFrame()

    rows = []
    wide = player_elo.pivot_table(index="id", columns="tournament_number", values="elo_final", aggfunc="first")

    for n in n_tournaments:
        n = int(n)
        sub = player_elo[(player_elo["tournament_number"] == n) & player_elo["elo_calib"].notna()].copy()
        sub["t_minus_1"] = sub["id"].map(wide.get(n - 1, pd.Series(dtype=float)))
        sub["t_minus_2"] = sub["id"].map(wide.get(n - 2, pd.Series(dtype=float)))
        sub["raw_blend"] = sub.apply(
            lambda r: calculate_elo_final(r, "t_minus_2", "t_minus_1", "elo_calib"), axis=1
        )
        target_sum = len(sub) * 1000
        actual_sum = sub["raw_blend"].sum()
        factor = target_sum / actual_sum if actual_sum else None
        sub["rating_after"] = sub["raw_blend"] * factor if factor else sub["raw_blend"]

        raw_source = elo_norm[(elo_norm["tournament_number"] == n) & (elo_norm["rating_stage"] == "n")]
        raw_map = raw_source.set_index("id")["elo_diff"]

        for _, r in sub.iterrows():
            flags = ["nn_computed_from_confirmed_formula_not_source_elo_diff_column"]
            if r["id"] not in raw_map.index:
                flags.append("nn_no_matching_source_row_in_elo_norm")
            rows.append({
                "private_player_key": r["id"],
                "tournament_number": n,
                "rating_stage": "n",
                "rating_stage_order": 30,
                "rating_code": f"{n}n",
                "event_type": "blended_normalized",
                "event_sequence": 0,
                "match_id": None,
                "map_id_raw": None,
                "map_number_canonical": None,
                "rating_before": r["elo_calib"],
                "rating_change": r["rating_after"] - r["elo_calib"],
                "rating_after": r["rating_after"],
                "normalization_factor": factor,
                "source_type": "notebook_formula",
                "source_file": "cs_farpost_tournament_calib.ipynb (calculate_elo_final)",
                "source_row": None,
                "formula_version": FORMULA_VERSIONS["nn_blended"],
                "data_quality_flags": flags,
            })
    return pd.DataFrame(rows)


def main() -> int:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    player_elo, elo_changes, elo_norm = _base_frames()

    print("[build_rating_events] normalized_entry (Na) ...")
    na_events = build_normalized_entry_events(player_elo)

    print("[build_rating_events] previous_tournament_final ...")
    prev_final_events = build_previous_tournament_final_events(player_elo)

    print("[build_rating_events] calibration_game (Nc, по картам) ...")
    calib_map_events = build_map_level_events(elo_changes, player_elo, "c", "elo_start", "calibration_game")

    print("[build_rating_events] calibration_final ...")
    calib_final_events = build_stage_final_events(
        player_elo, calib_map_events, "elo_start", "elo_calib", "c", "calibration_final"
    )

    print("[build_rating_events] blended_normalized (Nn) ...")
    nn_events = build_blended_normalized_events(player_elo, elo_norm)

    print("[build_rating_events] tournament_map_change (Nt, по картам) ...")
    tournament_map_events = build_map_level_events(elo_changes, player_elo, "t", "elo_calib", "tournament_map_change")

    print("[build_rating_events] tournament_final ...")
    tournament_final_events = build_stage_final_events(
        player_elo, tournament_map_events, "elo_calib", "elo_final", "t", "tournament_final"
    )

    all_events = pd.concat([
        na_events, prev_final_events, calib_map_events, calib_final_events,
        nn_events, tournament_map_events, tournament_final_events,
    ], ignore_index=True)

    all_events = all_events.sort_values(
        ["private_player_key", "tournament_number", "rating_stage_order", "event_sequence"]
    ).reset_index(drop=True)

    write_table(all_events, PROCESSED_DIR / "fact_player_rating_event.parquet")

    # --- быстрые инварианты (полная проверка — в validate_pipeline.py) ---
    na_sums = na_events.groupby("tournament_number")["rating_after"].sum()
    na_counts = na_events.groupby("tournament_number").size()
    print("[build_rating_events] Na суммы по турнирам (факт vs N*1000):")
    for n in na_sums.index:
        print(f"    турнир {int(n)}: {na_sums[n]:.1f} vs {na_counts[n]*1000}")

    if not nn_events.empty:
        nn_sums = nn_events.groupby("tournament_number")["rating_after"].sum()
        nn_counts = nn_events.groupby("tournament_number").size()
        print("[build_rating_events] Nn суммы по турнирам (факт vs N*1000):")
        for n in nn_sums.index:
            print(f"    турнир {int(n)}: {nn_sums[n]:.1f} vs {nn_counts[n]*1000}")

    n_dupes = all_events.duplicated(
        subset=["private_player_key", "tournament_number", "rating_stage", "event_type", "event_sequence"]
    ).sum()
    print(f"[build_rating_events] дублей по каноническому ключу: {n_dupes}")

    print(f"[build_rating_events] Готово. Всего событий: {len(all_events)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
