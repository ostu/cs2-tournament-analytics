"""
Интеграционные тесты — читают РЕАЛЬНЫЕ артефакты, собранные pipeline'ом
(data/processed/*.parquet). Если pipeline ещё не запускался в этой рабочей
копии, тесты пропускаются (skip), а не падают — они не пересобирают данные
сами (это делает `python pipeline/run_pipeline.ps1` / отдельные build_*).
"""

import pandas as pd
import pytest

from config import PROCESSED_DIR, INTERIM_DIR

pytestmark = pytest.mark.skipif(
    not (PROCESSED_DIR / "fact_player_rating_event.parquet").exists(),
    reason="pipeline ещё не собран — запустите python pipeline/run_pipeline.ps1 (или отдельные build_*.py)",
)


@pytest.fixture(scope="module")
def rating_events():
    return pd.read_parquet(PROCESSED_DIR / "fact_player_rating_event.parquet")


@pytest.fixture(scope="module")
def fact_player_map():
    return pd.read_parquet(PROCESSED_DIR / "fact_player_map.parquet")


@pytest.fixture(scope="module")
def fact_team_map():
    return pd.read_parquet(PROCESSED_DIR / "fact_team_map.parquet")


class TestRatingSumInvariant:
    def test_na_sum_equals_player_count_times_1000(self, rating_events):
        na = rating_events[rating_events["event_type"] == "normalized_entry"]
        for n, grp in na.groupby("tournament_number"):
            target = len(grp) * 1000
            assert abs(grp["rating_after"].sum() - target) < 1.0, f"турнир {n}"

    def test_nn_sum_equals_player_count_times_1000(self, rating_events):
        nn = rating_events[rating_events["event_type"] == "blended_normalized"]
        assert len(nn) > 0, "ожидался хотя бы один турнир со стадией Nn (турнир 7)"
        for n, grp in nn.groupby("tournament_number"):
            target = len(grp) * 1000
            assert abs(grp["rating_after"].sum() - target) < 1.0, f"турнир {n}"

    def test_rating_after_equals_before_plus_change(self, rating_events):
        sub = rating_events.dropna(subset=["rating_before", "rating_change", "rating_after"])
        assert len(sub) > 0
        mismatch = (sub["rating_after"] - (sub["rating_before"] + sub["rating_change"])).abs() > 0.5
        assert mismatch.sum() == 0


class TestDuplicateFactDetection:
    def test_no_duplicate_rating_events(self, rating_events):
        key = ["private_player_key", "tournament_number", "rating_stage", "event_type", "event_sequence"]
        assert rating_events.duplicated(subset=key).sum() == 0

    def test_no_duplicate_player_map_rows(self, fact_player_map):
        key = ["private_player_key", "tournament_number", "tournament_phase", "map_number"]
        assert fact_player_map.duplicated(subset=key).sum() == 0

    def test_no_duplicate_team_map_rows(self, fact_team_map):
        key = ["tournament_number", "tournament_phase", "map_number", "team_key"]
        assert fact_team_map.duplicated(subset=key).sum() == 0


class TestMapIdCorrection:
    def test_join_coverage_meets_confirmed_threshold(self):
        results = pd.read_parquet(INTERIM_DIR / "tournament_results_normalized.parquet")
        changes = pd.read_parquet(INTERIM_DIR / "elo_changes_normalized.parquet")
        merged = results.merge(
            changes[["id", "tournament_number", "rating_stage", "map_number_canonical", "elo_diff"]],
            on=["id", "tournament_number", "rating_stage", "map_number_canonical"], how="left",
        )
        coverage = merged["elo_diff"].notna().mean()
        # подтверждённый Audit v2 уровень — 99.96%; допуск на единственную
        # известную несматченную строку (5t/mapId10/Player-Gamma, идентификатор анонимизирован)
        assert coverage >= 0.995

    def test_elo_changes_mapid_is_0_based_before_correction(self):
        changes_raw_min = pd.read_parquet(INTERIM_DIR / "elo_changes_normalized.parquet").groupby(
            "tournament_number"
        )["map_id_raw"].min()
        assert (changes_raw_min == 0).all()


