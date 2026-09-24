"""Every number in an answer must be traceable to the tool results behind it."""

from src.grounding import check_grounding, compact_json


ENVELOPE = {
    "status": "success",
    "data": {
        "prediction": {"home_win_probability": 0.692998, "away_win_probability": 0.307002},
        "record": {"wins": 61, "losses": 21},
        "as_of": "2026-04-12",
        "mean_wins": 57.14,
    },
    "limitations": ["Holdout MAE 10.57 points vs 11.66 for a constant home edge."],
}


def test_rounded_values_and_percentages_are_supported():
    result = check_grounding(
        "Home 69.3%, away 30.7%; projected 57.1 wins; record 61-21 as of 2026-04-12.",
        [ENVELOPE],
    )
    assert result["grounded"], result["unsupported"]
    assert result["numbers_checked"] >= 5


def test_numbers_in_limitations_count_as_sources():
    assert check_grounding("Margin error is about 10.57 points.", [ENVELOPE])["grounded"]


def test_invented_numbers_are_flagged():
    result = check_grounding("Boston wins 72% of the time and scores 118 points.", [ENVELOPE])
    assert not result["grounded"]
    assert "72%" in result["unsupported"]
    assert "118" in result["unsupported"]


def test_rounding_tolerance_follows_the_answers_precision():
    assert check_grounding("about 69%", [ENVELOPE])["grounded"]
    assert not check_grounding("69.9%", [ENVELOPE])["grounded"]


def test_dates_and_season_labels():
    assert not check_grounding("as of 2025-01-01", [ENVELOPE])["grounded"]
    assert check_grounding("the 2025-26 season", [{"season": 2025}])["grounded"]
    assert not check_grounding("the 2019-20 season", [{"season": 2025}])["grounded"]


def test_small_integers_and_interval_labels_are_not_data():
    text = "The top 6 seeds, best-of-7 series, 80% range and the 5th-95th percentile."
    assert check_grounding(text, [{}])["grounded"]


def test_question_numbers_are_allowed():
    assert check_grounding("In 2015 he averaged 30.1.", [{"ppg": 30.1}], question="in 2015?")["grounded"]


def test_compact_json_truncates_and_says_so():
    text = compact_json({"rows": list(range(10000))}, limit=100)
    assert len(text) < 150
    assert text.endswith('"[truncated]"')
