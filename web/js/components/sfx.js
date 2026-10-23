// components/sfx.js — arcade sounds for the character select and the VS
// screen, synthesized with the Web Audio API (no audio files). Off unless the
// viewer turns them on (prefs.soundOn), and silent under reduced motion.

import { soundOn } from "./prefs.js";
import { reduceMotion } from "./motion.js";

let context = null;

function audio() {
  if (!soundOn() || reduceMotion()) return null;
  try {
    context ||= new (window.AudioContext || window.webkitAudioContext)();
    if (context.state === "suspended") context.resume();
    return context;
  } catch (err) {
    return null;
  }
}

function tone(ctx, { type = "square", from, to = from, start = 0, length = 0.08, gain = 0.06 }) {
  const osc = ctx.createOscillator();
  const amp = ctx.createGain();
  const t = ctx.currentTime + start;
  osc.type = type;
  osc.frequency.setValueAtTime(from, t);
  osc.frequency.exponentialRampToValueAtTime(Math.max(20, to), t + length);
  amp.gain.setValueAtTime(gain, t);
  amp.gain.exponentialRampToValueAtTime(0.0001, t + length);
  osc.connect(amp).connect(ctx.destination);
  osc.start(t);
  osc.stop(t + length + 0.02);
}

function noise(ctx, { start = 0, length = 0.25, gain = 0.12, from = 1800, to = 200 }) {
  const frames = Math.floor(ctx.sampleRate * length);
  const buffer = ctx.createBuffer(1, frames, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < frames; i += 1) data[i] = (Math.random() * 2 - 1) * (1 - i / frames);
  const source = ctx.createBufferSource();
  const filter = ctx.createBiquadFilter();
  const amp = ctx.createGain();
  const t = ctx.currentTime + start;
  filter.type = "bandpass";
  filter.frequency.setValueAtTime(from, t);
  filter.frequency.exponentialRampToValueAtTime(to, t + length);
  amp.gain.setValueAtTime(gain, t);
  source.buffer = buffer;
  source.connect(filter).connect(amp).connect(ctx.destination);
  source.start(t);
}

/** Cursor moves to another tile. */
export function move() {
  const ctx = audio();
  if (ctx) tone(ctx, { from: 880, to: 660, length: 0.05, gain: 0.035 });
}

/** One tick of the random roulette. */
export function tick(step = 0) {
  const ctx = audio();
  if (ctx) tone(ctx, { type: "triangle", from: 520 + step * 30, length: 0.04, gain: 0.05 });
}

/** A player locked in. */
export function lock() {
  const ctx = audio();
  if (!ctx) return;
  tone(ctx, { type: "sawtooth", from: 220, to: 55, length: 0.35, gain: 0.09 });
  tone(ctx, { from: 660, start: 0.02, length: 0.12, gain: 0.05 });
  tone(ctx, { from: 990, start: 0.1, length: 0.18, gain: 0.04 });
  noise(ctx, { length: 0.3, gain: 0.08 });
}

/** The VS screen slams in. */
export function versus() {
  const ctx = audio();
  if (!ctx) return;
  noise(ctx, { length: 0.5, gain: 0.14, from: 3000, to: 120 });
  tone(ctx, { type: "sawtooth", from: 110, to: 40, start: 0.05, length: 0.6, gain: 0.1 });
  tone(ctx, { type: "square", from: 440, to: 880, start: 0.35, length: 0.2, gain: 0.04 });
}