class TestTeamRoundDiffDirect:
    def test_round_diff_matches_score_difference_directly(self, fact_team_map):
        valid = fact_team_map[fact_team_map["result_parse_status"] == "ok"]
        assert len(valid) > 0
        mismatch = (valid["round_diff"] - (valid["team_score"] - valid["opponent_score"])).abs() > 0
        assert mismatch.sum() == 0

    def test_non_standard_roster_maps_still_get_correct_round_diff(self, fact_player_map, fact_team_map):
        non_standard_player_rows = fact_player_map[
            fact_player_map["data_quality_flags"].apply(lambda fl: "non_standard_roster_size" in fl)
        ]
        assert len(non_standard_player_rows) > 0, "ожидались известные 5 карт с составом из 4 игроков"
        keys = non_standard_player_rows[["tournament_number", "tournament_phase", "map_number", "team_key"]].drop_duplicates()
        merged = keys.merge(fact_team_map, on=["tournament_number", "tournament_phase", "map_number", "team_key"], how="left")
        # round_diff НЕ должен зависеть от players_count (легаси PP/5 сломался бы именно здесь)
        assert merged["players_count"].eq(4).all()
        ok_rows = merged[merged["result_parse_status"] == "ok"]
        if len(ok_rows):
            assert (ok_rows["round_diff"] == ok_rows["team_score"] - ok_rows["opponent_score"]).all()


class TestCanonicalAvgKills:
    def test_canonical_denominator_uses_composite_key_not_bare_mapid(self, fact_player_map):
        legacy_denom = fact_player_map["map_number"].nunique()
        canonical_denom = fact_player_map[["tournament_number", "tournament_phase", "map_number"]].drop_duplicates().shape[0]
        # регресс-проверка на саму находку Audit v2: легаси-знаменатель занижен
        assert canonical_denom > legacy_denom


class TestAvailabilityAwareADR:
    def test_adr_is_null_exactly_when_not_tracked(self, fact_player_map):
        violation = fact_player_map[(~fact_player_map["adr_tracked"]) & fact_player_map["adr"].notna()]
        assert len(violation) == 0

    def test_tracked_and_untracked_both_present(self, fact_player_map):
        assert fact_player_map["adr_tracked"].any()
        assert (~fact_player_map["adr_tracked"]).any()


class TestRatingHistoryMapName:
    """map_name в mart_player_rating_history (секция 5.2 задания) — джойн
    через fact_player_map.map_key -> dim_map.canonical_map_name."""

    @pytest.fixture(scope="class")
    @classmethod
    def rating_history(cls):
        return pd.read_parquet(PROCESSED_DIR / "mart_player_rating_history.parquet")

    def test_map_events_have_map_name(self, rating_history):
        map_events = rating_history[rating_history["event_type"].isin(["tournament_map_change", "calibration_game"])]
        assert len(map_events) > 0
        assert map_events["map_name"].notna().all()

    def test_non_map_events_have_no_map_name(self, rating_history):
        non_map_events = rating_history[~rating_history["event_type"].isin(["tournament_map_change", "calibration_game"])]
        assert len(non_map_events) > 0
        assert non_map_events["map_name"].isna().all()


class TestRosterSizeQualityFlag:
    """roster_size_not_five (секция 2.3 задания) — предупреждение, не блокер."""

    def test_flag_present_where_roster_size_not_five(self):
        team_summary = pd.read_parquet(PROCESSED_DIR / "mart_team_summary.parquet")
        mismatched = team_summary[team_summary["players_count"] != 5]
        assert len(mismatched) > 0, "ожидались известные аномалии состава (см. docs/unresolved_questions.md §6)"
        assert mismatched["data_quality_flags"].apply(lambda fl: "roster_size_not_five" in fl).all()

    def test_flag_absent_where_roster_size_is_five(self):
        team_summary = pd.read_parquet(PROCESSED_DIR / "mart_team_summary.parquet")
        matched = team_summary[team_summary["players_count"] == 5]
        assert len(matched) > 0
        assert not matched["data_quality_flags"].apply(lambda fl: "roster_size_not_five" in fl).any()
