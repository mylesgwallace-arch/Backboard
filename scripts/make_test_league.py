"""Generate a made-up league database for tests and CI.

Real franchise ids and names (public facts), invented players and simulated
games, in the table layout of the Kaggle NBA database the pipeline reads.
Twelve seasons (2014-15 to 2025-26, with playoffs) and the start of 2026-27,
with "today" fixed at 2026-10-23 so runs are repeatable.

    python scripts/make_test_league.py --out data/database/nba.db

It refuses to replace a database it did not make (one without its
``synthetic_league`` marker table) unless --force is given, so it can never
overwrite a real download by accident. The GitHub Actions workflow builds the
test database this way; tests marked ``real_data`` need the real database and
are skipped there.
"""
import argparse
import random
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

FIRST_SEASON, LAST_FULL, CURRENT = 2014, 2025, 2026
TODAY = date(2026, 10, 23)
rng = random.Random(20261023)

TEAMS = [
    (1610612737, "Atlanta", "Hawks", "ATL", "E"), (1610612738, "Boston", "Celtics", "BOS", "E"),
    (1610612739, "Cleveland", "Cavaliers", "CLE", "E"), (1610612740, "New Orleans", "Pelicans", "NOP", "W"),
    (1610612741, "Chicago", "Bulls", "CHI", "E"), (1610612742, "Dallas", "Mavericks", "DAL", "W"),
    (1610612743, "Denver", "Nuggets", "DEN", "W"), (1610612744, "Golden State", "Warriors", "GSW", "W"),
    (1610612745, "Houston", "Rockets", "HOU", "W"), (1610612746, "LA", "Clippers", "LAC", "W"),
    (1610612747, "Los Angeles", "Lakers", "LAL", "W"), (1610612748, "Miami", "Heat", "MIA", "E"),
    (1610612749, "Milwaukee", "Bucks", "MIL", "E"), (1610612750, "Minnesota", "Timberwolves", "MIN", "W"),
    (1610612751, "Brooklyn", "Nets", "BKN", "E"), (1610612752, "New York", "Knicks", "NYK", "E"),
    (1610612753, "Orlando", "Magic", "ORL", "E"), (1610612754, "Indiana", "Pacers", "IND", "E"),
    (1610612755, "Philadelphia", "76ers", "PHI", "E"), (1610612756, "Phoenix", "Suns", "PHX", "W"),
    (1610612757, "Portland", "Trail Blazers", "POR", "W"), (1610612758, "Sacramento", "Kings", "SAC", "W"),
    (1610612759, "San Antonio", "Spurs", "SAS", "W"), (1610612760, "Oklahoma City", "Thunder", "OKC", "W"),
    (1610612761, "Toronto", "Raptors", "TOR", "E"), (1610612762, "Utah", "Jazz", "UTA", "W"),
    (1610612763, "Memphis", "Grizzlies", "MEM", "W"), (1610612764, "Washington", "Wizards", "WAS", "E"),
    (1610612765, "Detroit", "Pistons", "DET", "E"), (1610612766, "Charlotte", "Hornets", "CHA", "E"),
]
TEAM = {t[0]: t for t in TEAMS}
EAST = [t[0] for t in TEAMS if t[4] == "E"]
WEST = [t[0] for t in TEAMS if t[4] == "W"]

FIRST = ("Avery Bram Cal Dario Eli Faris Gage Hollis Idris Jalen Kato Lazar Milo Nico Orrin Pax Quade Rafe "
         "Silas Tavi Ugo Vance Wes Xavi Yuri Zeke Ari Beck Cyrus Dax Emeka Finn Gus Hugo Ira Jace Kofi Luca "
         "Marek Nate Otis Pierce Remy Soren Teo Umar Vito Wade Yan Zion Andre Basil Corin Deon Ezra").split()
LAST = ("Achterberg Baptiste Calloway Draxler Eberhardt Fontaine Galloway Holloway Iwobi Jansen Kowalczyk "
        "Lindqvist Moreau Nakamura Okafor Pellegrini Quarles Rasmussen Szabo Tremblay Underhill Vasquez "
        "Whitlock Xiong Yarbrough Zielinski Abernathy Bellamy Castellano Dimitrov Ellison Falk Granger "
        "Hendrix Ingram-Shaw Jorgensen Kessler Lachance Mbeki Novak Oyelaran Prescott Quintero Rourke "
        "Sandoval Thibault Ulrich Valdivia Wexler Yeboah Zavala Ashby Brannigan Coulter Delacroix").split()

