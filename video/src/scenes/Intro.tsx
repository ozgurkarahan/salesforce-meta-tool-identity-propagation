import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { appear, fade } from "../components/anim";
import { IconAlert, IconClock, IconPipeline, IconTicket } from "../components/icons";
import { Pill, Slide } from "../components/Slide";
import { C, F } from "../theme";

export const Hook: React.FC = () => {
  const frame = useCurrentFrame();
  const remaining = 300 - Math.floor(frame / 30);
  const mm = String(Math.floor(remaining / 60)).padStart(2, "0");
  const ss = String(remaining % 60).padStart(2, "0");
  const spread = interpolate(frame, [130, 200], [0, 220], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <Slide center eyebrow="A real Monday morning" title={<>Your customer call starts in <span style={{ color: C.amber }}>5 minutes.</span></>} titleSize={88}>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 56 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 28,
            padding: "22px 48px",
            borderRadius: 28,
            background: C.amberSoft,
            border: `2px solid ${C.amberLine}`,
            ...appear(frame, 24),
          }}
        >
          <IconClock size={84} color={C.amber} />
          <span style={{ fontFamily: F.mono, fontWeight: 500, fontSize: 120, color: C.amber, letterSpacing: "0.04em" }}>
            {mm}:{ss}
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 30, opacity: fade(frame, 110) }}>
          <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 52 }}>Your data lives in 2 systems.</div>
          <div style={{ display: "flex", gap: 40 + spread }}>
            <Pill size={32}>CRM · Salesforce</Pill>
            <Pill size={32}>ITSM · ServiceNow</Pill>
          </div>
        </div>
      </div>
    </Slide>
  );
};

const SiloCard: React.FC<{ icon: React.ReactNode; who: string; sees: string; start: number }> = ({ icon, who, sees, start }) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 26,
        padding: 44,
        borderRadius: 24,
        background: C.node,
        border: `1.5px solid ${C.line}`,
        ...appear(frame, start),
      }}
    >
      {icon}
      <div style={{ fontFamily: F.mono, fontSize: 28, letterSpacing: "0.18em", color: C.muted, textTransform: "uppercase" }}>{who}</div>
      <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 54, textAlign: "center" }}>{sees}</div>
    </div>
  );
};

export const Silo: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <Slide eyebrow="The problem" title="Two teams. Two systems. One customer.">
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 34 }}>
        <div style={{ flex: 1, display: "flex", alignItems: "stretch", gap: 0 }}>
          <SiloCard icon={<IconPipeline size={96} color={C.azure} />} who="Sales · CRM" sees="sees the pipeline" start={24} />
          <div style={{ width: 120, display: "flex", alignItems: "center", justifyContent: "center", opacity: fade(frame, 60) }}>
            <div style={{ height: "80%", borderLeft: `4px dashed ${C.muted}` }} />
          </div>
          <SiloCard icon={<IconTicket size={96} color={C.azure} />} who="Support · ITSM" sees="sees the incidents" start={42} />
        </div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 22,
            padding: "26px 36px",
            borderRadius: 20,
            background: C.amberSoft,
            border: `2px solid ${C.amberLine}`,
            ...appear(frame, 110),
          }}
        >
          <IconAlert size={56} color={C.amber} />
          <span style={{ fontFamily: F.head, fontWeight: 800, fontSize: 52, color: C.amber }}>Nobody sees the risk.</span>
        </div>
      </div>
    </Slide>
  );
};
