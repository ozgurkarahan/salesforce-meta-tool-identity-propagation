// Exports the burned-in captions as an .srt file (useful as a LinkedIn caption track).
// Usage: npm run srt
import fs from "node:fs";
import { ABS_CAPTIONS } from "../src/timeline";
import { FPS } from "../src/theme";

const ts = (frame: number) => {
  const ms = Math.round((frame / FPS) * 1000);
  const p = (n: number, l = 2) => String(n).padStart(l, "0");
  return `${p(Math.floor(ms / 3600000))}:${p(Math.floor((ms % 3600000) / 60000))}:${p(Math.floor((ms % 60000) / 1000))},${p(ms % 1000, 3)}`;
};

const srt = ABS_CAPTIONS.map((c, i) => `${i + 1}\n${ts(c.from)} --> ${ts(c.to)}\n${c.text}\n`).join("\n");
fs.mkdirSync("out", { recursive: true });
fs.writeFileSync("out/customer360-story.en.srt", srt, "utf8");
console.log(`Wrote ${ABS_CAPTIONS.length} captions to out/customer360-story.en.srt`);
