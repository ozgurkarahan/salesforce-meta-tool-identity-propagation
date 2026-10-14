import { FPS } from "./theme";

export const TRANSITION = 12;

type Caption = { from: number; to: number; text: string };
export type SceneDef = { id: string; seconds: number; captions: Caption[] };

export const SCENES: SceneDef[] = [
  {
    id: "hook",
    seconds: 8,
    captions: [
      { from: 0.4, to: 4, text: "Meet Alice, an account manager." },
      { from: 4, to: 7.8, text: "Her biggest customer, Contoso, joins a call in 5 minutes." },
    ],
  },
  {
    id: "silo",
    seconds: 10,
    captions: [
      { from: 0.3, to: 5, text: "Deals live in Salesforce. Support tickets live in ServiceNow." },
      { from: 5, to: 9.8, text: "Connecting the dots by hand takes much longer than five minutes." },
    ],
  },
  {
    id: "ask360",
    seconds: 12,
    captions: [
      { from: 0.3, to: 6, text: "So Alice asks her assistant, right in a Teams group chat." },
      { from: 6, to: 11.8, text: "It queries Salesforce and ServiceNow using Alice's own access." },
    ],
  },
  {
    id: "answer360",
    seconds: 28,
    captions: [
      { from: 0.3, to: 7, text: "Seconds later, she has the open deal, worth $350k…" },
      { from: 7, to: 14, text: "…the customer's open support cases…" },
      { from: 14, to: 21, text: "…and the IT incidents behind them." },
      { from: 21, to: 27.8, text: "One root cause behind both cases, and a $350k deal at risk." },
    ],
  },
  {
    id: "later",
    seconds: 5,
    captions: [{ from: 0.3, to: 4.8, text: "The call goes well. Now the CRM needs an update." }],
  },
  {
    id: "askUpdate",
    seconds: 12,
    captions: [
      { from: 0.3, to: 6, text: "One sentence, in the chat she already has open." },
      { from: 6, to: 11.8, text: "She asks for a note for the team and a new close date." },
    ],
  },
  {
    id: "write",
    seconds: 15,
    captions: [
      { from: 0.3, to: 7, text: "The assistant logs the note in Salesforce and moves the close date." },
      { from: 7, to: 14.8, text: "And the record shows exactly who made the change." },
    ],
  },
  {
    id: "pivot",
    seconds: 8,
    captions: [{ from: 0.3, to: 7.8, text: "Salesforce recorded the change under Alice's own name." }],
  },
  {
    id: "identity",
    seconds: 32,
    captions: [
      { from: 0.3, to: 6, text: "How does it work? Follow Alice's identity." },
      { from: 6, to: 12, text: "She is signed in to Teams with her work account." },
      { from: 12, to: 18, text: "The agent has no master key. It carries Alice's token." },
      { from: 18, to: 26, text: "The gateway checks it, then gets Alice's own token for each system." },
      { from: 26, to: 31.8, text: "Each system enforces its own rules. Alice sees only what she may see." },
    ],
  },
  {
    id: "consent",
    seconds: 10,
    captions: [
      { from: 0.3, to: 5, text: "The first time, Alice approves access once for each system." },
      { from: 5, to: 9.8, text: "After that, every request runs as her." },
    ],
  },
  {
    id: "why",
    seconds: 10,
    captions: [
      { from: 0.3, to: 5, text: "There is no over-privileged service account in the data path." },
      { from: 5, to: 9.8, text: "Every read and every write can be traced to a real person." },
    ],
  },
  {
    id: "everywhere",
    seconds: 8,
    captions: [
      { from: 0.3, to: 7.8, text: "It works in 1:1 chats, in group chats, and as a Copilot agent." },
    ],
  },
  {
    id: "close",
    seconds: 7,
    captions: [{ from: 0.3, to: 6.8, text: "Customer 360 Assistant: the code is open source on GitHub." }],
  },
];

export const sceneFrames = (s: SceneDef) => Math.round(s.seconds * FPS);

export const sceneStarts = (() => {
  const starts: number[] = [];
  let t = 0;
  SCENES.forEach((s, i) => {
    starts.push(t);
    t += sceneFrames(s) - (i < SCENES.length - 1 ? TRANSITION : 0);
  });
  return starts;
})();

export const TOTAL_FRAMES =
  SCENES.reduce((a, s) => a + sceneFrames(s), 0) - TRANSITION * (SCENES.length - 1);

export const ABS_CAPTIONS = SCENES.flatMap((s, i) =>
  s.captions.map((c) => ({
    from: sceneStarts[i] + Math.round(c.from * FPS),
    to: sceneStarts[i] + Math.round(c.to * FPS),
    text: c.text,
  })),
);
