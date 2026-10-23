// components/share.js — a share image for a matchup, a player or a VS
// screen, drawn on a canvas in the zine style (black ground, a torn paper
// sheet, a red title strip, two team-color slabs) and handed to the system
// share sheet, or downloaded as a PNG where sharing files isn't supported.
// Every number on the image is one the page already shows.

import { readableInk } from "../colorInk.js";
import { toast } from "./toast.js";

const W = 1200;
const H = 630; // the Open Graph image size
const INK = "#101010";
const PAPER = "#f3f1ea";
const RED = "#ee3a1f";
const YELLOW = "#f3e32a";

function rng(seed) {
  let state = (Math.abs(seed) % 2147483646) + 1;
  return () => {
    state = (state * 16807) % 2147483647;
    return (state - 1) / 2147483646;
  };
}

/** A rectangle with torn edges, as a path. */
function tornRect(ctx, x, y, w, h, seed, depth = 7) {
  const random = rng(seed);
  ctx.beginPath();
  ctx.moveTo(x, y + random() * depth);
  for (let px = x; px <= x + w; px += 14) ctx.lineTo(px, y + random() * depth);
  for (let py = y; py <= y + h; py += 14) ctx.lineTo(x + w - random() * depth, py);
  for (let px = x + w; px >= x; px -= 14) ctx.lineTo(px, y + h - random() * depth);
  for (let py = y + h; py >= y; py -= 14) ctx.lineTo(x + random() * depth, py);
  ctx.closePath();
}

function fitText(ctx, text, maxWidth, size, family) {
  let current = size;
  do {
    ctx.font = `${current}px ${family}`;
    if (ctx.measureText(text).width <= maxWidth) break;
    current -= 2;
  } while (current > 12);
  return current;
}

const HEAD = '"Anton", Impact, "Arial Narrow", sans-serif';
const TYPE = '"Courier Prime", "Courier New", monospace';

/**
 * Draw the card. spec: { kicker, title, left: {name, sub, value, label, color},
 * right: {...}, lines: [string], footer }. Returns a PNG Blob.
 */