GAME_COLS = ["gameId", "gameDateTimeEst", "hometeamCity", "hometeamName", "hometeamId", "awayteamCity",
             "awayteamName", "awayteamId", "homeScore", "awayScore", "winner", "gameType", "gameSubtype",
             "gameLabel", "gameSubLabel", "seriesGameNumber", "attendance", "arenaId", "arenaName",
             "arenaCity", "arenaState", "officials", "gameDate"]
TS_COLS = ["gameId", "gameDateTimeEst", "teamCity", "teamName", "teamId", "opponentTeamCity",
           "opponentTeamName", "opponentTeamId", "home", "win", "teamScore", "opponentScore", "assists",
           "blocks", "steals", "fieldGoalsAttempted", "fieldGoalsMade", "fieldGoalsPercentage",
           "threePointersAttempted", "threePointersMade", "threePointersPercentage", "freeThrowsAttempted",
           "freeThrowsMade", "freeThrowsPercentage", "reboundsDefensive", "reboundsOffensive",
           "reboundsTotal", "foulsPersonal", "turnovers", "plusMinusPoints", "numMinutes", "q1Points",
           "q2Points", "q3Points", "q4Points", "benchPoints", "biggestLead", "biggestScoringRun",
           "leadChanges", "pointsFastBreak", "pointsFromTurnovers", "pointsInThePaint",
           "pointsSecondChance", "timesTied", "timeoutsRemaining", "seasonWins", "seasonLosses",
           "coachId", "gameType", "gameLabel", "seed", "gameDate"]
PS_COLS = ["firstName", "lastName", "personId", "gameId", "gameDateTimeEst", "playerteamCity",
           "playerteamName", "opponentteamCity", "opponentteamName", "gameType", "gameLabel",
           "gameSubLabel", "seriesGameNumber", "win", "home", "numMinutes", "points", "assists", "blocks",
           "steals", "fieldGoalsAttempted", "fieldGoalsMade", "fieldGoalsPercentage",
           "threePointersAttempted", "threePointersMade", "threePointersPercentage", "freeThrowsAttempted",
           "freeThrowsMade", "freeThrowsPercentage", "reboundsDefensive", "reboundsOffensive",
           "reboundsTotal", "foulsPersonal", "turnovers", "plusMinusPoints", "playerteamId",
           "opponentteamId", "comment", "startingPosition", "gameDate"]
PSX_COLS = ["personId", "gameId", "gameDateTimeEst", "playerteamId", "opponentteamId", "numMinutes",
            "offensiveRating", "defensiveRating", "netRating", "usagePercentage", "trueShootingPercentage",
            "pace", "possessions", "gameType"]
TSX_COLS = ["gameId", "gameDateTimeEst", "teamId", "opponentTeamId", "offensiveRating", "defensiveRating",
            "netRating", "pace", "possessions", "gameType"]
PLAYER_COLS = ["personId", "firstName", "lastName", "birthDate", "school", "country", "heightInches",
               "bodyWeightLbs", "jersey", "guard", "forward", "center", "draftYear", "draftRound", "draftNumber",
               "fromYear", "toYear", "nbaFlag"]


class Player:
    next_id = 9_000_001

    def __init__(self, season, age=None, talent=None):
        self.id = Player.next_id
        Player.next_id += 1
        self.first, self.last = rng.choice(FIRST), rng.choice(LAST)
        self.age0 = age if age is not None else rng.randint(19, 22)
        self.born = season - self.age0
        self.talent = talent if talent is not None else rng.gauss(0, 1)
        self.pos = rng.choice("GGGFFFC")
        self.three = {"G": 0.42, "F": 0.34, "C": 0.12}[self.pos] + rng.uniform(-0.1, 0.1)
        self.reb = {"G": 0.6, "F": 1.0, "C": 1.7}[self.pos] * rng.uniform(0.8, 1.2)
        self.ast = {"G": 1.7, "F": 0.9, "C": 0.6}[self.pos] * rng.uniform(0.7, 1.3)
        self.first_season = season
        self.draft = (season, None, None)
        self.retired = False

    def age(self, season):
        return season - self.born

    def skill(self, season):
        a = self.age(season)
        curve = -0.012 * (a - 27) ** 2 + 0.25
        return self.talent + curve


