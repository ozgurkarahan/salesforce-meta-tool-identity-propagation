import React from "react";
import { useCurrentFrame } from "remotion";
import { C, F } from "../theme";
import { appear } from "./anim";
import { IconSpark } from "./icons";

export type ChatMsg = {
  author: "Alice" | "Bob" | "Bot";
  time: string;
  at: number;
  content: React.ReactNode;
};

export const Avatar: React.FC<{ who: ChatMsg["author"]; size?: number }> = ({ who, size = 56 }) => {
  if (who === "Bot") {
    return (
      <div
        style={{
          width: size,
          height: size,
          borderRadius: size * 0.28,
          background: C.azure,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <IconSpark size={size * 0.6} color="#FFFFFF" />
      </div>
    );
  }
  const bg = who === "Alice" ? C.amber : C.ok;
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: bg,
        color: "#FFFFFF",
        fontFamily: F.body,
        fontWeight: 600,
        fontSize: size * 0.42,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}
    >
      {who[0]}
    </div>
  );
};

export const Mention: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span style={{ color: C.teams, fontWeight: 600 }}>{children}</span>
);

const names = { Alice: "Alice", Bob: "Bob", Bot: "Customer 360 Assistant" };

export const ChatWindow: React.FC<{
  messages: ChatMsg[];
  composer?: string;
  composerActive?: boolean;
  style?: React.CSSProperties;
}> = ({ messages, composer = "", composerActive = false, style }) => {
  const frame = useCurrentFrame();
  const caretOn = Math.floor(frame / 15) % 2 === 0;
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        borderRadius: 22,
        border: `1.5px solid ${C.line}`,
        background: "#FAFAFC",
        overflow: "hidden",
        boxShadow: "0 18px 50px rgba(22,32,47,0.10)",
        ...style,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 18,
          padding: "20px 32px",
          background: "#FFFFFF",
          borderBottom: `1.5px solid ${C.line}`,
        }}
      >
        <div style={{ display: "flex" }}>
          {(["Alice", "Bob", "Bot"] as const).map((w, i) => (
            <div key={w} style={{ marginLeft: i === 0 ? 0 : -12, border: "3px solid #fff", borderRadius: "50%" }}>
              <Avatar who={w} size={44} />
            </div>
          ))}
        </div>
        <div style={{ fontFamily: F.body, fontWeight: 600, fontSize: 30 }}>Customer 360 Demo</div>
        <div style={{ fontFamily: F.mono, fontSize: 20, color: C.muted, letterSpacing: "0.08em" }}>GROUP CHAT · TEAMS</div>
      </div>
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          justifyContent: "flex-end",
          gap: 26,
          padding: "26px 40px",
          overflow: "hidden",
        }}
      >
        {messages
          .filter((m) => frame >= m.at)
          .map((m, i) => {
            const mine = m.author === "Alice";
            return (
              <div
                key={i}
                style={{
                  display: "flex",
                  flexDirection: mine ? "row-reverse" : "row",
                  alignItems: "flex-start",
                  gap: 18,
                  ...appear(frame, m.at, 14, 18),
                }}
              >
                {mine ? null : <Avatar who={m.author} />}
                <div style={{ maxWidth: "78%", display: "flex", flexDirection: "column", alignItems: mine ? "flex-end" : "flex-start", gap: 6 }}>
                  <div style={{ fontFamily: F.body, fontSize: 22, color: C.muted }}>
                    {mine ? "" : `${names[m.author]}  `}
                    <span style={{ fontFamily: F.mono, fontSize: 19 }}>{m.time}</span>
                  </div>
                  <div
                    style={{
                      padding: "18px 26px",
                      borderRadius: 16,
                      background: mine ? C.teamsSoft : "#FFFFFF",
                      border: mine ? "none" : `1.5px solid ${C.line}`,
                      fontFamily: F.body,
                      fontSize: 34,
                      lineHeight: 1.35,
                    }}
                  >
                    {m.content}
                  </div>
                </div>
              </div>
            );
          })}
      </div>
      <div style={{ padding: "0 32px 26px 32px" }}>
        <div
          style={{
            borderRadius: 14,
            background: "#FFFFFF",
            border: `2px solid ${composerActive ? C.teams : C.line}`,
            padding: "18px 24px",
            fontFamily: F.body,
            fontSize: 32,
            minHeight: 44,
            color: composer ? C.ink : C.muted,
          }}
        >
          {composer ? <ComposerText text={composer} /> : "Type a message"}
          {composerActive && caretOn ? <span style={{ color: C.teams }}>|</span> : null}
        </div>
      </div>
    </div>
  );
};

const MENTION = "@Customer 360 Assistant";
const ComposerText: React.FC<{ text: string }> = ({ text }) => {
  if (text.startsWith("@")) {
    const m = text.slice(0, Math.min(text.length, MENTION.length));
    return (
      <>
        <Mention>{m}</Mention>
        {text.slice(m.length)}
      </>
    );
  }
  return <>{text}</>;
};
