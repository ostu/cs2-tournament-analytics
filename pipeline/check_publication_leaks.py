"""
check_publication_leaks.py
============================

Автоматическая проверка утечек PII перед публикацией статического дашборда.
См. `docs/privacy_and_publication.md`.

Берёт все известные реальные ники/идентификаторы игроков из `data/raw/` (и,
если есть, приватную таблицу псевдонимов из `data/private/`) и ищет их
буквальное присутствие во всех файлах указанной директории будущей
публичной сборки — HTML, встроенный JSON, атрибуты DOM, URL, имена файлов.
Скрипт завершается с ненулевым кодом, если найдено хотя бы одно совпадение.

Запуск (из корня репозитория):

    python pipeline/check_publication_leaks.py <путь_к_публичной_сборке>
    python pipeline/check_publication_leaks.py --self-test   (тест механизма на временных данных)

Скрипт только читает `data/raw/`, `data/private/` и указанную директорию
сборки — ничего не изменяет, сети не касается.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT_DIR / "data" / "raw"
PRIVATE_DIR = ROOT_DIR / "data" / "private"
DASHBOARD_DIR = ROOT_DIR / "dashboard"

# бинарные/крупные форматы, которые не нужно грепать как текст
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".woff", ".woff2", ".ttf", ".ico"}

MIN_TOKEN_LEN = 3  # избегаем ложных срабатываний на однобуквенных/двубуквенных значениях


def collect_forbidden_tokens() -> set[str]:
    tokens: set[str] = set()

    players_path = RAW_DIR / "cs_farpost_players.xlsx"
    if players_path.exists():
        players = pd.read_excel(players_path)
        pii_columns = [
            "id", "player_id", "steam_name", "steam_account", "fio", "city", "department",
            "discord_name", "faceit_name", "faceit_link", "another_names",
        ]
        for col in pii_columns:
            if col in players.columns:
                for v in players[col].dropna().astype(str):
                    v = v.strip()
                    if len(v) >= MIN_TOKEN_LEN:
                        tokens.add(v)
        # ники команд-в-разрезе-турниров тоже не должны палить состав напрямую через team+id
        team_cols = [c for c in players.columns if re.fullmatch(r"t\d+_team", c)]
        # (названия команд не запрещены сами по себе — публично можно показывать команды;
        #  запрещены только персональные идентификаторы игрока, поэтому team_cols не добавляем)
        _ = team_cols

    # id из остальных таблиц (на случай новых игроков, которых нет в players.xlsx)
    for fname, col in [
        ("cs_farpost_tournament_results.xlsx", "id"),
        ("cs_farpost_elo_changes.xlsx", "id"),
        ("cs_farpost_elo_norm.xlsx", "id"),
    ]:
        p = RAW_DIR / fname
        if p.exists():
            df = pd.read_excel(p)
            if col in df.columns:
                tokens.update(v.strip() for v in df[col].dropna().astype(str) if len(v.strip()) >= MIN_TOKEN_LEN)

    aliases_path = PRIVATE_DIR / "player_aliases.csv"
    if aliases_path.exists():
        aliases = pd.read_csv(aliases_path, encoding="utf-8-sig")
        if "private_player_key" in aliases.columns:
            tokens.update(v.strip() for v in aliases["private_player_key"].dropna().astype(str) if len(v.strip()) >= MIN_TOKEN_LEN)

    return tokens


def iter_text_files(build_dir: Path):
    """build_dir может быть директорией (data/public/) ИЛИ одним файлом
    (dashboard.html) — оба варианта используются в pipeline."""
    if build_dir.is_file():
        if build_dir.suffix.lower() not in SKIP_SUFFIXES:
            yield build_dir
        return
    for p in build_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() not in SKIP_SUFFIXES:
            yield p


def load_known_static_assets() -> list[str]:
    """dashboard.html инлайнит dashboard/css/styles.css и dashboard/js/app.js
    ВЕРБАТИМ (build_dashboard.py). Это статический код приложения, не данные
    — он не может "утечь", он и так публикуется как есть. Короткие реальные
    private-токены (например, id из трёх букв) иногда совпадают с обычными
    словами/идентификаторами кода — без этого
    вычитания такие совпадения дают ложную блокировку публикации. Вычитаем
    точный текст известных статических файлов перед поиском по СОДЕРЖИМОМУ
    (не по именам файлов) — если сам dashboard/js/app.js когда-нибудь будет
    содержать реальные данные, это уже не пройдёт другие проверки (эти файлы
    не генерируются из data/raw)."""
    assets = []
    for rel in ("css/styles.css", "js/app.js"):
        p = DASHBOARD_DIR / rel
        if p.exists():
            assets.append(p.read_text(encoding="utf-8"))
    return assets


def scan_build_dir(build_dir: Path, tokens: set[str]) -> list[dict]:
    findings = []
    token_patterns = {t: re.compile(re.escape(t), re.IGNORECASE) for t in tokens}
    static_assets = load_known_static_assets()

    for path in iter_text_files(build_dir):
        # 1. имя файла
        for token, pat in token_patterns.items():
            if pat.search(path.name):
                findings.append({"file": str(path), "location": "filename", "token": token})

        # 2. содержимое файла (HTML/JSON/JS/CSS/логи и т.п.)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (UnicodeDecodeError, OSError):
            continue

        for asset_text in static_assets:
            if asset_text in text:
                text = text.replace(asset_text, "")
        for token, pat in token_patterns.items():
            if pat.search(text):
                findings.append({"file": str(path), "location": "content", "token": token})

    return findings


def self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        build_dir = Path(tmp)
        clean_file = build_dir / "index.html"
        clean_file.write_text("<html><body>Team Alpha vs Team Beta, ADR 78.5</body></html>", encoding="utf-8")

        tokens = {"zz_fake_id", "zz_fake_nick", "very_real_steam_name_123"}
        findings_clean = scan_build_dir(build_dir, tokens)
        assert not findings_clean, f"self-test: ожидали 0 находок на чистом файле, получили {findings_clean}"

        leaky_file = build_dir / "player_zz_fake_id.html"
        leaky_file.write_text("<html><body>Player zz_fake_nick had 30 kills</body></html>", encoding="utf-8")
        findings_leak = scan_build_dir(build_dir, tokens)
        assert findings_leak, "self-test: ожидали находки на файле с реальными идентификаторами, получили 0"

        locations = {f["location"] for f in findings_leak}
        assert "filename" in locations and "content" in locations, (
            f"self-test: ожидали находки и в filename, и в content, получили {locations}"
        )
        print("[check_publication_leaks] self-test OK: механизм корректно ловит утечки в имени файла и в содержимом")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_dir", nargs="?", help="путь к директории публичной сборки для проверки")
    parser.add_argument("--self-test", action="store_true", help="прогнать самотест механизма на временных данных")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    if not args.build_dir:
        print("Использование: python pipeline/check_publication_leaks.py <путь_к_публичной_сборке>", file=sys.stderr)
        print("               python pipeline/check_publication_leaks.py --self-test", file=sys.stderr)
        return 2

    build_dir = Path(args.build_dir)
    if not build_dir.exists():
        print(f"[FATAL] директория сборки не найдена: {build_dir}", file=sys.stderr)
        return 2

    tokens = collect_forbidden_tokens()
    print(f"[check_publication_leaks] запрещённых токенов (реальные id/ники/PII): {len(tokens)}")

    if not tokens:
        # Без data/raw/ и data/private/ (их нет в публичном репозитории) список
        # известных идентификаторов пуст: скан формально проходит, но ничего не
        # проверяет. Код возврата остаётся 0, чтобы dashboard.html можно было
        # пересобрать из data/public/, но результат нельзя считать проверкой.
        print("[check_publication_leaks] ВНИМАНИЕ / WARNING: список известных идентификаторов пуст "
              "(нет data/raw/ и data/private/). Проверка НЕ ВЫПОЛНЕНА — "
              "the leak scan is a no-op without the private reference data.")
        return 0

    findings = scan_build_dir(build_dir, tokens)

    if findings:
        print(f"[check_publication_leaks] НАЙДЕНО {len(findings)} совпадений — публикация ЗАБЛОКИРОВАНА:")
        for f in findings[:200]:
            print(f"  - {f['file']} [{f['location']}] содержит запрещённый токен")
        return 1

    print("[check_publication_leaks] совпадений не найдено — сборка чистая")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
