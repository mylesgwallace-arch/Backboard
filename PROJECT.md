# Backboard — Project Specification

## 1. Project Overview

**Backboard** is an AI-powered sports analytics platform designed to answer complex sports questions using **real data, statistical models, simulations, and natural-language reasoning**.

The long-term goal is to create something closer to a combination of:

* ESPN
* Basketball Reference
* An advanced sports analytics platform
* A trade/game simulator
* An AI research analyst

The system should **not simply guess answers**. When a question can be answered quantitatively, the system should retrieve relevant data, run appropriate calculations/models, and then explain the results.

### Example

A user asks:

> "If Stephen Curry were traded to the San Antonio Spurs, how would their ratings improve?"

The eventual system should be able to:

1. Identify Stephen Curry and San Antonio.
2. Retrieve relevant player and team data.
3. Analyze the existing Spurs roster/team performance.
4. Model Curry's potential impact.
5. Simulate the hypothetical roster change.
6. Produce projected changes to metrics such as:

   * Offensive Rating
   * Defensive Rating
   * Net Rating
   * Wins
   * Playoff probability
   * Potentially championship probability
7. Explain the methodology and uncertainty behind the result.

---

# 2. Core Philosophy

The most important principle is:

> **The LLM should not invent the analysis. The data and statistical models should generate the analysis; the LLM should determine what information is needed and explain the results.**

The intended architecture is:

```text
User Question
      ↓
AI Agent
      ↓
Determine required information
      ↓
Data Tools / APIs / Database
      ↓
Statistical Models / Simulations
      ↓
Quantitative Results
      ↓
AI explains results
      ↓
User
```

The system should distinguish between:

* factual data
* calculated statistics
* model predictions
* assumptions
* uncertainty

The AI should never present an unsupported estimate as factual information.

---

# 3. Initial Scope

## Sport

Start with:

**NBA only**

Do not initially attempt to support every major sport.

The architecture should eventually be extensible to other sports, but NBA is the first implementation.

## Initial Objective

Build a system capable of answering NBA questions using historical data and statistical models.

The first major analytical milestone is:

> Given an NBA question, retrieve the relevant data and produce a mathematically supported answer.

---

# 4. Development Philosophy

Build incrementally.

The project should generally progress from:

```text
Environment
    ↓
Historical NBA Data
    ↓
Database
    ↓
Data Exploration
    ↓
Feature Engineering
    ↓
Baseline Statistical Model
    ↓
Improved Models
    ↓
Player/Team Impact Modeling
    ↓
Simulation Engine
    ↓
AI / Tool Layer
    ↓
Live Data / MCP / APIs
    ↓
Website
```

The analytical engine is more important than the initial user interface.

Do not build the entire platform simultaneously.

---

# 5. Technology Stack

## Development

* VS Code
* Git
* GitHub
* Python
* Jupyter

## Python

Primary libraries may include:

* pandas
* NumPy
* scikit-learn

Additional libraries should only be added when they provide a clear purpose.

## Environment

Use a project-specific virtual environment:

```text
.venv/
```

The `.venv` directory should not be committed to GitHub.

## Database

Initial database:

**SQLite**

Potential future database:

**PostgreSQL**

---

# 6. Repository Architecture

The general repository structure is:

```text
backboard/
│
├── data/
│   ├── raw/
│   ├── processed/
│   └── database/
│
├── models/
│
├── src/
│
├── tests/
│
├── notebooks/
│
├── .github/
│   └── agents/
│
├── .gitignore
├── README.md
├── PROJECT.md
└── PROJECT_CONTEXT.md
```

### `data/raw/`

Original downloaded datasets.

### `data/processed/`

Cleaned and transformed datasets.

### `data/database/`

SQLite databases and related database artifacts.

### `models/`

Trained models and model-related artifacts.

### `src/`

Reusable production/application code, including:

* data ingestion
* database interaction
* feature engineering
* statistical models
* simulations
* AI/tool integrations
* application logic

### `tests/`

Automated tests.

### `notebooks/`

Exploratory analysis and experiments.

