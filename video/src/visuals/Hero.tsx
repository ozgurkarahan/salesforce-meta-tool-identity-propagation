import React from "react";
import { IconChat, IconCheck, IconGateway, IconGlobe, IconSpark, IconUser } from "../components/icons";
import { C, F } from "../theme";
import { Chip, Frame, IdentityBadge } from "./Frame";

// The plug board: hosts plug in once on the left, one agent routes, systems of record plug in on the right.
export const HOSTS = [
  { title: "Teams chats", sub: "1:1 and group chats", Icon: IconChat },
  { title: "Microsoft 365 Copilot", sub: "via Foundry publishing", Icon: IconSpark },
  { title: "Web chat", sub: "sample app included", Icon: IconGlobe },
];

export const SYSTEMS = [
  { name: "Salesforce", kind: "CRM", live: true },
  { name: "ServiceNow", kind: "ITSM", live: true },
  { name: "Jira", kind: "Work tracking", live: false },
  { name: "SAP", kind: "ERP", live: false },
  { name: "Workday", kind: "HR", live: false },
  { name: "Your API", kind: "Custom", live: false },
];

const HOST = { x: 48, w: 262, h: 78, y0: 122, gap: 22 };
const HUB = { x: 398, y: 132, w: 214, h: 252 };
const GW = { x: 680, y: 168, w: 156, h: 180 };
const SYS = { x: 890, w: 262, h: 54, y0: 104, gap: 10 };
const MID = HUB.y + HUB.h / 2;

const hostY = (i: number) => HOST.y0 + i * (HOST.h + HOST.gap);
const sysY = (i: number) => SYS.y0 + i * (SYS.h + SYS.gap);

const Zone: React.FC<{ x: number; title: string; sub: string }> = ({ x, title, sub }) => (
  <div style={{ position: "absolute", left: x, top: 36 }}>
    <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 21, lineHeight: 1.1 }}>{title}</div>
    <div style={{ fontFamily: F.body, fontSize: 16, color: C.muted, marginTop: 4 }}>{sub}</div>
  </div>
);

