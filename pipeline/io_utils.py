"""
io_utils.py
===========

Общие функции чтения/записи и разбора, используемые несколькими
pipeline-скриптами. Ничего не читает из сети. Все функции — чистые
преобразования данных, кроме файловых read_*/write_* хелперов.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import PLAYER_ALIASES_PATH, RAW_DIR, RAW_TABLE_FILES, TEAM_ALIASES_REVIEW_PATH  # noqa: E402


def load_private_alias_bridge() -> pd.DataFrame:
    """Читает data/private/player_aliases.csv (private_player_key <->
    public_player_id/public_nickname). ТОЛЬКО для внутреннего использования
    pipeline-скриптами при джойне приватных фактов на публичные псевдонимы —
    результат этого джойна не должен попадать в data/public/ как есть с
    private_player_key (см. docs/privacy_and_publication.md)."""
    return pd.read_csv(PLAYER_ALIASES_PATH, encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# Excel raw readers
# ---------------------------------------------------------------------------

def read_raw_table(table_key: str) -> pd.DataFrame:
    """Читает одну из 5 исходных таблиц data/raw/ по короткому ключу
    (см. RAW_TABLE_FILES). Добавляет source_file/source_row для lineage."""
    fname = RAW_TABLE_FILES[table_key]
    df = pd.read_excel(RAW_DIR / fname)
    df = df.reset_index(drop=True)
    df["source_file"] = fname
    df["source_row"] = df.index
    return df


# ---------------------------------------------------------------------------
# Parquet + CSV preview
# ---------------------------------------------------------------------------

def _is_null_scalar(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (list, dict, tuple)):
        return False
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _is_plain_numeric(v) -> bool:
    import numbers

    return isinstance(v, numbers.Number) and not isinstance(v, bool)


def _sanitize_for_parquet(df: pd.DataFrame) -> pd.DataFrame:
    """Исходные Excel-таблицы иногда хранят ГЕНУИННО смешанные типы в одной
    колонке (например, faceit_name со строками и одним числом) —
    parquet/pyarrow этого не допускает. Приводим к строке ТОЛЬКО такие
    колонки, сохраняя null как null. Не трогаем:
      - однородно числовые object-колонки (int/float/numpy-числа вперемешку —
        типичный результат pd.concat разных facts с int64 и float64 —
        pyarrow сам сведёт их к float64);
      - list/dict-колонки (data_quality_flags).
    Иначе числовые/list-колонки после round-trip через parquet превратились
    бы в строки."""
    df = df.copy()
    for col in df.columns:
        if df[col].dtype != object:
            continue
        non_null = [v for v in df[col] if not _is_null_scalar(v)]
        if not non_null:
            continue
        if any(isinstance(v, (list, dict, tuple)) for v in non_null):
            continue
        if all(_is_plain_numeric(v) for v in non_null):
            continue
        non_null_types = {type(v) for v in non_null}
        if len(non_null_types) > 1:
            df[col] = df[col].apply(lambda v: v if _is_null_scalar(v) else str(v))
    return df


def write_table(df: pd.DataFrame, path: Path, csv_preview_rows: int = 200) -> None:
    """Пишет parquet (основной формат pipeline) + небольшой CSV-preview для
    ручной проверки рядом (тот же путь, суффикс .preview.csv)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _sanitize_for_parquet(df).to_parquet(path, index=False)
    preview_path = path.with_suffix(".preview.csv")
    df.head(csv_preview_rows).to_csv(preview_path, index=False, encoding="utf-8-sig")


