import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { appear, fade } from "../components/anim";
import { IconChat, IconCheck, IconGroup, IconKeyOff, IconShield, IconSpark, IconTrail } from "../components/icons";
import { Pill, Slide } from "../components/Slide";
import { C, F } from "../theme";

const Cursor: React.FC<{ x: number; y: number; pressed: boolean }> = ({ x, y, pressed }) => (
  <svg
    width={44}
    height={44}
    viewBox="0 0 24 24"
    style={{ position: "absolute", left: x, top: y, scale: pressed ? 0.85 : 1, filter: "drop-shadow(0 3px 4px rgba(0,0,0,0.3))" }}
    aria-hidden
  >
    <path d="M4 2 4 19 8.5 15 11.5 22 14 21 11 14 17 14z" fill="#16202F" stroke="#fff" strokeWidth={1.4} strokeLinejoin="round" />
  </svg>
);

export const Consent: React.FC = () => {
  const frame = useCurrentFrame();
  const CLICK1 = 95;
  const CLICK2 = 190;
  const allowed = frame >= CLICK1 + 6;
  const resumed = frame >= CLICK2 + 6;
  const cx = interpolate(frame, [30, CLICK1 - 10], [720, 170], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const cy = interpolate(frame, [30, CLICK1 - 10], [340, 250], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const pressed = (frame >= CLICK1 && frame < CLICK1 + 6) || (frame >= CLICK2 && frame < CLICK2 + 6);
  const buttonLabel = allowed ? "Resume" : "Allow access";
  return (
    <Slide eyebrow="First time only" title="Consent once. Then get back to work." titleSize={64}>
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 60 }}>
        <div style={{ position: "relative", width: 900, ...appear(frame, 14) }}>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 22,
              padding: "34px 40px",
              borderRadius: 22,
              background: "#FFFFFF",
              border: `1.5px solid ${C.line}`,
              boxShadow: "0 18px 50px rgba(22,32,47,0.10)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
              <IconShield size={48} color={resumed ? C.ok : C.azure} />
              <span style={{ fontFamily: F.head, fontWeight: 600, fontSize: 38 }}>{resumed ? "Connected as Alice" : "OAuth consent required"}</span>
            </div>
            <div style={{ fontFamily: F.body, fontSize: 31, color: C.muted, lineHeight: 1.35 }}>
              {resumed
                ? "Every Salesforce call now runs with Alice's own permissions."
                : allowed
                  ? "Access granted. Resume the conversation to continue."
                  : "Customer 360 Assistant wants to access Salesforce as you."}
            </div>
            {resumed ? (
              <div style={{ display: "flex", alignItems: "center", gap: 12, color: C.ok, fontFamily: F.body, fontSize: 30, fontWeight: 600 }}>
                <IconCheck size={36} color={C.ok} /> Ready
              </div>
            ) : (
              <div
                style={{
                  alignSelf: "flex-start",
                  padding: "14px 34px",
                  borderRadius: 12,
                  background: pressed ? "#0B5596" : C.azure,
                  color: "#fff",
                  fontFamily: F.body,
                  fontWeight: 600,
                  fontSize: 30,
                }}
              >
                {buttonLabel}
              </div>
            )}
          </div>
          {resumed ? null : <Cursor x={cx} y={cy} pressed={pressed} />}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 20, ...appear(frame, 220) }}>
          <Pill size={26}>Once per system</Pill>
          <Pill size={26} color={C.amber} bg={C.amberSoft} border={C.amberLine}>
            Standard OAuth
          </Pill>
        </div>
      </div>
    </Slide>
  );
};

const Tile: React.FC<{ icon: React.ReactNode; title: string; text: string; start: number; amber?: boolean }> = ({ icon, title, text, start, amber }) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        gap: 20,
        padding: "40px 38px",
        borderRadius: 24,
        background: amber ? C.amberSoft : C.node,
        border: `2px solid ${amber ? C.amberLine : C.line}`,
        ...appear(frame, start),
      }}
    >
      {icon}
      <div style={{ fontFamily: F.head, fontWeight: 800, fontSize: 48, lineHeight: 1.05 }}>{title}</div>
      <div style={{ fontFamily: F.body, fontSize: 32, color: C.muted, lineHeight: 1.35 }}>{text}</div>
    </div>
  );
};

export const Why: React.FC = () => (
  <Slide eyebrow="Why it matters" title="Trust built in, not bolted on." titleSize={68}>
    <div style={{ flex: 1, display: "flex", gap: 34, alignItems: "center", paddingBottom: 30 }}>
      <Tile icon={<IconShield size={72} color={C.azure} />} title="Her permissions" text="Each system enforces its own sharing rules and field security." start={20} />
      <Tile icon={<IconKeyOff size={72} color={C.amber} />} title="No master key" text="No shared service account reads or writes the data." start={70} amber />
      <Tile icon={<IconTrail size={72} color={C.azure} />} title="Fully traceable" text="Every read and write is logged under a real name." start={150} />
    </div>
  </Slide>
);

export const Everywhere: React.FC = () => (
  <Slide eyebrow="Where you already work" title="Inside Microsoft Teams." titleSize={68}>
    <div style={{ flex: 1, display: "flex", gap: 34, alignItems: "center", paddingBottom: 30 }}>
      <Tile icon={<IconChat size={72} color={C.azure} />} title="1:1 chat" text="Ask anything about your accounts." start={14} />
      <Tile icon={<IconGroup size={72} color={C.azure} />} title="Group chats" text="@mention it where the team talks." start={44} />
      <Tile icon={<IconSpark size={72} color={C.azure} />} title="Copilot agent" text="Available right inside Copilot." start={74} />
    </div>
  </Slide>
);

export const Close: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <Slide center>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 34 }}>
        <div
          style={{
            width: 132,
            height: 132,
            borderRadius: 36,
            background: C.azure,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            ...appear(frame, 4),
          }}
        >
          <IconSpark size={84} color="#fff" />
        </div>
        <div style={{ fontFamily: F.head, fontWeight: 800, fontSize: 104, letterSpacing: "-0.02em", ...appear(frame, 14) }}>Customer 360 Assistant</div>
        <div style={{ fontFamily: F.body, fontSize: 44, color: C.muted, ...appear(frame, 26) }}>
          Identity-aware AI across your <span style={{ color: C.azure, fontWeight: 600 }}>CRM</span> and{" "}
          <span style={{ color: C.azure, fontWeight: 600 }}>ITSM</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16, marginTop: 12, opacity: fade(frame, 50) }}>
          <Pill size={26}>github.com/ozgurkarahan/salesforce-meta-tool-identity-propagation</Pill>
          <Pill size={26}>github.com/ozgurkarahan/snow-meta-tool</Pill>
        </div>
      </div>
    </Slide>
  );
};
