import json

import pytest

from config import PUBLIC_DIR, DASHBOARD_OUTPUT_PATH
from check_publication_leaks import (
    collect_forbidden_tokens,
    scan_build_dir,
    self_test as leak_self_test,
)
import generate_player_aliases as gpa

FORBIDDEN_KEYS = {
    "private_player_key", "id", "player_id", "steam_name", "steam_account",
    "fio", "city", "department", "discord_name", "faceit_name", "faceit_link",
    "another_names",
}

# Список запрещённых токенов строится из data/raw/ и data/private/, которых нет
# в публичном репозитории. Скан с пустым списком ничего не доказывает, поэтому
# такие тесты пропускаются (skip), а не проходят "вхолостую".
NO_TOKENS_REASON = "нет data/raw/ и data/private/ — список известных идентификаторов пуст, скан был бы пустым"


class TestLeakDetectionMechanism:
    def test_self_test_passes(self):
        assert leak_self_test() == 0

    def test_scan_build_dir_catches_planted_token(self, tmp_path):
        (tmp_path / "leaky.html").write_text("hello RealPlayerName123 world", encoding="utf-8")
        findings = scan_build_dir(tmp_path, {"RealPlayerName123"})
        assert len(findings) >= 1

    def test_scan_build_dir_clean_when_no_token_present(self, tmp_path):
        (tmp_path / "clean.html").write_text("hello world, team Alpha won", encoding="utf-8")
        findings = scan_build_dir(tmp_path, {"RealPlayerName123"})
        assert findings == []


class TestPseudonymGeneration:
    def test_nickname_avoids_collision_with_used_set(self):
        used = {f"{a} {n}" for a in gpa.ADJECTIVES[:5] for n in gpa.NOUNS}
        nickname = gpa.generate_nickname(used)
        assert nickname not in used

    def test_public_player_id_avoids_collision(self):
        used = set()
        for _ in range(50):
            new_id = gpa.generate_public_player_id(used)
            assert new_id not in used
            used.add(new_id)
        assert len(used) == 50  # 50 последовательных вызовов не дали коллизий

    def test_public_player_id_forced_collision_is_avoided(self, monkeypatch):
        # эмулируем коллизию: token_hex всегда возвращает один и тот же байт
        # первый раз, затем другой — generate_public_player_id должен
        # обнаружить совпадение с уже занятым id и сгенерировать другой.
        import secrets as secrets_module

        calls = {"n": 0}
        real_token_hex = secrets_module.token_hex

        def fake_token_hex(n):
            calls["n"] += 1
            return "aaaaaaaa" if calls["n"] == 1 else real_token_hex(n)

        monkeypatch.setattr(gpa.secrets, "token_hex", fake_token_hex)
        used = {"P-aaaaaaaa"}
        new_id = gpa.generate_public_player_id(used)
        assert new_id != "P-aaaaaaaa"
        assert new_id not in used

    def test_public_player_id_format(self):
        pid = gpa.generate_public_player_id(set())
        assert pid.startswith("P-")
        assert len(pid) == len("P-") + 8


@pytest.mark.skipif(not PUBLIC_DIR.exists(), reason="data/public/ ещё не собран")
class TestPublicPrivateSeparation:
    def test_no_forbidden_columns_in_any_public_json(self):
        for f in PUBLIC_DIR.glob("*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            rows = data if isinstance(data, list) else [data]
            for row in rows[:5]:
                if isinstance(row, dict):
                    overlap = FORBIDDEN_KEYS & set(row.keys())
                    assert not overlap, f"{f.name} содержит запрещённые поля: {overlap}"

    def test_no_player_aliases_file_in_public(self):
        assert list(PUBLIC_DIR.rglob("*player_aliases*")) == []

    def test_public_data_passes_leak_check(self):
        tokens = collect_forbidden_tokens()
        if not tokens:
            pytest.skip(NO_TOKENS_REASON)
        findings = scan_build_dir(PUBLIC_DIR, tokens)
        assert findings == [], f"утечки в data/public/: {findings[:5]}"


@pytest.mark.skipif(not DASHBOARD_OUTPUT_PATH.exists(), reason="dashboard.html ещё не собран")
class TestDashboardPrivacy:
    def test_dashboard_passes_leak_check(self):
        # scan_build_dir (не наивный substring-поиск) — учитывает, что
        # dashboard.html инлайнит статический код dashboard/js/app.js и
        # dashboard/css/styles.css, в котором короткие реальные id могут
        # случайно совпасть с обычными словами кода — см. check_publication_leaks.load_known_static_assets.
        tokens = collect_forbidden_tokens()
        if not tokens:
            pytest.skip(NO_TOKENS_REASON)
        findings = scan_build_dir(DASHBOARD_OUTPUT_PATH, tokens)
        assert findings == [], f"утечки в dashboard.html: {findings[:5]}"
