import pandas as pd
import pytest

from src.roster_change_data import (
    load_roster_change_events,
    load_nba_player_movement_source,
    main,
    normalize_nba_player_movement_records,
    summarize_nba_player_movement_normalization,
    summarize_roster_change_events,
    validate_roster_change_events,
)


def _events():
    return pd.DataFrame(
        [
            {
                "event_id": "b",
                "event_timestamp": "2024-10-02T15:00:00-04:00",
                "team_id": 20,
                "person_id": 200,
                "change_type": "REMOVE",
                "source": "independent source",
                "source_url": "https://example.test/event-b",
            },
            {
                "event_id": "a",
                "event_timestamp": "2024-10-01T15:00:00-04:00",
                "team_id": 10,
                "person_id": 100,
                "change_type": "add",
                "source": "independent source",
                "source_url": "https://example.test/event-a",
            },
        ]
    )


def _player_movement_rows():
    return pd.DataFrame(
        [
            {
                "transaction_type": "Signing",
                "transaction_date": "2024-07-01T00:00:00",
                "transaction_description": "Boston Celtics signed guard Sample One.",
                "team_id": 1610612738,
                "team_slug": "celtics",
                "player_id": 100,
                "player_slug": "sample-one",
                "additional_sort": 0,
                "groupsort": "Signing 1",
            },
            {
                "transaction_type": "Waive",
                "transaction_date": "2024-07-02T00:00:00",
                "transaction_description": "Boston Celtics waived guard Sample One.",
                "team_id": 1610612738,
                "team_slug": "celtics",
                "player_id": 100,
                "player_slug": "sample-one",
                "additional_sort": 0,
                "groupsort": "Waive 1",
            },
            {
                "transaction_type": "AwardOnWaivers",
                "transaction_date": "2024-07-03T00:00:00",
                "transaction_description": "Los Angeles Lakers claimed forward Sample Two off waivers.",
                "team_id": 1610612747,
                "team_slug": "lakers",
                "player_id": 200,
                "player_slug": "sample-two",
                "additional_sort": 0,
                "groupsort": "AwardedOnWaivers 1",
            },
            {
                "transaction_type": "Trade",
                "transaction_date": "2024-07-04T00:00:00",
                "transaction_description": "Los Angeles Lakers received forward Sample Three from Boston Celtics.",
                "team_id": 1610612747,
                "team_slug": "lakers",
                "player_id": 300,
                "player_slug": "sample-three",
                "additional_sort": 1610612738,
                "groupsort": "Trade 1",
            },
            {
                "transaction_type": "Trade",
                "transaction_date": "2024-07-04T00:00:00",
                "transaction_description": "Los Angeles Lakers received draft consideration from Boston Celtics.",
                "team_id": 1610612747,
                "team_slug": "lakers",
                "player_id": 0,
                "player_slug": None,
                "additional_sort": 1610612738,
                "groupsort": "Trade 1",
            },
            {
                "transaction_type": "ContractConverted",
                "transaction_date": "2024-07-05T00:00:00",
                "transaction_description": "Boston Celtics converted the contract of guard Sample Four to an NBA Contract.",
                "team_id": 1610612738,
                "team_slug": "celtics",
                "player_id": 400,
                "player_slug": "sample-four",
                "additional_sort": 0,
                "groupsort": "ContractConverted 1",
            },
        ]
    )


def test_roster_events_are_normalized_and_time_ordered():
    result = validate_roster_change_events(_events())

    assert result["event_id"].tolist() == ["a", "b"]
    assert result["change_type"].tolist() == ["add", "remove"]
    assert result["event_timestamp"].dt.tz is not None
    assert result["team_id"].dtype == "int64"


def test_loader_reads_csv_through_the_same_validation_path(tmp_path):
    path = tmp_path / "roster_changes.csv"
    _events().to_csv(path, index=False)

    result = load_roster_change_events(path)

    assert len(result) == 2
    assert result["event_id"].tolist() == ["a", "b"]


def test_summary_reports_counts_and_time_window():
    summary = summarize_roster_change_events(_events())

    assert summary["event_count"] == 2
    assert summary["add_count"] == 1
    assert summary["remove_count"] == 1
    assert summary["team_count"] == 2
    assert summary["person_count"] == 2
    assert summary["first_event_timestamp"].startswith("2024-10-01T19:00:00+00:00")
    assert summary["last_event_timestamp"].startswith("2024-10-02T19:00:00+00:00")


def test_main_validate_mode_prints_json_summary(tmp_path, capsys):
    path = tmp_path / "roster_changes.csv"
    _events().to_csv(path, index=False)

    exit_code = main(["--validate", str(path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"event_count": 2' in captured.out
    assert '"add_count": 1' in captured.out


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda frame: frame.assign(event_id=["a", "a"]), "event_id"),
        (lambda frame: frame.assign(change_type="trade"), "change_type"),
        (lambda frame: frame.assign(source_url="not-a-url"), "source_url"),
        (lambda frame: frame.assign(event_timestamp="not-a-time"), "event_timestamp"),
    ],
)
def test_invalid_source_data_fails_explicitly(mutator, message):
    with pytest.raises(ValueError, match=message):
        validate_roster_change_events(mutator(_events()))


def test_naive_timestamps_are_accepted_and_normalized_to_utc():
    events = _events().assign(event_timestamp="2024-10-01 15:00:00")

    result = validate_roster_change_events(events)

    assert result.loc[0, "event_timestamp"].tzinfo is not None