Notebooks should generally be used for exploration rather than becoming the main production application.

### `.github/agents/`

Repository-specific instructions for coding agents.

Agents should use these instructions to understand how they are expected to inspect, modify, and validate different parts of the project.

---

# 7. Data Requirements

The eventual platform should support several categories of NBA data.

## Player Data

Potential fields include:

* Player ID
* Name
* Team
* Position
* Age
* Minutes
* Points
* Rebounds
* Assists
* Steals
* Blocks
* Turnovers
* Field goal percentage
* Three-point percentage
* Free throw percentage
* Usage
* True Shooting %
* Assist %
* Rebound %
* Turnover %
* Offensive Rating
* Defensive Rating
* Plus/minus
* On/off metrics
* Other advanced impact metrics

## Team Data

Potential fields include:

* Team ID
* Team name
* Season
* Wins
* Losses
* Offensive Rating
* Defensive Rating
* Net Rating
* Pace
* Points per game
* Opponent points per game
* Home/away performance
* Lineup information

## Game Data

Potential fields include:

* Game ID
* Date
* Season
* Home team
* Away team
* Home score
* Away score
* Winner
* Player statistics
* Team statistics
* Potentially play-by-play data

## Future Live Data

Potentially:

* Live scores
* Game status
* Quarter
* Time remaining
* Live player statistics
* Live team statistics
* Play-by-play
* Current rosters
* Injuries
* Transactions
* Schedules

---

# 8. Historical Data

Historical data will initially be used to develop and validate models.

Potential sources include:

* Kaggle datasets
* Sports APIs
* NBA data sources
* Other reputable structured datasets

Historical datasets should be evaluated based on:

1. Number of seasons
2. Player-level detail
3. Team-level detail
4. Game-level detail
5. Advanced statistics
6. Lineup availability
7. Data consistency
8. Licensing/usage restrictions
9. Ease of importing into SQLite/PostgreSQL
10. Compatibility with future production schemas

The exact dataset and schema should be determined from the actual data rather than assumed in advance.

---

# 9. Database Architecture

The initial database may contain tables such as:

```text
players
teams
seasons
games
player_game_stats
team_game_stats
```

Potential future tables include:

```text
rosters
lineups
transactions
injuries
play_by_play
player_advanced_stats
team_advanced_stats
model_predictions
```

The exact schema should be based on the selected datasets.

Do not prematurely over-engineer the database.

> **Always inspect the actual schema before writing SQL.**

---

# 10. Statistical Modeling

Statistical modeling is a core component of Backboard.

Potential progression:

```text
Simple baseline
      ↓
Logistic regression / regression models
      ↓
Team + player statistics
      ↓
ELO / strength ratings
      ↓
Gradient boosting
      ↓
Ensemble models
      ↓
Simulation / Monte Carlo
```

The actual progression should be determined by empirical performance rather than predetermined assumptions.

---

# 11. Model Evaluation

Models must be evaluated against historical data.

### Classification

Potential metrics:

* Accuracy
* Precision
* Recall
* ROC-AUC

### Probabilistic Predictions

Potential metrics:

* Log Loss
* Brier Score
* Calibration

### Regression

Potential metrics:

* MAE
* RMSE
* R²

Simple baselines should always be considered.

A more complicated model should only be considered an improvement if historical testing supports that conclusion.

Avoid data leakage.

Historical predictions must only use information that would have been available at the prediction timestamp.

---

# 12. Player Impact Modeling

One of the eventual core capabilities is estimating how a player's addition or removal affects a team.

Example:

```text
Current Team

ORTG: 112.4
DRTG: 114.1
Net Rating: -1.7

        ↓

Hypothetical Player Addition

        ↓

Projected Team

ORTG: ???
DRTG: ???
Net Rating: ???
```

Potential inputs include:

* Player efficiency
* Usage
* Shooting
* Playmaking
* Defensive metrics
* Minutes
* Team context
* Lineup context
* On/off data
* Opponent strength

The initial implementation may be relatively simple and should become more sophisticated as better data and validation become available.

---

# 13. Trade Simulation

A major eventual feature is a trade simulator.

