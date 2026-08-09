"""
Тесты на исправления Audit v3 (dashboard bugfix): классификация
permanent/calibration команд, SUM-агрегация ростера Nc/Nt, каскадные
данные для фронтенд-фильтров. Интеграционные — читают реальные артефакты
pipeline, skip если pipeline не собран.
"""

import pandas as pd
import pytest

from config import PROCESSED_DIR, PUBLIC_DIR

pytestmark = pytest.mark.skipif(
    not (PROCESSED_DIR / "dim_team.parquet").exists(),
    reason="pipeline ещё не собран",
)


@pytest.fixture(scope="module")
def dim_team():
    return pd.read_parquet(PROCESSED_DIR / "dim_team.parquet")


@pytest.fixture(scope="module")
def fact_player_map():
    return pd.read_parquet(PROCESSED_DIR / "fact_player_map.parquet")


class TestTeamTypeClassification:
    def test_every_team_has_a_valid_type(self, dim_team):
        assert set(dim_team["team_type"].unique()) <= {
            "permanent_tournament_team", "calibration_team", "unknown"
        }

    def test_classification_matches_phase_signal(self, dim_team, fact_player_map):
        phase_sets = fact_player_map.groupby("team_key")["tournament_phase"].apply(lambda s: set(s.unique()))
        for _, row in dim_team.iterrows():
            stages = phase_sets.get(row["team_key"])
            if stages == {"t"}:
                assert row["team_type"] == "permanent_tournament_team", row["team_key"]
            elif stages == {"c"}:
                assert row["team_type"] == "calibration_team", row["team_key"]

    def test_no_team_is_both_permanent_and_calibration(self, dim_team):
        permanent = set(dim_team[dim_team["team_type"] == "permanent_tournament_team"]["team_key"])
        calibration = set(dim_team[dim_team["team_type"] == "calibration_team"]["team_key"])
        assert permanent & calibration == set()

    def test_calibration_teams_excluded_from_fact_team_map_based_marts(self, dim_team):
        calibration = set(dim_team[dim_team["team_type"] == "calibration_team"]["team_key"])
        team_summary = pd.read_parquet(PROCESSED_DIR / "mart_team_summary.parquet")
        assert set(team_summary["team_key"]) & calibration == set()

        matrix = pd.read_parquet(PROCESSED_DIR / "mart_team_map_matrix.parquet")
        assert set(matrix["team_key"]) & calibration == set()

        comparison = pd.read_parquet(PROCESSED_DIR / "mart_team_rating_comparison.parquet")
        assert set(comparison["team_key"]) & calibration == set()

    def test_calibration_events_still_present_for_players(self):
        events = pd.read_parquet(PROCESSED_DIR / "fact_player_rating_event.parquet")
        assert (events["event_type"] == "calibration_game").sum() > 0
        assert (events["event_type"] == "calibration_final").sum() > 0


class TestFinalRosterResolution:
    def test_no_player_assigned_to_two_teams_in_same_tournament(self):
        import sys as _sys
        _sys.path.insert(0, str(PROCESSED_DIR.parents[1] / "pipeline"))
        from build_marts import resolve_final_roster

        player_map = pd.read_parquet(PROCESSED_DIR / "fact_player_map.parquet")
        roster = resolve_final_roster(player_map)
        dupes = roster.duplicated(subset=["private_player_key", "tournament_number"]).sum()
        assert dupes == 0

    def test_roster_uses_last_map_when_player_switched_team_mid_tournament(self):
        import sys as _sys
        _sys.path.insert(0, str(PROCESSED_DIR.parents[1] / "pipeline"))
        from build_marts import resolve_final_roster

        player_map = pd.read_parquet(PROCESSED_DIR / "fact_player_map.parquet")
        t_only = player_map[player_map["tournament_phase"] == "t"]
        multi = t_only.groupby(["private_player_key", "tournament_number"])["team_key"].nunique()
        churned = multi[multi > 1]
        assert len(churned) > 0, "ожидались известные случаи смены команды в течение турнира"

        pid, tnum = churned.index[0]
        expected_last_team = (
            t_only[(t_only["private_player_key"] == pid) & (t_only["tournament_number"] == tnum)]
            .sort_values("map_number")
            .iloc[-1]["team_key"]
        )
        roster = resolve_final_roster(player_map)
        actual = roster[(roster["private_player_key"] == pid) & (roster["tournament_number"] == tnum)]
        assert len(actual) == 1
        assert actual.iloc[0]["team_key"] == expected_last_team


