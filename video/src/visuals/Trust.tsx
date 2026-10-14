import React from "react";
import { IconKeyOff } from "../components/icons";
import { C, F } from "../theme";
import { Chip, Frame, Title } from "./Frame";

const STEPS = [
  { title: "Sign in once", text: "Alice signs in to Teams with her work account (Microsoft Entra ID)." },
  { title: "Consent once per system, by design", text: "Standard OAuth consent the first time; a known platform issue can ask again after about an hour." },
  { title: "The gateway swaps the token", text: "API Management checks Alice's token, then gets Alice's own token for each system." },
  { title: "Each system applies its rules", text: "Sharing rules, field-level security and ACLs decide what Alice can see and change." },
];

const COL = 1104 / 5;
const LINE_Y = 186;
const cx = (i: number) => 48 + COL * i + COL / 2;

const Station: React.FC<{ i: number }> = ({ i }) => (
  <div
    style={{
      position: "absolute",
      left: cx(i) - 23,
      top: LINE_Y - 23,
      width: 46,
      height: 46,
      borderRadius: 14,
      background: C.amber,
      color: "#FFFFFF",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontFamily: F.head,
      fontWeight: 800,
      fontSize: 24,
      boxShadow: "0 6px 14px rgba(194,98,10,0.28)",
    }}
  >
    {i + 1}
  </div>
);

export const Trust: React.FC = () => (
  <Frame pad="0">
    <div style={{ position: "absolute", left: 48, top: 40, right: 48 }}>
      <Title size={40}>Acts as the user, never as a super-user.</Title>
      <div style={{ fontSize: 19, color: C.muted, marginTop: 10 }}>Follow Alice's identity from Teams to the record she changes.</div>
    </div>

    <svg width={1200} height={560} style={{ position: "absolute", inset: 0 }} aria-hidden>
      <line x1={cx(0)} y1={LINE_Y} x2={cx(4)} y2={LINE_Y} stroke={C.amber} strokeWidth={3} />
    </svg>

    {[0, 1, 2, 3, 4].map((i) => (
      <Station key={i} i={i} />
    ))}

    {STEPS.map((s, i) => (
      <div key={s.title} style={{ position: "absolute", left: cx(i) - 98, top: LINE_Y + 46, width: 196, textAlign: "center" }}>
        <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 20, lineHeight: 1.15 }}>{s.title}</div>
        <div style={{ fontSize: 16.5, color: C.muted, lineHeight: 1.38, marginTop: 8 }}>{s.text}</div>
      </div>
    ))}

    <div style={{ position: "absolute", left: cx(4) - 102, top: LINE_Y + 46, width: 204 }}>
      <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 20, lineHeight: 1.15, textAlign: "center" }}>Writes carry her name</div>
      <div
        style={{
          marginTop: 12,
          padding: "12px 14px",
          borderRadius: 14,
          background: "#FFFFFF",
          border: `1.5px solid ${C.line}`,
          boxShadow: "0 10px 24px rgba(22,32,47,0.08)",
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        <div style={{ fontSize: 13.5, color: C.muted }}>Opportunity · example data</div>
        <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 17, lineHeight: 1.15 }}>Contoso Analytics Add-on</div>
        <div style={{ height: 1, background: C.line, margin: "4px 0" }} />
        <div style={{ fontSize: 13.5, color: C.muted }}>Last modified by</div>
        <div>
          <Chip tone="amber" size={14}>
            Alice
          </Chip>
        </div>
      </div>
    </div>

    <div
      style={{
        position: "absolute",
        left: 48,
        right: 48,
        top: 470,
        height: 54,
        boxSizing: "border-box",
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "0 18px",
        borderRadius: 14,
        background: C.azureSoft,
        border: `1.5px solid ${C.azureLine}`,
        fontSize: 17.5,
      }}
    >
      <IconKeyOff size={28} color={C.azure} stroke={1.9} />
      <span>
        <b>No shared credential touches business data.</b> A minimal-access service account only maps Alice to her Salesforce user.
      </span>
    </div>
  </Frame>
);
