"""
Прогоняет tests/dashboard_check.js (Node + минимальный DOM-шим,
tests/dom_shim.js) — реальное исполнение dashboard/js/app.js с синтетическими
данными, без сборки dashboard.html и без браузера. Проверяет поведенческие
требования Audit v3 (секция 7 задания): удаление графиков Overview,
позиционирование Nc/Nt, отсутствие колонки ADR coverage, CTA выбора игрока,
легенду A/C/N/T и форматирование подписей карт.

Требует Node.js в PATH. Если Node недоступен — тест skip, а не fail (это не
core Python pipeline, а фронтенд-смоук).
"""

import shutil
import subprocess

import pytest

from config import ROOT_DIR

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js не найден в PATH")


def test_dashboard_frontend_behavior():
    result = subprocess.run(
        ["node", "tests/dashboard_check.js"],
        cwd=ROOT_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, (
        f"dashboard_check.js провалился:\n{result.stdout}\n{result.stderr}"
    )
    assert "FAIL" not in result.stdout