def make_schedule(season):
    """82 games per team: 2 vs other conference, 3 or 4 vs own conference."""
    pairs = []
    for e in EAST:
        for w in WEST:
            pairs += [(e, w), (w, e)]
    for conf in (EAST, WEST):
        n = len(conf)
        for i in range(n):
            for j in range(i + 1, n):
                d = min(j - i, n - (j - i))
                a, b = conf[i], conf[j]
                if d <= 2:  # 3-game pairs (each team has four of them)
                    first = a if rng.random() < 0.5 else b
                    other = b if first == a else a
                    pairs += [(first, other), (other, first), (first, other)]
                else:
                    pairs += [(a, b), (b, a), (a, b), (b, a)]
    rng.shuffle(pairs)
    start = date(season, 10, 21 if season == CURRENT else 22)
    days, day = [], start
    remaining = pairs
    while remaining:
        used, today, rest = set(), [], []
        cap = rng.randint(5, 10)
        for h, a in remaining:
            if len(today) < cap and h not in used and a not in used:
                today.append((h, a))
                used.update((h, a))
            else:
                rest.append((h, a))
        days.append((day, today))
        remaining = rest
        day += timedelta(days=1)
    return days


def team_minutes(roster, season):
    active = [p for p in roster if rng.random() > 0.06]
    active.sort(key=lambda p: -p.skill(season))
    active = active[:11]
    weights = [36, 34, 33, 31, 30, 24, 20, 16, 9, 5, 2][: len(active)]
    scale = 240 / sum(weights)
    mins = [w * scale * rng.uniform(0.9, 1.1) for w in weights]
    fix = 240 / sum(mins)
    return [(p, m * fix, i < 5) for i, (p, m) in enumerate(zip(active, mins))]


def strength(lineup, season):
    return sum(p.skill(season) * m / 48 for p, m, _ in lineup) * 2.2