Example:

```text
TRADE SIMULATION

Team A receives:
Player X

Team B receives:
Player Y
Player Z
Draft pick
```

The system should potentially estimate changes to:

* Wins
* Offensive Rating
* Defensive Rating
* Net Rating
* Playoff probability
* Championship probability

These values must come from the statistical/simulation system rather than being invented by the LLM.

---

# 14. Game Prediction

The eventual system should support questions such as:

> "Who is more likely to win tonight?"

Potential inputs include:

* Team strength
* Recent performance
* Home-court advantage
* Player availability
* Expected lineups
* Rest
* Opponent strength
* Current-season performance
* Historical information

Example output:

```text
Team A win probability: 63%
Team B win probability: 37%
```

Probabilities should come from a calibrated statistical model.

---

# 15. Live Analysis

Eventually the platform should support live games.

Potential capabilities:

* Live win probability
* Live player statistics
* Play-by-play analysis
* Explanation of probability changes
* Automatic game summaries
* "Why did the win probability change?"
* "Who is controlling the game?"
* "What changed in the third quarter?"

Live analysis must use current data rather than the LLM's static knowledge.

---

# 16. Live Data & MCP Integration

The eventual platform will need access to current NBA information for live analysis, current player/team information, schedules, injuries, game data, and other time-sensitive questions.

One potential integration is **ESPN through the Model Context Protocol (MCP)**.

MCP can provide the AI agent with standardized tools for interacting with external data sources.

Conceptually:

```text
                    AI AGENT
                       │
                       ▼
                ┌─────────────┐
                │ MCP Tools   │
                └──────┬──────┘
                       │
                       ▼
                ESPN / Sports
                   Data
```

Potential capabilities may include tools for:

```text
get_live_games()
get_game()
get_box_score()
get_player_stats()
get_team_stats()
get_roster()
get_schedule()
get_injuries()
```

These are examples of desired capabilities, **not assumptions about what an ESPN MCP implementation actually provides**.

Before integrating ESPN MCP into the production architecture, the project should:

1. Identify the specific ESPN MCP implementation being used.
2. Determine exactly which tools and data it provides.
3. Test the reliability and completeness of its data.
4. Determine whether it provides the historical/current information required by the project.
5. Evaluate usage restrictions, availability, and licensing considerations.
6. Design the application's data layer so it is not unnecessarily dependent on a single external provider.

The project should treat MCP as a **tool-access layer**, not as the statistical analysis engine itself.

The intended architecture is:

```text
User Question
      ↓
AI Agent
      ↓
Determine required information
      ↓
MCP / APIs / Database
      ↓
Retrieve data
      ↓
Statistical Models
      ↓
Simulation
      ↓
Quantitative Results
      ↓
AI Explanation
```

Historical SQLite data should remain useful even when external live-data services are unavailable.

The exact MCP integration should be implemented only when the analytical system has reached the stage where current/live data is actually required.

---

# 17. AI Agent

The eventual AI layer should operate as an agent capable of using tools.

Potential tools include:

```text
search_players()
search_teams()

get_player_stats()
get_team_stats()

get_player_advanced_stats()
get_team_advanced_stats()

get_lineups()
get_rosters()
get_injuries()

get_live_games()
get_live_box_score()
get_play_by_play()

run_player_projection()
run_team_projection()

simulate_trade()
simulate_game()

calculate_win_probability()
```

The exact tool set will evolve as the project develops.

The AI agent should be capable of using:

* Historical database tools
* Statistical model tools
* Simulation tools
* MCP tools
* External APIs
* Other specialized tools as the project expands

The agent should select tools based on the question rather than attempting to answer every question from language-model knowledge.

---

# 18. AI Responsibilities

The AI should:

* Interpret natural-language questions.
* Determine what data is required.
* Select appropriate tools.
* Retrieve relevant information.
* Call statistical models when appropriate.
* Interpret model outputs.
* Explain results.
* Communicate uncertainty.
* Distinguish factual information from model output.

The AI should not:

