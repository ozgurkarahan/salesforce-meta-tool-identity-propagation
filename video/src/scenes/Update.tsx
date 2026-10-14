import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { appear, fade, typed } from "../components/anim";
import { Avatar, ChatWindow, Mention } from "../components/ChatWindow";
import { IconCheck, IconClock, IconSpark } from "../components/icons";
import { Pill, Slide } from "../components/Slide";
import { C, F } from "../theme";

export const Later: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <Slide center>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 34 }}>
        <div style={{ ...appear(frame, 4), rotate: `${interpolate(frame, [0, 150], [0, 30])}deg` }}>
          <IconClock size={150} color={C.azure} />
        </div>
        <div style={{ fontFamily: F.head, fontWeight: 800, fontSize: 96, letterSpacing: "-0.02em", ...appear(frame, 14) }}>30 minutes later…</div>
        <div style={{ fontFamily: F.body, fontSize: 44, color: C.muted, ...appear(frame, 30) }}>The call is done. Time to update the CRM.</div>
      </div>
    </Slide>
  );
};

const Q2 =
  "@Customer 360 Assistant Add a note on the Contoso Analytics Add-on opp: customer wants a POC first. Move the close date to next quarter.";

export const AskUpdate: React.FC = () => {
  const frame = useCurrentFrame();
  const sent = frame >= 205;
  return (
    <Slide eyebrow="Update by asking" title="No forms. Just ask." titleSize={64}>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 24, minHeight: 0 }}>
        <ChatWindow
          messages={[
            {
              author: "Bot",
              time: "9:55 AM",
              at: 0,
              content: <span style={{ color: C.muted }}>Customer 360 view for Contoso Ltd: pipeline, cases, incidents, next actions.</span>,
            },
            {
              author: "Alice",
              time: "10:32 AM",
              at: 205,
              content: (
                <>
                  <Mention>@Customer 360 Assistant</Mention> Add a note on the Contoso Analytics Add-on opp: customer wants a POC first. Move the
                  close date to next quarter.
                </>
              ),
            },
          ]}
          composer={sent ? "" : typed(Q2, frame, 24, 30)}
          composerActive={!sent}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 20, height: 60, opacity: fade(frame, 240) }}>
          <span style={{ fontFamily: F.body, fontSize: 30, color: C.muted }}>Behind the scenes, acting as Alice:</span>
          <span style={appear(frame, 255)}>
            <Pill>salesforce · write_record</Pill>
          </span>
        </div>
      </div>
    </Slide>
  );
};

const Field: React.FC<{ label: string; children: React.ReactNode; style?: React.CSSProperties }> = ({ label, children, style }) => (
  <div style={{ display: "flex", flexDirection: "column", gap: 6, ...style }}>
    <span style={{ fontFamily: F.mono, fontSize: 21, letterSpacing: "0.1em", color: C.muted, textTransform: "uppercase" }}>{label}</span>
    <span style={{ fontFamily: F.body, fontSize: 32, fontWeight: 500 }}>{children}</span>
  </div>
);

