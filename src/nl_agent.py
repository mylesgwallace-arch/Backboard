"""Natural-language orchestration over the deterministic tool registry.

A question goes in; a plan of tool calls comes out; every call goes through
``src.tools.execute_tool``; the answer is written from the returned envelopes
only, in four labeled parts -- **Facts** (database records), **Model
output** (predictions and simulations), **How reliable** (stored validation
evidence) and **Uncertainty & limits** (the tools' own limitations and
confidence flags) -- and every number in it is checked against the envelopes
by ``src.grounding.check_grounding`` before it is returned.

Two planners share that contract:

* ``deterministic`` (default, always available): pattern-based detectors
  that may fire together, so one question can become several tool calls
  ("predicted score and who wins" -> ``predict_matchup`` + ``predict_margin``).
  Answers are rendered by per-tool templates, so they are grounded by
  construction; the grounding check verifies it.
* ``llm`` (opt-in: ``BACKBOARD_NL_MODE=llm`` or ``mode="llm"``): Claude
  chooses and sequences the tools through the Messages API tool-use loop and
  writes the explanation. Needs the ``anthropic`` package and credentials.
  Its answer is grounding-checked too; if it contains numbers the tools did
  not return it gets one chance to fix them, after which the unsupported
  numbers are listed with the answer. On any failure (no SDK, no
  credentials, API error, refusal) the deterministic planner answers
  instead and the result says so.
"""

import json
import os
import re
import sqlite3

import pandas as pd

try:
    from src.assistant import (
        DEFAULT_SEASON,
        extract_player_ids,
        extract_team_ids,
        load_team_labels,
        normalize_text,
    )
    from src.grounding import check_grounding, compact_json
    from src.tools import TOOLS, execute_tool, list_tools
except ImportError:  # pragma: no cover - direct-script support
    from assistant import (
        DEFAULT_SEASON,
        extract_player_ids,
        extract_team_ids,
        load_team_labels,
        normalize_text,
    )
    from grounding import check_grounding, compact_json
    from tools import TOOLS, execute_tool, list_tools


LATEST_POSTSEASON = 2025
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
WRITTEN_DATE = re.compile(
    r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.IGNORECASE,
)
SEASON_RANGE = re.compile(r"\b(19\d{2}|20\d{2})\s*[-/]\s*(\d{2})\b")
YEAR = re.compile(r"\b(19[4-9]\d|20\d{2})\b")
PERSON_ID = re.compile(r"\bplayer\s+(?:id\s+)?(\d{3,8})\b", re.IGNORECASE)
PATH_TOKEN = re.compile(r"((?:data|templates)[\\/][\w\-./\\]+\.csv)", re.IGNORECASE)

CATEGORY_OF_TOOL = {
    "team_record": "facts", "head_to_head": "facts", "resolve_team_name": "facts",
    "list_teams": "facts", "describe_database": "facts", "query_database": "facts",
    "resolve_player": "facts", "player_season_stats": "facts", "team_roster": "facts",
    "data_status": "facts", "describe_raw_files": "facts", "validate_roster_file": "facts",
    "list_tools": "facts", "team_form": "facts",
    "validation_report": "reliability",
}
SECTION_TITLES = {
    "facts": "Facts (from the database)",
    "model": "Model output (predictions and simulations, not facts)",
    "reliability": "How reliable (stored validation evidence)",
    "uncertainty": "Uncertainty & limits",
}


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_dates(question):
    """ISO and written dates ('January 15, 2026') as YYYY-MM-DD strings."""
    found = [(m.start(), m.group(1)) for m in ISO_DATE.finditer(question)]
    for match in WRITTEN_DATE.finditer(question):
        month = MONTHS[match.group(1).lower().rstrip(".")]
        try:
            stamp = pd.Timestamp(year=int(match.group(3)), month=month, day=int(match.group(2)))
        except ValueError:
            continue
        found.append((match.start(), str(stamp.date())))
    return [date for _, date in sorted(found)]


def extract_seasons(question):
    """Season start years mentioned ('2015-16' -> 2015; a bare year as is)."""
    text = ISO_DATE.sub(" ", question)
    text = WRITTEN_DATE.sub(" ", text)
    seasons = [int(match.group(1)) for match in SEASON_RANGE.finditer(text)]
    text = SEASON_RANGE.sub(" ", text)
    seasons += [int(match.group(1)) for match in YEAR.finditer(text)]
    return seasons


def season_of_date(date):
    stamp = pd.Timestamp(date)
    return stamp.year - (1 if stamp.month < 9 else 0)


def extract_person_ids(question):
    ids = [int(match.group(1)) for match in PERSON_ID.finditer(question)]
    return ids + [pid for pid in extract_player_ids(question) if pid not in ids]


def order_home_away(question, team_ids):
    """(home, away) from 'A at B', 'A hosts B', 'A visits B'; default first = home."""
    if len(team_ids) < 2:
        return None
    first, second = team_ids[0], team_ids[1]
    normalized = f" {normalize_text(question)} "
    if re.search(r"\bat\b|\bvisit(s|ing)?\b|\bon the road\b", normalized) and not re.search(
            r"\bhost(s|ing)?\b|\bhome\b", normalized):
        return second, first
    return first, second


# ---------------------------------------------------------------------------
# Deterministic planner
# ---------------------------------------------------------------------------

def _step(tool, parameters, focus=None):
    return {"tool": tool, "parameters": parameters, "focus": focus}


