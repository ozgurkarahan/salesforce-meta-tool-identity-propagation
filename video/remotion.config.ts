import { Config } from "@remotion/cli/config";
import fs from "node:fs";

// Remotion ships no Chrome Headless Shell for Windows ARM64: fall back to the installed Edge.
const edge = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
if (process.env.REMOTION_BROWSER) {
  Config.setBrowserExecutable(process.env.REMOTION_BROWSER);
} else if (process.platform === "win32" && process.arch === "arm64" && fs.existsSync(edge)) {
  Config.setBrowserExecutable(edge);
}

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setCodec("h264");
Config.setCrf(18);
