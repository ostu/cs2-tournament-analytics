"""
Тесты диагностики аномалий состава команд (pipeline/audit_team_rosters.py,
секция 2 задания). Проверяют, что скрипт только ДИАГНОСТИРУЕТ (не меняет
resolve_final_roster()/рейтинг) и что отчёт имеет ожидаемую структуру.
"""

import json

import pandas as pd
import pytest

from config import PROCESSED_DIR, VALIDATION_DIR

pytestmark = pytest.mark.skipif(
    not (PROCESSED_DIR / "fact_player_map.parquet").exists(),
    reason="pipeline ещё не собран",
)

CAUSE_TAXONOMY = {
    "real_four_player_map", "substitution", "mid_tournament_transfer",
    "missing_player_map_rows", "conflicting_source_roster",
    "final_map_roster_artifact", "unknown",
}


@pytest.fixture(scope="module")
def anomalies_report():
    from audit_team_rosters import analyze

    reports, summary = analyze()
    return reports, summary


class TestRosterAnomalyDiagnosis:
    def test_finds_at_least_one_anomaly(self, anomalies_report):
        reports, summary = anomalies_report
        assert summary["anomaly_count"] == len(reports)
        assert len(reports) > 0, "ожидались известные случаи roster_size != 5 (см. docs/unresolved_questions.md §6)"

    def test_roster_size_4_or_6_cases_present(self, anomalies_report):
        reports, _ = anomalies_report
        sizes = {r["roster_size"] for r in reports}
        assert sizes & {4, 6}, "ожидались известные случаи с составом 4 или 6 игроков"

    def test_all_causes_from_taxonomy(self, anomalies_report):
        reports, _ = anomalies_report
        for r in reports:
            assert r["probable_cause"] in CAUSE_TAXONOMY, r["probable_cause"]

    def test_does_not_mutate_resolve_final_roster(self, anomalies_report):
        """Диагностика не должна менять состав, используемый для Nc/Nt —
        сравниваем roster_size из отчёта с players_count/roster в mart-е."""
        from build_marts import resolve_final_roster

        reports, _ = anomalies_report
        player_map = pd.read_parquet(PROCESSED_DIR / "fact_player_map.parquet")
        roster = resolve_final_roster(player_map)
        roster_counts = roster.groupby(["team_key", "tournament_number"])["private_player_key"].nunique()

        for r in reports:
            key = (r["team"], float(r["tournament_number"]))
            if key not in roster_counts.index:
                key = (r["team"], r["tournament_number"])
            assert int(roster_counts.loc[key]) == r["roster_size"]

    def test_each_anomaly_has_required_fields(self, anomalies_report):
        reports, _ = anomalies_report
        required = {
            "tournament_number", "team", "roster_size", "final_roster_used_for_nc_nt",
            "all_players_this_tournament", "last_physical_map_number", "last_physical_map_roster",
            "modal_roster", "source_roster_players_xlsx", "has_mid_tournament_transfer",
            "has_internal_gap_in_player_map", "players", "probable_cause",
        }
        for r in reports:
            assert required <= set(r.keys())


@pytest.mark.skipif(
    not (VALIDATION_DIR / "team_roster_anomalies.json").exists(),
    reason="pipeline/audit_team_rosters.py ещё не запускался",
)
class TestRosterAnomalyReportFiles:
    def test_json_report_structure(self):
        data = json.loads((VALIDATION_DIR / "team_roster_anomalies.json").read_text(encoding="utf-8"))
        assert "summary" in data and "anomalies" in data
        assert data["summary"]["anomaly_count"] == len(data["anomalies"])
        assert set(data["summary"]["by_cause"].keys()) == CAUSE_TAXONOMY

    def test_markdown_report_exists_and_nonempty(self):
        md_path = VALIDATION_DIR / "team_roster_anomalies.md"
        text = md_path.read_text(encoding="utf-8")
        assert "roster_size" in text
        assert "ДИАГНОСТИКА" in text

    def test_quality_flag_present_in_team_summary(self):
        ts_path = PROCESSED_DIR / "mart_team_summary.parquet"
        if not ts_path.exists():
            pytest.skip("mart_team_summary не собран")
        ts = pd.read_parquet(ts_path)
        flagged = ts[ts["data_quality_flags"].apply(lambda fl: "roster_size_not_five" in fl)]
        assert len(flagged) > 0
