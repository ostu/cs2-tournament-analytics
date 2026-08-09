"""
build_dashboard.py
====================

Собирает единый self-contained `dashboard.html` из:
  - `pipeline/dashboard_template.html` (Jinja2)
  - `dashboard/css/styles.css`
  - `dashboard/js/app.js`
  - `data/public/*.json` (единственный источник данных для дашборда)
  - `config/dashboard_config.json`

После сборки прогоняет `pipeline/check_publication_leaks.py` на самом
`dashboard.html` — при обнаружении утечки файл НЕ считается готовым к публикации
(сообщение об ошибке, ненулевой код возврата), хотя файл на диске остаётся
(чтобы можно было изучить причину).

Запуск (из корня репозитория):

    python pipeline/build_dashboard.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (  # noqa: E402
    DASHBOARD_CONFIG_PATH,
    DASHBOARD_DIR,
    DASHBOARD_OUTPUT_PATH,
    DASHBOARD_TEMPLATE_PATH,
    PIPELINE_DIR,
    PUBLIC_DIR,
    ROOT_DIR,
)

PUBLIC_JSON_FILES = [
    "players", "player_rating_history", "player_maps",
    "teams", "team_rating_history", "team_maps", "team_map_matrix", "team_rating_comparison", "metadata",
]


def load_app_data() -> dict:
    data = {}
    for name in PUBLIC_JSON_FILES:
        with (PUBLIC_DIR / f"{name}.json").open("r", encoding="utf-8") as fh:
            data[name] = json.load(fh)
    return data


def render_dashboard() -> str:
    css = (DASHBOARD_DIR / "css" / "styles.css").read_text(encoding="utf-8")
    js = (DASHBOARD_DIR / "js" / "app.js").read_text(encoding="utf-8")
    dashboard_config = json.loads(DASHBOARD_CONFIG_PATH.read_text(encoding="utf-8"))
    app_data = load_app_data()

    env = Environment(loader=FileSystemLoader(str(PIPELINE_DIR)), autoescape=False)
    template = env.get_template(DASHBOARD_TEMPLATE_PATH.name)

    return template.render(
        report_title=app_data["metadata"]["report_title"],
        css=css,
        js=js,
        dashboard_config=json.dumps(dashboard_config, ensure_ascii=False),
        app_data=json.dumps(app_data, ensure_ascii=False),
    )


def run_leak_check(target: Path) -> int:
    import os

    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT_DIR / "pipeline" / "check_publication_leaks.py"), str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def main() -> int:
    print("[build_dashboard] рендерю dashboard.html из шаблона ...")
    html = render_dashboard()
    DASHBOARD_OUTPUT_PATH.write_text(html, encoding="utf-8")
    size_kb = DASHBOARD_OUTPUT_PATH.stat().st_size / 1024
    print(f"[build_dashboard] Записано: {DASHBOARD_OUTPUT_PATH.relative_to(ROOT_DIR)} ({size_kb:.0f} КБ)")

    print("[build_dashboard] проверяю dashboard.html leak checker'ом ...")
    leak_code = run_leak_check(DASHBOARD_OUTPUT_PATH)
    if leak_code != 0:
        print("[build_dashboard] ОШИБКА: найдены совпадения с приватными идентификаторами в dashboard.html!",
              file=sys.stderr)
        return leak_code

    print("[build_dashboard] Готово, утечек не найдено.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