def _check_ambiguous_team_phrases(text, teams):
    """Raise when a phrase like 'Los Angeles' could be two teams and neither is named."""
    try:
        from src.assistant import load_team_mentions
    except ImportError:  # pragma: no cover
        from assistant import load_team_mentions
    mentions = load_team_mentions()
    for phrase, team_ids in mentions.items():
        if len(team_ids) > 1 and re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text):
            if not any(team in teams for team in team_ids):
                labels = load_team_labels()
                raise ValueError(
                    f"'{phrase.title()}' could mean " + " or ".join(labels.get(t, str(t)) for t in team_ids)
                    + "; please name the team."
                )


QUESTION_WORDS = {
    "who", "what", "how", "which", "when", "where", "why", "did", "does", "is", "are",
    "was", "were", "the", "and", "his", "her", "their", "in", "of", "for", "per", "by",
    "at", "if", "on", "to", "from", "with", "vs", "versus", "give", "show", "tell", "me",
    "can", "could", "would", "should", "will", "do", "whos", "whats", "hows", "nba",
    "predict", "compare", "list", "a", "an", "since", "as", "this", "that", "i",
}


def _players_from_partial_names(question, teams):
    """Resolve capitalized name fragments ('Curry', 'Steph Curry') or ask which one."""
    try:
        from src.player_lookup import find_players
    except ImportError:  # pragma: no cover
        from player_lookup import find_players
    labels = {normalize_text(label) for label in load_team_labels().values()}
    team_words = {word for label in labels for word in label.split()}
    candidates = re.findall(r"\b([A-Z][a-zA-Z'\.\-]+(?:\s+[A-Z][a-zA-Z'\.\-]+)*)", question)
    for phrase in candidates:
        words = [w for w in phrase.split()
                 if re.sub(r"[^a-z]", "", w.lower()) not in QUESTION_WORDS]
        words = [w for w in words if normalize_text(w).strip() not in team_words]
        # A lone short token is far more likely an ordinary word than a surname.
        if not words or (len(words) == 1 and len(words[0]) < 4):
            continue
        name = " ".join(words)
        result = find_players(name, limit=5)
        if result["match_count"] == 1:
            return [result["person_id"]]
        if result["match_count"] > 1:
            listed = "; ".join(
                f"{c['full_name']} ({c['from_year']}-{c['to_year'] or 'present'}, personId {c['person_id']})"
                for c in result["candidates"]
            )
            raise ValueError(
                f"'{name}' matches {result['match_count']} players: {listed}. Which one do you mean?"
            )
    return []


