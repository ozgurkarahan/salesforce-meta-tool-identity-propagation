import React from "react";
import { useCurrentFrame } from "remotion";
import { appear, fade, typed } from "../components/anim";
import { ChatWindow, Mention } from "../components/ChatWindow";
import { IconAlert, IconArrowRight, IconPipeline, IconSpark, IconTicket } from "../components/icons";
import { Pill, Slide } from "../components/Slide";
import { C, F } from "../theme";

// Vertical centers of the two rows inside a Block (padding 22 + header ~46 + gap 14, rows 74 + gap 14).
const ROW1 = 119;
const ROW2 = 207;

const Q1 = "@Customer 360 Assistant Give me a Customer 360 view for Contoso Ltd";

export const Ask360: React.FC = () => {
  const frame = useCurrentFrame();
  const sent = frame >= 140;
  return (
    <Slide eyebrow="One question" title="Alice just asks in Teams." titleSize={64}>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 24, minHeight: 0 }}>
        <ChatWindow
          messages={[
            { author: "Bob", time: "9:54 AM", at: 0, content: "Contoso call at 10. Anything I should know?" },
            {
              author: "Alice",
              time: "9:55 AM",
              at: 140,
              content: (
                <>
                  <Mention>@Customer 360 Assistant</Mention> Give me a Customer 360 view for Contoso Ltd
                </>
              ),
            },
          ]}
          composer={sent ? "" : typed(Q1, frame, 36)}
          composerActive={!sent}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 20, height: 60, opacity: fade(frame, 175) }}>
          <span style={{ fontFamily: F.body, fontSize: 30, color: C.muted }}>Behind the scenes, acting as Alice:</span>
          <span style={appear(frame, 190)}>
            <Pill>salesforce · soql_query</Pill>
          </span>
          <span style={appear(frame, 215)}>
            <Pill>servicenow · query</Pill>
          </span>
        </div>
      </div>
    </Slide>
  );
};

const Row: React.FC<{ id: string; text: string; highlight: number; frame: number }> = ({ id, text, highlight, frame }) => {
  const h = fade(frame, highlight, 20);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 18,
        height: 74,
        padding: "0 20px",
        borderRadius: 12,
        background: `rgba(255, 244, 228, ${h})`,
        border: `2px solid rgba(240, 192, 120, ${h})`,
        boxSizing: "border-box",
        paddingTop: 16,
      }}
    >
      <span style={{ fontFamily: F.mono, fontSize: 26, color: C.azure, fontWeight: 500 }}>{id}</span>
      <span style={{ fontFamily: F.body, fontSize: 31, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{text}</span>
    </div>
  );
};

const Block: React.FC<{ icon: React.ReactNode; label: string; source: string; start: number; children: React.ReactNode; style?: React.CSSProperties }> = ({
  icon,
  label,
  source,
  start,
  children,
  style,
}) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 14,
        padding: "22px 26px",
        borderRadius: 20,
        background: "#FFFFFF",
        border: `1.5px solid ${C.azureLine}`,
        ...appear(frame, start),
        ...style,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        {icon}
        <span style={{ fontFamily: F.head, fontWeight: 600, fontSize: 36 }}>{label}</span>
        <span style={{ marginLeft: "auto", fontFamily: F.mono, fontSize: 22, color: C.muted, letterSpacing: "0.1em" }}>{source}</span>
      </div>
      {children}
    </div>
  );
};

export const Answer360: React.FC = () => {
  const frame = useCurrentFrame();
  const LINK = 630;
  const linkP = fade(frame, LINK, 40);
  return (
    <Slide eyebrow="Customer 360 view · Contoso Ltd">
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 22, marginTop: -8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 18, ...appear(frame, 6) }}>
          <div style={{ width: 54, height: 54, borderRadius: 15, background: C.azure, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <IconSpark size={34} color="#fff" />
          </div>
          <span style={{ fontFamily: F.body, fontWeight: 600, fontSize: 32 }}>Customer 360 Assistant</span>
          <span style={{ fontFamily: F.body, fontSize: 28, color: C.muted }}>replied in the group chat</span>
        </div>

        <Block icon={<IconPipeline size={40} color={C.azure} />} label="Open pipeline" source="SALESFORCE" start={24}>
          <div style={{ display: "flex", alignItems: "center", gap: 28 }}>
            <span style={{ fontFamily: F.body, fontSize: 36, fontWeight: 600 }}>Contoso Analytics Add-on</span>
            <span style={{ fontFamily: F.head, fontWeight: 800, fontSize: 48, color: C.azure }}>$350k</span>
            <Pill size={24}>Stage · Qualification</Pill>
          </div>
        </Block>

        <div style={{ display: "flex", alignItems: "stretch", gap: 0 }}>
          <Block icon={<IconTicket size={40} color={C.azure} />} label="Open cases" source="SALESFORCE" start={210} style={{ flex: 1 }}>
            <Row id="00001029" text="Trading desk dashboard timeout" highlight={LINK} frame={frame} />
            <Row id="00001028" text="Report generation slow" highlight={LINK} frame={frame} />
          </Block>
          <div style={{ width: 110, position: "relative" }}>
            <svg width={110} height={266} style={{ position: "absolute", top: 0, left: 0, overflow: "visible" }}>
              <path d={`M0 ${ROW1} L 110 ${ROW1}`} stroke={C.amber} strokeWidth={4} fill="none" strokeDasharray="200" strokeDashoffset={200 * (1 - linkP)} />
              <path d={`M0 ${ROW2} C 55 ${ROW2}, 55 ${ROW1}, 110 ${ROW1}`} stroke={C.amber} strokeWidth={4} fill="none" strokeDasharray="200" strokeDashoffset={200 * (1 - linkP)} />
            </svg>
          </div>
          <Block icon={<IconAlert size={40} color={C.azure} />} label="IT incidents" source="SERVICENOW" start={420} style={{ flex: 1 }}>
            <Row id="INC0010004" text="Database performance degradation" highlight={LINK} frame={frame} />
            <Row id="INC0010007" text="Network latency, EU-West" highlight={100000} frame={frame} />
          </Block>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 22,
            padding: "20px 30px",
            borderRadius: 20,
            background: C.amberSoft,
            border: `2px solid ${C.amberLine}`,
            ...appear(frame, 690),
          }}
        >
          <IconAlert size={46} color={C.amber} />
          <span style={{ fontFamily: F.head, fontWeight: 800, fontSize: 38, color: C.amber }}>$350k at risk</span>
          <span style={{ fontFamily: F.body, fontSize: 31 }}>Both cases trace back to one DB incident.</span>
          <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10, fontFamily: F.body, fontSize: 29, fontWeight: 600, color: C.azure }}>
            <IconArrowRight size={34} color={C.azure} /> Share the fix ETA on the call
          </span>
        </div>
      </div>
    </Slide>
  );
};
