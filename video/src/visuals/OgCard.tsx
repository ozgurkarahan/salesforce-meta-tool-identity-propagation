import React from "react";
import { IconArrowRight, IconSpark, IconUser } from "../components/icons";
import { C, F } from "../theme";
import { Chip, Frame, IdentityBadge } from "./Frame";

/** 1200x630 social card (Open Graph / LinkedIn / GitHub social preview). */
export const OgCard: React.FC = () => (
  <Frame bleed wash pad="56px 72px 48px">
    <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center" }}>
      <div style={{ fontFamily: F.head, fontWeight: 800, fontSize: 66, lineHeight: 1.03, letterSpacing: "-0.025em", maxWidth: 1040 }}>
        Build the engagement layer once.
        <br />
        <span style={{ color: C.azure }}>Let one agent act across all your systems of record.</span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 48, flexWrap: "wrap" }}>
        <Chip size={20}>Microsoft Teams</Chip>
        <IconArrowRight size={28} color={C.muted} stroke={2} />
        <Chip size={20} tone="azure" icon={<IconSpark size={22} color={C.azure} />}>
          One Foundry agent
        </Chip>
        <IconArrowRight size={28} color={C.muted} stroke={2} />
        <Chip size={20}>Salesforce</Chip>
        <Chip size={20}>ServiceNow</Chip>
        <Chip size={20} tone="dashed">
          your next system
        </Chip>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 26, fontSize: 25 }}>
        <IdentityBadge size={38}>
          <IconUser size={23} color="#FFFFFF" stroke={2.2} />
        </IdentityBadge>
        <span>
          Every action runs as the <b>signed-in user</b>, with their own permissions.
        </span>
      </div>
    </div>

    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 19, color: C.muted }}>
      <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ width: 11, height: 11, borderRadius: 3, background: C.azure, display: "inline-block" }} />
        Customer 360 Assistant · open-source reference implementation
      </span>
      <span style={{ fontFamily: F.mono, fontSize: 18 }}>ozgurkarahan.com/customer-360</span>
    </div>
  </Frame>
);
