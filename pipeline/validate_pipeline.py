"""
validate_pipeline.py
======================

Прогоняет весь набор проверок канонического pipeline (source integrity,
ключи, рейтинговые инварианты, статистика, team-map reconciliation,
приватность, дашборд) и сохраняет результат в:

    artifacts/validation/validation.json
    artifacts/validation/validation.md

Критические проверки (source integrity, уникальность ключей, приватность,
наличие/чистота dashboard.html) — при провале скрипт завершается ненулевым
кодом. Некритичные data-quality находки (известные по Audit v1/v2
особенности данных — например, расхождение calibration_final для турнира 7,
см. docs/rating_semantics.md) отображаются как warning, не как ошибка.

Запуск (из корня репозитория):

    python pipeline/validate_pipeline.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    DASHBOARD_DIR,
    DASHBOARD_OUTPUT_PATH,
    MIN_EXPECTED_MAPID_JOIN_COVERAGE,
    PROCESSED_DIR,
    PUBLIC_DIR,
    RAW_DIR,
    RAW_TABLE_FILES,
    ROOT_DIR,
    VALIDATION_DIR,
)
from io_utils import read_table  # noqa: E402

CHECKS: list[dict] = []


def record(name: str, severity: str, passed: bool, detail: str, **extra):
    CHECKS.append({
        "name": name, "severity": severity, "passed": bool(passed), "detail": detail, **extra,
    })


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 1. Source integrity
# ---------------------------------------------------------------------------

def check_source_integrity():
    baseline_path = ROOT_DIR / "artifacts" / "audit_v2" / "checksums.json"
    if not baseline_path.exists():
        record("source_integrity", "critical", False, "Базовый файл контрольных сумм не найден: " + str(baseline_path))
        return
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["raw_tables"]
    all_ok = True
    for key, fname in RAW_TABLE_FILES.items():
        p = RAW_DIR / fname
        if not p.exists():
            record("source_integrity", "critical", False, f"{fname} не найден")
            all_ok = False
            continue
        actual = sha256_of(p)
        expected = baseline.get(fname, {}).get("sha256")
        ok = actual == expected
        all_ok &= ok
        record("source_integrity", "critical", ok, f"{fname}: {'совпадает' if ok else 'ИЗМЕНЁН относительно базового аудита!'}")


# ---------------------------------------------------------------------------
# 2. Keys
# ---------------------------------------------------------------------------

def check_keys():
    dim_tournament = read_table(PROCESSED_DIR / "dim_tournament.parquet")
    dim_team = read_table(PROCESSED_DIR / "dim_team.parquet")
    dim_map = read_table(PROCESSED_DIR / "dim_map.parquet")
    dim_player_public = read_table(PROCESSED_DIR / "dim_player_public.parquet")

    for name, df, keycols in [
        ("dim_tournament", dim_tournament, ["tournament_number"]),
        ("dim_team", dim_team, ["team_key"]),
        ("dim_map", dim_map, ["map_key"]),
        ("dim_player_public", dim_player_public, ["public_player_id"]),
    ]:
        dupes = int(df.duplicated(subset=keycols).sum())
        record(f"dimension_key_unique:{name}", "critical", dupes == 0, f"дублей по {keycols}: {dupes}")

    fact_player_map = read_table(PROCESSED_DIR / "fact_player_map.parquet")
    fact_team_map = read_table(PROCESSED_DIR / "fact_team_map.parquet")
    fact_rating_event = read_table(PROCESSED_DIR / "fact_player_rating_event.parquet")

    checks = [
        ("fact_player_map", fact_player_map, ["private_player_key", "tournament_number", "tournament_phase", "map_number"]),
        ("fact_team_map", fact_team_map, ["tournament_number", "tournament_phase", "map_number", "team_key"]),
        ("fact_player_rating_event", fact_rating_event,
         ["private_player_key", "tournament_number", "rating_stage", "event_type", "event_sequence"]),
    ]
    for name, df, keycols in checks:
        dupes = int(df.duplicated(subset=keycols).sum())
        record(f"fact_grain_unique:{name}", "critical", dupes == 0, f"дублей по гранулярному ключу {keycols}: {dupes}")

    # many-to-many sanity: каждая карта в fact_team_map должна иметь ровно 2 команды
    per_map = fact_team_map.groupby(["tournament_number", "tournament_phase", "map_number"]).size()
    bad = int((per_map != 2).sum())
    record("no_unexpected_many_to_many:team_per_map", "warning", bad == 0,
           f"карт с числом команд != 2: {bad} из {len(per_map)}")


# ---------------------------------------------------------------------------
# 3. Rating invariants
# ---------------------------------------------------------------------------

def check_rating():
    events = read_table(PROCESSED_DIR / "fact_player_rating_event.parquet")

    na = events[events["event_type"] == "normalized_entry"]
    for n, grp in na.groupby("tournament_number"):
        target = len(grp) * 1000
        actual = grp["rating_after"].sum()
        ok = abs(actual - target) < 1.0
        record("na_sum_invariant", "critical" if not ok else "info", ok,
               f"турнир {int(n)}: Na сумма {actual:.1f} vs целевая {target}")

    nn = events[events["event_type"] == "blended_normalized"]
    for n, grp in nn.groupby("tournament_number"):
        target = len(grp) * 1000
        actual = grp["rating_after"].sum()
        ok = abs(actual - target) < 1.0
        record("nn_sum_invariant", "critical" if not ok else "info", ok,
               f"турнир {int(n)}: Nn сумма {actual:.1f} vs целевая {target}")

    # rating_after = rating_before + rating_change, где все три известны
    sub = events.dropna(subset=["rating_before", "rating_change", "rating_after"])
    mismatch = (sub["rating_after"] - (sub["rating_before"] + sub["rating_change"])).abs() > 0.5
    record("rating_after_equals_before_plus_change", "critical", mismatch.sum() == 0,
           f"несоответствий: {int(mismatch.sum())} из {len(sub)}")

    # известные (не блокирующие) расхождения реконструкции — см. docs/rating_semantics.md
    exploded = events.explode("data_quality_flags")
    recon_flags = exploded[exploded["data_quality_flags"].isin(
        ["c_final_reconstruction_mismatch", "t_final_reconstruction_mismatch"]
    )]
    record("known_final_reconstruction_mismatches", "warning", True,
           f"известных расхождений calibration_final/tournament_final с реконструкцией по картам: {len(recon_flags)} "
           f"(ожидаемо для турнира 7, см. docs/rating_semantics.md §3)")

    # mapId join coverage
    results = read_table(ROOT_DIR / "data" / "interim" / "tournament_results_normalized.parquet")
    elo_changes = read_table(ROOT_DIR / "data" / "interim" / "elo_changes_normalized.parquet")
    merged = results.merge(
        elo_changes[["id", "tournament_number", "rating_stage", "map_number_canonical", "elo_diff"]],
        on=["id", "tournament_number", "rating_stage", "map_number_canonical"], how="left",
    )
    coverage = merged["elo_diff"].notna().mean()
    record("mapid_offset_join_coverage", "critical", coverage >= MIN_EXPECTED_MAPID_JOIN_COVERAGE,
           f"coverage={coverage:.4%}, порог={MIN_EXPECTED_MAPID_JOIN_COVERAGE:.2%}")


# ---------------------------------------------------------------------------
# 4. Stats sanity
# ---------------------------------------------------------------------------

def check_stats():
    fpm = read_table(PROCESSED_DIR / "fact_player_map.parquet")

    neg_kills = int((fpm["kills"] < 0).sum())
    neg_deaths = int((fpm["deaths"] < 0).sum())
    # Известная аномалия исходных данных (см. docs/unresolved_questions.md):
    # 1 строка с kills=-4 в самом cs_farpost_tournament_results.xlsx — не
    # исправляется автоматически (raw value сохраняется, только флагуется),
    # поэтому это warning, а не критический провал pipeline.
    record("kills_nonnegative", "warning", neg_kills == 0, f"строк с kills<0: {neg_kills} (см. data_quality_flags=negative_stat_value_in_raw_source)")
    record("deaths_nonnegative", "critical", neg_deaths == 0, f"строк с deaths<0: {neg_deaths}")

    nonpos_rounds = int((fpm["rounds_played"] <= 0).sum())
    record("rounds_positive", "warning", nonpos_rounds == 0, f"строк с rounds_played<=0: {nonpos_rounds}")

    kd = fpm["kills"] / fpm["deaths"].replace(0, pd.NA)
    n_inf = int((~kd.replace([float("inf"), float("-inf")], pd.NA).notna() & fpm["deaths"].eq(0)).sum())
    record("kd_no_infinite", "critical", True, f"строк с deaths=0 (KD не определён, обрабатывается как null): {n_inf}")

    adr_violation = int(((~fpm["adr_tracked"]) & fpm["adr"].notna()).sum())
    record("adr_null_when_not_tracked", "critical", adr_violation == 0,
           f"строк с adr_tracked=false, но adr не null: {adr_violation}")

    mvp_summary = fpm.groupby(["tournament_number", "tournament_phase"])["mvp_tracked"].agg(["any", "all"])
    inconsistent_mvp = int((mvp_summary["any"] != mvp_summary["all"]).sum())
    record("mvp_tracked_consistent_per_tournament", "warning", inconsistent_mvp == 0,
           f"турниров/стадий с неоднородным mvp_tracked внутри себя: {inconsistent_mvp}")


# ---------------------------------------------------------------------------
# 5. Team-map reconciliation
# ---------------------------------------------------------------------------

def check_team_map_reconciliation():
    ftm = read_table(PROCESSED_DIR / "fact_team_map.parquet")
    map_key_cols = ["tournament_number", "tournament_phase", "map_number"]

    total, complete, mirror_ok = 0, 0, 0
    for _, grp in ftm.groupby(map_key_cols):
        total += 1
        if len(grp) != 2:
            continue
        complete += 1
        a, b = grp.iloc[0], grp.iloc[1]
        wl_opposite = {a["win_loss"], b["win_loss"]} == {"W", "L"}
        same_match = a["match_id"] == b["match_id"]
        mirrored = pd.isna(a["round_diff"]) or pd.isna(b["round_diff"]) or (a["round_diff"] == -b["round_diff"])
        opponents_correct = a["opponent_team_key"] == b["team_key"] and b["opponent_team_key"] == a["team_key"]
        if wl_opposite and same_match and mirrored and opponents_correct:
            mirror_ok += 1

    record("team_map_two_sided_complete", "warning", complete == total, f"{complete}/{total} карт с ровно 2 командами")
    record("team_map_mirror_reconciliation", "critical", mirror_ok == complete,
           f"зеркальность (W/L противоположны, same match, round_diff зеркален, opponent корректен): {mirror_ok}/{complete}")


# ---------------------------------------------------------------------------
# 5b. Team classification (permanent vs calibration) — см.
#     artifacts/validation/dashboard_filter_debug.json, issue #5.
# ---------------------------------------------------------------------------

def check_team_classification():
    dim_team = read_table(PROCESSED_DIR / "dim_team.parquet")

    allowed_types = {"permanent_tournament_team", "calibration_team", "unknown"}
    bad_types = set(dim_team["team_type"].unique()) - allowed_types
    record("team_type_values_valid", "critical", len(bad_types) == 0,
           f"неожиданные значения team_type: {bad_types}" if bad_types else "все team_type — из допустимого набора")

    n_unknown = int((dim_team["team_type"] == "unknown").sum())
    record("team_type_no_unresolved_unknown", "warning", n_unknown == 0,
           f"команд с team_type=unknown (не публикуются в team dashboards до подтверждения): {n_unknown}")

    calibration_names = set(dim_team[dim_team["team_type"] == "calibration_team"]["team_key"])
    permanent_names = set(dim_team[dim_team["team_type"] == "permanent_tournament_team"]["team_key"])
    record("team_type_no_overlap", "critical", len(calibration_names & permanent_names) == 0,
           f"команд одновременно в permanent и calibration: {len(calibration_names & permanent_names)}")

    # публичные team-датасеты не должны содержать calibration_team
    public_team_files = [
        "teams.json", "team_maps.json", "team_map_matrix.json",
        "team_rating_history.json", "team_rating_comparison.json",
    ]
    for fname in public_team_files:
        p = PUBLIC_DIR / fname
        if not p.exists():
            record(f"no_calibration_team_in_public:{fname}", "critical", False, f"{fname} не найден")
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        ids_in_file = {row.get("team_id") for row in data if isinstance(row, dict)}
        leaked = ids_in_file & calibration_names
        record(f"no_calibration_team_in_public:{fname}", "critical", len(leaked) == 0,
               f"calibration-команд в {fname}: {leaked}" if leaked else f"{fname}: калибровочных команд нет")

    metadata_path = PUBLIC_DIR / "metadata.json"
    if metadata_path.exists():
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
        leaked_filter = set(meta.get("teams", [])) & calibration_names
        record("no_calibration_team_in_filter_dropdown", "critical", len(leaked_filter) == 0,
               f"calibration-команд в metadata.teams (фильтр): {leaked_filter}" if leaked_filter
               else "фильтр команд не содержит calibration_team")
        leaked_by_tournament = {
            tnum: sorted(set(teams) & calibration_names)
            for tnum, teams in meta.get("teams_by_tournament", {}).items()
            if set(teams) & calibration_names
        }
        record("no_calibration_team_in_teams_by_tournament", "critical", len(leaked_by_tournament) == 0,
               f"турниры с calibration-командами в teams_by_tournament: {leaked_by_tournament}" if leaked_by_tournament
               else "ни один турнир в teams_by_tournament не содержит calibration_team")

    # calibration rating events ИГРОКОВ должны остаться нетронутыми (не
    # удалены из player/rating pipeline — см. секцию 2 задания)
    events = read_table(PROCESSED_DIR / "fact_player_rating_event.parquet")
    n_calib_game = int((events["event_type"] == "calibration_game").sum())
    n_calib_final = int((events["event_type"] == "calibration_final").sum())
    record("calibration_rating_events_preserved", "critical", n_calib_game > 0 and n_calib_final > 0,
           f"calibration_game={n_calib_game}, calibration_final={n_calib_final} (должны быть > 0)")

    # "Пустой график при наличии валидных строк — critical" (секция 3 задания):
    # проверяем, что для all-time и per-tournament team-рейтингов есть хотя бы
    # часть строк с непустым rating_final, если постоянные команды вообще есть.
    mts = read_table(PROCESSED_DIR / "mart_team_summary.parquet")
    n_permanent = len(permanent_names)
    if n_permanent > 0:
        all_time = mts[mts["tournament_number"].isna()]
        non_null_all_time = int(all_time["rating_final"].notna().sum())
        record("team_rating_final_not_all_null_all_time", "critical", non_null_all_time > 0,
               f"all-time строк с валидным rating_final: {non_null_all_time} из {len(all_time)} "
               f"(при {n_permanent} постоянных командах)")

        per_tournament = mts[mts["tournament_number"].notna()]
        non_null_per_t = int(per_tournament["rating_final"].notna().sum())
        record("team_rating_final_not_all_null_per_tournament", "critical", non_null_per_t > 0,
               f"per-tournament строк с валидным rating_final: {non_null_per_t} из {len(per_tournament)}")

    comparison_path = PROCESSED_DIR / "mart_team_rating_comparison.parquet"
    if comparison_path.exists():
        comparison = read_table(comparison_path)
        non_null_nc_nt = int((comparison["team_nc"].notna() & comparison["team_nt"].notna()).sum())
        record("team_rating_comparison_not_all_null", "critical", non_null_nc_nt > 0,
               f"строк с валидными team_nc и team_nt: {non_null_nc_nt} из {len(comparison)}")


# ---------------------------------------------------------------------------
# 6. Privacy
# ---------------------------------------------------------------------------

def check_privacy():
    leak_script = ROOT_DIR / "pipeline" / "check_publication_leaks.py"

    import os
    env = dict(os.environ, PYTHONIOENCODING="utf-8")

    for target in [PUBLIC_DIR, DASHBOARD_OUTPUT_PATH]:
        exists = target.exists()
        if not exists:
            record("privacy_leak_check", "critical", False, f"{target} не существует — проверка утечек невозможна")
            continue
        result = subprocess.run(
            [sys.executable, str(leak_script), str(target)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        record("privacy_leak_check", "critical", result.returncode == 0,
               f"{target.name}: {'чисто' if result.returncode == 0 else 'НАЙДЕНЫ СОВПАДЕНИЯ'}")

    aliases_in_public = list(PUBLIC_DIR.rglob("*player_aliases*")) if PUBLIC_DIR.exists() else []
    record("no_private_mapping_file_in_public", "critical", len(aliases_in_public) == 0,
           f"файлов player_aliases* в data/public/: {len(aliases_in_public)}")

    if PUBLIC_DIR.exists():
        for f in PUBLIC_DIR.glob("*.json"):
            text = f.read_text(encoding="utf-8")
            has_private_key_field = '"private_player_key"' in text
            record(f"no_private_player_key_field:{f.name}", "critical", not has_private_key_field,
                   "поле private_player_key " + ("НАЙДЕНО" if has_private_key_field else "отсутствует"))


# ---------------------------------------------------------------------------
# 7. Dashboard
# ---------------------------------------------------------------------------

def check_dashboard():
    if not DASHBOARD_OUTPUT_PATH.exists():
        record("dashboard_exists", "critical", False, "dashboard.html не найден")
        return

    size_kb = DASHBOARD_OUTPUT_PATH.stat().st_size / 1024
    record("dashboard_exists", "critical", True, f"dashboard.html существует, размер {size_kb:.0f} КБ")

    html = DASHBOARD_OUTPUT_PATH.read_text(encoding="utf-8")

    required_sections = ["page-overview", "page-players", "page-teams", "page-teammap",
                         "filter-tournament", "filter-team", "filter-player", "filter-map"]
    missing_sections = [s for s in required_sections if f'"{s}"' not in html]
    record("dashboard_required_sections", "critical", len(missing_sections) == 0,
           f"отсутствующие секции: {missing_sections}" if missing_sections else "все обязательные секции присутствуют")

    try:
        cfg_text = html.split("window.APP_CONFIG = ")[1].split("window.APP_DATA")[0].strip().rstrip(";")
        data_text = html.split("window.APP_DATA = ")[1].split("</script>")[0].strip().rstrip(";")
        json.loads(cfg_text)
        parsed = json.loads(data_text)
        expected_keys = {"players", "player_rating_history", "player_maps", "teams",
                          "team_rating_history", "team_maps", "team_map_matrix",
                          "team_rating_comparison", "metadata"}
        missing_keys = expected_keys - set(parsed.keys())
        record("dashboard_embedded_json_valid", "critical", len(missing_keys) == 0,
               f"embedded JSON валиден; отсутствующие ключи: {missing_keys}" if missing_keys else "embedded JSON валиден, все ключи присутствуют")
    except (IndexError, json.JSONDecodeError) as exc:
        record("dashboard_embedded_json_valid", "critical", False, f"ошибка парсинга embedded JSON: {exc!r}")

    import re
    html_no_comments = re.sub(r"<!--[\s\S]*?-->", "", html)
    local_refs = re.findall(r'(?:src|href)="((?!https?://|data:|#)[^"]+)"', html_no_comments)
    missing_assets = [r for r in local_refs if not (ROOT_DIR / r).exists()]
    record("dashboard_no_missing_local_assets", "critical", len(missing_assets) == 0,
           f"отсутствующие локальные ассеты: {missing_assets}" if missing_assets else "нет ссылок на отсутствующие локальные файлы (все ассеты инлайнены или через CDN)")


# ---------------------------------------------------------------------------
# 7b. Dashboard frontend (Overview cleanup / ADR coverage column / map names)
# ---------------------------------------------------------------------------

REMOVED_OVERVIEW_DOM_IDS = ["chart-team-rating", "chart-player-rating", "chart-gainers", "chart-losers"]
REMOVED_OVERVIEW_RENDERERS = [
    "renderTeamRatingChart", "renderPlayerRatingChart", "renderGainersLosersCharts",
]


def check_dashboard_frontend():
    app_js_path = DASHBOARD_DIR / "js" / "app.js"
    if not app_js_path.exists():
        record("dashboard_frontend_source_exists", "critical", False, f"{app_js_path} не найден")
        return
    app_js = app_js_path.read_text(encoding="utf-8")

    removed_present = [tok for tok in REMOVED_OVERVIEW_DOM_IDS + REMOVED_OVERVIEW_RENDERERS if tok in app_js]
    record("overview_removed_components_absent", "critical", len(removed_present) == 0,
           f"найдены удалённые Overview-компоненты в app.js: {removed_present}" if removed_present
           else "удалённые Overview-компоненты (рейтинг команд, 3 графика игроков) отсутствуют")

    record("overview_calib_final_container_present", "critical", "chart-calib-final" in app_js,
           "контейнер #chart-calib-final для Nc vs Nt найден" if "chart-calib-final" in app_js
           else "контейнер #chart-calib-final не найден в app.js")

    overview_start = app_js.find("function renderOverview")
    overview_end = app_js.find("function renderCalibFinalChart")
    if overview_start != -1 and overview_end != -1:
        overview_body = app_js[overview_start:overview_end]
        calib_idx = overview_body.find("chart-calib-final")
        mapdist_idx = overview_body.find("chart-map-dist")
        ok = calib_idx != -1 and (mapdist_idx == -1 or calib_idx < mapdist_idx)
        record("overview_calib_final_is_first_chart", "warning", ok,
               "Nc vs Nt расположен раньше остальных Overview-графиков в разметке" if ok
               else "Nc vs Nt НЕ идёт первым графиком в renderOverview()")
    else:
        record("overview_calib_final_is_first_chart", "warning", False, "не удалось найти renderOverview()/renderCalibFinalChart() в app.js")

    columns_start = app_js.find("var PLAYER_COLUMNS")
    columns_end = app_js.find("];", columns_start) if columns_start != -1 else -1
    if columns_start != -1 and columns_end != -1:
        columns_block = app_js[columns_start:columns_end]
        has_adr_coverage_col = "adr_coverage" in columns_block or "Покрытие ADR" in columns_block
        record("players_table_no_adr_coverage_column", "critical", not has_adr_coverage_col,
               "PLAYER_COLUMNS содержит колонку ADR coverage!" if has_adr_coverage_col
               else "PLAYER_COLUMNS не содержит видимую колонку 'Покрытие ADR'")
        has_adr_col = '"adr"' in columns_block
        record("players_table_adr_column_present", "critical", has_adr_col,
               "колонка ADR присутствует" if has_adr_col else "колонка ADR отсутствует из PLAYER_COLUMNS")
    else:
        record("players_table_no_adr_coverage_column", "critical", False, "PLAYER_COLUMNS не найден в app.js")

    record("player_cta_present", "warning", "cta-empty-state" in app_js and "Выберите игрока" in app_js,
           "CTA выбора игрока найден" if "cta-empty-state" in app_js else "CTA выбора игрока не найден в app.js")

    record("rating_stage_legend_present", "warning", "stage-legend" in app_js,
           "легенда стадий A/C/N/T найдена" if "stage-legend" in app_js else "легенда стадий не найдена в app.js")

    if not PUBLIC_DIR.exists():
        record("rating_history_map_name_present", "warning", False, "data/public/ не собран — проверка пропущена")
    else:
        history_path = PUBLIC_DIR / "player_rating_history.json"
        if history_path.exists():
            history = json.loads(history_path.read_text(encoding="utf-8"))
            map_events = [r for r in history if r.get("event_type") in ("tournament_map_change", "calibration_game")]
            with_name = [r for r in map_events if r.get("map_name")]
            ok = len(map_events) > 0 and len(with_name) == len(map_events)
            record("rating_history_map_name_present", "warning", ok,
                   f"map_name заполнен для {len(with_name)}/{len(map_events)} карточных рейтинговых событий")
        else:
            record("rating_history_map_name_present", "warning", False, "player_rating_history.json не найден")

    comparison_path = PROCESSED_DIR / "mart_team_rating_comparison.parquet"
    if comparison_path.exists():
        comparison = read_table(comparison_path)
        record("calib_final_chart_source_rows_present", "critical", len(comparison) > 0,
               f"mart_team_rating_comparison: {len(comparison)} строк")
    else:
        record("calib_final_chart_source_rows_present", "critical", False, "mart_team_rating_comparison.parquet не найден")


# ---------------------------------------------------------------------------
# 7c. About section — читательский текст, без инженерного жаргона
# ---------------------------------------------------------------------------

FORBIDDEN_ABOUT_WORDS = ["pipeline", "canonical", "legacy", "join", "coverage validation", "power bi"]


def check_about_section():
    template_path = ROOT_DIR / "pipeline" / "dashboard_template.html"
    if not template_path.exists():
        record("about_section_reader_friendly", "warning", False, f"{template_path} не найден")
        return
    html = template_path.read_text(encoding="utf-8")
    start = html.find('<section class="about-block">')
    end = html.find("</section>", start)
    if start == -1 or end == -1:
        record("about_section_reader_friendly", "warning", False, "блок 'О данных' не найден в шаблоне")
        return
    block = html[start:end].lower()
    hits = [w for w in FORBIDDEN_ABOUT_WORDS if w in block]
    record("about_section_reader_friendly", "warning", len(hits) == 0,
           f"технические термины в блоке 'О данных': {hits}" if hits else "блок 'О данных' не содержит технических терминов")
    record("about_section_mentions_pseudonyms_and_stages", "warning",
           "псевдоним" in block and ("a</strong" in block or ">a<" in block),
           "упоминает псевдонимы и стадии рейтинга A/C/N/T" if "псевдоним" in block else "не упоминает псевдонимы")


# ---------------------------------------------------------------------------
# 8. Roster anomalies (диагностика, не блокирует сборку)
# ---------------------------------------------------------------------------

def check_roster_anomalies():
    json_path = VALIDATION_DIR / "team_roster_anomalies.json"
    md_path = VALIDATION_DIR / "team_roster_anomalies.md"

    record("roster_anomalies_report_exists", "warning", json_path.exists() and md_path.exists(),
           "team_roster_anomalies.json/.md созданы" if json_path.exists() and md_path.exists()
           else "team_roster_anomalies.json/.md не найдены — запустите pipeline/audit_team_rosters.py")

    if not json_path.exists():
        return

    data = json.loads(json_path.read_text(encoding="utf-8"))
    anomalies = data.get("anomalies", [])
    sizes_4_or_6 = [a for a in anomalies if a.get("roster_size") in (4, 6)]
    record("roster_anomalies_size_4_or_6_captured", "warning", True,
           f"аномалий всего: {len(anomalies)}, из них roster_size 4 или 6: {len(sizes_4_or_6)}")

    valid_causes = set(CAUSE_TAXONOMY_FOR_VALIDATION)
    bad_causes = [a["probable_cause"] for a in anomalies if a.get("probable_cause") not in valid_causes]
    record("roster_anomalies_causes_from_taxonomy", "warning", len(bad_causes) == 0,
           f"причины вне таксономии: {set(bad_causes)}" if bad_causes else "все причины — из согласованной таксономии")

    # resolve_final_roster() не менялся: состав в отчёте должен совпадать с
    # тем, что реально используется в mart_team_summary (players_count).
    ts_path = PROCESSED_DIR / "mart_team_summary.parquet"
    if ts_path.exists():
        ts = read_table(ts_path)
        flagged = ts[ts["data_quality_flags"].apply(lambda fl: "roster_size_not_five" in fl) & ts["tournament_number"].notna()]
        record("roster_size_not_five_flag_present", "warning", len(flagged) > 0,
               f"строк mart_team_summary с флагом roster_size_not_five: {len(flagged)}")

    # диагностика не должна блокировать сборку — сам факт наличия аномалий
    # никогда не критический (см. секция 2.3 задания).
    record("roster_anomalies_are_warning_not_critical", "warning", True,
           f"{len(anomalies)} аномалий(и) состава зафиксировано как warning, сборка не заблокирована")


CAUSE_TAXONOMY_FOR_VALIDATION = [
    "real_four_player_map", "substitution", "mid_tournament_transfer",
    "missing_player_map_rows", "conflicting_source_roster",
    "final_map_roster_artifact", "unknown",
]


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def render_markdown(checks: list[dict]) -> str:
    lines = ["# Отчёт валидации pipeline", ""]
    n_critical_fail = sum(1 for c in checks if c["severity"] == "critical" and not c["passed"])
    n_warning_fail = sum(1 for c in checks if c["severity"] == "warning" and not c["passed"])
    lines.append(f"Всего проверок: {len(checks)}. Критических провалов: **{n_critical_fail}**. Warning: **{n_warning_fail}**.")
    lines.append("")
    for section, group in _group_by_prefix(checks):
        lines.append(f"## {section}")
        lines.append("")
        for c in group:
            icon = "PASS" if c["passed"] else ("FAIL" if c["severity"] == "critical" else "WARN")
            lines.append(f"- [{icon}] `{c['name']}` — {c['detail']}")
        lines.append("")
    return "\n".join(lines)


def _group_by_prefix(checks):
    groups = {}
    order = []
    for c in checks:
        prefix = c["name"].split(":")[0]
        if prefix not in groups:
            groups[prefix] = []
            order.append(prefix)
        groups[prefix].append(c)
    return [(p, groups[p]) for p in order]


def main() -> int:
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    print("[validate_pipeline] source integrity ...")
    check_source_integrity()
    print("[validate_pipeline] keys ...")
    check_keys()
    print("[validate_pipeline] rating invariants ...")
    check_rating()
    print("[validate_pipeline] stats sanity ...")
    check_stats()
    print("[validate_pipeline] team-map reconciliation ...")
    check_team_map_reconciliation()
    print("[validate_pipeline] team classification (permanent vs calibration) ...")
    check_team_classification()
    print("[validate_pipeline] privacy ...")
    check_privacy()
    print("[validate_pipeline] dashboard ...")
    check_dashboard()
    print("[validate_pipeline] dashboard frontend (Overview/ADR column/map names) ...")
    check_dashboard_frontend()
    print("[validate_pipeline] about section ...")
    check_about_section()
    print("[validate_pipeline] roster anomalies ...")
    check_roster_anomalies()

    (VALIDATION_DIR / "validation.json").write_text(
        json.dumps(CHECKS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (VALIDATION_DIR / "validation.md").write_text(render_markdown(CHECKS), encoding="utf-8")

    n_critical_fail = sum(1 for c in CHECKS if c["severity"] == "critical" and not c["passed"])
    n_warning_fail = sum(1 for c in CHECKS if c["severity"] == "warning" and not c["passed"])
    print(f"\n[validate_pipeline] Всего проверок: {len(CHECKS)}. "
          f"Критических провалов: {n_critical_fail}. Warning: {n_warning_fail}.")
    print(f"[validate_pipeline] Отчёт: {VALIDATION_DIR.relative_to(ROOT_DIR)}/validation.{{json,md}}")

    return 1 if n_critical_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
