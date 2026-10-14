import { Easing, interpolate } from "remotion";

const ease = Easing.bezier(0.16, 1, 0.3, 1);
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

/** Fade + small upward move, starting at `start` frame. */
export const appear = (frame: number, start: number, dur = 18, dy = 14) => ({
  opacity: interpolate(frame, [start, start + dur], [0, 1], { ...clamp, easing: ease }),
  translate: `0px ${interpolate(frame, [start, start + dur], [dy, 0], { ...clamp, easing: ease })}px`,
});

export const fade = (frame: number, start: number, dur = 15) =>
  interpolate(frame, [start, start + dur], [0, 1], { ...clamp, easing: ease });

export const fadeOut = (frame: number, start: number, dur = 12) =>
  interpolate(frame, [start, start + dur], [1, 0], { ...clamp, easing: ease });

export const progress = (frame: number, start: number, end: number, easing = ease) =>
  interpolate(frame, [start, end], [0, 1], { ...clamp, easing });

/** Number of characters to show for a typing effect. */
export const typed = (text: string, frame: number, start: number, charsPerSecond = 28, fps = 30) =>
  text.slice(0, Math.max(0, Math.floor(((frame - start) / fps) * charsPerSecond)));