class TestTeamRatingSumFormula:
    def test_incomplete_coverage_yields_null_not_partial_sum(self):
        import sys as _sys
        _sys.path.insert(0, str(PROCESSED_DIR.parents[1] / "pipeline"))
        from build_marts import team_roster_rating_sum

        rating_events = pd.DataFrame({
            "private_player_key": ["p1", "p2"],
            "tournament_number": [1, 1],
            "event_type": ["tournament_final", "tournament_final"],
            "rating_after": [1000.0, 900.0],
        })
        roster = pd.DataFrame({
            "private_player_key": ["p1", "p2", "p3"],  # p3 не имеет rating-события
            "tournament_number": [1, 1, 1],
            "team_key": ["Alpha", "Alpha", "Alpha"],
        })
        result = team_roster_rating_sum(rating_events, roster, "tournament_final")
        row = result[(result["team_key"] == "Alpha") & (result["tournament_number"] == 1)].iloc[0]
        assert pd.isna(row["rating_sum"]), "неполное покрытие должно давать null, а не частичную сумму 1900"

    def test_complete_coverage_yields_sum_not_average(self):
        import sys as _sys
        _sys.path.insert(0, str(PROCESSED_DIR.parents[1] / "pipeline"))
        from build_marts import team_roster_rating_sum

        rating_events = pd.DataFrame({
            "private_player_key": ["p1", "p2"],
            "tournament_number": [1, 1],
            "event_type": ["tournament_final", "tournament_final"],
            "rating_after": [1000.0, 900.0],
        })
        roster = pd.DataFrame({
            "private_player_key": ["p1", "p2"],
            "tournament_number": [1, 1],
            "team_key": ["Alpha", "Alpha"],
        })
        result = team_roster_rating_sum(rating_events, roster, "tournament_final")
        row = result[(result["team_key"] == "Alpha") & (result["tournament_number"] == 1)].iloc[0]
        assert row["rating_sum"] == 1900.0  # SUM, не среднее (450)


@pytest.mark.skipif(not PUBLIC_DIR.exists(), reason="data/public/ ещё не собран")
class TestPublicFilterData:
    def test_metadata_teams_excludes_calibration(self, dim_team):
        import json
        meta = json.loads((PUBLIC_DIR / "metadata.json").read_text(encoding="utf-8"))
        calibration = set(dim_team[dim_team["team_type"] == "calibration_team"]["team_key"])
        assert set(meta["teams"]) & calibration == set()
        for teams in meta["teams_by_tournament"].values():
            assert set(teams) & calibration == set()

    def test_teams_by_tournament_matches_team_maps(self):
        import json
        meta = json.loads((PUBLIC_DIR / "metadata.json").read_text(encoding="utf-8"))
        team_maps = json.loads((PUBLIC_DIR / "team_maps.json").read_text(encoding="utf-8"))
        for tnum_str, teams in meta["teams_by_tournament"].items():
            tnum = int(tnum_str)
            actual = {r["team_id"] for r in team_maps if r["tournament_number"] == tnum}
            assert set(teams) == actual, f"турнир {tnum}"

    def test_team_rating_comparison_has_no_calibration_and_full_coverage_somewhere(self):
        import json
        comparison = json.loads((PUBLIC_DIR / "team_rating_comparison.json").read_text(encoding="utf-8"))
        assert len(comparison) > 0
        assert any(row["rating_coverage_complete"] for row in comparison)