export const Hero: React.FC = () => (
  <Frame pad="0">
    <svg width={1200} height={600} style={{ position: "absolute", inset: 0 }} aria-hidden>
      <g fill="none" strokeLinecap="round">
        {HOSTS.map((_, i) => {
          const y = hostY(i) + HOST.h / 2;
          const x1 = HOST.x + HOST.w;
          return <path key={i} d={`M ${x1} ${y} C ${x1 + 50} ${y}, ${HUB.x - 50} ${MID}, ${HUB.x} ${MID}`} stroke={C.azure} strokeWidth={2.5} />;
        })}
        <path d={`M ${HUB.x + HUB.w} ${MID} L ${GW.x} ${MID}`} stroke={C.azure} strokeWidth={3} />
        {SYSTEMS.map((s, i) => {
          const y = sysY(i) + SYS.h / 2;
          const x0 = GW.x + GW.w;
          return (
            <path
              key={s.name}
              d={`M ${x0} ${MID} C ${x0 + 34} ${MID}, ${SYS.x - 34} ${y}, ${SYS.x} ${y}`}
              stroke={s.live ? C.azure : C.muted}
              strokeWidth={s.live ? 2.5 : 1.8}
              strokeDasharray={s.live ? undefined : "5 7"}
            />
          );
        })}
      </g>
    </svg>

    <Zone x={HOST.x} title="Engagement layer" sub="Built once, where people work" />
    <Zone x={HUB.x} title="One agent" sub="Routes every request" />
    <Zone x={GW.x} title="Identity gateway" sub="Azure API Management" />
    <Zone x={SYS.x} title="Systems of record" sub="Solid: included. Dashed: add yours." />

    {HOSTS.map(({ title, sub, Icon }, i) => (
      <div
        key={title}
        style={{
          position: "absolute",
          left: HOST.x,
          top: hostY(i),
          width: HOST.w,
          height: HOST.h,
          boxSizing: "border-box",
          display: "flex",
          alignItems: "center",
          gap: 14,
          padding: "0 16px",
          borderRadius: 16,
          background: "#FFFFFF",
          border: `2px solid ${C.line}`,
          boxShadow: "0 6px 16px rgba(22,32,47,0.06)",
        }}
      >
        <span style={{ width: 44, height: 44, borderRadius: 12, background: C.teamsSoft, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
          <Icon size={26} color={C.teams} stroke={1.9} />
        </span>
        <div>
          <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 19, lineHeight: 1.1 }}>{title}</div>
          <div style={{ fontSize: 15, color: C.muted, marginTop: 3 }}>{sub}</div>
        </div>
      </div>
    ))}

    <div
      style={{
        position: "absolute",
        left: HUB.x,
        top: HUB.y,
        width: HUB.w,
        height: HUB.h,
        boxSizing: "border-box",
        borderRadius: 26,
        background: C.azure,
        color: "#FFFFFF",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 14,
        padding: 22,
        textAlign: "center",
        boxShadow: "0 16px 36px rgba(15,108,189,0.30)",
      }}
    >
      <IconSpark size={62} color="#FFFFFF" />
      <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 25, lineHeight: 1.1 }}>Microsoft Foundry agent</div>
      <div style={{ fontSize: 16, lineHeight: 1.35, color: C.azureSoft }}>Understands the request and picks the right tools</div>
    </div>

    <div
      style={{
        position: "absolute",
        left: GW.x,
        top: GW.y,
        width: GW.w,
        height: GW.h,
        boxSizing: "border-box",
        borderRadius: 20,
        background: C.amberSoft,
        border: `2.5px solid ${C.amber}`,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 10,
        padding: 14,
        textAlign: "center",
      }}
    >
      <IconGateway size={42} color={C.amber} />
      <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 18, lineHeight: 1.15 }}>Swaps in the user's own token</div>
    </div>

    {SYSTEMS.map((s, i) => (
      <div
        key={s.name}
        style={{
          position: "absolute",
          left: SYS.x,
          top: sysY(i),
          width: SYS.w,
          height: SYS.h,
          boxSizing: "border-box",
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "0 10px 0 12px",
          borderRadius: 14,
          background: s.live ? "#FFFFFF" : "rgba(255,255,255,0.55)",
          border: s.live ? `2px solid ${C.azure}` : `1.8px dashed ${C.muted}`,
        }}
      >
        {s.live ? (
          <span style={{ width: 26, height: 26, borderRadius: 8, background: C.azure, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
            <IconCheck size={18} color="#FFFFFF" stroke={2.6} />
          </span>
        ) : (
          <span style={{ width: 22, height: 22, margin: 2, borderRadius: 7, border: `1.8px dashed ${C.muted}`, flexShrink: 0 }} />
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 19, lineHeight: 1.05, color: s.live ? C.ink : C.muted }}>{s.name}</div>
          <div style={{ fontSize: 14, color: C.muted, marginTop: 2 }}>{s.kind}</div>
        </div>
        {s.live ? (
          <Chip tone="amber" size={14}>
            as Alice
          </Chip>
        ) : (
          <Chip tone="dashed" size={13}>
            not included
          </Chip>
        )}
      </div>
    ))}

    <div
      style={{
        position: "absolute",
        left: 48,
        right: 48,
        top: 520,
        height: 52,
        boxSizing: "border-box",
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "0 16px",
        borderRadius: 14,
        background: C.amberSoft,
        border: `1.5px solid ${C.amberLine}`,
        fontSize: 18,
      }}
    >
      <IdentityBadge size={32}>
        <IconUser size={20} color="#FFFFFF" stroke={2.2} />
      </IdentityBadge>
      <span>
        <b>Alice signs in once.</b> Every wire carries her identity, so each system applies Alice's own permissions.
      </span>
    </div>
  </Frame>
);