def plan_question(question, context=None):
    """Map a question to an ordered list of tool calls (deterministic).

    ``context`` (optional) is the previous answer's ``context`` block
    (teams/players/season/date) so follow-ups like "what about on
    2026-04-12?" or "that matchup" can reuse them. Raises ``ValueError`` with
    a clarifying message when the question is ambiguous or unsupported.
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("The question is empty.")
    text = " ".join(normalize_text(question).split())
    context = context or {}
    teams = extract_team_ids(question)
    _check_ambiguous_team_phrases(text, teams)
    referring_back = re.search(r"\b(that|same|those|this) (matchup|game|team|teams|player)\b|\bthem\b", text)
    if not teams and referring_back and context.get("teams"):
        teams = list(context["teams"])
    players = extract_person_ids(question)
    if not players and referring_back and context.get("players"):
        players = list(context["players"])
    if not players and re.search(r"averag|per game|\bppg\b|stats|points|rebounds|assists|impact|diagnostic",
                                 text):
        players = _players_from_partial_names(question, teams)
    dates = extract_dates(question)
    seasons = extract_seasons(question)
    season = seasons[0] if seasons else None
    date = dates[0] if dates else None
    steps = []

    def has(pattern):
        return re.search(pattern, text) is not None

    # --- meta / data questions -------------------------------------------
    if has(r"what tools|which tools|tools (does|do|are|can)|what can (you|the engine)|capabilit"):
        steps.append(_step("list_tools", {}))
    if has(r"up to date|how (current|fresh)|latest data|last game (in|played)|data (status|freshness)|is the (database|data) current"):
        steps.append(_step("data_status", {}))
    if has(r"\btables?\b") and has(r"database|\bdb\b|populated|schema|exist|available"):
        steps.append(_step("describe_database", {}))
    if has(r"raw (data|files?|csv)"):
        steps.append(_step("describe_raw_files", {}))
    path = PATH_TOKEN.search(question)
    if has(r"roster") and has(r"valid") and path:
        steps.append(_step("validate_roster_file", {"path": path.group(1).replace("\\", "/")}))

    # --- validation evidence ---------------------------------------------
    model_quality = has(r"how (good|accurate|well) is the (prediction |production |win )?model|"
                        r"model s (accuracy|calibration|pick)|calibration error|candidate models|"
                        r"models compare|features? matter|drives the model|feature importance|"
                        r"features? (does|do) the (win |prediction )?model (rely|depend|use)|"
                        r"what drives|how accurate are (the )?predictions|"
                        r"(model|probabilities|predictions)\b.*\b(well )?calibrated|"
                        r"(win.probability|prediction|production) model\b.*\b(accura|calibrat|reliab|good)")
    if model_quality:
        focus = "features" if has(r"feature|drives") else (
            "comparison" if has(r"candidate|compare") else (
                "calibration" if has(r"calibrat") else "overall"))
        steps.append(_step("validation_report", {"component": "production_model"}, focus))
    if has(r"simulator|season projection|projections?") and has(
            r"\bwell\b|accurate|accuracy|backtest|reliable|trust|real seasons"):
        steps.append(_step("validation_report", {"component": "season_forward_projection"}))
    if has(r"title odds|championship odds|playoff odds|playoff model|bracket") and has(
            r"reliable|accurate|trust|how good|validated|how well"):
        steps.append(_step("validation_report", {"component": "playoffs"}))
    if has(r"margin model|score (model|prediction)s?") and has(r"reliable|accurate|how good|trust"):
        steps.append(_step("validation_report", {"component": "margin"}))

    # --- players ------------------------------------------------------------
    if players:
        if len(players) > 1 and not has(r"\bvs\b|versus|compare"):
            raise ValueError(
                f"Several players match ({players}); use one full name or a personId."
            )
        person = players[0]
        wants_stats = has(r"averag|per game|\bppg\b|stats|statistics|points|rebounds|assists|shoot|scor|"
                          r"numbers|season line|stat line|production")
        wants_impact = has(r"impact|diagnostic|net rating|on off|value")
        if wants_stats and not (wants_impact and not has(r"stats|per game|average")):
            stats_season = season if season is not None else DEFAULT_SEASON
            steps.append(_step("player_season_stats", {"person_id": person, "season": stats_season}))
        if wants_impact and len(teams) >= 2:
            home, away = order_home_away(question, teams)
            steps.append(_step("player_scenario", {"home_team_id": home, "away_team_id": away,
                                                   "person_id": person}))
        elif wants_impact:
            params = {"person_id": person}
            if date:
                params["before"] = date
            steps.append(_step("player_impact", params))
        elif len(teams) >= 2 and not wants_stats:
            home, away = order_home_away(question, teams)
            steps.append(_step("player_scenario", {"home_team_id": home, "away_team_id": away,
                                                   "person_id": person}))

    # --- rosters / current strength ------------------------------------------
    if teams and has(r"\badd(ed|s)?\b|sign(ed|ing)?|acquir|offseason|roster|arrival|departure|lost\b|who (left|joined)"):
        for team in teams[:2]:
            params = {"team_id": team}
            if date:
                params["as_of"] = date
            steps.append(_step("team_roster", params))
    if has(r"strongest|power rank|strength|best teams right now|how strong"):
        steps.append(_step("team_strength", {} if not date else {"as_of": date}))

    # --- postseason ------------------------------------------------------------
    title_question = has(r"\btitle\b|championship|win it all|champion|finals odds|bracket|"
                         r"conference finals|win the (east|west)")
    if title_question and not any(s["tool"] == "validation_report" and s["parameters"]["component"] == "playoffs"
                                  and not has(r"who|which|what are|odds") for s in steps):
        params = {"season": season if season is not None else LATEST_POSTSEASON}
        if date:
            params["season"] = season if season is not None else season_of_date(date)
            params["as_of"] = date
        if teams:
            params["team_id"] = teams[0]
        steps.append(_step("playoff_odds", params))

    # --- forward projection from a date ------------------------------------------
    rest_of_season = has(r"rest of (the )?season|projection (look|looked)|project(ed|ion)? from|as of|"
                         r"on (jan|feb|mar|apr|oct|nov|dec)")
    if date and not title_question and (rest_of_season or has(r"projected|projection|project|finish")) and not (
            len(teams) >= 2 and has(r"\bvs\b|versus|favored|who wins|beat")):
        params = {"season": season_of_date(date), "as_of": date}
        if teams:
            params["team_id"] = teams[0]
        steps.append(_step("project_rest_of_season", params))

    already = {step["tool"] for step in steps}

    # --- head to head / records ------------------------------------------------------
    all_time_series = len(teams) >= 2 and has(r"how (often|many times) (has|have|did)|all.time|series history|"
                                              r"historically|meetings|won more|head to head") and not has(
        r"favored|predict|who wins|will win")
    if (has(r"head.to.head|headtohead|series record|\bh2h\b") or all_time_series) and len(teams) >= 2:
        params = {"team_a_id": teams[0], "team_b_id": teams[1]}
        params["season"] = season if season is not None else DEFAULT_SEASON
        if has(r"all time|all-time|ever|history"):
            params.pop("season")
        steps.append(_step("head_to_head", params))
    h2h_only_record = has(r"head.to.head record|series record|h2h record") and not has(r"\brecords\b")
    if has(r"\brecords?\b|wins and losses|win.loss|how many games did") and teams and \
            "team_roster" not in already and not h2h_only_record:
        for team in teams[:2]:
            params = {"team_id": team}
            params["season"] = season if season is not None else DEFAULT_SEASON
            if has(r"all time|all-time|franchise history"):
                params.pop("season")
            steps.append(_step("team_record", params))
    if has(r"\bcompare\b") and len(teams) >= 2 and not any(s["tool"] in ("team_record", "head_to_head") for s in steps):
        for team in teams[:2]:
            steps.append(_step("team_record", {"team_id": team, "season": season if season is not None else DEFAULT_SEASON}))
        steps.append(_step("head_to_head", {"team_a_id": teams[0], "team_b_id": teams[1],
                                            "season": season if season is not None else DEFAULT_SEASON}))

    already = {step["tool"] for step in steps}

    # --- season projection ------------------------------------------------------------
    season_param = season if season is not None else DEFAULT_SEASON
    if "project_rest_of_season" not in already and "playoff_odds" not in already:
        league_wide = has(r"projected playoff|playoff (teams|field)|standings|best team|worst team|"
                          r"(teams|who) (are|is) (expected|projected|likely) to make the playoffs|"
                          r"best and worst|best/worst|league.?(average|mean)|average projected|"
                          r"projected to be the league|number one seed|#?1 seed in the|"
                          r"(east|west)\w* (#|number )?1 seed|east s #?1|west s #?1")
        team_level = teams and has(r"seed|projected wins|how many wins|mean wins|projection|"
                                   r"playoff (probability|chances|odds|spot)|makes? the (direct )?playoffs|"
                                   r"making the (direct )?playoffs|top (six|6)")
        if team_level and len(teams) == 1:
            steps.append(_step("team_projection", {"team_id": teams[0], "season": season_param}))
        elif league_wide:
            focus = "field" if has(r"playoff|seed") else "summary"
            steps.append(_step("simulate_season", {"season": season_param}, focus))

    already = {step["tool"] for step in steps}

    # --- one team's rating / recent form -------------------------------------------------
    if teams and len(teams) <= 2 and has(r"\belo\b|rating") and not has(r"net rating|impact"):
        for team in teams[:2]:
            params = {"team_id": team}
            if date:
                params["as_of"] = date
            steps.append(_step("team_elo_rating", params))
    if teams and len(teams) <= 2 and has(r"lately|recent(ly)? form|recently|last (10|ten)|hot streak|"
                                         r"cold streak|how (has|have) .* (played|been playing)|form\b"):
        for team in teams[:2]:
            params = {"team_id": team}
            if date:
                params["as_of"] = date
            steps.append(_step("team_form", params))

    already = {step["tool"] for step in steps}

    # --- single game --------------------------------------------------------------------
    matchup = len(teams) >= 2 and has(r"favored|favorite|\bvs\b|versus|win chance|win probability|who wins|"
                                      r"who would win|will win|\bbeat\b|predict|score|margin|\bhost|\bat\b|matchup|game")
    if matchup and not any(t in already for t in ("head_to_head", "player_scenario", "team_roster")) or (
            has(r"\bcompare\b") and len(teams) >= 2 and has(r"favored|favorite|who wins|host|at ")):
        home, away = order_home_away(question, teams)
        params = {"home_team_id": home, "away_team_id": away}
        if date:
            params["game_date"] = date
        if not has(r"^\s*(what is the )?(predicted )?(score|margin)\b") or has(r"who|win|favored"):
            steps.append(_step("predict_matchup", dict(params)))
        if has(r"score|margin|by how much|how many points|point spread|spread"):
            steps.append(_step("predict_margin", dict(params)))

    if not steps and teams and has(r"team id|teamid|resolve"):
        labels = load_team_labels()
        steps.append(_step("resolve_team_name", {"team": labels.get(teams[0], str(teams[0]))}))

    if not steps:
        if players or teams:
            raise ValueError(
                "I recognized "
                + (f"teams {teams} " if teams else "")
                + (f"player {players[0]} " if players else "")
                + "but not what you want to know. Ask for a prediction, a record, a projection, "
                  "playoff odds, a head-to-head, roster moves, stats, or an impact diagnostic."
            )
        raise ValueError(
            "I could not map that question to a supported analytical tool. Try a matchup "
            "('Who is favored in Celtics vs Lakers?'), a projection, playoff or title odds, a team "
            "record, a head-to-head series, a player's season stats, or how accurate the model is."
        )
    # De-duplicate identical calls, keep order.
    seen, unique = set(), []
    for step in steps:
        key = (step["tool"], json.dumps(step["parameters"], sort_keys=True))
        if key not in seen:
            seen.add(key)
            unique.append(step)
    return unique


# ---------------------------------------------------------------------------
# Execution and rendering
# ---------------------------------------------------------------------------

def run_step(step):
    if step["tool"] == "list_tools":
        return {
            "tool": "list_tools",
            "status": "success",
            "operation": "List the registered analytical tools.",
            "model": "tool registry (src/tools.py)",
            "assumptions": [],
            "limitations": [],
            "parameters": {},
            "data": {
                "tools": [{"name": t["name"], "category": t["category"],
                           "description": t["description"]} for t in list_tools()],
                "count": len(list_tools()),
            },
        }
    return execute_tool(step["tool"], step["parameters"])


def _pct(value, digits=1):
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def _name(labels, team_id, row=None):
    if row and row.get("teamName"):
        return row["teamName"]
    return labels.get(team_id, str(team_id))


def _season_label(season):
    return f"{season}-{str(season + 1)[-2:]}"


def render_step(step, envelope, labels):
    """Sentences for one tool result: ``[(section, text), ...]``."""
    tool = step["tool"]
    status = envelope.get("status")
    if status != "success":
        message = (envelope.get("error") or {}).get("message", "unknown error")
        prefix = "not available" if status == "unavailable" else "could not run"
        return [("uncertainty", f"{tool} {prefix}: {message}")]
    data = envelope.get("data") or {}
    section = CATEGORY_OF_TOOL.get(tool, "model")
    out = []

    if tool == "predict_matchup":
        p = data.get("prediction") or {}
        home, away = _name(labels, data.get("home_team_id")), _name(labels, data.get("away_team_id"))
        favorite = home if p.get("home_win_probability", 0) >= 0.5 else away
        out.append((section, (
            f"{favorite} are favored. The frozen production model ({p.get('model')}) gives {home} "
            f"(home) {_pct(p.get('home_win_probability'))} and {away} (away) "
            f"{_pct(p.get('away_win_probability'))}"
            + (f", using data as of {p['game_date']}." if p.get("game_date") else ", using each team's latest data.")
        )))
    elif tool == "predict_margin":
        home, away = _name(labels, data.get("home_team_id")), _name(labels, data.get("away_team_id"))
        m = data.get("predicted_home_margin")
        interval = data.get("margin_interval_80") or [None, None]
        total_interval = data.get("total_interval_80") or [None, None]
        out.append((section, (
            f"Predicted score: {home} {data.get('predicted_home_points'):.1f}, {away} "
            f"{data.get('predicted_away_points'):.1f} (home margin {m:+.1f}, 80% range "
            f"{interval[0]:+.1f} to {interval[1]:+.1f}; total {data.get('predicted_total_points'):.1f}, "
            f"80% range {total_interval[0]:.1f} to {total_interval[1]:.1f})."
        )))
    elif tool == "simulate_season":
        projection = data.get("projection") or {}
        league = projection.get("league_summary") or {}
        season = projection.get("season")
        best, worst = league.get("best_team") or {}, league.get("worst_team") or {}
        out.append((section, (
            f"In the {season} season replay ({projection.get('n_simulations')} simulations) the "
            f"strongest projected team is {_name(labels, best.get('teamId'), best)} "
            f"({best.get('mean_wins', 0):.1f} mean wins) and the weakest "
            f"{_name(labels, worst.get('teamId'), worst)} ({worst.get('mean_wins', 0):.1f}); "
            f"the league-average projection is {league.get('league_mean_wins', 0):.1f} wins."
        )))
        if step.get("focus") == "field":
            slots = projection.get("projected_seedings") or []
            for conference in ("East", "West"):
                picks = [s for s in slots if s["conference"] == conference]
                if picks:
                    out.append((section, f"Most likely {conference} seeds: " + "; ".join(
                        f"{s['seed']}. {_name(labels, s['teamId'], s)} ({_pct(s['probability'])})"
                        for s in picks) + "."))
    elif tool == "team_projection":
        row = data.get("projection") or {}
        team = _name(labels, data.get("team_id"), row)
        seeds = ", ".join(
            f"seed {n} {_pct(row.get(f'p_seed_{n}'))}" for n in range(1, 7) if row.get(f"p_seed_{n}") is not None)
        out.append((section, (
            f"{team}: {row.get('mean_wins', 0):.1f} mean wins (5th-95th percentile "
            f"{row.get('p5_wins', 0):.0f}-{row.get('p95_wins', 0):.0f}) in the {data.get('season')} replay, "
            f"top-6 (direct playoff) probability {_pct(row.get('direct_playoff_probability'))}"
            + (f"; {seeds}; out of the top six {_pct(row.get('out_of_playoffs_probability'))}." if seeds else ".")
        )))
    elif tool == "team_record":
        team = _name(labels, data.get("team_id"))
        when = f"in the {_season_label(data['season'])} regular season" if data.get("season") else "all-time in the regular season"
        out.append((section, f"{team} went {data.get('wins')}-{data.get('losses')} {when} ({data.get('games')} games)."))
    elif tool == "head_to_head":
        a, b = _name(labels, data.get("team_a_id")), _name(labels, data.get("team_b_id"))
        when = f"in the {_season_label(data['season'])} regular season" if data.get("season") else "all-time in the regular season"
        out.append((section, f"{a} vs {b} {when}: {data.get('team_a_wins')}-{data.get('team_b_wins')} in "
                             f"{data.get('games')} games."))
    elif tool == "resolve_team_name":
        out.append((section, f"{data.get('team')} has teamId {data.get('team_id')}."))
    elif tool == "team_elo_rating":
        team = _name(labels, data.get("team_id"))
        when = f"as of {data['as_of']}" if data.get("as_of") else "after its latest game"
        out.append(("model", (
            f"{team}'s Elo rating {when} is {data.get('elo_rating')} (every team starts at "
            f"{data.get('initial_rating'):.0f}; only differences between teams are meaningful)."
        )))
    elif tool == "team_form":
        team = _name(labels, data.get("team_id"))
        out.append((section, (
            f"{team}'s rolling form entering its game of {data.get('snapshot_date')} (previous 10 games): "
            f"won {_pct(data.get('win_rate_rolling_10'), 0)}, scored {data.get('teamScore_rolling_10'):.1f} and "
            f"allowed {data.get('opponentScore_rolling_10'):.1f} per game (margin "
            f"{data.get('plusMinusPoints_rolling_10'):+.1f})."
        )))
    elif tool == "player_impact":
        d = data.get("diagnostic") or {}
        out.append((section, (
            f"Player-impact diagnostic for personId {d.get('person_id')} ({data.get('confidence')} "
            f"confidence, {d.get('prior_games')} prior games): minutes-weighted net rating "
            f"{d.get('player_net_rating', 0):+.2f}, expected {d.get('expected_minutes', 0):.1f} minutes, "
            f"estimated team net-rating change {d.get('estimated_net_rating_change', 0):+.2f}. "
            "This is an association, not a causal forecast."
        )))
    elif tool == "player_scenario":
        scenario = data.get("scenario") or {}
        base = scenario.get("base_prediction") or {}
        impact = scenario.get("player_impact") or {}
        home, away = _name(labels, data.get("home_team_id")), _name(labels, data.get("away_team_id"))
        out.append((section, (
            f"The production model gives {home} {_pct(base.get('home_win_probability'))} against {away}; "
            f"the player's association-only diagnostic (net rating {impact.get('player_net_rating', 0):+.2f}, "
            f"estimated change {impact.get('estimated_net_rating_change', 0):+.2f}) is not used to change it."
        )))
    elif tool == "player_season_stats":
        out.append((section, (
            f"{data.get('player')} in the {_season_label(data.get('season'))} regular season "
            f"({', '.join(data.get('teams') or [])}): {data.get('games')} games, "
            f"{data.get('points_per_game')} points, {data.get('rebounds_per_game')} rebounds, "
            f"{data.get('assists_per_game')} assists in {data.get('minutes_per_game')} minutes per game; "
            f"shooting {_pct(data.get('field_goal_pct'))} from the field"
            + (f", {_pct(data.get('three_point_pct'))} from three" if data.get("three_point_pct") is not None else "")
            + f", {_pct(data.get('free_throw_pct'))} from the line."
        )))
    elif tool == "validation_report":
        out.extend(_render_validation(step, data, labels))
    elif tool == "playoff_odds":
        rows = data.get("teams") or []
        key = "champion" if rows and "champion" in rows[0] else "p_champion"
        mode = data.get("mode", "")
        top = rows[:5]
        out.append((section, (
            f"Title odds for {_season_label(data.get('season'))} ({mode}): " + "; ".join(
                f"{_name(labels, r['teamId'], r)} {_pct(r.get(key))}"
                + (f" ({r['actual_result']})" if r.get("actual_result") else "")
                for r in top) + "."
        )))
        if data.get("team_odds"):
            r = data["team_odds"]
            get = lambda k: r.get(k, r.get(f"p_{k}"))  # noqa: E731
            out.append((section, (
                f"{_name(labels, r['teamId'], r)}: reach the first round {_pct(get('made_playoffs'))}, "
                f"conference finals {_pct(get('won_conf_semifinals'))}, Finals {_pct(get('won_conf_finals'))}, "
                f"title {_pct(get('champion'))}"
                + (f"; actual result: {r['actual_result']}." if r.get("actual_result") else ".")
            )))
    elif tool == "project_rest_of_season":
        projection = data.get("projection") or {}
        row = data.get("team_projection")
        lead = (f"From {projection.get('as_of')} ({projection.get('games_completed')} games played, "
                f"{projection.get('games_remaining')} simulated with strength frozen that day)")
        if row:
            out.append((section, (
                f"{lead}: {_name(labels, row['teamId'], row)} were {row.get('current_wins')}-"
                f"{row.get('current_losses')} and projected to {row.get('mean_wins', 0):.1f} wins "
                f"(5th-95th percentile {row.get('p5_wins', 0):.0f}-{row.get('p95_wins', 0):.0f}), top-6 "
                f"probability {_pct(row.get('direct_playoff_probability'))}."
            )))
        else:
            ranked = sorted(projection.get("projected_standings") or [], key=lambda r: -r["mean_wins"])[:5]
            out.append((section, f"{lead}, the top projected teams: " + "; ".join(
                f"{_name(labels, r['teamId'], r)} {r['mean_wins']:.1f}" for r in ranked) + "."))
    elif tool == "team_roster":
        roster = data.get("roster") or {}
        team = _name(labels, data.get("team_id"))
        arrived = ", ".join(f"{p['name']} ({p['player_minutes_rolling_10']:.1f} min, "
                            f"{p['player_points_rolling_10']:.1f} pts per game)" for p in roster.get("arrived", [])) or "none recorded"
        departed = ", ".join(p["name"] for p in roster.get("departed", [])) or "none recorded"
        out.append((section, (
            f"{team} since their last game on {roster.get('snapshot_game_date')} (transaction feed as of "
            f"{data.get('as_of')}): arrived {arrived}; departed {departed}."
        )))
    elif tool == "team_strength":
        rows = data.get("teams") or []
        out.append((section, "Current strength (chance to beat an average opponent, frozen production model): " + "; ".join(
            f"{_name(labels, r['teamId'], r)} {_pct(r['win_probability_vs_average'])} (Elo {r['elo_rating']:.0f})"
            for r in rows[:5]) + f"; weakest: " + "; ".join(
            f"{_name(labels, r['teamId'], r)} {_pct(r['win_probability_vs_average'])}" for r in rows[-3:]) + "."))
    elif tool == "data_status":
        out.append((section, (
            f"Latest completed game {data.get('latest_completed_game')}; latest model feature row "
            f"{data.get('latest_feature_row')}; latest roster transaction {data.get('latest_transaction')}; "
            f"{data.get('upcoming_season_regular_season_games_in_database')} games of the "
            f"{_season_label(data.get('upcoming_season'))} schedule are loaded."
        )))
    elif tool == "describe_database":
        tables = data.get("tables") or []
        out.append((section, "Database tables: " + "; ".join(
            f"{t['table']} ({t['row_count']:,} rows, {t['column_count']} columns)" for t in tables) + "."))
    elif tool == "describe_raw_files":
        files = data.get("files") or []
        out.append((section, "Raw data files: " + "; ".join(
            f"{f['name']} ({f['size_mb']} MB" + (f", {f['column_count']} columns" if f.get("column_count") else "") + ")"
            for f in files) + "."))
    elif tool == "validate_roster_file":
        s = data.get("summary") or {}
        out.append((section, (
            f"{data.get('path')} is valid: {s.get('event_count')} events ({s.get('add_count')} adds, "
            f"{s.get('remove_count')} removes) for {s.get('team_count')} teams and {s.get('person_count')} "
            f"players, {s.get('first_event_timestamp')} to {s.get('last_event_timestamp')}."
        )))
    elif tool == "list_tools":
        tools = data.get("tools") or []
        out.append((section, f"{data.get('count')} tools: " + "; ".join(
            f"{t['name']} ({t['category']})" for t in tools) + "."))
    elif tool == "resolve_player":
        candidates = data.get("candidates") or []
        out.append((section, "Matching players: " + "; ".join(
            f"{c['full_name']} (personId {c['person_id']}, {c['from_year']}-{c['to_year'] or 'present'})"
            for c in candidates) + "."))
    else:
        out.append((section, f"{tool} returned a result; see the structured envelope."))

    if section in ("model",):
        for limitation in (envelope.get("limitations") or [])[:2]:
            out.append(("uncertainty", f"{tool}: {limitation}"))
    return out


def _render_validation(step, data, labels):
    component = data.get("component")
    report = data.get("report") or {}
    out = []
    if component == "production_model":
        metrics = (report.get("metrics") or {}).get("elo_boosted_ensemble", {})
        calibration = (report.get("calibration") or {}).get("elo_boosted_ensemble", {})
        out.append(("reliability", (
            f"The production model ({report.get('recommended_model')}) was chosen by "
            f"{report.get('recommendation_metric')} on a chronological holdout of {report.get('test_games')} "
            f"games from {str(report.get('split_start'))[:10]}: accuracy {_pct(metrics.get('accuracy'))}, "
            f"log loss {metrics.get('log_loss', 0):.4f}, Brier score {metrics.get('brier_score', 0):.4f}, "
            f"expected calibration error {calibration.get('expected_calibration_error', 0):.4f}."
        )))
        if step.get("focus") in ("features", "overall"):
            features = ((report.get("feature_importance") or {}).get("features") or [])[:5]
            if features:
                out.append(("reliability", "Top drivers (permutation importance, boosted component): " + "; ".join(
                    f"{f['feature']} {_pct(f['importance'])}" for f in features) + "."))
        if step.get("focus") == "comparison":
            rows = report.get("metrics") or {}
            out.append(("reliability", "Holdout comparison (log loss): " + "; ".join(
                f"{name} {values['log_loss']:.4f}" for name, values in sorted(
                    rows.items(), key=lambda item: item[1].get("log_loss", 9)) if "log_loss" in values) + "."))
    elif component == "season_forward_projection":
        calibrated = report.get("calibrated") or {}
        out.append(("reliability", (
            f"Forward-projection backtest on {report.get('evaluation_seasons')} (never used for tuning): "
            + "; ".join(
                f"{float(key):.0%} of the season played -> mean error {row['model_mae_wins']:.2f} wins "
                f"(carry-current-pace {row['pace_mae_wins']:.2f}), 5-95% range covered "
                f"{_pct(row['actual_within_p5_p95_share'], 0)}"
                for key, row in sorted(calibrated.items())) + "."
        )))
    elif component == "playoffs":
        games, series, bracket = report.get("games") or {}, report.get("series") or {}, report.get("bracket_summary") or {}
        out.append(("reliability", (
            f"Playoff replay {report.get('seasons', [None])[0]}-{report.get('seasons', [None])[-1]}: game log loss "
            f"{games.get('model_log_loss', 0):.3f} vs {games.get('baseline_log_loss', 0):.3f} baseline over "
            f"{games.get('count')} games; series log loss {series.get('model_log_loss', 0):.3f} vs "
            f"{series.get('baseline_log_loss', 0):.3f} ({series.get('count')} series), series picked right "
            f"{_pct(series.get('model_accuracy'))} vs {_pct(series.get('baseline_accuracy'))} for 'higher seed always'; "
            f"actual champions got {_pct(bracket.get('mean_title_probability_given_to_actual_champion'))} title "
            f"probability on average and were the favorite {_pct(bracket.get('champion_was_model_favorite_share'), 0)} of the time."
        )))
    elif component == "margin":
        margin = (report.get("margin") or {}).get("holdout") or {}
        out.append(("reliability", (
            f"Margin model holdout MAE {margin.get('selected_model', {}).get('mae', 0):.2f} points vs "
            f"{margin.get('baseline_elo_linear', {}).get('mae', 0):.2f} (Elo line) and "
            f"{margin.get('baseline_home_court_constant', {}).get('mae', 0):.2f} (constant home edge)."
        )))
    return out


def _compose(question, rendered, envelopes):
    sections = {key: [] for key in SECTION_TITLES}
    for section, text in rendered:
        if text not in sections[section]:
            sections[section].append(text)
    for envelope in envelopes:
        confidence = (envelope.get("data") or {}).get("confidence") if isinstance(envelope.get("data"), dict) else None
        if confidence == "low":
            sections["uncertainty"].append(f"{envelope['tool']}: low confidence (fewer than 5 prior games).")
    parts = []
    for key, title in SECTION_TITLES.items():
        if sections[key]:
            parts.append(f"{title}:\n" + "\n".join(f"- {line}" for line in sections[key]))
    return "\n\n".join(parts), sections


def _context_from(plan, envelopes):
    teams, players, seasons = [], [], []
    for step in plan:
        p = step["parameters"]
        for key in ("home_team_id", "away_team_id", "team_id", "team_a_id", "team_b_id"):
            if p.get(key) and p[key] not in teams:
                teams.append(p[key])
        if p.get("person_id") and p["person_id"] not in players:
            players.append(p["person_id"])
        if p.get("season") is not None:
            seasons.append(p["season"])
    return {"teams": teams, "players": players, "season": seasons[0] if seasons else None}


def answer_deterministic(question, context=None):
    labels = load_team_labels()
    try:
        plan = plan_question(question, context)
    except ValueError as exc:
        return {
            "question": question, "mode": "deterministic", "status": "error",
            "plan": [], "envelopes": [], "answer": f"I could not answer that: {exc}",
            "sections": {}, "grounding": check_grounding("", []), "context": context or {},
        }
    envelopes = [run_step(step) for step in plan]
    rendered = []
    for step, envelope in zip(plan, envelopes):
        rendered.extend(render_step(step, envelope, labels))
    answer, sections = _compose(question, rendered, envelopes)
    statuses = [envelope.get("status") for envelope in envelopes]
    status = "success" if all(s == "success" for s in statuses) else (
        "partial" if any(s == "success" for s in statuses) else statuses[0])
    return {
        "question": question,
        "mode": "deterministic",
        "status": status,
        "plan": [{"tool": s["tool"], "parameters": s["parameters"]} for s in plan],
        "envelopes": envelopes,
        "answer": answer,
        "sections": sections,
        "grounding": check_grounding(answer, envelopes + [labels], question),
        "context": _context_from(plan, envelopes),
    }


# ---------------------------------------------------------------------------
# LLM agent (Claude via the Messages API tool-use loop)
# ---------------------------------------------------------------------------

DEFAULT_LLM_MODEL = "claude-opus-5"
SYSTEM_PROMPT = """You are the analyst behind Backboard, an NBA analytics engine. You answer questions by calling the tools provided; they are the only source of numbers you may use.

