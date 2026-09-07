// teamColors.js — official NBA franchise brand colors, keyed by numeric
// teamId (the same id returned by the backend's list_teams/predict_matchup
// tools). Used only for selective visual accents on a specific team/matchup;
// the app's overall design system stays dark/neutral otherwise.

export const TEAM_COLORS = {
  1610612737: { primary: "#E03A3E", secondary: "#C1D32F" }, // Atlanta Hawks
  1610612738: { primary: "#007A33", secondary: "#BA9653" }, // Boston Celtics
  1610612751: { primary: "#000000", secondary: "#CD1041" }, // Brooklyn Nets
  1610612766: { primary: "#1D1160", secondary: "#00788C" }, // Charlotte Hornets
  1610612741: { primary: "#CE1141", secondary: "#000000" }, // Chicago Bulls
  1610612739: { primary: "#860038", secondary: "#FDBB30" }, // Cleveland Cavaliers
  1610612742: { primary: "#00538C", secondary: "#002B5E" }, // Dallas Mavericks
  1610612743: { primary: "#0E2240", secondary: "#FEC524" }, // Denver Nuggets
  1610612765: { primary: "#C8102E", secondary: "#1D42BA" }, // Detroit Pistons
  1610612744: { primary: "#1D428A", secondary: "#FFC72C" }, // Golden State Warriors
  1610612745: { primary: "#CE1141", secondary: "#C4CED4" }, // Houston Rockets
  1610612754: { primary: "#002D62", secondary: "#FDBB30" }, // Indiana Pacers
  1610612746: { primary: "#C8102E", secondary: "#1D428A" }, // Los Angeles Clippers
  1610612747: { primary: "#552583", secondary: "#FDB927" }, // Los Angeles Lakers
  1610612763: { primary: "#5D76A9", secondary: "#12173F" }, // Memphis Grizzlies
  1610612748: { primary: "#98002E", secondary: "#F9A01B" }, // Miami Heat
  1610612749: { primary: "#00471B", secondary: "#EEE1C6" }, // Milwaukee Bucks
  1610612750: { primary: "#0C2340", secondary: "#236192" }, // Minnesota Timberwolves
  1610612740: { primary: "#0C2340", secondary: "#C8102E" }, // New Orleans Pelicans
  1610612752: { primary: "#006BB6", secondary: "#F58426" }, // New York Knicks
  1610612760: { primary: "#007AC1", secondary: "#EF3B24" }, // Oklahoma City Thunder
  1610612753: { primary: "#0077C0", secondary: "#C4CED4" }, // Orlando Magic
  1610612755: { primary: "#006BB6", secondary: "#ED174C" }, // Philadelphia 76ers
  1610612756: { primary: "#1D1160", secondary: "#E56020" }, // Phoenix Suns
  1610612757: { primary: "#E03A3E", secondary: "#000000" }, // Portland Trail Blazers
  1610612758: { primary: "#5A2D81", secondary: "#63727A" }, // Sacramento Kings
  1610612759: { primary: "#8A8D8F", secondary: "#000000" }, // San Antonio Spurs
  1610612761: { primary: "#CE1141", secondary: "#000000" }, // Toronto Raptors
  1610612762: { primary: "#002B5C", secondary: "#F9A01B" }, // Utah Jazz
  1610612764: { primary: "#002B5C", secondary: "#E31837" }, // Washington Wizards
};

export function teamColor(teamId) {
  return TEAM_COLORS[teamId] || { primary: "#3b82f6", secondary: "#8d97a6" };
}
