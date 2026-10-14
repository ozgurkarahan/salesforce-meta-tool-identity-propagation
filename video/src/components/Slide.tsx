import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { C, F } from "../theme";
import { appear } from "./anim";

export const Slide: React.FC<{
  eyebrow?: string;
  title?: React.ReactNode;
  titleSize?: number;
  center?: boolean;
  children?: React.ReactNode;
}> = ({ eyebrow, title, titleSize = 76, center = false, children }) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ backgroundColor: C.paper, fontFamily: F.body, color: C.ink }}>
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: `radial-gradient(1200px 600px at 100% 0%, ${C.azureSoft} 0%, rgba(234,243,252,0) 60%), radial-gradient(900px 500px at 0% 100%, ${C.bg} 0%, rgba(237,241,247,0) 70%)`,
        }}
      />
      <div
        style={{
          position: "absolute",
          top: 44,
          right: 72,
          fontFamily: F.mono,
          fontSize: 22,
          letterSpacing: "0.16em",
          color: C.muted,
          textTransform: "uppercase",
          display: "flex",
          alignItems: "center",
          gap: 12,
        }}
      >
        <span style={{ width: 10, height: 10, borderRadius: 3, background: C.azure, display: "inline-block" }} />
        Customer 360 Assistant
      </div>
      <div
        style={{
          position: "absolute",
          inset: 0,
          padding: "84px 110px 200px 110px",
          display: "flex",
          flexDirection: "column",
          alignItems: center ? "center" : "stretch",
          textAlign: center ? "center" : "left",
          gap: 36,
        }}
      >
        {eyebrow ? (
          <div
            style={{
              fontFamily: F.mono,
              fontSize: 26,
              letterSpacing: "0.2em",
              textTransform: "uppercase",
              color: C.azure,
              fontWeight: 500,
              ...appear(frame, 4),
            }}
          >
            {eyebrow}
          </div>
        ) : null}
        {title ? (
          <div
            style={{
              fontFamily: F.head,
              fontWeight: 800,
              fontSize: titleSize,
              lineHeight: 1.05,
              letterSpacing: "-0.02em",
              marginTop: -18,
              ...appear(frame, 10),
            }}
          >
            {title}
          </div>
        ) : null}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>{children}</div>
      </div>
    </AbsoluteFill>
  );
};

export const Pill: React.FC<{ children: React.ReactNode; color?: string; bg?: string; border?: string; size?: number }> = ({
  children,
  color = C.azure,
  bg = C.azureSoft,
  border = C.azureLine,
  size = 26,
}) => (
  <span
    style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 10,
      padding: "8px 20px",
      borderRadius: 999,
      background: bg,
      border: `1.5px solid ${border}`,
      color,
      fontFamily: F.mono,
      fontSize: size,
      fontWeight: 500,
      letterSpacing: "0.04em",
      whiteSpace: "nowrap",
    }}
  >
    {children}
  </span>
);