def box(lineup, team_pts, season):
    """Player lines that add up exactly to team_pts."""
    usage = [max(0.05, (p.skill(season) + 2.5)) * m for p, m, _ in lineup]
    total = sum(usage)
    lines, left = [], team_pts
    for i, ((p, m, starter), u) in enumerate(zip(lineup, usage)):
        pts = left if i == len(lineup) - 1 else min(left, round(team_pts * u / total * rng.uniform(0.7, 1.3)))
        pts = max(0, pts)
        left -= pts
        tpm = min(pts // 3, int(round(pts * p.three * rng.uniform(0.5, 1.2) / 3)))
        rem = pts - 3 * tpm
        ftm = min(rem, int(round(rem * rng.uniform(0.1, 0.25))))
        if (rem - ftm) % 2:
            ftm += 1
        fg2 = (rem - ftm) // 2
        fgm = fg2 + tpm
        pct = min(0.7, max(0.3, 0.46 + 0.03 * p.skill(season) + rng.gauss(0, 0.06)))
        fga = max(fgm, int(round(fgm / pct))) if fgm else rng.randint(0, 3)
        tpa = max(tpm, int(round(tpm / max(0.25, rng.gauss(0.36, 0.06))))) if tpm else (rng.randint(0, 2) if p.three > 0.3 else 0)
        tpa = min(tpa, fga) if fga >= tpa else tpa
        fga = max(fga, tpa + fg2)
        fta = max(ftm, int(round(ftm / max(0.5, rng.gauss(0.78, 0.08))))) if ftm else rng.randint(0, 1)
        scale = m / 36
        oreb = max(0, int(round(rng.gauss(1.4 * p.reb * scale, 0.9))))
        dreb = max(0, int(round(rng.gauss(4.6 * p.reb * scale, 1.5))))
        ast = max(0, int(round(rng.gauss(3.4 * p.ast * scale * (1 + 0.15 * p.skill(season)), 1.4))))
        stl = max(0, int(round(rng.gauss(0.9 * scale, 0.7))))
        blk = max(0, int(round(rng.gauss((1.4 if p.pos == "C" else 0.4) * scale, 0.6))))
        tov = max(0, int(round(rng.gauss(1.6 * scale * (1.2 if p.pos == "G" else 0.9), 0.9))))
        pf = max(0, min(6, int(round(rng.gauss(2.2 * scale, 1.0)))))
        lines.append(dict(p=p, m=m, starter=starter, pts=pts, fgm=fgm, fga=fga, tpm=tpm, tpa=tpa, ftm=ftm,
                          fta=fta, oreb=oreb, dreb=dreb, ast=ast, stl=stl, blk=blk, tov=tov, pf=pf))
    return lines


def pct(a, b):
    return round(a / b, 3) if b else None


class League:
    def __init__(self):
        self.players = []
        self.rosters = {t: [] for t in TEAM}
        self.game_id = 1_000_000
        self.games, self.ts, self.ps, self.psx, self.tsx = [], [], [], [], []

    def seed_league(self, season):
        for t in TEAM:
            for _ in range(14):
                p = Player(season, age=rng.randint(20, 33), talent=rng.gauss(0, 1))
                p.first_season = season - rng.randint(0, 8)
                p.draft = (p.first_season, rng.choice([1, 1, 2]), rng.randint(1, 30))
                self.players.append(p)
                self.rosters[t].append(p)

    def offseason(self, season, standings):
        # retirements
        for t, roster in self.rosters.items():
            for p in list(roster):
                a = p.age(season)
                if a >= 35 or (a >= 31 and p.skill(season) < -1.2 and rng.random() < 0.5):
                    p.retired = True
                    roster.remove(p)
        # free agency: 14% move
        pool = []
        for t, roster in self.rosters.items():
            for p in list(roster):
                if rng.random() < 0.14:
                    roster.remove(p)
                    pool.append(p)
        rng.shuffle(pool)
        # draft: worst teams first
        order = sorted(TEAM, key=lambda t: standings.get(t, 41))
        for rnd in (1, 2):
            for pick, t in enumerate(order, 1):
                p = Player(season, talent=rng.gauss(0.9 - 0.035 * pick - (0.8 if rnd == 2 else 0), 0.8))
                p.draft = (season, rnd, pick)
                self.players.append(p)
                self.rosters[t].append(p)
        for p in pool:
            t = min(TEAM, key=lambda x: len(self.rosters[x]) + rng.random())
            self.rosters[t].append(p)
        for t, roster in self.rosters.items():
            roster.sort(key=lambda p: -p.skill(season))
            while len(roster) > 15:
                cut = roster.pop()
                cut.retired = True
            while len(roster) < 13:
                p = Player(season, age=rng.randint(22, 28), talent=rng.gauss(-0.8, 0.5))
                self.players.append(p)
                roster.append(p)

    def midseason_trade(self, season):
        a, b = rng.sample(list(TEAM), 2)
        if len(self.rosters[a]) > 12 and len(self.rosters[b]) > 12:
            pa, pb = rng.choice(self.rosters[a][2:]), rng.choice(self.rosters[b][2:])
            self.rosters[a].remove(pa); self.rosters[b].remove(pb)
            self.rosters[a].append(pb); self.rosters[b].append(pa)

    def play(self, season, when, home, away, gtype="Regular Season", label=None, series_no=None, seeds=None, record=None, played=True):
        self.game_id += 1
        gid = self.game_id
        h, a = TEAM[home], TEAM[away]
        stamp = when.strftime("%Y-%m-%d %H:%M:%S")
        row = dict(gameId=gid, gameDateTimeEst=stamp, hometeamCity=h[1], hometeamName=h[2], hometeamId=home,
                   awayteamCity=a[1], awayteamName=a[2], awayteamId=away, homeScore=None, awayScore=None,
                   winner=None, gameType=gtype, gameSubtype=None, gameLabel=label, gameSubLabel=None,
                   seriesGameNumber=series_no, attendance=float(rng.randint(15000, 20500)), arenaId=home % 1000,
                   arenaName=f"{h[1]} Arena", arenaCity=h[1], arenaState=None, officials=None,
                   gameDate=when.strftime("%Y-%m-%d"))
        if not played:
            self.games.append(row)
            return None
        hl, al = team_minutes(self.rosters[home], season), team_minutes(self.rosters[away], season)
        diff = strength(hl, season) - strength(al, season) + 2.6
        margin = rng.gauss(diff, 12.0)
        pace = rng.gauss(112 + (season - 2014) * 0.4, 9)
        hs, as_ = int(round(pace + margin / 2)), int(round(pace - margin / 2))
        if hs == as_:
            hs += rng.choice([2, -2]) if hs > 90 else 2
        row.update(homeScore=hs, awayScore=as_, winner=home if hs > as_ else away)
        self.games.append(row)
        poss = round(rng.gauss(99, 4), 1)
        for team, opp, lineup, pts, opts, is_home in ((home, away, hl, hs, as_, 1), (away, home, al, as_, hs, 0)):
            t, o = TEAM[team], TEAM[opp]
            lines = box(lineup, pts, season)
            win = int(pts > opts)
            agg = {k: sum(l[k] for l in lines) for k in ("fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf")}
            q = [pts // 4] * 4
            q[rng.randrange(4)] += pts - sum(q)
            rec = record.get(team, [0, 0]) if record is not None else [0, 0]
            if record is not None:
                rec = record.setdefault(team, [0, 0])
                rec[0 if win else 1] += 1
            self.ts.append(dict(
                gameId=gid, gameDateTimeEst=stamp, teamCity=t[1], teamName=t[2], teamId=team,
                opponentTeamCity=o[1], opponentTeamName=o[2], opponentTeamId=opp, home=is_home, win=win,
                teamScore=pts, opponentScore=opts, assists=agg["ast"], blocks=agg["blk"], steals=agg["stl"],
                fieldGoalsAttempted=agg["fga"], fieldGoalsMade=agg["fgm"], fieldGoalsPercentage=pct(agg["fgm"], agg["fga"]),
                threePointersAttempted=agg["tpa"], threePointersMade=agg["tpm"], threePointersPercentage=pct(agg["tpm"], agg["tpa"]),
                freeThrowsAttempted=agg["fta"], freeThrowsMade=agg["ftm"], freeThrowsPercentage=pct(agg["ftm"], agg["fta"]),
                reboundsDefensive=agg["dreb"], reboundsOffensive=agg["oreb"], reboundsTotal=agg["oreb"] + agg["dreb"],
                foulsPersonal=agg["pf"], turnovers=agg["tov"], plusMinusPoints=pts - opts, numMinutes=240.0,
                q1Points=q[0], q2Points=q[1], q3Points=q[2], q4Points=q[3],
                benchPoints=sum(l["pts"] for l in lines if not l["starter"]), biggestLead=max(0, pts - opts) + rng.randint(0, 9),
                biggestScoringRun=rng.randint(6, 16), leadChanges=rng.randint(0, 15), pointsFastBreak=rng.randint(4, 24),
                pointsFromTurnovers=rng.randint(8, 25), pointsInThePaint=rng.randint(30, 62), pointsSecondChance=rng.randint(5, 22),
                timesTied=rng.randint(0, 10), timeoutsRemaining=rng.randint(0, 3), seasonWins=rec[0], seasonLosses=rec[1],
                coachId=None, gameType=gtype, gameLabel=label, seed=(seeds or {}).get(team), gameDate=row["gameDate"]))
            ortg = round(100 * pts / poss, 1)
            drtg = round(100 * opts / poss, 1)
            self.tsx.append(dict(gameId=gid, gameDateTimeEst=stamp, teamId=team, opponentTeamId=opp, offensiveRating=ortg,
                                 defensiveRating=drtg, netRating=round(ortg - drtg, 1), pace=poss, possessions=poss, gameType=gtype))
            for l in lines:
                p = l["p"]
                pm = int(round((pts - opts) * l["m"] / 48 + rng.gauss(0, 4)))
                self.ps.append(dict(
                    firstName=p.first, lastName=p.last, personId=p.id, gameId=gid, gameDateTimeEst=stamp,
                    playerteamCity=t[1], playerteamName=t[2], opponentteamCity=o[1], opponentteamName=o[2],
                    gameType=gtype, gameLabel=label, gameSubLabel=None, seriesGameNumber=series_no, win=win, home=is_home,
                    numMinutes=f"{l['m']:.1f}", points=l["pts"], assists=l["ast"], blocks=l["blk"], steals=l["stl"],
                    fieldGoalsAttempted=l["fga"], fieldGoalsMade=l["fgm"], fieldGoalsPercentage=pct(l["fgm"], l["fga"]),
                    threePointersAttempted=l["tpa"], threePointersMade=l["tpm"], threePointersPercentage=pct(l["tpm"], l["tpa"]),
                    freeThrowsAttempted=l["fta"], freeThrowsMade=l["ftm"], freeThrowsPercentage=pct(l["ftm"], l["fta"]),
                    reboundsDefensive=l["dreb"], reboundsOffensive=l["oreb"], reboundsTotal=l["oreb"] + l["dreb"],
                    foulsPersonal=l["pf"], turnovers=l["tov"], plusMinusPoints=pm, playerteamId=team, opponentteamId=opp,
                    comment=None, startingPosition=p.pos if l["starter"] else None, gameDate=row["gameDate"]))
                tsa = 2 * (l["fga"] + 0.44 * l["fta"])
                self.psx.append(dict(
                    personId=p.id, gameId=gid, gameDateTimeEst=stamp, playerteamId=team, opponentteamId=opp,
                    numMinutes=round(l["m"], 1), offensiveRating=round(ortg + rng.gauss(0, 8), 1),
                    defensiveRating=round(drtg + rng.gauss(0, 8), 1), netRating=round(ortg - drtg + rng.gauss(0, 10), 1),
                    usagePercentage=round(min(0.45, (l["fga"] + 0.44 * l["fta"] + l["tov"]) / max(1, poss * l["m"] / 48)), 3),
                    trueShootingPercentage=round(l["pts"] / tsa, 3) if tsa else None, pace=poss,
                    possessions=round(poss * l["m"] / 48, 1), gameType=gtype))
        return row["winner"]

    def series(self, season, when, high, low, label, seeds):
        wins = {high: 0, low: 0}
        pattern = [high, high, low, low, high, low, high]
        n = 0
        while max(wins.values()) < 4:
            home = pattern[n]
            away = low if home == high else high
            w = self.play(season, when, home, away, "Playoffs", label, str(n + 1), seeds)
            wins[w] += 1
            n += 1
            when += timedelta(days=2)
        return (high if wins[high] == 4 else low), when

    def postseason(self, season, standings):
        when = datetime(season + 1, 4, 15, 19, 30)
        champs = {}
        last = when
        for conf, ids in (("East", EAST), ("West", WEST)):
            order = sorted(ids, key=lambda t: (-standings[t], rng.random()))
            seeds = {t: i + 1 for i, t in enumerate(order)}
            pin = lambda h, a, d: self.play(season, when + timedelta(days=d), h, a, "Play-in Tournament", f"{conf} Play-In", None, seeds)
            w78 = pin(order[6], order[7], 0)
            l78 = order[7] if w78 == order[6] else order[6]
            w910 = pin(order[8], order[9], 0)
            w8 = pin(l78, w910, 2)
            field = order[:6] + [w78, w8]
            seeds.update({w78: 7, w8: 8})
            t0 = when + timedelta(days=4)
            r1 = []
            for hi, lo in ((0, 7), (3, 4), (2, 5), (1, 6)):
                w, end = self.series(season, t0, field[hi], field[lo], f"{conf} First Round", seeds)
                r1.append(w)
            r2 = []
            for x, y in ((r1[0], r1[1]), (r1[2], r1[3])):
                hi, lo = sorted((x, y), key=lambda t: seeds[t])
                w, end = self.series(season, t0 + timedelta(days=16), hi, lo, f"{conf} Conf. Semifinals", seeds)
                r2.append(w)
            hi, lo = sorted(r2, key=lambda t: seeds[t])
            champs[conf], last = self.series(season, t0 + timedelta(days=32), hi, lo, f"{conf} Conf. Finals", seeds)
        e, w = champs["East"], champs["West"]
        hi, lo = (e, w) if standings[e] >= standings[w] else (w, e)
        self.series(season, last + timedelta(days=4), hi, lo, "NBA Finals", {})

    def season(self, season):
        days = make_schedule(season)
        record = {}
        trade_days = set(rng.sample(range(20, 110), 6))
        for i, (day, games) in enumerate(days):
            if i in trade_days:
                self.midseason_trade(season)
            for h, a in games:
                when = datetime(day.year, day.month, day.day, 19, 30)
                played = season < CURRENT or day < TODAY
                self.play(season, when, h, a, record=record, played=played)
        return {t: record.get(t, [0, 0])[0] for t in TEAM}


MARKER = "synthetic_league"


def is_synthetic(path):
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
            return bool(con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = ?", (MARKER,)).fetchone()[0])
    except sqlite3.Error:
        return False


def write(league, db):
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.execute(f"CREATE TABLE {MARKER} (generated_for TEXT, today TEXT)")
    con.execute(f"INSERT INTO {MARKER} VALUES ('tests', ?)", (TODAY.isoformat(),))
    def table(name, cols, rows):
        con.execute(f"CREATE TABLE {name} ({', '.join(cols)})")
        con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(cols))})",
                        [tuple(r.get(c) for c in cols) for r in rows])
    table("games", GAME_COLS, league.games)
    table("team_statistics", TS_COLS, league.ts)
    table("player_statistics", PS_COLS, league.ps)
    table("player_statistics_extended", PSX_COLS, league.psx)
    table("team_statistics_extended", TSX_COLS, league.tsx)
    con.execute("CREATE TABLE team_histories (teamId INTEGER, teamCity TEXT, teamName TEXT, teamAbbrev TEXT, "
                "seasonFounded INTEGER, seasonActiveTill INTEGER, league TEXT)")
    con.executemany("INSERT INTO team_histories VALUES (?,?,?,?,?,?,?)",
                    [(t[0], t[1], t[2], t[3], 1970, 2100, "NBA") for t in TEAMS])
    seen = {}
    for row in league.ps:
        seen.setdefault(row["personId"], []).append(int(row["gameDateTimeEst"][:4]))
    prow = []
    for p in league.players:
        years = seen.get(p.id)
        if not years:
            continue
        prow.append(dict(personId=p.id, firstName=p.first, lastName=p.last, birthDate=f"{p.born - 1}-1{rng.randint(0,2)}-1{rng.randint(0,9)}",
                         school="Synthetic U", country="USA", heightInches=rng.choice([74, 76, 78, 80, 82, 84]),
                         bodyWeightLbs=rng.randint(180, 260), jersey=str(rng.randint(0, 55)), guard=int(p.pos == "G"), forward=int(p.pos == "F"),
                         center=int(p.pos == "C"), draftYear=p.draft[0], draftRound=p.draft[1] or -1,
                         draftNumber=p.draft[2] or -1, fromYear=min(years), toYear=max(years), nbaFlag=1.0))
    table("players", PLAYER_COLS, prow)
    con.commit()
    con.close()
    print(f"games={len(league.games)} team_rows={len(league.ts)} player_rows={len(league.ps)} players={len(prow)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write a made-up league database for tests.")
    parser.add_argument("--out", required=True, type=Path, help="Database file to write.")
    parser.add_argument("--force", action="store_true", help="Replace an existing database that is not a test league.")
    args = parser.parse_args(argv)
    if args.out.exists() and not is_synthetic(args.out) and not args.force:
        sys.exit(f"{args.out} exists and is not a test league; refusing to replace it (use --force).")
    league = League()
    league.seed_league(FIRST_SEASON)
    standings = {}
    for season in range(FIRST_SEASON, CURRENT + 1):
        if season > FIRST_SEASON:
            league.offseason(season, standings)
        standings = league.season(season)
        if season <= LAST_FULL:
            league.postseason(season, standings)
        print("season", season, "done")
    write(league, args.out)


if __name__ == "__main__":
    main()
