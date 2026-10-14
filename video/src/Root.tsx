import "./fonts";
import React from "react";
import { AbsoluteFill, Composition, Still } from "remotion";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { Captions } from "./components/Captions";
import { Hook, Silo } from "./scenes/Intro";
import { Answer360, Ask360 } from "./scenes/Discover";
import { AskUpdate, Later, Pivot, WriteBack } from "./scenes/Update";
import { Identity } from "./scenes/Identity";
import { Close, Consent, Everywhere, Why } from "./scenes/Outro";
import { FPS, HEIGHT, WIDTH } from "./theme";
import { SCENES, sceneFrames, TOTAL_FRAMES, TRANSITION } from "./timeline";
import { Extend } from "./visuals/Extend";
import { Hero } from "./visuals/Hero";
import { OgCard } from "./visuals/OgCard";
import { Trust } from "./visuals/Trust";

const COMPONENTS: Record<string, React.FC> = {
  hook: Hook,
  silo: Silo,
  ask360: Ask360,
  answer360: Answer360,
  later: Later,
  askUpdate: AskUpdate,
  write: WriteBack,
  pivot: Pivot,
  identity: Identity,
  consent: Consent,
  why: Why,
  everywhere: Everywhere,
  close: Close,
};

export const Story: React.FC = () => (
  <AbsoluteFill style={{ backgroundColor: "#FFFFFF" }}>
    <TransitionSeries>
      {SCENES.flatMap((s, i) => {
        const Comp = COMPONENTS[s.id];
        const seq = (
          <TransitionSeries.Sequence key={s.id} name={s.id} durationInFrames={sceneFrames(s)}>
            <Comp />
          </TransitionSeries.Sequence>
        );
        return i < SCENES.length - 1
          ? [seq, <TransitionSeries.Transition key={`${s.id}-t`} presentation={fade()} timing={linearTiming({ durationInFrames: TRANSITION })} />]
          : [seq];
      })}
    </TransitionSeries>
    <Captions />
  </AbsoluteFill>
);

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Customer360Story" component={Story} durationInFrames={TOTAL_FRAMES} fps={FPS} width={WIDTH} height={HEIGHT} />
    {/* README and project-page visuals, rendered by `npm run visuals`. */}
    <Still id="Hero" component={Hero} width={1200} height={600} />
    <Still id="Trust" component={Trust} width={1200} height={560} />
    <Still id="Extend" component={Extend} width={1200} height={600} />
    <Still id="OgCard" component={OgCard} width={1200} height={630} />
  </>
);