Rules:
- Every number in your answer must come from a tool result in this conversation (you may round it). Do not use numbers from memory, even well-known ones. If the tools cannot answer part of the question, say so plainly.
- Keep facts, model output and uncertainty apart. Structure the answer under these headings, omitting any that do not apply: "Facts (from the database)", "Model output (predictions and simulations, not facts)", "How reliable (stored validation evidence)", "Uncertainty & limits".
- Model predictions, simulations and projections are estimates, never facts. Player-impact diagnostics are associations, not causal effects. Hypothetical or cross-era scenarios are what-if simulations, not historical claims.
- Carry the tools' limitations into "Uncertainty & limits" when they affect the answer.
- Seasons are named by the year they start (2025 = the 2025-26 season). Resolve player names with resolve_player before using a personId when the name could be ambiguous.
- Be concise: a short direct answer first, then the sections."""


def tool_schemas():
    """JSON-schema tool definitions generated from the registry."""
    type_map = {"int": "integer", "str": "string", "bool": "boolean"}
    schemas = []
    for spec in TOOLS.values():
        properties = {
            p["name"]: {"type": type_map.get(p["type"], "string"), "description": p["description"]}
            for p in spec["parameters"]
        }
        description = spec["description"]
        if spec["limitations"]:
            description += " Limitations: " + " ".join(spec["limitations"][:2])
        schemas.append({
            "name": spec["name"],
            "description": description[:1024],
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": [p["name"] for p in spec["parameters"] if p.get("required")],
                "additionalProperties": False,
            },
        })
    return schemas


def _default_client():
    import anthropic  # optional dependency; ImportError -> deterministic fallback

    return anthropic.Anthropic()


def answer_llm(question, client=None, model=None, max_turns=8):
    """Claude plans the tool calls and writes the explanation; answers are grounding-checked."""
    client = client or _default_client()
    model = model or os.environ.get("BACKBOARD_LLM_MODEL", DEFAULT_LLM_MODEL)
    messages = [{"role": "user", "content": question}]
    envelopes, plan = [], []
    tools = tool_schemas()
    retried = False
    final_text = ""
    for _ in range(max_turns):
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            tools=tools,
            messages=list(messages),
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("The model declined to answer this question.")
        messages.append({"role": "assistant", "content": response.content})
        tool_uses = [block for block in response.content if block.type == "tool_use"]
        if response.stop_reason == "tool_use" and tool_uses:
            results = []
            for block in tool_uses:
                arguments = dict(block.input or {})
                envelope = execute_tool(block.name, arguments)
                plan.append({"tool": block.name, "parameters": arguments})
                envelopes.append(envelope)
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": compact_json(envelope),
                    "is_error": envelope.get("status") == "error",
                })
            messages.append({"role": "user", "content": results})
            continue
        final_text = "\n".join(block.text for block in response.content if block.type == "text").strip()
        grounding = check_grounding(final_text, envelopes + [load_team_labels()], question)
        if grounding["grounded"] or retried:
            break
        retried = True
        messages.append({"role": "user", "content": (
            "These numbers in your answer do not appear in any tool result: "
            + ", ".join(grounding["unsupported"])
            + ". Remove them or replace them with values the tools returned, then give the full answer again."
        )})
    grounding = check_grounding(final_text, envelopes + [load_team_labels()], question)
    if not grounding["grounded"]:
        final_text += (
            "\n\nNote: these numbers could not be traced to a tool result and should not be relied on: "
            + ", ".join(grounding["unsupported"])
        )
    statuses = [e.get("status") for e in envelopes]
    return {
        "question": question,
        "mode": "llm",
        "model": model,
        "status": "success" if final_text and all(s == "success" for s in statuses) else (
            "partial" if final_text else "error"),
        "plan": plan,
        "envelopes": envelopes,
        "answer": final_text,
        "sections": {},
        "grounding": grounding,
        "context": _context_from([{"tool": p["tool"], "parameters": p["parameters"]} for p in plan], envelopes),
    }


def answer(question, mode=None, context=None, client=None):
    """Answer a question with the requested planner (default deterministic).

    The result keeps the older single-tool keys (``tool``, ``parameters``,
    ``envelope`` = the first step) so existing callers of ``POST /ask`` keep
    working, and adds ``plan``, ``envelopes``, ``sections``, ``grounding``,
    ``mode`` and ``context``.
    """
    mode = (mode or os.environ.get("BACKBOARD_NL_MODE") or "deterministic").lower()
    if mode == "llm":
        try:
            result = answer_llm(question, client=client)
        except Exception as exc:  # no SDK/credentials, API error, refusal
            result = answer_deterministic(question, context)
            result["fallback_reason"] = (
                f"LLM planner unavailable ({type(exc).__name__}: {exc}); answered deterministically."
            )
    else:
        result = answer_deterministic(question, context)
    first = result["plan"][0] if result["plan"] else None
    result["tool"] = first["tool"] if first else None
    result["parameters"] = first["parameters"] if first else None
    result["envelope"] = result["envelopes"][0] if result["envelopes"] else None
    return result
