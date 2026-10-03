// components/scenario.js — Sandbox scenarios as shareable URL text.
//
// A scenario is { mode, season, moves }. It travels in the hash query
// (`#/sandbox?s=<text>`) as base64url-encoded UTF-8 JSON, so a link opens the
// same moves on any machine running Backboard.

export function encodeScenario(scenario) {
  const json = JSON.stringify({ mode: scenario.mode, season: scenario.season, moves: scenario.moves });
  const bytes = new TextEncoder().encode(json);
  let binary = "";
  bytes.forEach((b) => { binary += String.fromCharCode(b); });
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function decodeScenario(text) {
  const base64 = String(text).replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(base64 + "===".slice((base64.length + 3) % 4));
  const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  const parsed = JSON.parse(new TextDecoder().decode(bytes));
  return { mode: parsed.mode || "next", season: parsed.season ?? null, moves: Array.isArray(parsed.moves) ? parsed.moves : [] };
}

/** Strip display-only fields (names) before sending moves to the backend. */
export function apiMoves(moves) {
  return moves.map((move) => {
    const copy = JSON.parse(JSON.stringify(move));
    delete copy.name;
    copy.assets?.forEach((asset) => delete asset.name);
    return copy;
  });
}
