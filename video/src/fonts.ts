import "@fontsource/bricolage-grotesque/latin-600.css";
import "@fontsource/bricolage-grotesque/latin-800.css";
import "@fontsource/instrument-sans/latin-400.css";
import "@fontsource/instrument-sans/latin-500.css";
import "@fontsource/instrument-sans/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import { continueRender, delayRender } from "remotion";

const handle = delayRender("Loading local fonts");
Promise.all([
  document.fonts.load("800 40px 'Bricolage Grotesque'"),
  document.fonts.load("600 40px 'Bricolage Grotesque'"),
  document.fonts.load("400 40px 'Instrument Sans'"),
  document.fonts.load("500 40px 'Instrument Sans'"),
  document.fonts.load("600 40px 'Instrument Sans'"),
  document.fonts.load("400 40px 'IBM Plex Mono'"),
  document.fonts.load("500 40px 'IBM Plex Mono'"),
])
  .then(() => continueRender(handle))
  .catch(() => continueRender(handle));
