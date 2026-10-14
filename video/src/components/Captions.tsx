import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { ABS_CAPTIONS } from "../timeline";
import { C, F } from "../theme";

export const Captions: React.FC = () => {
  const frame = useCurrentFrame();
  const cap = [...ABS_CAPTIONS].reverse().find((c) => frame >= c.from && frame < c.to);
  if (!cap) return null;
  const opacity = interpolate(frame, [cap.from, cap.from + 6, cap.to - 6, cap.to], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: 56, pointerEvents: "none" }}>
      <div
        style={{
          opacity,
          maxWidth: 1560,
          padding: "16px 34px",
          borderRadius: 16,
          background: "rgba(22, 32, 47, 0.88)",
          color: "#FFFFFF",
          fontFamily: F.body,
          fontWeight: 500,
          fontSize: 42,
          lineHeight: 1.3,
          textAlign: "center",
          boxShadow: `0 6px 24px rgba(22,32,47,0.18)`,
          borderBottom: `3px solid ${C.amber}`,
        }}
      >
        {cap.text}
      </div>
    </AbsoluteFill>
  );
};