def read_table(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def write_json(obj, path: Path, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent, default=_json_default)


def _json_default(obj):
    if isinstance(obj, (list, tuple)):
        return list(obj)
    if hasattr(obj, "tolist"):  # numpy.ndarray и т.п. (в т.ч. многоэлементные)
        return obj.tolist()
    if hasattr(obj, "item"):  # numpy-скаляр
        return obj.item()
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return str(obj)


# ---------------------------------------------------------------------------
# Rating code parsing (см. docs/rating_semantics.md)
# ---------------------------------------------------------------------------

def parse_rating_code(raw_code: str) -> dict:
    """Разбирает код турнира/стадии вида '8a','8c','8n','8t' или легаси 't8'
    на канонические поля. Возвращает dict с tournament_number/rating_stage/
    format ('number_stage' — основной формат данных, 'stage_number' — легаси
    't8', используется только как dashboard_filter_code, не как модельный
    ключ — см. docs/rating_semantics.md §1)."""
    code = str(raw_code).strip()

    m = re.fullmatch(r"(\d+)([a-zA-Z]+)", code)
    if m:
        return {
            "rating_code_raw": raw_code,
            "tournament_number": int(m.group(1)),
            "rating_stage": m.group(2).lower(),
            "code_format": "number_stage",
        }

    m = re.fullmatch(r"([a-zA-Z]+)(\d+)", code)
    if m:
        return {
            "rating_code_raw": raw_code,
            "tournament_number": int(m.group(2)),
            "rating_stage": None,
            "code_format": "stage_number_legacy",
        }

    return {
        "rating_code_raw": raw_code,
        "tournament_number": None,
        "rating_stage": None,
        "code_format": "unrecognized",
    }


# ---------------------------------------------------------------------------
# Team alias resolution (см. config/team_aliases_review.csv)
# ---------------------------------------------------------------------------

# Статусы, при которых предложенное каноническое имя команды можно применять
# автоматически. 'needs_review' НИКОГДА не входит сюда — только человек может
# подтвердить неоднозначное соответствие (см. docs/unresolved_questions.md §3.1).
APPLICABLE_ALIAS_STATUSES = {"auto_approved", "approved", "confirmed", "no_variant_found"}


def normalize_team_text(name: str) -> str:
    """Только для служебного normalized_text — НЕ определяет canonical team
    при неоднозначности (см. задание §5 "Команды")."""
    return re.sub(r"\s+", " ", str(name)).strip()


def load_team_alias_map(path: Path = TEAM_ALIASES_REVIEW_PATH) -> tuple[dict, set]:
    """Возвращает (alias_map, pending_raw_names).

    alias_map: raw_team_name -> canonical_team_name, только для строк со
    статусом из APPLICABLE_ALIAS_STATUSES.
    pending_raw_names: raw_team_name, для которых статус needs_review/pending/
    rejected — их нельзя автоматически объединять, raw-имя остаётся как есть,
    но помечается data-quality флагом на этапе normalize_sources.
    """
    df = pd.read_csv(path, encoding="utf-8-sig")
    alias_map: dict[str, str] = {}
    pending: set[str] = set()
    for _, row in df.iterrows():
        raw = row["raw_team_name"]
        status = str(row["review_status"])
        if status in APPLICABLE_ALIAS_STATUSES:
            alias_map[raw] = row["proposed_canonical_team"]
        else:
            pending.add(raw)
            alias_map[raw] = raw  # canonical = сам raw, пока не подтверждено иное
    return alias_map, pending


# ---------------------------------------------------------------------------
# Result / score parsing (см. docs/powerbi_logic_audit.md §3)
# ---------------------------------------------------------------------------

VALID_SCORE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def parse_result(raw_value, win_loss: str) -> dict:
    """Парсит result ТОЛЬКО подтверждённый формат 'N-N'. Строки, которые
    pandas/Excel превратили в дату (см. docs/powerbi_logic_audit.md §3),
    НЕ пытаемся восстановить эвристикой — это неподтверждённая догадка, а не
    установленный факт (см. docs/unresolved_questions.md §2.2). Значения
    остаются null, result_raw сохраняется как есть, статус явно помечается.

    team_score/opponent_score определяются так же, как r1/r2 в Power Query
    'cs' (см. docs/powerbi_logic_audit.md §4): text-порядок счёта фиксирован
    независимо от исхода, а какая сторона "своя" зависит от w/l.
    """
    if pd.isna(raw_value):
        return {
            "result_raw": None,
            "team_score": None,
            "opponent_score": None,
            "round_diff": None,
            "result_parse_status": "empty",
        }

    if hasattr(raw_value, "year"):  # datetime/Timestamp — Excel-порча счёта
        return {
            "result_raw": str(raw_value),
            "team_score": None,
            "opponent_score": None,
            "round_diff": None,
            "result_parse_status": "unparseable_date_like",
        }

    m = VALID_SCORE_RE.match(str(raw_value))
    if not m:
        return {
            "result_raw": str(raw_value),
            "team_score": None,
            "opponent_score": None,
            "round_diff": None,
            "result_parse_status": "other_unrecognized",
        }

    r1, r2 = int(m.group(1)), int(m.group(2))
    if win_loss == "W":
        team_score, opponent_score = r1, r2
    else:
        team_score, opponent_score = r2, r1

    return {
        "result_raw": str(raw_value),
        "team_score": team_score,
        "opponent_score": opponent_score,
        "round_diff": team_score - opponent_score,
        "result_parse_status": "ok",
    }


# ---------------------------------------------------------------------------
# Canonical map key
# ---------------------------------------------------------------------------

def canonical_map_key(tournament_number: int, rating_stage: str, map_number: int) -> str:
    """Уникальный ключ карты, устойчивый к сбросу mapId между турнирами и
    стадиями (см. docs/powerbi_logic_audit.md §2/§5 — mapId и matchId
    переиспользуются по турнирам, глобально не уникальны)."""
    return f"{tournament_number}-{rating_stage}-{map_number:03d}"
