import pandas as pd
import pytest

from config import TEAM_ALIASES_REVIEW_PATH
from io_utils import (
    APPLICABLE_ALIAS_STATUSES,
    canonical_map_key,
    load_team_alias_map,
    parse_rating_code,
    parse_result,
)


class TestParseRatingCode:
    def test_number_then_stage(self):
        r = parse_rating_code("8a")
        assert r == {"rating_code_raw": "8a", "tournament_number": 8, "rating_stage": "a", "code_format": "number_stage"}

    def test_all_four_stages(self):
        for stage in ["a", "c", "n", "t"]:
            r = parse_rating_code(f"3{stage}")
            assert r["tournament_number"] == 3
            assert r["rating_stage"] == stage
            assert r["code_format"] == "number_stage"

    def test_legacy_stage_then_number_not_treated_as_stage(self):
        r = parse_rating_code("t8")
        assert r["tournament_number"] == 8
        assert r["rating_stage"] is None
        assert r["code_format"] == "stage_number_legacy"

    def test_double_digit_tournament_number(self):
        r = parse_rating_code("10t")
        assert r["tournament_number"] == 10
        assert r["rating_stage"] == "t"

    def test_unrecognized(self):
        r = parse_rating_code("???")
        assert r["tournament_number"] is None
        assert r["code_format"] == "unrecognized"


class TestParseResult:
    def test_valid_score_winner(self):
        r = parse_result("19-17", "W")
        assert r["result_parse_status"] == "ok"
        assert r["team_score"] == 19
        assert r["opponent_score"] == 17
        assert r["round_diff"] == 2

    def test_valid_score_loser_flips_perspective(self):
        r = parse_result("19-17", "L")
        assert r["team_score"] == 17
        assert r["opponent_score"] == 19
        assert r["round_diff"] == -2

    def test_excel_corrupted_date_not_recovered(self):
        r = parse_result(pd.Timestamp("2022-07-16"), "W")
        assert r["result_parse_status"] == "unparseable_date_like"
        assert r["team_score"] is None
        assert r["opponent_score"] is None
        assert r["round_diff"] is None
        assert r["result_raw"] is not None  # raw сохранён, не выброшен

    def test_empty(self):
        r = parse_result(None, "W")
        assert r["result_parse_status"] == "empty"

    def test_other_unrecognized(self):
        r = parse_result("forfeit", "W")
        assert r["result_parse_status"] == "other_unrecognized"
        assert r["team_score"] is None


@pytest.mark.skipif(
    not TEAM_ALIASES_REVIEW_PATH.exists(),
    reason="config/team_aliases_review.csv не публикуется (строится из data/raw/) — тест требует приватных данных",
)
class TestTeamAliases:
    def test_only_applicable_statuses_are_merged(self, root_dir):
        alias_map, pending = load_team_alias_map()
        df = pd.read_csv(root_dir / "config" / "team_aliases_review.csv", encoding="utf-8-sig")
        for _, row in df.iterrows():
            if row["review_status"] in APPLICABLE_ALIAS_STATUSES:
                assert alias_map[row["raw_team_name"]] == row["proposed_canonical_team"]
            else:
                assert alias_map[row["raw_team_name"]] == row["raw_team_name"]
                assert row["raw_team_name"] in pending

    def test_needs_review_pairs_not_auto_merged(self):
        alias_map, pending = load_team_alias_map()
        assert "RunChickenRun" in pending or "RunChikenRun" in pending
        assert alias_map.get("RunChikenRun") == "RunChikenRun"


class TestCanonicalMapKey:
    def test_format_is_zero_padded_and_stable(self):
        assert canonical_map_key(8, "t", 5) == "8-t-005"
        assert canonical_map_key(8, "t", 25) == "8-t-025"

    def test_distinguishes_stage_and_tournament(self):
        a = canonical_map_key(1, "c", 3)
        b = canonical_map_key(1, "t", 3)
        c = canonical_map_key(2, "c", 3)
        assert len({a, b, c}) == 3