def test_player_movement_rows_are_normalized_into_high_confidence_events_with_audit():
    events, audit = normalize_nba_player_movement_records(_player_movement_rows())

    assert len(events) == 5
    assert events["change_type"].tolist() == ["add", "remove", "add", "add", "remove"]
    assert set(events["confidence_level"]) == {"high"}
    assert set(events["reconstruction_rule"]) == {
        "signing_add",
        "waive_remove",
        "award_on_waivers_add",
        "trade_destination_add",
        "trade_origin_remove",
    }
    trade_remove = events[events["reconstruction_rule"] == "trade_origin_remove"].iloc[0]
    trade_add = events[events["reconstruction_rule"] == "trade_destination_add"].iloc[0]
    assert trade_remove["team_id"] == 1610612738
    assert trade_add["team_id"] == 1610612747
    assert trade_add["source_groupsort"] == "Trade 1"

    excluded = audit[audit["reconstruction_status"] == "excluded"]
    assert len(excluded) == 2
    assert excluded["normalized_event_count"].tolist() == [0, 0]
    assert "non-player consideration" in excluded.iloc[0]["exclusion_reason"]
    assert "contract conversion" in excluded.iloc[1]["exclusion_reason"]


@pytest.mark.requires_data
def test_player_movement_summary_matches_real_raw_source_counts_and_date_coverage():
    source = load_nba_player_movement_source()
    events, audit = normalize_nba_player_movement_records(source)
    summary = summarize_nba_player_movement_normalization(events, audit)

    assert summary["raw_row_count"] == 9746
    assert summary["normalized_source_row_count"] == 9102
    assert summary["excluded_source_row_count"] == 644
    assert summary["raw_transaction_type_counts"] == {
        "Signing": 4608,
        "Waive": 3152,
        "Trade": 1788,
        "ContractConverted": 128,
        "AwardOnWaivers": 70,
    }
    assert summary["high_confidence_event_count"] == 10374
    assert summary["add_count"] == 5950
    assert summary["remove_count"] == 4424
    assert summary["raw_unique_transaction_dates"] == 1979
    assert summary["event_unique_transaction_dates"] == 1978
    assert summary["missing_raw_transaction_dates_from_events"] == ["2023-09-14"]
    assert summary["excluded_rows_by_reason"] == {
        "trade row has no player_id, so it only documents non-player consideration": 516,
        "contract conversion changes contract status but does not prove a roster add/remove transition": 128,
    }


def test_roster_events_include_confidence_metadata_and_high_confidence_summary():
    events = pd.DataFrame(
        [
            {
                "event_id": "high-1",
                "event_timestamp": "2024-10-01T15:00:00-04:00",
                "team_id": 10,
                "person_id": 100,
                "change_type": "add",
                "source": "independent source",
                "source_url": "https://example.test/high-1",
                "confidence_level": "high",
                "reconstruction_rule": "signing_add",
            },
            {
                "event_id": "low-1",
                "event_timestamp": "2024-10-02T15:00:00-04:00",
                "team_id": 20,
                "person_id": 200,
                "change_type": "add",
                "source": "independent source",
                "source_url": "https://example.test/low-1",
                "confidence_level": "low",
                "reconstruction_rule": "fallback_add",
            },
        ]
    )

    validated = validate_roster_change_events(events)
    summary = summarize_roster_change_events(validated)

    assert validated["confidence_level"].tolist() == ["high", "low"]
    assert summary["confidence_level_counts"] == {"high": 1, "low": 1}
    assert summary["high_confidence_event_count"] == 1


@pytest.mark.requires_data
def test_basketball_reference_add_event_is_resolved_to_repo_ids():
    from bs4 import BeautifulSoup

    from src.roster_change_data import _transaction_paragraph_to_events, load_player_registry, load_team_registry

    players = load_player_registry()
    team_histories = load_team_registry()
    paragraph = BeautifulSoup(
        '<p>The <a data-attr-to="BOS" href="/teams/BOS/1990.html">Boston Celtics</a> signed <a href="/players/a/abdelal01.html">Alaa Abdelnaby</a> to a contract extension.</p>',
        "html.parser",
    ).find("p")

    result = _transaction_paragraph_to_events(paragraph, 1990, players=players, team_histories=team_histories)

    assert result and result[0]["change_type"] == "add"
    assert result[0]["team_id"] == 1610612738
    assert result[0]["person_id"] == 76001


@pytest.mark.requires_data
def test_basketball_reference_trade_event_is_split_into_two_roster_moves():
    from bs4 import BeautifulSoup

    from src.roster_change_data import _transaction_paragraph_to_events, load_player_registry, load_team_registry

    players = load_player_registry()
    team_histories = load_team_registry()
    paragraph = BeautifulSoup(
        '<p>The <a data-attr-from="BOS" href="/teams/BOS/1990.html">Boston Celtics</a> traded <a href="/players/a/abdelal01.html">Alaa Abdelnaby</a> to the <a data-attr-to="LAL" href="/teams/LAL/1990.html">Los Angeles Lakers</a> for <a href="/players/a/abernte01.html">Tom Abernethy</a>.</p>',
        "html.parser",
    ).find("p")

    result = _transaction_paragraph_to_events(paragraph, 1990, players=players, team_histories=team_histories)

    assert len(result) == 4
    assert {item["change_type"] for item in result} == {"remove", "add"}
    assert {item["person_id"] for item in result} == {76001, 76005}