export const RecordCard: React.FC<{ frame: number; dateSwap: number; noteAt: number; modifiedAt: number }> = ({ frame, dateSwap, noteAt, modifiedAt }) => {
  const swap = fade(frame, dateSwap, 18);
  const mod = fade(frame, modifiedAt, 20);
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        gap: 24,
        padding: "28px 36px",
        borderRadius: 22,
        background: "#FFFFFF",
        border: `1.5px solid ${C.line}`,
        boxShadow: "0 18px 50px rgba(22,32,47,0.10)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <span style={{ fontFamily: F.mono, fontSize: 22, letterSpacing: "0.16em", color: C.azure }}>SALESFORCE · OPPORTUNITY</span>
      </div>
      <div style={{ fontFamily: F.head, fontWeight: 800, fontSize: 50, marginTop: -10 }}>Contoso Analytics Add-on</div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", rowGap: 22, columnGap: 30 }}>
        <Field label="Account">Contoso Ltd</Field>
        <Field label="Amount">$350,000</Field>
        <Field label="Stage">Qualification</Field>
        <Field label="Close date">
          <span style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 16 }}>
            <span style={{ color: swap > 0.5 ? C.muted : C.ink, textDecoration: swap > 0.5 ? "line-through" : "none" }}>Dec 18, 2026</span>
            <span
              style={{
                opacity: swap,
                padding: "2px 14px",
                borderRadius: 10,
                background: C.okSoft,
                color: C.ok,
                fontWeight: 600,
                border: `1.5px solid ${C.ok}`,
              }}
            >
              Mar 31, 2027
            </span>
          </span>
        </Field>
      </div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "16px 20px",
          borderRadius: 14,
          background: C.node,
          border: `1.5px solid ${C.line}`,
          ...appear(frame, noteAt),
        }}
      >
        <span style={{ fontFamily: F.mono, fontSize: 21, color: C.muted, letterSpacing: "0.1em" }}>NOTE</span>
        <span style={{ fontFamily: F.body, fontSize: 30 }}>“Customer wants a POC first.”</span>
      </div>
      <div
        style={{
          marginTop: "auto",
          display: "flex",
          alignItems: "center",
          gap: 16,
          paddingTop: 18,
          borderTop: `1.5px solid ${C.line}`,
          opacity: 0.35 + 0.65 * mod,
        }}
      >
        <span style={{ fontFamily: F.mono, fontSize: 22, color: C.muted, letterSpacing: "0.08em" }}>LAST MODIFIED BY</span>
        <Avatar who="Alice" size={40} />
        <span style={{ fontFamily: F.body, fontSize: 30, fontWeight: 600 }}>Alice</span>
        <span style={{ fontFamily: F.body, fontSize: 26, color: C.muted }}>· just now</span>
      </div>
    </div>
  );
};

export const WriteBack: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <Slide eyebrow="Written to Salesforce" title="Done. Two updates, zero clicks." titleSize={64}>
      <div style={{ flex: 1, display: "flex", gap: 40, alignItems: "stretch" }}>
        <div style={{ width: 620, display: "flex", flexDirection: "column", gap: 18, ...appear(frame, 20) }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <div style={{ width: 50, height: 50, borderRadius: 14, background: C.azure, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <IconSpark size={32} color="#fff" />
            </div>
            <span style={{ fontFamily: F.body, fontWeight: 600, fontSize: 28 }}>Customer 360 Assistant</span>
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 22,
              padding: "26px 30px",
              borderRadius: 18,
              background: "#FFFFFF",
              border: `1.5px solid ${C.line}`,
              fontFamily: F.body,
              fontSize: 32,
              lineHeight: 1.35,
            }}
          >
            <div style={{ display: "flex", gap: 14, ...appear(frame, 40) }}>
              <IconCheck size={40} color={C.ok} />
              <span>Note logged on Contoso Analytics Add-on</span>
            </div>
            <div style={{ display: "flex", gap: 14, ...appear(frame, 70) }}>
              <IconCheck size={40} color={C.ok} />
              <span>Close date moved to Mar 31, 2027</span>
            </div>
          </div>
        </div>
        <div style={{ flex: 1, display: "flex", ...appear(frame, 100) }}>
          <RecordCard frame={frame} dateSwap={150} noteAt={180} modifiedAt={240} />
        </div>
      </div>
    </Slide>
  );
};

export const Pivot: React.FC = () => {
  const frame = useCurrentFrame();
  const zoom = interpolate(frame, [20, 70], [0.9, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <Slide center eyebrow="Look closer" title={<>Not a bot. Not a service account.</>} titleSize={78}>
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 30,
            padding: "36px 56px",
            borderRadius: 28,
            background: "#FFFFFF",
            border: `3px solid ${C.amber}`,
            boxShadow: `0 0 0 ${interpolate(frame, [60, 100], [0, 14], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })}px ${C.amberSoft}`,
            scale: zoom,
            ...{ opacity: fade(frame, 20) },
          }}
        >
          <span style={{ fontFamily: F.mono, fontSize: 34, color: C.muted, letterSpacing: "0.08em" }}>LAST MODIFIED BY</span>
          <Avatar who="Alice" size={88} />
          <span style={{ fontFamily: F.head, fontWeight: 800, fontSize: 92, color: C.amber }}>Alice</span>
        </div>
      </div>
    </Slide>
  );
};
