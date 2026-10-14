import React from "react";
import { AbsoluteFill } from "remotion";
import { C, F } from "../theme";

/** White card used by every README / project-page visual. `bleed` drops the rounded transparent corners (social cards);
 * `wash` adds the video's soft radial tint (kept off for README PNGs: gradients defeat PNG compression). */
export const Frame: React.FC<{ children: React.ReactNode; pad?: string; bleed?: boolean; wash?: boolean }> = ({
  children,
  pad = "36px 48px",
  bleed = false,
  wash = false,
}) => (
  <AbsoluteFill>
    <div
      style={{
        position: "absolute",
        inset: 0,
        borderRadius: bleed ? 0 : 28,
        overflow: "hidden",
        background: C.paper,
        border: bleed ? "none" : `2px solid ${C.line}`,
        boxSizing: "border-box",
        fontFamily: F.body,
        color: C.ink,
      }}
    >
      {wash ? (
        <div
          style={{
            position: "absolute",
            inset: 0,
            background: `radial-gradient(900px 460px at 100% 0%, ${C.azureSoft} 0%, rgba(234,243,252,0) 62%), radial-gradient(760px 420px at 0% 100%, ${C.bg} 0%, rgba(237,241,247,0) 70%)`,
          }}
        />
      ) : null}
      <div style={{ position: "relative", height: "100%", boxSizing: "border-box", padding: pad, display: "flex", flexDirection: "column" }}>
        {children}
      </div>
    </div>
  </AbsoluteFill>
);

export const Brand: React.FC<{ right?: string }> = ({ right }) => (
  <div
    style={{
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      fontFamily: F.mono,
      fontSize: 15,
      letterSpacing: "0.16em",
      textTransform: "uppercase",
      color: C.muted,
    }}
  >
    <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <span style={{ width: 9, height: 9, borderRadius: 3, background: C.azure, display: "inline-block" }} />
      Customer 360 Assistant
    </span>
    {right ? <span>{right}</span> : null}
  </div>
);

export const Title: React.FC<{ children: React.ReactNode; size?: number }> = ({ children, size = 42 }) => (
  <div style={{ fontFamily: F.head, fontWeight: 800, fontSize: size, lineHeight: 1.06, letterSpacing: "-0.02em" }}>{children}</div>
);

export const Label: React.FC<{ children: React.ReactNode; color?: string }> = ({ children, color = C.muted }) => (
  <div style={{ fontFamily: F.mono, fontSize: 15, letterSpacing: "0.14em", textTransform: "uppercase", color, fontWeight: 500 }}>{children}</div>
);

type Tone = "plain" | "azure" | "amber" | "dashed";

const TONES: Record<Tone, React.CSSProperties> = {
  plain: { background: "#FFFFFF", border: `1.5px solid ${C.line}`, color: C.ink },
  azure: { background: C.azureSoft, border: `1.5px solid ${C.azureLine}`, color: C.azure },
  amber: { background: C.amberSoft, border: `1.5px solid ${C.amberLine}`, color: C.amberInk },
  dashed: { background: "transparent", border: `1.5px dashed ${C.muted}`, color: C.muted },
};

export const Chip: React.FC<{ children: React.ReactNode; tone?: Tone; icon?: React.ReactNode; size?: number }> = ({ children, tone = "plain", icon, size = 16 }) => (
  <span
    style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 8,
      padding: size < 16 ? "4px 10px" : "6px 14px",
      borderRadius: 999,
      fontFamily: F.body,
      fontSize: size,
      fontWeight: 600,
      whiteSpace: "nowrap",
      ...TONES[tone],
    }}
  >
    {icon}
    {children}
  </span>
);

/** Square amber badge with a white glyph or digit, used for the user-identity motif. */
export const IdentityBadge: React.FC<{ size?: number; children: React.ReactNode }> = ({ size = 34, children }) => (
  <span
    style={{
      width: size,
      height: size,
      borderRadius: size * 0.3,
      background: C.amber,
      color: "#FFFFFF",
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      flexShrink: 0,
      fontFamily: F.mono,
      fontWeight: 500,
      fontSize: size * 0.55,
    }}
  >
    {children}
  </span>
);
