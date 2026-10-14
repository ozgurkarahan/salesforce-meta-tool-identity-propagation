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
npm run visuals  # -> ../site/assets/visuals/*.png + ../site/assets/og-image.png
npm run poster   # -> ../site/media/customer360-story-v1-poster.jpg
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
| README / project-page visuals (plug board, trust, extend, social card) | `src/visuals/*.tsx`, rendered by `scripts/render-visuals.ps1` |

## Publishing

- **README:** the video is a GitHub user attachment (the URL alone on its own
  line in `README.md`). Only GitHub's README renderer can play it; anonymous
  direct requests return 404.
- **Project page:** `site/media/customer360-story-v1.mp4` is the committed render
  (H.264, 1920×1080, 160.2 s, silent; SHA-256
  `4BC24BE62AE4FB1FBD4BD0E01C62437E3A36280D894455A54EE94BCC86EBB5AC`). It was
  rendered from the source committed in `219b382`. After a re-render, bump `-v1`
  in the MP4 and poster file names and in `site/index.html`, so Pages caches
  never serve a stale file.
- **Transcript:** `site/index.html` carries a scene-by-scene transcript. Update
  it whenever `src/timeline.ts` captions change.
