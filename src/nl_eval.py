"""Evaluate the natural-language layer on the audit questions + harder ones.

For each question: the plan (tools called), whether it matches the expected
tools, the tool statuses, and whether every number in the answer is grounded
in the tool envelopes (``grounding.check_grounding``). Writes
``models/nl_eval_report.json``.

The first 24 questions are the list in ``readme.md`` section 10 (the few that
refer back to a previous question are written out in full, with one
follow-up tested through ``context``). The rest need several tools or
disambiguation.
"""

import argparse
import json
import time
from pathlib import Path

try:
    from src.nl_agent import answer
except ImportError:  # pragma: no cover
    from nl_agent import answer

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "models" / "nl_eval_report.json"

AUDIT_QUESTIONS = [
    ("Who would win between the Celtics and Lakers?", {"predict_matchup"}),
    ("What would the model have predicted for the Celtics vs Lakers on 2026-04-12?", {"predict_matchup"}),
    ("What drives the model's pick?", {"validation_report"}),
    ("How good is the prediction model overall?", {"validation_report"}),
    ("What is the head-to-head record between the Celtics and Lakers?", {"head_to_head"}),
    ("Who is projected to be the league's best and worst team in 2025?", {"simulate_season"}),
    ("What are OKC's projected wins and playoff odds?", {"team_projection"}),
    ("Who is projected to get the East's #1 seed?", {"simulate_season"}),
    ("What is the projected playoff field?", {"simulate_season"}),
    ("What is Boston's probability of making the direct playoffs?", {"team_projection"}),
    ("What are the Knicks' exact seed probabilities?", {"team_projection"}),
    ("Does the simulator actually predict real seasons well?", {"validation_report"}),
    ("What is the league-average projected win total?", {"simulate_season"}),
    ("What was Boston's actual 2025 record?", {"team_record"}),
    ("What is the numeric team id for the Lakers?", {"resolve_team_name"}),
    ("What tables are in the database and are they populated?", {"describe_database"}),
    ("What do the raw data files look like?", {"describe_raw_files"}),
    ("Which features matter most to the model?", {"validation_report"}),
    ("What is the model's calibration error?", {"validation_report"}),
    ("How do all the candidate models compare?", {"validation_report"}),
    ("What is player 203507's descriptive impact estimate?", {"player_impact"}),
    ("What does the model say about a Celtics vs Lakers game, and what is player 203507's impact diagnostic?",
     {"player_scenario"}),
    ("Is the roster-change file data/raw/roster_change_events_valid.csv valid, and what does it contain?",
     {"validate_roster_file"}),
    ("What tools does the analytical engine expose?", {"list_tools"}),
]

HARD_QUESTIONS = [
    ("Who is most likely to win the 2025-26 title, and how reliable are the model's title odds?",
     {"playoff_odds", "validation_report"}),
    ("What is the predicted score of Nuggets vs Suns, and who wins?", {"predict_matchup", "predict_margin"}),
    ("Compare the Knicks and Celtics: 2025 records, head-to-head, and who is favored if the Knicks host the Celtics?",
     {"team_record", "head_to_head", "predict_matchup"}),
    ("As of January 15, 2026, what did the rest-of-season projection look like for the Spurs?",
     {"project_rest_of_season"}),
    ("Who did the Philadelphia 76ers add this offseason, and how strong are teams right now?",
     {"team_roster", "team_strength"}),
    ("How many points per game did Stephen Curry average in 2015-16?", {"player_season_stats"}),
    ("What were Michael Jordan's per-game stats in 1992-93?", {"player_season_stats"}),
    ("What were the Thunder's title odds when the 2024-25 playoffs started, and did they win?", {"playoff_odds"}),
    ("Is the database up to date?", {"data_status"}),
    ("What are Stephen Curry's impact diagnostic and his 2024-25 per-game stats?",
     {"player_impact", "player_season_stats"}),
    ("How would the 1993 Chicago Bulls change if they had 2016 Steph Curry instead of B.J. Armstrong?",
     {"simulate_era_swap"}),
]

# Written after the planner was tuned on the two lists above, and scored once
# before any further change, to measure how it generalizes to new wording.
HELD_OUT_QUESTIONS = [
    ("Which team wins if the Bucks play at the Heat?", {"predict_matchup"}),
    ("Give me the Lakers' 2024 regular season record.", {"team_record"}),
    ("How often has Boston beaten New York all-time?", {"head_to_head"}),
    ("What's the chance Denver makes the top six in 2025?", {"team_projection"}),
    ("Show me the projected standings for 2024.", {"simulate_season"}),
    ("What is Nikola Jokic averaging in 2025-26?", {"player_season_stats"}),
    ("Is the win-probability model well calibrated?", {"validation_report"}),
    ("Who are the title favorites for 2024-25?", {"playoff_odds"}),
    ("What changed on the Knicks roster this summer?", {"team_roster"}),
    ("Predict the final score: Warriors at Kings.", {"predict_margin"}),
    ("How strong is every team at the moment?", {"team_strength"}),
    ("Which schema tables exist?", {"describe_database"}),
    ("How did the 2025 playoff bracket odds look for the Knicks?", {"playoff_odds"}),
    ("What is the Celtics' Elo rating?", {"team_elo_rating"}),
    ("How has the Thunder played lately?", {"team_form"}),
]

