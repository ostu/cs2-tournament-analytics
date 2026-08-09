"""
inspect_sources.py
===================

Этап 1 миграции CS2-турнирного отчёта из Power BI в статический HTML-дашборд.

Скрипт выполняет ТОЛЬКО чтение и профилирование исходных таблиц из ``data/raw/``.
Он не изменяет, не переименовывает и не удаляет исходные файлы, не пишет ничего
внутрь ``data/raw/`` и не выполняет сетевых запросов.

Запуск (из корня репозитория):

    python pipeline/inspect_sources.py

Требования:
    - Python 3.12
    - pandas, openpyxl (для чтения .xlsx)

Результат сохраняется в ``artifacts/data_profile/``:
    - <table>.profile.json   — полный машиночитаемый профиль таблицы
    - columns_profile.csv    — построчный профиль по каждой колонке каждой таблицы
    - naming_issues.csv      — потенциальные проблемы с написанием team/steam_name/map/tournament
    - alias_matrix.csv       — какие из "ожидаемых" вариантов имён колонок реально встречаются
    - errors.json            — явный список ошибок чтения (пустой список, если ошибок нет)
    - checksums.json         — SHA-256 контрольные суммы исходных файлов (для проверки, что
                                data/raw/ не была изменена после аудита)
    - data_profile_summary.md — человекочитаемая сводка по всем таблицам

Скрипт НЕ придумывает формулы и НЕ восстанавливает бизнес-логику — он только
описывает то, что фактически находится в файлах.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import traceback
from datetime import datetime, date
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Пути (все относительно корня репозитория, независимо от текущей директории)
# ---------------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT_DIR / "data" / "raw"
OUT_DIR = ROOT_DIR / "artifacts" / "data_profile"

MAX_SAMPLE_VALUES = 8
MAX_LIST_VALUES = 300  # предохранитель от аномально длинных списков в отчётах

# ---------------------------------------------------------------------------
# Реестр таблиц.
#
# `candidate_keys` — гипотезы о первичном/составном ключе, сформированные по
# итогам ручного изучения файлов и notebook-логики (см. docs/source_audit.md).
# Это ГИПОТЕЗЫ, а не подтверждённые ограничения БД.
#
# `categorical_columns_for_naming_check` — колонки, где могут встречаться
# варианты написания одного и того же значения (регистр, пробелы, регистр букв).
# ---------------------------------------------------------------------------

TABLE_REGISTRY = [
    {
        "file_name": "cs_farpost_elo_changes.xlsx",
        "description": "Изменение Elo по картам: id игрока, mapId (порядковый номер карты "
                        "внутри турнира), elo_diff, tournament. Судя по notebook "
                        "cs_farpost_tournament.ipynb, это накопленный вручную экспорт "
                        "серии прогонов (elo_diff.csv) по каждому турниру/калибровке.",
        "candidate_keys": [["id", "mapId", "tournament"]],
        "categorical_columns_for_naming_check": ["id", "tournament"],
    },
    {
        "file_name": "cs_farpost_elo_norm.xlsx",
        "description": "Нормализация Elo между турнирами (build_elo_diff_table в "
                        "cs_farpost_tournament.ipynb): разница между elo_start нового "
                        "турнира и elo, с которым игрок закончил предыдущий. tournament "
                        "здесь использует суффикс 'a' (и один 'n').",
        "candidate_keys": [["id", "tournament"]],
        "categorical_columns_for_naming_check": ["id", "tournament"],
    },
    {
        "file_name": "cs_farpost_players.xlsx",
        "description": "Справочник игроков: анкетные данные, команда и Elo "
                        "(elo_start/elo_calib/elo) по каждому из турниров t1..t7, "
                        "флаги участия в калибровочных матчах map1..map13.",
        "candidate_keys": [["id"], ["player_id"]],
        "categorical_columns_for_naming_check": [
            "steam_name", "t1_team", "t2_team", "t3_team", "t4_team",
            "t5_team", "t6_team", "t7_team",
        ],
    },
    {
        "file_name": "cs_farpost_tournament_results.xlsx",
        "description": "Построчная статистика игрока на карте: tournament, matchId, "
                        "mapId, map (де-название), team, id игрока, kills/deaths/assists/"
                        "adr и др. Грануляция — 'игрок на карте'.",
        "candidate_keys": [["tournament", "mapId", "id"]],
        "categorical_columns_for_naming_check": ["team", "id", "map", "tournament"],
    },
    {
        "file_name": "cs_farpost_tournament_score.xlsx",
        "description": "Итоговое место команды на турнире: team, place, tournament.",
        "candidate_keys": [["team", "tournament"]],
        "categorical_columns_for_naming_check": ["team", "tournament"],
    },
]

# "Ожидаемые" варианты написания колонок, которые нужно явно проверить по ТЗ.
EXPECTED_ALIAS_GROUPS = {
    "tournament": ["tournament", "Tournament"],
    "team": ["team", "team_name", "t1_team", "t2_team", "t3_team", "t4_team",
             "t5_team", "t6_team", "t7_team"],
    "player": ["steam_name", "player", "id", "player_id"],
    "map": ["map", "mapId", "mapId-map"],
    "elo": ["elo", "elo_start", "elo_calib", "EloFinal",
            "t1_elo_start", "t1_elo_calib", "t1_elo"],
    "elo_diff": ["elo_diff", "elo_diff%"],
    "player_stats": ["kills", "assists", "deaths", "ADR", "adr", "KD", "KR"],
}


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_default(obj):
    """Сериализация объектов pandas/numpy/datetime в JSON."""
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()
    if pd.isna(obj):
        return None
    if hasattr(obj, "item"):  # numpy scalar
        return obj.item()
    return str(obj)


def safe_sample_values(series: pd.Series, n: int = MAX_SAMPLE_VALUES) -> list:
    vals = series.dropna().unique()[:n]
    out = []
    for v in vals:
        s = str(v)
        out.append(s if len(s) <= 120 else s[:117] + "...")
    return out


def looks_numeric_string(value: str) -> bool:
    v = value.strip()
    if v == "":
        return False
    v2 = v.replace(" ", "")
    return bool(re.fullmatch(r"[+-]?\d{1,3}(?:[.,]\d+)?", v2)) or bool(
        re.fullmatch(r"[+-]?\d+", v2)
    )


def looks_percent_string(value: str) -> bool:
    return bool(re.fullmatch(r"\s*[+-]?\d+([.,]\d+)?\s*%\s*", str(value)))


def looks_comma_decimal(value: str) -> bool:
    return bool(re.fullmatch(r"\s*[+-]?\d+,\d+\s*", str(value)))


def is_index_like(series: pd.Series) -> bool:
    """Проверка: не является ли колонка техническим индексом 0..n-1 / 1..n."""
    if not pd.api.types.is_numeric_dtype(series):
        return False
    vals = series.dropna()
    if len(vals) < 2:
        return False
    vals_sorted = vals.sort_values().reset_index(drop=True)
    n = len(vals_sorted)
    as_range_from_0 = list(range(n))
    as_range_from_1 = list(range(1, n + 1))
    try:
        vi = vals_sorted.astype("int64").tolist()
    except (ValueError, TypeError):
        return False
    return vi == as_range_from_0 or vi == as_range_from_1


def infer_type(series: pd.Series) -> str:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_bool_dtype(series):
        return "bool"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "float"
    non_null = series.dropna()
    if non_null.empty:
        return "empty"
    if non_null.map(lambda v: isinstance(v, (int, float))).all():
        return "numeric_mixed"
    return "text"


def profile_column(df: pd.DataFrame, col: str) -> dict:
    series = df[col]
    n = len(series)
    n_null = int(series.isna().sum())
    non_null = series.dropna()
    as_str = non_null.astype(str)

    numeric_as_string = 0
    percent_as_string = 0
    comma_decimal = 0
    if series.dtype == object or pd.api.types.is_string_dtype(series):
        numeric_as_string = int(as_str.map(looks_numeric_string).sum())
        percent_as_string = int(as_str.map(looks_percent_string).sum())
        comma_decimal = int(as_str.map(looks_comma_decimal).sum())

    return {
        "column": col,
        "pandas_dtype": str(series.dtype),
        "inferred_type": infer_type(series),
        "n_null": n_null,
        "pct_null": round(100.0 * n_null / n, 2) if n else 0.0,
        "n_unique": int(non_null.nunique()),
        "sample_values": safe_sample_values(series),
        "looks_like_technical_index": is_index_like(series),
        "is_unnamed_column": bool(re.match(r"^Unnamed:\s*\d+$", str(col))),
        "numeric_values_stored_as_string": numeric_as_string,
        "percent_values_stored_as_string": percent_as_string,
        "comma_decimal_values": comma_decimal,
    }


def normalize_for_naming_check(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def find_naming_variants(df: pd.DataFrame, col: str) -> list[dict]:
    """Находит значения, которые совпадают после strip+casefold, но различаются
    по исходному написанию (регистр/пробелы/буквы), например
    'brainrot' vs 'Brainrot' или 'Мидоеды' vs 'мИдоеды'."""
    if col not in df.columns:
        return []
    series = df[col].dropna().astype(str)
    groups: dict[str, set[str]] = {}
    for v in series:
        key = normalize_for_naming_check(v)
        groups.setdefault(key, set()).add(v)
    issues = []
    for key, variants in groups.items():
        if len(variants) > 1:
            issues.append({
                "column": col,
                "normalized_value": key,
                "raw_variants": sorted(variants),
                "variant_count": len(variants),
            })
    return issues


def extract_tournament_number(value: str) -> int | None:
    """Извлекает числовую часть из кода турнира вида '1t', 't1', '4c', '5a'."""
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def profile_table(entry: dict) -> dict:
    file_path = RAW_DIR / entry["file_name"]
    result: dict = {
        "file_name": entry["file_name"],
        "description": entry["description"],
        "path": str(file_path.relative_to(ROOT_DIR)).replace("\\", "/"),
        "exists": file_path.exists(),
    }

    if not file_path.exists():
        result["error"] = "Файл не найден"
        return result

    result["file_format"] = file_path.suffix.lstrip(".")
    result["file_size_bytes"] = file_path.stat().st_size
    result["sha256"] = sha256_of_file(file_path)
    result["encoding"] = "N/A — .xlsx это OOXML/zip-контейнер, понятие 'кодировка текстового файла' неприменимо"
    result["delimiter"] = "N/A — не текстовый CSV/TSV, разделителей нет"

    try:
        xls = pd.ExcelFile(file_path, engine="openpyxl")
        result["sheet_names_raw"] = xls.sheet_names
        result["sheet_names_note"] = (
            "Имена листов могут отображаться нечитаемо (mojibake) в консоли Windows "
            "с кодировкой cp1251 — это артефакт терминала, а не проблема файла."
        )
        if len(xls.sheet_names) != 1:
            result["multi_sheet_warning"] = (
                f"В файле {len(xls.sheet_names)} листов, профилируется первый: "
                f"{xls.sheet_names[0]}"
            )
        df = xls.parse(xls.sheet_names[0])
    except Exception as exc:  # noqa: BLE001 - нужно поймать и явно записать любую ошибку чтения
        result["error"] = f"Ошибка чтения файла: {exc!r}"
        result["traceback"] = traceback.format_exc()
        return result

    result["n_rows"] = int(df.shape[0])
    result["n_columns"] = int(df.shape[1])
    result["columns"] = list(map(str, df.columns))

    # технические/безымянные колонки
    result["unnamed_columns"] = [c for c in result["columns"] if re.match(r"^Unnamed:\s*\d+$", c)]

    # полные дубликаты строк
    try:
        result["full_duplicate_rows"] = int(df.duplicated().sum())
    except TypeError:
        result["full_duplicate_rows"] = None
        result["full_duplicate_rows_error"] = "Не удалось сравнить строки (несравнимые типы)"

    # дубликаты по гипотетическим ключам
    key_checks = []
    for key_cols in entry["candidate_keys"]:
        present = [c for c in key_cols if c in df.columns]
        if not present:
            key_checks.append({"key": key_cols, "status": "колонки не найдены"})
            continue
        dup_count = int(df.duplicated(subset=present).sum())
        key_checks.append({
            "key": key_cols,
            "columns_used": present,
            "duplicate_rows": dup_count,
            "is_unique_key": dup_count == 0,
        })
    result["candidate_key_checks"] = key_checks

    # профиль колонок
    result["columns_profile"] = [profile_column(df, c) for c in df.columns]

    # проверка написания значений в ключевых категориальных колонках
    naming_issues = []
    for col in entry["categorical_columns_for_naming_check"]:
        naming_issues.extend(find_naming_variants(df, col))
    result["naming_issues"] = naming_issues

    # какие из "ожидаемых" альтернативных имён колонок реально встречаются
    alias_hits = {}
    for group_name, aliases in EXPECTED_ALIAS_GROUPS.items():
        alias_hits[group_name] = [a for a in aliases if a in df.columns]
    result["expected_alias_hits"] = alias_hits

    # домен-специфичные срезы
    domain = {}
    if "tournament" in df.columns:
        tournaments = sorted(df["tournament"].dropna().astype(str).unique().tolist())
        domain["tournament_values"] = tournaments[:MAX_LIST_VALUES]
        domain["tournament_count"] = len(tournaments)
        nums = [n for n in (extract_tournament_number(t) for t in tournaments) if n is not None]
        if nums:
            domain["tournament_number_range"] = [min(nums), max(nums)]
    if "map" in df.columns:
        maps_ = sorted(df["map"].dropna().astype(str).unique().tolist())
        domain["map_values"] = maps_[:MAX_LIST_VALUES]
        domain["map_count"] = len(maps_)
    if "team" in df.columns:
        teams = sorted(df["team"].dropna().astype(str).unique().tolist())
        domain["team_values"] = teams[:MAX_LIST_VALUES]
        domain["team_count"] = len(teams)
    if "id" in df.columns:
        domain["player_id_count"] = int(df["id"].dropna().astype(str).nunique())
    if "date" in df.columns:
        try:
            dates = pd.to_datetime(df["date"], errors="coerce")
            domain["date_min"] = json_default(dates.min())
            domain["date_max"] = json_default(dates.max())
            domain["date_unparseable_count"] = int(dates.isna().sum() - df["date"].isna().sum())
        except Exception as exc:  # noqa: BLE001
            domain["date_range_error"] = repr(exc)
    if "place" in df.columns:
        domain["place_range"] = [
            json_default(df["place"].min()),
            json_default(df["place"].max()),
        ]
    result["domain_summary"] = domain

    return result


def build_columns_csv(profiles: list[dict]) -> pd.DataFrame:
    rows = []
    for p in profiles:
        if "columns_profile" not in p:
            continue
        for cp in p["columns_profile"]:
            row = {"table": p["file_name"]}
            row.update(cp)
            row["sample_values"] = " | ".join(cp["sample_values"])
            rows.append(row)
    return pd.DataFrame(rows)


def build_naming_issues_csv(profiles: list[dict]) -> pd.DataFrame:
    rows = []
    for p in profiles:
        for issue in p.get("naming_issues", []):
            rows.append({
                "table": p["file_name"],
                "column": issue["column"],
                "normalized_value": issue["normalized_value"],
                "raw_variants": " | ".join(issue["raw_variants"]),
                "variant_count": issue["variant_count"],
            })
    return pd.DataFrame(rows)


def build_alias_matrix_csv(profiles: list[dict]) -> pd.DataFrame:
    rows = []
    for p in profiles:
        hits = p.get("expected_alias_hits")
        if not hits:
            continue
        for group_name, found in hits.items():
            rows.append({
                "table": p["file_name"],
                "alias_group": group_name,
                "expected_variants": " | ".join(EXPECTED_ALIAS_GROUPS[group_name]),
                "found_in_table": " | ".join(found) if found else "",
            })
    return pd.DataFrame(rows)


def render_markdown_summary(profiles: list[dict], errors: list[dict]) -> str:
    lines = []
    lines.append("# Профиль исходных данных (data/raw/)")
    lines.append("")
    lines.append(f"Сгенерировано скриптом `pipeline/inspect_sources.py`. Дата генерации не проставляется")
    lines.append("автоматически (см. поле `generated_at` в JSON-профилях каждой таблицы).")
    lines.append("")
    lines.append(f"Обработано таблиц: **{len(profiles)}**. Ошибок чтения: **{len(errors)}**.")
    lines.append("")

    for p in profiles:
        lines.append(f"## {p['file_name']}")
        lines.append("")
        lines.append(p.get("description", ""))
        lines.append("")
        if p.get("error"):
            lines.append(f"**ОШИБКА:** {p['error']}")
            lines.append("")
            continue
        lines.append(f"- Формат: `{p['file_format']}`")
        lines.append(f"- Размер: {p['file_size_bytes']:,} байт".replace(",", " "))
        lines.append(f"- SHA-256: `{p['sha256']}`")
        lines.append(f"- Кодировка/разделитель: {p['encoding']}; {p['delimiter']}")
        lines.append(f"- Строк: {p['n_rows']}, колонок: {p['n_columns']}")
        lines.append(f"- Полных дублей строк: {p['full_duplicate_rows']}")
        if p.get("unnamed_columns"):
            lines.append(f"- Безымянные (технические) колонки: {', '.join(p['unnamed_columns'])}")
        lines.append("")
        lines.append("Колонки: `" + "`, `".join(p["columns"]) + "`")
        lines.append("")
        lines.append("Проверка гипотетических ключей:")
        for kc in p["candidate_key_checks"]:
            if "duplicate_rows" in kc:
                status = "OK, уникален" if kc["is_unique_key"] else f"НЕ уникален ({kc['duplicate_rows']} дублей)"
                lines.append(f"- `{kc['key']}` → {status}")
            else:
                lines.append(f"- `{kc['key']}` → {kc['status']}")
        lines.append("")
        dom = p.get("domain_summary", {})
        if dom:
            lines.append("Домен-специфичные сведения:")
            for k, v in dom.items():
                if isinstance(v, list) and len(v) > 20:
                    lines.append(f"- {k}: [{len(v)} значений, см. JSON-профиль]")
                else:
                    lines.append(f"- {k}: {v}")
            lines.append("")
        if p.get("naming_issues"):
            lines.append(f"⚠ Найдено потенциальных вариантов написания одного и того же значения: "
                         f"{len(p['naming_issues'])} (см. naming_issues.csv)")
            lines.append("")

    if errors:
        lines.append("## Ошибки чтения")
        lines.append("")
        for e in errors:
            lines.append(f"- `{e['file_name']}`: {e['error']}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not RAW_DIR.exists():
        print(f"[FATAL] Каталог с исходными данными не найден: {RAW_DIR}", file=sys.stderr)
        return 1

    profiles = []
    errors = []

    for entry in TABLE_REGISTRY:
        print(f"[inspect_sources] профилирую {entry['file_name']} ...")
        try:
            profile = profile_table(entry)
        except Exception as exc:  # noqa: BLE001 - таблица не должна ронять весь прогон
            profile = {
                "file_name": entry["file_name"],
                "error": f"Необработанное исключение: {exc!r}",
                "traceback": traceback.format_exc(),
            }
        if profile.get("error"):
            errors.append({"file_name": entry["file_name"], "error": profile["error"]})
            print(f"  -> ОШИБКА: {profile['error']}")
        else:
            print(f"  -> OK: {profile['n_rows']} строк, {profile['n_columns']} колонок")
        profiles.append(profile)

        out_json = OUT_DIR / f"{Path(entry['file_name']).stem}.profile.json"
        with out_json.open("w", encoding="utf-8") as fh:
            json.dump(profile, fh, ensure_ascii=False, indent=2, default=json_default)

    # сводные артефакты
    columns_df = build_columns_csv(profiles)
    columns_df.to_csv(OUT_DIR / "columns_profile.csv", index=False, encoding="utf-8-sig")

    naming_df = build_naming_issues_csv(profiles)
    naming_df.to_csv(OUT_DIR / "naming_issues.csv", index=False, encoding="utf-8-sig")

    alias_df = build_alias_matrix_csv(profiles)
    alias_df.to_csv(OUT_DIR / "alias_matrix.csv", index=False, encoding="utf-8-sig")

    checksums = {
        p["file_name"]: p.get("sha256") for p in profiles if p.get("sha256")
    }
    with (OUT_DIR / "checksums.json").open("w", encoding="utf-8") as fh:
        json.dump(checksums, fh, ensure_ascii=False, indent=2)

    with (OUT_DIR / "errors.json").open("w", encoding="utf-8") as fh:
        json.dump(errors, fh, ensure_ascii=False, indent=2)

    summary_md = render_markdown_summary(profiles, errors)
    (OUT_DIR / "data_profile_summary.md").write_text(summary_md, encoding="utf-8")

    print()
    print(f"[inspect_sources] Готово. Обработано таблиц: {len(profiles)}, ошибок: {len(errors)}.")
    print(f"[inspect_sources] Результаты сохранены в: {OUT_DIR.relative_to(ROOT_DIR)}")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
