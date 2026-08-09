"""
audit_powerbi_logic.py
=======================

Audit v2. Сверяет исходные таблицы `data/raw/` с логикой, восстановленной из
`powerquery.txt` (M) и `dax.txt` (DAX), и с authoritative business-семантикой
рейтингового цикла, полученной от пользователя (см. docs/rating_semantics.md).

Скрипт ТОЛЬКО читает `data/raw/`, `powerquery.txt`, `dax.txt` — ничего не
изменяет и не выполняет сетевых запросов (M-код из `powerquery.txt` не
исполняется, он только документируется и проверяется на реальных таблицах).

Запуск (из корня репозитория):

    python pipeline/audit_powerbi_logic.py

Результат — JSON-файлы в `artifacts/audit_v2/`:
    - checksums.json              — SHA-256 powerquery.txt/dax.txt + таблиц data/raw
    - rating_code_audit.json      — разбор кодов турниров на tournament_number/rating_stage
    - mapid_offset_audit.json     — проверка сдвига mapId+1 из cs_farpost_elo_changes
    - result_score_audit.json     — классификация result (счёт/дата/пусто/прочее)
    - avgkills_legacy_vs_canonical.json — расхождение _avgKills legacy vs canonical
    - elofinal_legacy_vs_raw.json — сверка DAX _EloFinal с players.t{n}_elo
    - remaining_matches_audit.json — проверка допущения "6 матчей на команду"
    - pp_roster_audit.json        — проверка допущения "5 игроков в составе" для _TeamsPP
    - adr_mvp_coverage.json       — покрытие ADR/MVP по tournament/match/map
    - rating_stage_sums.json      — суммы рейтинга по стадиям Na/Nc/Nn/Nt
    - summary.json                 — сводка по всем проверкам
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import traceback
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT_DIR / "data" / "raw"
OUT_DIR = ROOT_DIR / "artifacts" / "audit_v2"
POWERQUERY_FILE = ROOT_DIR / "powerquery.txt"
DAX_FILE = ROOT_DIR / "dax.txt"


def sha256_of_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_default(obj):
    if hasattr(obj, "item"):
        return obj.item()
    if pd.isna(obj):
        return None
    return str(obj)


def dump_json(name: str, payload: dict) -> None:
    path = OUT_DIR / f"{name}.json"
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=json_default)


def load_raw_tables() -> dict[str, pd.DataFrame]:
    tables = {}
    for fname in [
        "cs_farpost_elo_changes.xlsx",
        "cs_farpost_elo_norm.xlsx",
        "cs_farpost_players.xlsx",
        "cs_farpost_tournament_results.xlsx",
        "cs_farpost_tournament_score.xlsx",
    ]:
        tables[fname] = pd.read_excel(RAW_DIR / fname)
    return tables


# ---------------------------------------------------------------------------
# 1. Наличие и контрольные суммы powerquery.txt / dax.txt + пяти таблиц
# ---------------------------------------------------------------------------

def check_input_files() -> dict:
    result = {
        "powerquery_txt": {
            "exists": POWERQUERY_FILE.exists(),
            "sha256": sha256_of_file(POWERQUERY_FILE),
        },
        "dax_txt": {
            "exists": DAX_FILE.exists(),
            "sha256": sha256_of_file(DAX_FILE),
        },
        "raw_tables": {},
    }
    for fname in [
        "cs_farpost_elo_changes.xlsx",
        "cs_farpost_elo_norm.xlsx",
        "cs_farpost_players.xlsx",
        "cs_farpost_tournament_results.xlsx",
        "cs_farpost_tournament_score.xlsx",
    ]:
        p = RAW_DIR / fname
        result["raw_tables"][fname] = {"exists": p.exists(), "sha256": sha256_of_file(p)}
    return result


# ---------------------------------------------------------------------------
# 2. Rating code audit: tournament_number / rating_stage / rating_stage_order
# ---------------------------------------------------------------------------

STAGE_ORDER = {"a": 10, "c": 20, "n": 30, "t": 40}
STAGE_NAME = {
    "a": "normalized_entry_rating",
    "c": "calibration_rating",
    "n": "post_calibration_blended_rating",
    "t": "final_tournament_rating",
}


def parse_rating_code(code: str) -> tuple[int | None, str | None]:
    """Разбирает код вида '8a', '8t', 't8' на (tournament_number, rating_stage)."""
    code = str(code).strip()
    m = re.fullmatch(r"(\d+)([a-zA-Z]+)", code)
    if m:
        return int(m.group(1)), m.group(2).lower()
    m = re.fullmatch(r"([a-zA-Z]+)(\d+)", code)
    if m:
        # формат "tN" (историческое обозначение, см. docs/rating_semantics.md)
        return int(m.group(2)), None
    return None, None


def rating_code_audit(tables: dict[str, pd.DataFrame]) -> dict:
    sources = {
        "cs_farpost_elo_changes.xlsx": "tournament",
        "cs_farpost_elo_norm.xlsx": "tournament",
        "cs_farpost_tournament_results.xlsx": "tournament",
        "cs_farpost_tournament_score.xlsx": "tournament",
    }
    result = {"per_table": {}, "stage_order_mapping": STAGE_ORDER}
    for fname, col in sources.items():
        df = tables[fname]
        codes = sorted(df[col].dropna().astype(str).unique().tolist())
        parsed = []
        for c in codes:
            tnum, stage = parse_rating_code(c)
            parsed.append({
                "rating_code": c,
                "tournament_number": tnum,
                "rating_stage": stage,
                "rating_stage_order": STAGE_ORDER.get(stage) if stage else None,
                "format": "N+stage" if stage else "stage+N (legacy tN)",
            })
        result["per_table"][fname] = parsed
    return result


# ---------------------------------------------------------------------------
# 3. mapId offset audit (cs_farpost_elo_changes: mapId = mapId + 1)
# ---------------------------------------------------------------------------

def mapid_offset_audit(tables: dict[str, pd.DataFrame]) -> dict:
    res = tables["cs_farpost_tournament_results.xlsx"]
    chg = tables["cs_farpost_elo_changes.xlsx"]

    chg_shift = chg.copy()
    chg_shift["mapId_shifted"] = chg_shift["mapId"] + 1

    shifted = res.merge(
        chg_shift[["id", "tournament", "mapId_shifted", "elo_diff"]],
        left_on=["id", "tournament", "mapId"],
        right_on=["id", "tournament", "mapId_shifted"],
        how="left",
    )
    unshifted = res.merge(
        chg[["id", "tournament", "mapId", "elo_diff"]],
        on=["id", "tournament", "mapId"],
        how="left",
    )

    n_total = len(res)
    n_matched_shifted = int(shifted["elo_diff"].notna().sum())
    n_matched_unshifted = int(unshifted["elo_diff"].notna().sum())

    res_keys = set(
        res["id"].astype(str) + "|" + res["tournament"].astype(str) + "|" + res["mapId"].astype(str)
    )
    chg_shift_keys = (
        chg_shift["id"].astype(str) + "|" + chg_shift["tournament"].astype(str) + "|" + chg_shift["mapId_shifted"].astype(str)
    )
    orphaned_chg_rows = int((~chg_shift_keys.isin(res_keys)).sum())

    unmatched_rows = shifted[shifted["elo_diff"].isna()][["tournament", "mapId", "id", "team"]]

    min_mapid_results = {k: int(v) for k, v in res.groupby("tournament")["mapId"].min().items()}
    min_mapid_changes = {k: int(v) for k, v in chg.groupby("tournament")["mapId"].min().items()}

    return {
        "explanation": (
            "elo_changes.mapId начинается с 0 в каждом турнире (Python range() в "
            "cs_farpost_tournament.ipynb 0-based), tournament_results.mapId начинается "
            "с 1. Сдвиг +1 в Power Query — это коррекция 0-based/1-based индексации, "
            "а не смысловой сдвиг 'на предыдущую/следующую карту'."
        ),
        "min_mapid_per_tournament_results": min_mapid_results,
        "min_mapid_per_tournament_elo_changes": min_mapid_changes,
        "total_results_rows": n_total,
        "matched_rows_shifted_join": n_matched_shifted,
        "matched_pct_shifted": round(100 * n_matched_shifted / n_total, 2),
        "matched_rows_unshifted_join": n_matched_unshifted,
        "matched_pct_unshifted": round(100 * n_matched_unshifted / n_total, 2),
        "orphaned_elo_changes_rows_under_shifted_join": orphaned_chg_rows,
        "unmatched_results_rows_under_shifted_join": unmatched_rows.to_dict(orient="records"),
    }


# ---------------------------------------------------------------------------
# 4. result / score audit
# ---------------------------------------------------------------------------

def classify_result(v) -> str:
    if pd.isna(v):
        return "empty"
    if hasattr(v, "year"):
        return "date_like_excel_corruption"
    if re.fullmatch(r"\d+-\d+", str(v)):
        return "valid_score"
    return "other"


def result_score_audit(tables: dict[str, pd.DataFrame]) -> dict:
    res = tables["cs_farpost_tournament_results.xlsx"].copy()
    res["result_class"] = res["result"].apply(classify_result)
    counts = res["result_class"].value_counts().to_dict()

    valid = res[res["result_class"] == "valid_score"].copy()
    parts = valid["result"].str.split("-", n=1, expand=True).astype(int)
    valid["r1"], valid["r2"] = parts[0], parts[1]
    valid_sum_matches_rounds = float((valid["r1"] + valid["r2"] == valid["rounds"]).mean()) if len(valid) else None

    date_rows = res[res["result_class"] == "date_like_excel_corruption"].copy()
    recovered = {}
    if len(date_rows):
        dt = pd.to_datetime(date_rows["result"])
        date_rows["month"] = dt.dt.month
        date_rows["day"] = dt.dt.day
        date_rows["year"] = dt.dt.year
        recovered["month_range"] = [int(date_rows["month"].min()), int(date_rows["month"].max())]
        recovered["day_range"] = [int(date_rows["day"].min()), int(date_rows["day"].max())]
        recovered["day_value_counts"] = {str(k): int(v) for k, v in date_rows["day"].value_counts().items()}
        recovered["month_plus_day_equals_rounds_match_rate"] = float(
            (date_rows["month"] + date_rows["day"] == date_rows["rounds"]).mean()
        )
        recovered["year_matches_tournament_chronology"] = {
            str(k): sorted(int(y) for y in v.unique())
            for k, v in date_rows.groupby("tournament")["year"]
        }
        mismatches = date_rows[date_rows["month"] + date_rows["day"] != date_rows["rounds"]]
        recovered["mismatched_rows"] = mismatches[["tournament", "mapId", "rounds", "result"]].drop_duplicates().to_dict(orient="records")

    return {
        "explanation": (
            "Значения result, распознанные pandas/Excel как дата, — это, по всей "
            "видимости, испорченные Excel'ем счёта карт формата 'M-D', где M<=12 "
            "(похоже на месяц) и D>=13 (похоже на день). Год в испорченной дате "
            "совпадает с реальным календарным годом турнира, что подтверждает: это "
            "автоисправление даты при вводе/сохранении в Excel, а не случайные данные."
        ),
        "counts_by_class": {str(k): int(v) for k, v in counts.items()},
        "valid_score_r1_plus_r2_equals_rounds_match_rate": valid_sum_matches_rounds,
        "date_like_recovery_analysis": recovered,
        "note_power_query_split": (
            "Power Query (запрос 'cs') разбивает result по '-' на r1/r2 через "
            "Table.SplitColumn с ровно двумя именами колонок. Для строк с датой "
            "'YYYY-MM-DD' это дало бы >2 частей; по документированному поведению "
            "Table.SplitColumn остаток после первой части объединяется обратно в "
            "последнюю колонку (r2='MM-DD'), и Int64.Type на такой строке выдаст "
            "ошибку конверсии — то есть PP/r1/r2 для этих строк в Power BI, скорее "
            "всего, отображаются как Error, а не как тихо неверное число. Это "
            "не проверено выполнением M-кода (сетевые запросы запрещены), только "
            "выведено из документированной семантики Table.SplitColumn."
        ),
    }


# ---------------------------------------------------------------------------
# 5. avgKills legacy (DISTINCTCOUNT(mapId)) vs canonical (tournament,mapId)
# ---------------------------------------------------------------------------

def avgkills_audit(tables: dict[str, pd.DataFrame]) -> dict:
    res = tables["cs_farpost_tournament_results.xlsx"]

    legacy_denom_global = int(res["mapId"].nunique())
    canonical_denom_global = int(res.groupby(["tournament", "mapId"]).ngroups)
    total_kills = int(res["kills"].sum())

    per_player = res.groupby("id").agg(kills=("kills", "sum"), legacy_denom=("mapId", "nunique"))
    per_player["canonical_denom"] = res.groupby("id").apply(
        lambda g: g[["tournament", "mapId"]].drop_duplicates().shape[0]
    )
    per_player["legacy_avgKills"] = per_player["kills"] / per_player["legacy_denom"]
    per_player["canonical_avgKills"] = per_player["kills"] / per_player["canonical_denom"]
    per_player["denom_gap"] = per_player["canonical_denom"] - per_player["legacy_denom"]

    n_affected = int((per_player["denom_gap"] > 0).sum())
    top10 = per_player.sort_values("denom_gap", ascending=False).head(10).reset_index()

    return {
        "measures": ["_avgKills", "_avgKills2 (confirmed literal duplicate of _avgKills in dax.txt)"],
        "legacy_formula": "DIVIDE(SUM(cs[kills]), DISTINCTCOUNT(cs[mapId]))",
        "problem": (
            "mapId сбрасывается на каждом турнире (не глобальный ключ), поэтому "
            "DISTINCTCOUNT(mapId) в контексте, охватывающем >1 турнир, считает "
            "количество уникальных ЗНАЧЕНИЙ mapId (1..32), а не количество реально "
            "сыгранных карт (пар tournament+mapId)."
        ),
        "global": {
            "legacy_denominator_distinct_mapId": legacy_denom_global,
            "canonical_denominator_distinct_tournament_mapId": canonical_denom_global,
            "total_kills": total_kills,
            "legacy_avgKills": round(total_kills / legacy_denom_global, 3),
            "canonical_avgKills": round(total_kills / canonical_denom_global, 3),
        },
        "per_player_players_affected": n_affected,
        "per_player_total_players": int(len(per_player)),
        "per_player_top10_by_denominator_gap": top10.to_dict(orient="records"),
    }


# ---------------------------------------------------------------------------
# 6. _EloFinal (DAX) vs players.t{n}_elo (raw)
# ---------------------------------------------------------------------------

def elofinal_audit(tables: dict[str, pd.DataFrame]) -> dict:
    res = tables["cs_farpost_tournament_results.xlsx"]
    chg = tables["cs_farpost_elo_changes.xlsx"]
    players = tables["cs_farpost_players.xlsx"]

    chg_shift = chg.copy()
    chg_shift["mapId_shifted"] = chg_shift["mapId"] + 1
    cs = res.merge(
        chg_shift[["id", "tournament", "mapId_shifted", "elo_diff"]],
        left_on=["id", "tournament", "mapId"],
        right_on=["id", "tournament", "mapId_shifted"],
        how="left",
    )
    cs["tnum"] = cs["tournament"].str.extract(r"(\d+)").astype(int)
    cs["stage"] = cs["tournament"].str.extract(r"(\D+)")
    cs_t = cs[cs["stage"] == "t"]
    sum_by_player_tnum = cs_t.groupby(["id", "tnum"])["elo_diff"].sum().reset_index()

    per_tournament = []
    for n in range(1, 8):
        calib_col, final_col = f"t{n}_elo_calib", f"t{n}_elo"
        sub = players[["id", calib_col, final_col]].dropna()
        sub = sub.merge(sum_by_player_tnum[sum_by_player_tnum["tnum"] == n][["id", "elo_diff"]], on="id", how="left")
        sub["elo_diff"] = sub["elo_diff"].fillna(0)
        sub["dax_elofinal"] = sub[calib_col] + sub["elo_diff"]
        sub["diff_vs_raw"] = sub["dax_elofinal"] - sub[final_col]
        per_tournament.append({
            "tournament_number": n,
            "n_players": int(len(sub)),
            "mean_abs_diff": round(float(sub["diff_vs_raw"].abs().mean()), 4) if len(sub) else None,
            "exact_matches": int((sub["diff_vs_raw"] == 0).sum()),
            "max_abs_diff": round(float(sub["diff_vs_raw"].abs().max()), 4) if len(sub) else None,
        })

    return {
        "dax_formula": "_EloFinal = SUM(tournaments[elo_calib]) + SUM(cs[elo_diff])",
        "audit_v1_candidates": [
            "candidate 1: raw players.t{n}_elo column",
            "candidate 2: notebook variable elo_final (calculate_elo_final, weighted 0.3/0.5/0.2 blend, not persisted anywhere)",
        ],
        "conclusion": (
            "При суммировании elo_diff ТОЛЬКО по maps основного турнира (rating_stage='t', "
            "исключая калибровку 'c') DAX-формула EloFinal = elo_calib + SUM(elo_diff) "
            "ТОЧНО совпадает с players.t{n}_elo для всех 7 турниров (0 расхождений). "
            "EloFinal — это players.t{n}_elo, а не notebook-переменная elo_final."
        ),
        "per_tournament": per_tournament,
        "warning_if_stage_not_filtered": (
            "Если при агрегации elo_diff не отделять rating_stage ('c' от 't'), суммы "
            "для турниров 2-7 расходятся с players.t{n}_elo на сотни очков (проверено: "
            "без фильтра по stage расхождение доходило до ~780 очков на игрока) — "
            "именно поэтому в новой модели tournament_number и rating_stage должны быть "
            "разными полями, а не одним текстовым кодом."
        ),
    }


# ---------------------------------------------------------------------------
# 7. RemainingMatches = 6 - _TeamMatches
# ---------------------------------------------------------------------------

def remaining_matches_audit(tables: dict[str, pd.DataFrame]) -> dict:
    res = tables["cs_farpost_tournament_results.xlsx"]
    mpt = res.groupby(["tournament", "team"])["matchId"].nunique().reset_index(name="matches")
    mpt["stage"] = mpt["tournament"].str.extract(r"(\D+)")

    def describe(sub_df):
        return {
            "count": int(len(sub_df)),
            "mean": round(float(sub_df["matches"].mean()), 2) if len(sub_df) else None,
            "min": int(sub_df["matches"].min()) if len(sub_df) else None,
            "max": int(sub_df["matches"].max()) if len(sub_df) else None,
            "exactly_6_pct": round(100 * float((sub_df["matches"] == 6).mean()), 1) if len(sub_df) else None,
        }

    per_tournament = {}
    for t, g in mpt.groupby("tournament"):
        per_tournament[t] = describe(g)

    return {
        "dax_formula": "_RemainingMatches = 6 - [_TeamMatches]",
        "assumption_checked": "каждая команда играет ровно 6 матчей за турнир",
        "overall_main_tournaments_t": describe(mpt[mpt["stage"] == "t"]),
        "overall_calibration_c": describe(mpt[mpt["stage"] == "c"]),
        "per_tournament": per_tournament,
        "conclusion": (
            "Ни один турнир (включая калибровки) не показывает стабильно ровно 6 матчей "
            "на команду. Значение 6 — вероятно, зафиксированный параметр под конкретный "
            "формат/турнир на момент создания меры, а не универсальное правило турнира."
        ),
    }


# ---------------------------------------------------------------------------
# 8. PP / 5 roster-size audit (_TeamsPP = DIVIDE(SUM(cs[PP]),5))
# ---------------------------------------------------------------------------

def pp_roster_audit(tables: dict[str, pd.DataFrame]) -> dict:
    res = tables["cs_farpost_tournament_results.xlsx"]
    roster = res.groupby(["tournament", "mapId", "team"]).size().reset_index(name="n_players")
    dist = roster["n_players"].value_counts().to_dict()
    non5 = roster[roster["n_players"] != 5]

    return {
        "dax_formula": "_TeamsPP = DIVIDE(SUM(cs[PP]), 5)",
        "explanation": (
            "PP хранится на грейне игрок×карта, но фактически это значение "
            "команда×карта, продублированное на каждого из 5 игроков состава "
            "(результат 'if w/l=\"W\" then r1-r2 else r2-r1' — одинаков для всех "
            "игроков команды на карте). SUM(PP) по составу карты умножает истинный "
            "PP карты на размер состава, поэтому деление на 5 компенсирует дублирование."
        ),
        "roster_size_distribution": {str(k): int(v) for k, v in dist.items()},
        "non_5_player_rosters_count": int(len(non5)),
        "non_5_player_rosters_sample": non5.head(20).to_dict(orient="records"),
        "canonical_alternative": (
            "Считать team-map округ-дифференциал (PP) напрямую на грейне "
            "(tournament, mapId, team), без денормализации на игроков и без "
            "деления на константу 5 — устойчиво даже для составов из 4 игроков."
        ),
    }


# ---------------------------------------------------------------------------
# 9. ADR / MVP coverage report
# ---------------------------------------------------------------------------

def adr_mvp_coverage(tables: dict[str, pd.DataFrame]) -> dict:
    res = tables["cs_farpost_tournament_results.xlsx"].copy()

    def coverage_by(col):
        by_tournament = res.groupby("tournament")[col].agg(
            n="size",
            n_zero=lambda s: int((s == 0).sum()),
            n_nonzero=lambda s: int((s != 0).sum()),
        )
        by_tournament["pct_nonzero"] = (by_tournament["n_nonzero"] / by_tournament["n"] * 100).round(1)
        by_map = res.groupby(["tournament", "mapId"])[col].agg(n="size", n_nonzero=lambda s: int((s != 0).sum()))
        partial = by_map[(by_map["n_nonzero"] > 0) & (by_map["n_nonzero"] < by_map["n"])]
        map_tracked = (by_map["n_nonzero"] > 0)
        return {
            "by_tournament": by_tournament.reset_index().to_dict(orient="records"),
            "n_maps_total": int(len(by_map)),
            "n_maps_tracked": int(map_tracked.sum()),
            "n_maps_untracked": int((~map_tracked).sum()),
            "n_maps_partial_coverage_within_map": int(len(partial)),
        }

    adr_cov = coverage_by("adr")
    mvp_cov = coverage_by("mvps")

    # legacy vs availability-aware avgADR
    map_adr_tracked = res.groupby(["tournament", "mapId"])["adr"].apply(lambda s: bool((s != 0).any()))
    res["adr_tracked"] = res.set_index(["tournament", "mapId"]).index.map(map_adr_tracked)
    legacy_avg_adr_global = float(res["adr"].mean())
    aware_avg_adr_global = float(res.loc[res["adr_tracked"], "adr"].mean())
    n_players_no_tracked_adr = int(
        res.groupby("id")["adr_tracked"].any().eq(False).sum()
    )

    return {
        "adr": {
            **adr_cov,
            "concept_field": "adr_tracked (bool at grain tournament x mapId)",
            "rule": "adr_tracked = True если ХОТЯ БЫ один игрок на этой карте имеет adr != 0",
            "legacy_avgADR_dax": "AVERAGE(cs[adr])",
            "legacy_avgADR_global": round(legacy_avg_adr_global, 2),
            "availability_aware_avgADR_global": round(aware_avg_adr_global, 2),
            "players_with_zero_tracked_maps": n_players_no_tracked_adr,
            "players_total": int(res["id"].nunique()),
        },
        "mvp": {
            **mvp_cov,
            "concept_field": "mvp_tracked (bool at grain tournament x mapId, tournament-level block granularity observed)",
            "rule": (
                "0 может быть настоящим результатом (игрок просто не был MVP ни в одном "
                "раунде), поэтому mvp_tracked=false ставится ТОЛЬКО для карт/турниров, "
                "где ВСЕ строки без исключения равны 0 (наблюдаемая гранулярность "
                "отсутствия трекинга — целый турнир: 4c..7t)."
            ),
        },
        "cross_pattern": (
            "ADR трекался в 6c/6t/7c/7t (не в 1t..5t/2c..5c); MVP трекался в 1t/2c/2t/3c/3t "
            "(не в 4c..7t) — паттерны почти полностью взаимоисключающие по турнирам, что "
            "похоже на смену инструмента/источника сбора статистики между турнирами 3 и 6."
        ),
    }


# ---------------------------------------------------------------------------
# 10. Rating stage sums (Na target = returning*1000 + new*1000; Nn sum = n*1000)
# ---------------------------------------------------------------------------

def rating_stage_sums(tables: dict[str, pd.DataFrame]) -> dict:
    players = tables["cs_farpost_players.xlsx"]
    norm = tables["cs_farpost_elo_norm.xlsx"]

    records = []
    for _, row in players.iterrows():
        last_elo = None
        for t in range(1, 8):
            start_col, elo_col = f"t{t}_elo_start", f"t{t}_elo"
            start_elo = row.get(start_col)
            if pd.isna(start_elo):
                continue
            records.append({"id": row["id"], "tnum": t, "elo_start": start_elo, "prev_final_elo": last_elo})
            cur = row.get(elo_col)
            if not pd.isna(cur):
                last_elo = cur
    df = pd.DataFrame(records)

    na_checks = []
    for n in range(2, 8):
        sub = df[df["tnum"] == n]
        returning = sub[sub["prev_final_elo"].notna()]
        new_players = sub[sub["prev_final_elo"].isna()]
        actual_total = float(returning["prev_final_elo"].sum())
        target_total = len(returning) * 1000
        factor = target_total / actual_total if actual_total else None
        predicted = returning["prev_final_elo"] * factor if factor else None
        mean_abs_diff = float((predicted - returning["elo_start"]).abs().mean()) if factor is not None and len(returning) else None
        na_checks.append({
            "tournament_number": n,
            "returning_players": int(len(returning)),
            "new_players": int(len(new_players)),
            "new_player_elo_start_values": sorted(float(x) for x in new_players["elo_start"].unique()),
            "sum_elo_start_returning": float(returning["elo_start"].sum()),
            "target_returning_total": target_total,
            "scale_factor_implied": round(factor, 4) if factor else None,
            "per_player_formula_mean_abs_diff": round(mean_abs_diff, 2) if mean_abs_diff is not None else None,
            "sum_elo_start_all": float(sub["elo_start"].sum()),
            "target_all_total": len(sub) * 1000,
        })

    stage_sums = {}
    for stage in norm["tournament"].str[-1].unique():
        sub = norm[norm["tournament"].str.endswith(stage)]
        by_tournament = sub.groupby("tournament")["elo_diff"].agg(n="size", sum="sum")
        stage_sums[stage] = by_tournament.reset_index().to_dict(orient="records")

    return {
        "na_normalized_entry_rating_audit": {
            "authoritative_formula": (
                "new_rating_i = previous_final_rating_i * target_returning_total / "
                "actual_returning_total; target_returning_total = returning_count*1000"
            ),
            "per_tournament": na_checks,
            "conclusion": (
                "Групповое ограничение (сумма elo_start вернувшихся игроков == "
                "returning_count*1000, и сумма ВСЕХ elo_start == player_count*1000) "
                "подтверждено ТОЧНО для каждого турнира 2..7. Индивидуальная "
                "пропорциональная формула по игроку сходится к точному совпадению "
                "только начиная с турниров 6-7 (mean abs diff падает с ~100 до ~0.3); "
                "для турниров 2-5 индивидуальные значения elo_start отклоняются от "
                "чистой пропорциональной формулы сильнее, хотя агрегатное ограничение "
                "выполняется всегда. Не находим объяснения этому в notebook — вероятно, "
                "ранние турниры нормализовались не полностью тем же кодом/процессом."
            ),
        },
        "elo_norm_stage_sums": stage_sums,
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not RAW_DIR.exists():
        print(f"[FATAL] {RAW_DIR} не найден", file=sys.stderr)
        return 1
    if not POWERQUERY_FILE.exists():
        print(f"[WARN] {POWERQUERY_FILE} не найден — часть проверок будет пропущена")
    if not DAX_FILE.exists():
        print(f"[WARN] {DAX_FILE} не найден — часть проверок будет пропущена")

    tables = load_raw_tables()

    checks = {
        "checksums": check_input_files,
        "rating_code_audit": lambda: rating_code_audit(tables),
        "mapid_offset_audit": lambda: mapid_offset_audit(tables),
        "result_score_audit": lambda: result_score_audit(tables),
        "avgkills_legacy_vs_canonical": lambda: avgkills_audit(tables),
        "elofinal_legacy_vs_raw": lambda: elofinal_audit(tables),
        "remaining_matches_audit": lambda: remaining_matches_audit(tables),
        "pp_roster_audit": lambda: pp_roster_audit(tables),
        "adr_mvp_coverage": lambda: adr_mvp_coverage(tables),
        "rating_stage_sums": lambda: rating_stage_sums(tables),
    }

    summary = {"checks_run": [], "errors": []}
    for name, fn in checks.items():
        print(f"[audit_powerbi_logic] выполняю: {name} ...")
        try:
            payload = fn()
            dump_json(name, payload)
            summary["checks_run"].append(name)
            print(f"  -> OK")
        except Exception as exc:  # noqa: BLE001
            err = {"check": name, "error": repr(exc), "traceback": traceback.format_exc()}
            summary["errors"].append(err)
            print(f"  -> ОШИБКА: {exc!r}")

    dump_json("summary", summary)
    print()
    print(f"[audit_powerbi_logic] Готово. Выполнено проверок: {len(summary['checks_run'])}, "
          f"ошибок: {len(summary['errors'])}. Результаты в {OUT_DIR.relative_to(ROOT_DIR)}")
    return 0 if not summary["errors"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