# Second held-out set: written after the fixes prompted by the first one,
# scored once, and not used for any further tuning.
HELD_OUT_V2_QUESTIONS = [
    ("Who's favored when the Celtics visit the Knicks?", {"predict_matchup"}),
    ("How many games did the Pistons win in 2025-26?", {"team_record"}),
    ("Did Boston or Philadelphia win more of their meetings in 2024?", {"head_to_head"}),
    ("What seed will Houston probably get in 2025?", {"team_projection"}),
    ("Which teams are expected to make the playoffs in 2023?", {"simulate_season"}),
    ("What were Luka Doncic's numbers in 2023-24?", {"player_season_stats"}),
    ("Which features does the win model rely on most?", {"validation_report"}),
    ("Who was the favorite to win the 2023-24 championship?", {"playoff_odds"}),
    ("Who joined the Lakers since last season?", {"team_roster"}),
    ("By how many points should the Nuggets beat the Jazz?", {"predict_margin"}),
    ("What does Denver's recent form look like?", {"team_form"}),
    ("How accurate is the margin model?", {"validation_report"}),
]

FOLLOW_UP = {
    "first": "Who is favored in Celtics vs Lakers?",
    "second": "What would the model have predicted for that matchup on 2026-04-12?",
    "expected": {"predict_matchup"},
}

AMBIGUOUS = [
    "How many points did Curry average in 2015?",   # surname only: several Currys
    "Who is favored in Los Angeles vs Denver?",       # 'Los Angeles' is two teams
    "What is the meaning of life?",                   # unsupported
]


def evaluate(mode="deterministic"):
    rows = []
    for group, questions in (("audit", AUDIT_QUESTIONS), ("hard", HARD_QUESTIONS),
                             ("held_out", HELD_OUT_QUESTIONS), ("held_out_v2", HELD_OUT_V2_QUESTIONS)):
        for question, expected in questions:
            started = time.time()
            result = answer(question, mode=mode)
            tools = {step["tool"] for step in result["plan"]}
            rows.append({
                "group": group,
                "question": question,
                "expected_tools": sorted(expected),
                "tools": sorted(tools),
                "routing_correct": expected <= tools,
                "extra_tools": sorted(tools - expected),
                "status": result["status"],
                "grounded": result["grounding"]["grounded"],
                "numbers_checked": result["grounding"]["numbers_checked"],
                "unsupported_numbers": result["grounding"]["unsupported"],
                "seconds": round(time.time() - started, 2),
                "answer": result["answer"],
            })
    first = answer(FOLLOW_UP["first"], mode=mode)
    second = answer(FOLLOW_UP["second"], mode=mode, context=first["context"])
    follow_up_tools = {step["tool"] for step in second["plan"]}
    follow_up = {
        "questions": [FOLLOW_UP["first"], FOLLOW_UP["second"]],
        "tools": sorted(follow_up_tools),
        "routing_correct": FOLLOW_UP["expected"] <= follow_up_tools,
        "used_context_teams": [step["parameters"] for step in second["plan"]],
        "status": second["status"],
        "grounded": second["grounding"]["grounded"],
        "answer": second["answer"],
    }
    ambiguous = []
    for question in AMBIGUOUS:
        result = answer(question, mode=mode)
        ambiguous.append({
            "question": question,
            "status": result["status"],
            "declined_or_clarified": result["status"] == "error",
            "answer": result["answer"],
        })

    def share(key, group=None):
        selected = [row for row in rows if group is None or row["group"] == group]
        return sum(1 for row in selected if row[key]) / max(len(selected), 1)

    summary = {
        "mode": mode,
        "questions": len(rows),
        "audit_routing_correct": share("routing_correct", "audit"),
        "hard_routing_correct": share("routing_correct", "hard"),
        "held_out_routing_correct": share("routing_correct", "held_out"),
        "held_out_v2_routing_correct": share("routing_correct", "held_out_v2"),
        "wrong_tool_answers": sum(
            1 for row in rows if not row["routing_correct"] and row["status"] != "error"
        ),
        "success_status": sum(1 for row in rows if row["status"] == "success") / len(rows),
        "grounded": share("grounded"),
        "numbers_checked": sum(row["numbers_checked"] for row in rows),
        "follow_up_routing_correct": follow_up["routing_correct"],
        "ambiguous_declined": sum(1 for row in ambiguous if row["declined_or_clarified"]) / len(ambiguous),
    }
    return {"summary": summary, "rows": rows, "follow_up": follow_up, "ambiguous": ambiguous}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate the NL layer on the audit questions.")
    parser.add_argument("--mode", default="deterministic", choices=["deterministic", "llm"])
    args = parser.parse_args(argv)
    report = evaluate(args.mode)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    for row in report["rows"]:
        flag = "OK " if row["routing_correct"] and row["status"] == "success" and row["grounded"] else "!! "
        print(f"{flag}[{row['group']}] {row['question'][:70]:70s} -> {row['tools']} {row['status']} "
              f"grounded={row['grounded']} ({row['numbers_checked']} nums) {row['seconds']}s")
        if row["unsupported_numbers"]:
            print("     unsupported:", row["unsupported_numbers"])
    print("follow-up:", report["follow_up"]["tools"], report["follow_up"]["status"])
    for row in report["ambiguous"]:
        print("ambiguous:", row["question"], "->", row["answer"][:140])
    print(json.dumps(report["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
