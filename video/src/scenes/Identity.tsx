import React from "react";
import { Easing, interpolate, useCurrentFrame } from "remotion";
import { appear, fade, fadeOut } from "../components/anim";
import { IconDatabase, IconGateway, IconSpark, IconUser } from "../components/icons";
import { Pill, Slide } from "../components/Slide";
import { C, F } from "../theme";

const W = 1550;
const H = 540;
const LANE_Y = 36;
const LINE_Y = 230;
const X = { teams: 150, agent: 520, gw: 960, sys: 1400 };
const T = { teams: 170, agent: 360, gw: 540, exchange: 700, sf: 760, sn: 790, rules: 830 };

const Node: React.FC<{
  x: number;
  top: number;
  w: number;
  h: number;
  icon: React.ReactNode;
  title: string;
  sub: string;
  start: number;
  active: number;
  children?: React.ReactNode;
}> = ({ x, top, w, h, icon, title, sub, start, active, children }) => {
  const frame = useCurrentFrame();
  const a = fade(frame, active, 15);
  return (
    <div
      style={{
        position: "absolute",
        left: x - w / 2,
        top,
        width: w,
        height: h,
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 10,
        padding: 18,
        borderRadius: 20,
        background: a > 0.5 ? C.azureSoft : C.node,
        border: `2px solid ${a > 0.5 ? C.azure : C.line}`,
        textAlign: "center",
        ...appear(frame, start),
      }}
    >
      {icon}
      <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 32, lineHeight: 1.1 }}>{title}</div>
      {sub ? <div style={{ fontFamily: F.body, fontSize: 23, color: C.muted, lineHeight: 1.25 }}>{sub}</div> : null}
      {children}
    </div>
  );
};

const Step: React.FC<{ n: number; text: string; at: number }> = ({ n, text, at }) => {
  const frame = useCurrentFrame();
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, width: "100%", ...appear(frame, at) }}>
      <span
        style={{
          width: 36,
          height: 36,
          borderRadius: 10,
          background: C.amber,
          color: "#fff",
          fontFamily: F.mono,
          fontSize: 22,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        {n}
      </span>
      <span style={{ fontFamily: F.body, fontSize: 25, textAlign: "left", lineHeight: 1.2 }}>{text}</span>
    </div>
  );
};

export const Identity: React.FC = () => {
  const frame = useCurrentFrame();
  const ease = Easing.bezier(0.45, 0, 0.2, 1);
  const tokenX = interpolate(frame, [T.teams, T.agent - 40, T.agent, T.gw - 40, T.gw], [X.teams, X.teams, X.agent, X.agent, X.gw], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });
  const tokenOpacity = fade(frame, T.teams - 20) * fadeOut(frame, T.exchange + 20);
  const sysTop = 110;
  return (
    <Slide eyebrow="Behind the scenes" title="Follow Alice's identity." titleSize={64}>
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ position: "relative", width: W, height: H }}>
          <svg width={W} height={H} style={{ position: "absolute", inset: 0, overflow: "visible" }} aria-hidden>
            <line x1={0} y1={LANE_Y} x2={X.gw} y2={LANE_Y} stroke={C.amberLine} strokeWidth={2} strokeDasharray="6 8" opacity={fade(frame, T.teams - 30)} />
            <g stroke={C.muted} strokeWidth={2.5} fill="none" opacity={fade(frame, 90)}>
              <path d={`M ${X.teams + 150} ${LINE_Y} L ${X.agent - 150} ${LINE_Y}`} />
              <path d={`M ${X.agent + 150} ${LINE_Y} L ${X.gw - 220} ${LINE_Y}`} />
              <path d={`M ${X.gw + 220} ${LINE_Y} C ${X.gw + 260} ${LINE_Y}, ${X.gw + 260} 210, ${X.sys - 150} 210`} />
              <path d={`M ${X.gw + 220} ${LINE_Y} C ${X.gw + 260} ${LINE_Y}, ${X.gw + 260} 430, ${X.sys - 150} 430`} />
            </g>
          </svg>

          <Node
            x={X.teams}
            top={110}
            w={300}
            h={240}
            icon={<IconUser size={56} color={C.azure} />}
            title="Alice in Teams"
            sub="Signed in with her work account"
            start={14}
            active={T.teams}
          />
          <Node
            x={X.agent}
            top={110}
            w={300}
            h={240}
            icon={<IconSpark size={56} color={C.azure} />}
            title="The agent"
            sub="Teams bot + Foundry agent. No master key."
            start={34}
            active={T.agent}
          />
          <div
            style={{
              position: "absolute",
              left: X.gw - 220,
              top: 90,
              width: 440,
              height: 420,
              boxSizing: "border-box",
              borderRadius: 22,
              border: `2.5px solid ${C.amber}`,
              background: fade(frame, T.gw, 15) > 0.5 ? C.amberSoft : "#FFFFFF",
              padding: "22px 26px",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 14,
              ...appear(frame, 54),
            }}
          >
            <IconGateway size={52} color={C.amber} />
            <div style={{ fontFamily: F.head, fontWeight: 600, fontSize: 32 }}>API gateway</div>
            <div style={{ fontFamily: F.mono, fontSize: 20, color: C.muted, letterSpacing: "0.08em", marginTop: -8 }}>AZURE API MANAGEMENT</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 14, marginTop: 6, width: "100%" }}>
              <Step n={1} text="Validate Alice's token" at={T.gw + 30} />
              <Step n={2} text="Find her account in each system" at={T.gw + 80} />
              <Step n={3} text="Get a token for Alice in each system" at={T.gw + 130} />
            </div>
          </div>
          <Node
            x={X.sys}
            top={sysTop}
            w={300}
            h={200}
            icon={<IconDatabase size={40} color={C.azure} />}
            title="Salesforce"
            sub=""
            start={74}
            active={T.sf}
          >
            <span style={{ opacity: fade(frame, T.sf) }}>
              <Pill size={20} color={C.amber} bg={C.amberSoft} border={C.amberLine}>
                as Alice
              </Pill>
            </span>
          </Node>
          <Node
            x={X.sys}
            top={sysTop + 220}
            w={300}
            h={200}
            icon={<IconDatabase size={40} color={C.azure} />}
            title="ServiceNow"
            sub=""
            start={84}
            active={T.sn}
          >
            <span style={{ opacity: fade(frame, T.sn) }}>
              <Pill size={20} color={C.amber} bg={C.amberSoft} border={C.amberLine}>
                as Alice
              </Pill>
            </span>
          </Node>

          <div
            style={{
              position: "absolute",
              left: tokenX,
              top: LANE_Y,
              translate: "-50% -50%",
              opacity: tokenOpacity,
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "10px 20px",
              borderRadius: 999,
              background: C.amber,
              color: "#fff",
              fontFamily: F.mono,
              fontWeight: 500,
              fontSize: 22,
              whiteSpace: "nowrap",
              boxShadow: "0 6px 18px rgba(194,98,10,0.35)",
            }}
          >
            <IconUser size={26} color="#fff" stroke={2} />
            ALICE'S TOKEN
          </div>
          <div
            style={{
              position: "absolute",
              left: X.sys - 210,
              top: 548,
              width: 420,
              textAlign: "center",
              fontFamily: F.body,
              fontWeight: 600,
              fontSize: 26,
              color: C.ink,
              ...appear(frame, T.rules),
            }}
          >
            Her permissions. Their rules.
          </div>
        </div>
      </div>
    </Slide>
  );
};
