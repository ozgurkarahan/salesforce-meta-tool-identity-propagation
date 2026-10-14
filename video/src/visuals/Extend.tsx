import React from "react";
import { IconArrowRight, IconCheck, IconSpark } from "../components/icons";
import { C, F } from "../theme";
import { Frame, Title } from "./Frame";

const ROWS = [
  { name: "Salesforce", kind: "CRM", tools: "7 tools", source: "this repo", live: true },
  { name: "ServiceNow", kind: "ITSM", tools: "3 tools", source: "snow-meta-tool", live: true },
  { name: "Jira", kind: "Work tracking", live: false },
  { name: "SAP", kind: "ERP", live: false },
  { name: "Workday", kind: "HR", live: false },
  { name: "Your API", kind: "Custom system", live: false },
];

const Y0 = 228;
const RH = 48;
const GAP = 10;
const rowY = (i: number) => Y0 + i * (RH + GAP);
const HUB = { x: 48, w: 168 };
const SEG = [
  { x: 256, w: 158, head: "Tool server", sub: "discover, query, act" },
  { x: 426, w: 172, head: "Gateway policy", sub: "user-token exchange" },
  { x: 610, w: 214, head: "Foundry connection", sub: "attaches the tools" },
];
const SYS = { x: 866, w: 286, head: "System of record", sub: "keeps its own rules" };

const Seg: React.FC<{ x: number; w: number; y: number; live: boolean; text: string }> = ({ x, w, y, live, text }) => (
  <div
    style={{
      position: "absolute",
      left: x,
      top: y,
      width: w,
      height: RH,
      boxSizing: "border-box",
      borderRadius: 12,
      display: "flex",
      alignItems: "center",
      gap: 8,
      padding: "0 12px",
      background: live ? C.azureSoft : "transparent",
      border: live ? `1.5px solid ${C.azureLine}` : `1.6px dashed ${C.muted}`,
      color: live ? C.ink : C.muted,
      fontSize: 16,
      fontWeight: live ? 600 : 500,
    }}
  >
    {live ? <IconCheck size={18} color={C.azure} stroke={2.6} /> : null}
    {text}
  </div>
);

export const Extend: React.FC = () => (
  <Frame pad="0">
    <div style={{ position: "absolute", left: 48, top: 40, right: 48 }}>
      <Title size={40}>Your next system plugs into the same agent.</Title>
      <div style={{ fontSize: 19, color: C.muted, marginTop: 10, lineHeight: 1.35, maxWidth: 980 }}>
        Salesforce and ServiceNow are working examples. Jira, SAP, Workday and your own APIs are not included: each follows the same three-part pattern.
      </div>
    </div>

    {[...SEG, SYS].map((h) => (
      <div key={h.head} style={{ position: "absolute", left: h.x, top: 168, width: h.w }}>
        <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 18, lineHeight: 1.1 }}>{h.head}</div>
        <div style={{ fontSize: 14.5, color: C.muted, marginTop: 3 }}>{h.sub}</div>
      </div>
    ))}

    <svg width={1200} height={600} style={{ position: "absolute", inset: 0 }} aria-hidden>
      {ROWS.map((r, i) => {
        const y = rowY(i) + RH / 2;
        return (
          <line
            key={r.name}
            x1={HUB.x + HUB.w}
            y1={y}
            x2={SEG[0].x}
            y2={y}
            stroke={r.live ? C.azure : C.muted}
            strokeWidth={r.live ? 2.5 : 1.8}
            strokeDasharray={r.live ? undefined : "5 6"}
          />
        );
      })}
    </svg>

    <div
      style={{
        position: "absolute",
        left: HUB.x,
        top: Y0,
        width: HUB.w,
        height: rowY(5) + RH - Y0,
        boxSizing: "border-box",
        borderRadius: 24,
        background: C.azure,
        color: "#FFFFFF",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 14,
        textAlign: "center",
        padding: 16,
        boxShadow: "0 16px 36px rgba(15,108,189,0.28)",
      }}
    >
      <IconSpark size={56} color="#FFFFFF" />
      <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 24, lineHeight: 1.1 }}>One agent</div>
      <div style={{ fontSize: 15.5, color: C.azureSoft, lineHeight: 1.35 }}>Same Teams experience, same identity flow</div>
    </div>

    {ROWS.map((r, i) => {
      const y = rowY(i);
      return (
        <React.Fragment key={r.name}>
          <Seg x={SEG[0].x} w={SEG[0].w} y={y} live={r.live} text={r.live ? (r.tools as string) : "you add"} />
          <Seg x={SEG[1].x} w={SEG[1].w} y={y} live={r.live} text={r.live ? "OBO policy" : "you add"} />
          <Seg x={SEG[2].x} w={SEG[2].w} y={y} live={r.live} text={r.live ? "OAuth2 connection" : "you add"} />
          <div style={{ position: "absolute", left: SEG[2].x + SEG[2].w + 4, top: y + 12 }}>
            <IconArrowRight size={24} color={r.live ? C.azure : C.muted} stroke={2} />
          </div>
          <div
            style={{
              position: "absolute",
              left: SYS.x,
              top: y,
              width: SYS.w,
              height: RH,
              boxSizing: "border-box",
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "0 12px",
              borderRadius: 12,
              background: r.live ? "#FFFFFF" : "transparent",
              border: r.live ? `2px solid ${C.azure}` : `1.6px dashed ${C.muted}`,
            }}
          >
            <div style={{ lineHeight: 1.05 }}>
              <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 18, color: r.live ? C.ink : C.muted }}>{r.name}</div>
              <div style={{ fontSize: 13, color: C.muted, marginTop: 2 }}>{r.kind}</div>
            </div>
            <span style={{ marginLeft: "auto", whiteSpace: "nowrap", fontFamily: r.live ? F.mono : F.body, fontSize: 13, color: r.live ? C.azure : C.muted }}>
              {r.live ? r.source : "same pattern"}
            </span>
          </div>
        </React.Fragment>
      );
    })}
  </Frame>
);
