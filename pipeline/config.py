"""
config.py
=========

Общие пути и константы для всего pipeline. Ничего не читает и не вычисляет —
только объявляет пути (относительно корня репозитория) и зафиксированные
параметры, восстановленные в Audit v1/v2 (см. docs/rating_semantics.md,
docs/powerbi_logic_audit.md).

Пути всегда относительны корню репозитория, независимо от текущей рабочей
директории — работает одинаково из PyCharm, PowerShell и `python
pipeline/xxx.py` из любого места.
"""

from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]

# --- данные -----------------------------------------------------------------
RAW_DIR = ROOT_DIR / "data" / "raw"
PRIVATE_DIR = ROOT_DIR / "data" / "private"
INTERIM_DIR = ROOT_DIR / "data" / "interim"
PROCESSED_DIR = ROOT_DIR / "data" / "processed"
PUBLIC_DIR = ROOT_DIR / "data" / "public"

PLAYER_ALIASES_PATH = PRIVATE_DIR / "player_aliases.csv"

# --- конфиги -----------------------------------------------------------------
CONFIG_DIR = ROOT_DIR / "config"
TEAM_ALIASES_REVIEW_PATH = CONFIG_DIR / "team_aliases_review.csv"
METRIC_DEFINITIONS_PATH = CONFIG_DIR / "metric_definitions.json"
DASHBOARD_CONFIG_PATH = CONFIG_DIR / "dashboard_config.json"

# --- артефакты ---------------------------------------------------------------
ARTIFACTS_DIR = ROOT_DIR / "artifacts"
AUDIT_V2_DIR = ARTIFACTS_DIR / "audit_v2"
PIPELINE_ARTIFACTS_DIR = ARTIFACTS_DIR / "pipeline"
VALIDATION_DIR = ARTIFACTS_DIR / "validation"

# --- дашборд -----------------------------------------------------------------
PIPELINE_DIR = ROOT_DIR / "pipeline"
DASHBOARD_TEMPLATE_PATH = PIPELINE_DIR / "dashboard_template.html"
DASHBOARD_DIR = ROOT_DIR / "dashboard"
DASHBOARD_OUTPUT_PATH = ROOT_DIR / "dashboard.html"

DOCS_DIR = ROOT_DIR / "docs"

RAW_TABLE_FILES = {
    "elo_changes": "cs_farpost_elo_changes.xlsx",
    "elo_norm": "cs_farpost_elo_norm.xlsx",
    "players": "cs_farpost_players.xlsx",
    "tournament_results": "cs_farpost_tournament_results.xlsx",
    "tournament_score": "cs_farpost_tournament_score.xlsx",
}

# --- рейтинговые стадии (docs/rating_semantics.md) ---------------------------
# Порядок зафиксирован пользователем в Audit v2/v3, не выведен из данных.
RATING_STAGE_ORDER = {"a": 10, "c": 20, "n": 30, "t": 40}

RATING_STAGE_INFO = {
    "a": {"stage_name": "normalized_entry", "description": "Нормализованный рейтинг на входе в турнир"},
    "c": {"stage_name": "calibration_final", "description": "Итоговый рейтинг после блока калибровочных игр"},
    "n": {"stage_name": "blended_normalized", "description": "Смешанный рейтинг после калибровки для формирования команд"},
    "t": {"stage_name": "tournament_final", "description": "Итоговый рейтинг после основного турнира"},
}

# --- допущения, подтверждённые/опровергнутые аудитом --------------------------
# mapId в cs_farpost_elo_changes.xlsx на 1 меньше, чем в cs_farpost_tournament_results.xlsx
# (0-based цикл в notebook vs 1-based нумерация карт) — см. docs/powerbi_logic_audit.md §2.
MAPID_OFFSET_CORRECTION = 1
# Подтверждённый Audit v2 минимальный порог совпадения join после коррекции (99.96% факт).
MIN_EXPECTED_MAPID_JOIN_COVERAGE = 0.995

EXPECTED_ROSTER_SIZE = 5

# Порог "маленькой выборки" для heatmap/team-map метрик — конфигурируемый,
# см. также config/dashboard_config.json (может переопределяться там).
DEFAULT_LOW_SAMPLE_THRESHOLD = 3

# Версии формул, применённых при построении витрины — для отчётов
# legacy-vs-canonical и для поля formula_version в fact_player_rating_event.
FORMULA_VERSIONS = {
    "mapid_offset_correction": "mapid_offset_v1_notebook_0based_correction",
    "na_normalized_entry": "na_v1_authoritative_user_formula",
    "nc_calibration": "nc_v1_notebook_calc_elo",
    "nn_blended": "nn_v1_notebook_calculate_elo_final",
    "nt_tournament_final": "nt_v1_notebook_and_dax_confirmed",
    "avg_kills_canonical": "avgkills_v1_composite_key_denominator",
    "adr_availability_aware": "adr_v1_tracked_flag",
    "mvp_availability_aware": "mvp_v1_tracked_flag",
    "team_round_diff_direct": "team_pp_v1_direct_no_division",
}

# Кодировка при печати в консоль Windows может ломаться на кириллице —
# скрипты используют явную utf-8 запись файлов независимо от консоли.
TEXT_ENCODING = "utf-8"
