"""
generate_player_aliases.py
============================

Строит/дополняет `data/private/player_aliases.csv` — приватную таблицу
соответствия реальных идентификаторов игроков стабильным случайным
псевдонимам. См. `docs/privacy_and_publication.md`.

Свойства:
  - таблица создаётся один раз; повторный запуск НЕ переназначает псевдонимы
    уже известным игрокам, только добавляет новых (идемпотентно);
  - псевдоним — случайный (secrets/random), не производная (не hash) от
    реального идентификатора — поэтому не восстанавливается по формуле;
  - `data/private/` в `.gitignore`, эта таблица никогда не публикуется и не
    попадает в git.

Запуск (из корня репозитория):

    python pipeline/generate_player_aliases.py

Скрипт только читает `data/raw/cs_farpost_players.xlsx` (список
`private_player_key` = колонка `id`) и читает/дополняет
`data/private/player_aliases.csv`. Ничего в `data/raw/` не меняется.
"""

from __future__ import annotations

import csv
import secrets
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT_DIR / "data" / "raw"
PRIVATE_DIR = ROOT_DIR / "data" / "private"
ALIASES_PATH = PRIVATE_DIR / "player_aliases.csv"

FIELDNAMES = ["private_player_key", "public_player_id", "public_nickname", "created_at"]

ADJECTIVES = [
    "Quiet", "Rapid", "Bold", "Calm", "Sharp", "Steady", "Swift", "Silent", "Bright", "Cold",
    "Fierce", "Lucky", "Nimble", "Stormy", "Golden", "Iron", "Crimson", "Shadow", "Frozen", "Wild",
    "Loyal", "Clever", "Brave", "Sly", "Grim", "Vivid", "Nomad", "Solar", "Lunar", "Ember",
    "Marble", "Velvet", "Amber", "Cobalt", "Onyx", "Copper", "Jade", "Ivory", "Slate", "Cedar",
]
NOUNS = [
    "Falcon", "Wolf", "Panther", "Hawk", "Fox", "Bear", "Otter", "Lynx", "Raven", "Viper",
    "Tiger", "Heron", "Badger", "Puma", "Owl", "Cobra", "Eagle", "Wren", "Bison", "Marten",
    "Sable", "Kestrel", "Jackal", "Orca", "Stag", "Gull", "Mantis", "Falcon2", "Ferret", "Osprey",
    "Coyote", "Ibis", "Lynx2", "Marmot", "Petrel", "Serval", "Skink", "Teal", "Vole", "Weasel",
]


def existing_aliases() -> dict[str, dict]:
    if not ALIASES_PATH.exists():
        return {}
    with ALIASES_PATH.open("r", encoding="utf-8-sig", newline="") as fh:
        return {row["private_player_key"]: row for row in csv.DictReader(fh)}


def generate_nickname(used: set[str]) -> str:
    for _ in range(2000):
        candidate = f"{secrets.choice(ADJECTIVES)} {secrets.choice(NOUNS)}"
        if candidate not in used:
            return candidate
    # исчерпали комбинации словаря (не ожидается при текущем размере ростера) — добавляем суффикс
    while True:
        candidate = f"{secrets.choice(ADJECTIVES)} {secrets.choice(NOUNS)} {secrets.randbelow(9000) + 1000}"
        if candidate not in used:
            return candidate


def generate_public_player_id(used: set[str]) -> str:
    for _ in range(2000):
        candidate = "P-" + secrets.token_hex(4)
        if candidate not in used:
            return candidate
    raise RuntimeError("не удалось сгенерировать уникальный public_player_id")


def main() -> int:
    players = pd.read_excel(RAW_DIR / "cs_farpost_players.xlsx")
    real_keys = sorted(players["id"].dropna().astype(str).unique().tolist())

    rows = existing_aliases()
    used_ids = {r["public_player_id"] for r in rows.values()}
    used_nicknames = {r["public_nickname"] for r in rows.values()}

    now_iso = datetime.now(timezone.utc).isoformat()
    added = 0
    for key in real_keys:
        if key in rows:
            continue
        new_id = generate_public_player_id(used_ids)
        new_nick = generate_nickname(used_nicknames)
        used_ids.add(new_id)
        used_nicknames.add(new_nick)
        rows[key] = {
            "private_player_key": key,
            "public_player_id": new_id,
            "public_nickname": new_nick,
            "created_at": now_iso,
        }
        added += 1

    # проверка на коллизии перед записью
    ids_seen = [r["public_player_id"] for r in rows.values()]
    nicks_seen = [r["public_nickname"] for r in rows.values()]
    assert len(ids_seen) == len(set(ids_seen)), "коллизия public_player_id — не должна происходить"
    assert len(nicks_seen) == len(set(nicks_seen)), "коллизия public_nickname — не должна происходить"

    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    with ALIASES_PATH.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow(rows[key])

    print(f"[generate_player_aliases] всего игроков: {len(rows)}, добавлено новых: {added}")
    print(f"[generate_player_aliases] файл: {ALIASES_PATH.relative_to(ROOT_DIR)} (не публикуется, в .gitignore)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