export async function renderShareImage(spec) {
  if (document.fonts?.ready) {
    try {
      await Promise.all([document.fonts.load(`80px ${HEAD}`), document.fonts.load(`20px ${TYPE}`)]);
    } catch (err) {
      /* fallback faces are fine */
    }
  }
  const canvas = document.createElement("canvas");
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#141414";
  ctx.fillRect(0, 0, W, H);

  // The paper sheet.
  ctx.fillStyle = PAPER;
  tornRect(ctx, 40, 44, W - 80, H - 96, 11, 9);
  ctx.fill();

  // Kicker + title strip.
  ctx.fillStyle = INK;
  ctx.font = `bold 22px ${TYPE}`;
  ctx.textBaseline = "top";
  ctx.fillText(String(spec.kicker || "BACKBOARD").toUpperCase(), 80, 78);
  const title = String(spec.title || "").toUpperCase();
  const size = fitText(ctx, title, W - 200, 64, HEAD);
  const titleWidth = ctx.measureText(title).width;
  ctx.fillStyle = RED;
  tornRect(ctx, 70, 112, titleWidth + 30, size + 16, 23, 5);
  ctx.fill();
  ctx.fillStyle = PAPER;
  ctx.font = `${size}px ${HEAD}`;
  ctx.fillText(title, 85, 118);

  // Two slabs.
  const slabY = 220;
  const slabH = 250;
  const slabW = (W - 200) / 2;
  [spec.left, spec.right].forEach((side, index) => {
    if (!side) return;
    const x = 80 + index * (slabW + 40);
    const color = side.color || "#2b2e32";
    const ink = readableInk(color);
    ctx.fillStyle = color;
    tornRect(ctx, x, slabY, slabW, slabH, 41 + index * 7, 8);
    ctx.fill();
    ctx.fillStyle = ink;
    ctx.font = `bold 20px ${TYPE}`;
    ctx.fillText(String(side.sub || "").toUpperCase(), x + 24, slabY + 22);
    const nameSize = fitText(ctx, String(side.name).toUpperCase(), slabW - 48, 48, HEAD);
    ctx.font = `${nameSize}px ${HEAD}`;
    ctx.fillText(String(side.name).toUpperCase(), x + 24, slabY + 52);
    // The number on a white stamp.
    ctx.fillStyle = PAPER;
    ctx.fillRect(x + 24, slabY + 128, slabW - 48, 94);
    ctx.fillStyle = INK;
    const value = String(side.value ?? "—");
    const valueSize = fitText(ctx, value, slabW / 2, 80, HEAD);
    ctx.font = `${valueSize}px ${HEAD}`;
    ctx.fillText(value, x + 40, slabY + 136);
    const valueWidth = ctx.measureText(value).width;
    ctx.font = `bold 18px ${TYPE}`;
    wrap(ctx, String(side.label || "").toUpperCase(), x + 56 + valueWidth, slabY + 150, slabW - 96 - valueWidth, 22);
  });
  if (spec.left && spec.right) {
    // The VS splat.
    const cx = W / 2;
    const cy = slabY + slabH / 2;
    ctx.fillStyle = YELLOW;
    ctx.beginPath();
    for (let i = 0; i < 28; i++) {
      const r = i % 2 ? 40 : 56;
      const a = (i / 28) * Math.PI * 2 - 0.1;
      ctx[i ? "lineTo" : "moveTo"](cx + Math.cos(a) * r, cy + Math.sin(a) * r);
    }
    ctx.closePath();
    ctx.fill();
    ctx.fillStyle = INK;
    ctx.font = `36px ${HEAD}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("VS", cx, cy + 2);
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
  }

  // Fine print.
  ctx.fillStyle = INK;
  ctx.font = `bold 18px ${TYPE}`;
  (spec.lines || []).slice(0, 2).forEach((line, i) => ctx.fillText(String(line).toUpperCase(), 80, 490 + i * 26));
  ctx.fillStyle = PAPER;
  ctx.font = `28px ${HEAD}`;
  ctx.fillText("BACKBOARD", 40, H - 40);
  ctx.font = `bold 16px ${TYPE}`;
  ctx.fillStyle = "#c9c7bf";
  ctx.textAlign = "right";
  ctx.fillText(String(spec.footer || "Probabilities are not guarantees.").toUpperCase(), W - 40, H - 34);
  ctx.textAlign = "left";

  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

function wrap(ctx, text, x, y, width, lineHeight) {
  const words = text.split(/\s+/);
  let line = "";
  let row = 0;
  for (const word of words) {
    const test = line ? `${line} ${word}` : word;
    if (ctx.measureText(test).width > width && line) {
      ctx.fillText(line, x, y + row * lineHeight);
      line = word;
      row += 1;
      if (row > 2) return;
    } else {
      line = test;
    }
  }
  if (line) ctx.fillText(line, x, y + row * lineHeight);
}

/** Share the image with the page link (system share sheet), else download it. */
export async function shareImage(spec, { filename = "backboard.png", url = window.location.href } = {}) {
  const blob = await renderShareImage(spec);
  if (!blob) {
    toast("Could not draw the image in this browser.", { tone: "warn" });
    return;
  }
  const file = new File([blob], filename, { type: "image/png" });
  if (navigator.canShare?.({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: spec.title, text: `${spec.title} — Backboard`, url });
      return;
    } catch (err) {
      if (err?.name === "AbortError") return;
    }
  }
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1500);
  toast("Image saved");
}

/** Markup for a share button. */
export function shareButton(id, label = "Share") {
  return `<button type="button" class="btn btn-secondary share-btn" id="${id}" title="Share an image of this">
    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="m8.2 10.9 7.6-3.8M8.2 13.1l7.6 3.8"/></svg>${label}</button>`;
}