* Invent statistics.
* Pretend to have live information.
* Perform complex numerical analysis through unsupported guessing.
* Present speculative values as facts.
* Hide important uncertainty.

---

# 19. Website

The website should be developed after the analytical backend is functional.

Potential technologies:

* React
* Next.js
* Modern CSS/UI frameworks

The initial interface can be simple.

Potential future features:

* Player pages
* Team pages
* Interactive charts
* Trade simulator
* Live games
* Win probability
* Model explanations
* Analysis history
* Data-source information

---

# 20. User Experience

Users should be able to ask natural-language questions without needing statistical expertise.

Examples:

> "Would the Celtics get better if they traded Player A for Player B?"

> "Why has OKC's defense improved?"

> "Who's the most underrated player this season?"

> "Predict tonight's Warriors game."

> "What would happen if Luka missed 20 games?"

> "Which team improved the most this offseason?"

The AI should translate these questions into quantitative analysis whenever possible.

---

# 21. Model Transparency

Analysis should explain, when practical:

* What data was used
* What model was used
* What assumptions were made
* What the prediction means
* How uncertain the prediction is

Example:

```text
Projected Net Rating: +4.2

Confidence: Moderate

Main factors:
• Player offensive impact
• Team spacing
• Expected minutes
• Existing roster construction

Limitation:
This simulation assumes historical player production
translates approximately to the new team environment.
```

---

# 22. Engineering Principles

### Inspect before assuming

Never assume a file, table, column, script, or feature exists.

Verify it.

### Keep data separate from application code

Do not hard-code large datasets into Python files.

### Keep experiments separate from production code

Use notebooks for exploration and `src/` for reusable production logic.

### Avoid duplicate functionality

Before creating a new script, check whether an existing script already performs the same task.

### Test models

Do not assume a model is good because its output looks reasonable.

### Avoid data leakage

Never allow future information into historical predictions.

### Prefer reproducibility

Another machine should eventually be able to clone the repository, install dependencies, and run the project.

### Don't over-engineer early

Start with the simplest tools that can accomplish the current task.

### Add dependencies deliberately

Install additional libraries only when they provide a clear benefit.

### Keep factual data separate from predictions

Clearly distinguish:

* factual observations
* calculations
* model projections
* assumptions
* uncertainty

### Build incrementally

Complete and validate each layer before building heavily on top of it.

### Preserve provider independence

External data providers such as ESPN should be treated as replaceable data sources where practical. Core analytical logic should not unnecessarily depend on a single provider.

---

# 23. Long-Term Architecture

The eventual system should resemble:

```text
                         USER
                           │
                           ▼
                    ┌─────────────┐
                    │   WEBSITE   │
                    │ Chat / UI   │
                    └──────┬──────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  AI AGENT   │
                    │  Reasoning  │
                    └──────┬──────┘
                           │
             ┌─────────────┼──────────────┐
             │             │              │
             ▼             ▼              ▼
       ┌──────────┐  ┌───────────┐  ┌────────────┐
       │ Live Data│  │ Historical│  │ Statistical│
       │ MCP / API│  │ Database  │  │ Models     │
       └──────────┘  └───────────┘  └────────────┘
             │             │              │
             └─────────────┼──────────────┘
                           ▼
                    ┌─────────────┐
                    │ SIMULATION  │
                    │ ENGINE      │
                    └──────┬──────┘
                           │
                           ▼
                    Quantitative
                      Results
                           │
                           ▼
                    AI Explanation
                           │
                           ▼
                          USER
```

---

# 24. Future Expansion

Once the NBA implementation is mature, the architecture may expand to:

* NFL
* MLB
* NHL
* Soccer
* College sports

Each sport should have domain-specific data structures and statistical methods.

The system should not force every sport into an identical analytical framework.

---

# 25. Ultimate Goal

The ultimate goal is to create a system where a user can ask almost any reasonable sports analytics question in natural language and receive an answer that is:

**Current + Data-driven + Quantitative + Explainable + Honest about uncertainty**

The project should feel less like a generic chatbot and more like:

> **A sports research analyst with access to live data, a statistical laboratory, and a simulation engine.**
