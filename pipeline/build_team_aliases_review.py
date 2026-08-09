"""
build_team_aliases_review.py
==============================

Собирает все написания названий команд из `data/raw/` и строит
`config/team_aliases_review.csv` — кандидат-таблицу для РУЧНОЙ проверки.

Ничего не утверждается автоматически, кроме тривиальных случаев (регистр,
пробелы). Скрипт только читает `data/raw/`, ничего не изменяет.

Запуск (из корня репозитория):

    python pipeline/build_team_aliases_review.py
"""

from __future__ import annotations

import csv
import difflib
import re
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT_DIR / "data" / "raw"
OUT_PATH = ROOT_DIR / "config" / "team_aliases_review.csv"

DECORATIVE_CHARS_RE = re.compile(
    "[☀-➿\U0001F300-\U0001FAFF←-⇿✀-➿]"
)

FUZZY_THRESHOLD = 0.90

# Числовые "заглушки" (Команда 1, Команда 2-1, Team 3, ...) — это разные команды,
# похожие текстуально из-за общего шаблона имени, а не варианты написания одной
# и той же команды. Фаззи-склейку для них отключаем: пусть требуется точное
# совпадение (регистр/пробелы), иначе получим ложную склейку "Команда 1" со
# всеми "Команда N-M".
GENERIC_PLACEHOLDER_RE = re.compile(r"^(команда|team)\s*\d+", re.IGNORECASE)


def is_generic_placeholder(name: str) -> bool:
    return bool(GENERIC_PLACEHOLDER_RE.match(str(name).strip()))


def normalize_basic(name: str) -> str:
    return re.sub(r"\s+", " ", str(name)).strip().casefold()


def strip_decorative(name: str) -> str:
    stripped = DECORATIVE_CHARS_RE.sub("", str(name))
    return re.sub(r"\s+", " ", stripped).strip()


def collect_occurrences() -> list[dict]:
    occurrences = []

    res = pd.read_excel(RAW_DIR / "cs_farpost_tournament_results.xlsx")
    for name, cnt in res["team"].dropna().astype(str).value_counts().items():
        occurrences.append({"raw_team_name": name, "source_file": "cs_farpost_tournament_results.xlsx", "occurrence_count": int(cnt)})

    score = pd.read_excel(RAW_DIR / "cs_farpost_tournament_score.xlsx")
    for name, cnt in score["team"].dropna().astype(str).value_counts().items():
        occurrences.append({"raw_team_name": name, "source_file": "cs_farpost_tournament_score.xlsx", "occurrence_count": int(cnt)})

    players = pd.read_excel(RAW_DIR / "cs_farpost_players.xlsx")
    team_cols = [c for c in players.columns if re.fullmatch(r"t\d+_team", c)]
    for col in team_cols:
        for name, cnt in players[col].dropna().astype(str).value_counts().items():
            occurrences.append({"raw_team_name": name, "source_file": f"cs_farpost_players.xlsx:{col}", "occurrence_count": int(cnt)})

    return occurrences


def build_clusters(names: list[str]) -> dict[str, list[str]]:
    """Группирует по normalize_basic, затем расширяет декоративным стриппингом."""
    basic_groups: dict[str, list[str]] = {}
    for n in names:
        basic_groups.setdefault(normalize_basic(n), []).append(n)

    decorative_groups: dict[str, list[str]] = {}
    for key, variants in basic_groups.items():
        decor_key = normalize_basic(strip_decorative(variants[0]))
        decorative_groups.setdefault(decor_key, []).extend(variants)

    return decorative_groups


def fuzzy_pairs(cluster_keys: list[str]) -> list[tuple[str, str, float]]:
    pairs = []
    for i in range(len(cluster_keys)):
        for j in range(i + 1, len(cluster_keys)):
            a, b = cluster_keys[i], cluster_keys[j]
            if is_generic_placeholder(a) and is_generic_placeholder(b):
                continue
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            if ratio >= FUZZY_THRESHOLD:
                pairs.append((a, b, ratio))
    return pairs


def main() -> int:
    occurrences = collect_occurrences()
    names = sorted({o["raw_team_name"] for o in occurrences})

    name_sources: dict[str, list[str]] = {}
    name_total_count: Counter = Counter()
    for o in occurrences:
        name_sources.setdefault(o["raw_team_name"], []).append(o["source_file"])
        name_total_count[o["raw_team_name"]] += o["occurrence_count"]

    clusters = build_clusters(names)
    cluster_keys = list(clusters.keys())
    fuzzy = fuzzy_pairs(cluster_keys)

    # union cluster keys connected by fuzzy match into one review-group id
    parent = {k: k for k in cluster_keys}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    fuzzy_lookup = {}
    for a, b, ratio in fuzzy:
        union(a, b)
        fuzzy_lookup[(a, b)] = ratio
        fuzzy_lookup[(b, a)] = ratio

    review_groups: dict[str, list[str]] = {}
    for key in cluster_keys:
        root = find(key)
        review_groups.setdefault(root, []).extend(clusters[key])

    rows = []
    for root, variant_list in review_groups.items():
        variant_list = sorted(set(variant_list))
        counts = {v: name_total_count[v] for v in variant_list}
        canonical = max(counts, key=counts.get)
        is_multi_variant = len(variant_list) > 1

        # классификация на уровне ГРУППЫ (как варианты соотносятся друг с другом),
        # а не отдельно взятого варианта — иначе опечатка типа RunChickenRun/
        # RunChikenRun ошибочно попадёт в "просто разный регистр".
        basic_keys = {normalize_basic(v) for v in variant_list}
        decorated_keys = {normalize_basic(strip_decorative(v)) for v in variant_list}
        if not is_multi_variant:
            group_method, group_confidence, group_status = "unique_no_variant", "high", "no_variant_found"
        elif len(basic_keys) == 1:
            group_method, group_confidence, group_status = "exact_casefold_or_whitespace", "high", "auto_approved"
        elif len(decorated_keys) == 1:
            group_method, group_confidence, group_status = "decorative_symbol_stripping", "medium", "needs_review"
        else:
            group_method, group_confidence, group_status = "fuzzy_similarity", "low", "needs_review"

        for v in variant_list:
            basic_key = normalize_basic(v)
            method, confidence, status = group_method, group_confidence, group_status

            rows.append({
                "raw_team_name": v,
                "normalized_text": basic_key,
                "proposed_canonical_team": canonical,
                "source_file": " | ".join(sorted(set(name_sources[v]))),
                "occurrence_count": name_total_count[v],
                "match_method": method,
                "confidence": confidence,
                "review_status": status,
                "notes": (
                    f"cluster of {len(variant_list)} variant(s): {', '.join(variant_list)}"
                    if is_multi_variant else "no other written variant found for this normalized value"
                ),
            })

    rows.sort(key=lambda r: (r["proposed_canonical_team"], -r["occurrence_count"]))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "raw_team_name", "normalized_text", "proposed_canonical_team", "source_file",
            "occurrence_count", "match_method", "confidence", "review_status", "notes",
        ])
        writer.writeheader()
        writer.writerows(rows)

    n_needs_review = sum(1 for r in rows if r["review_status"] == "needs_review")
    print(f"[build_team_aliases_review] {len(rows)} строк записано в {OUT_PATH.relative_to(ROOT_DIR)}")
    print(f"[build_team_aliases_review] needs_review: {n_needs_review}, auto_approved: "
          f"{sum(1 for r in rows if r['review_status']=='auto_approved')}, "
          f"no_variant_found: {sum(1 for r in rows if r['review_status']=='no_variant_found')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
