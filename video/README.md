# Customer 360 Assistant — story video

A ~2:40 LinkedIn video (1920×1080, 30 fps, English, burned-in captions, no voice-over)
that tells the Customer 360 Assistant story: a business hook (360° view and CRM update from
Teams), then a behind-the-scenes view of end-to-end user identity propagation.

Built with [Remotion](https://www.remotion.dev/) in the Build presentation style
(white 16:9, blue/amber accents). All screens are animated mock-ups with demo data
(Contoso, Alice, Bob). There are no real tenant data, screenshots or secrets.

## Commands

```bash
npm install
npm run studio   # live preview at http://localhost:3000
npm run render   # -> out/customer360-story.mp4
npm run srt      # -> out/customer360-story.en.srt (caption track for upload)
```

On Windows ARM64, Remotion ships neither Chrome Headless Shell nor FFmpeg.
`remotion.config.ts` falls back to the installed Edge, and `scripts/render.ps1`
renders a JPEG sequence and encodes it with a system FFmpeg (`winget install Gyan.FFmpeg`).

## Where to edit

| What | File |
|---|---|
| Scene order, durations, captions | `src/timeline.ts` |
| Colors and fonts | `src/theme.ts`, `src/fonts.ts` |
| Scenes | `src/scenes/*.tsx` |
| Shared mock-ups (Teams chat, slide frame, captions) | `src/components/*.tsx` |
