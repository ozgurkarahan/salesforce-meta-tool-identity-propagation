# Agent Instructions

## Shared knowledge base (memory wiki)

A compounding cross-project knowledge base lives at `~/projects/memory`
(`%USERPROFILE%\projects\memory` on Windows; resolve `~` to your home dir).
**Consult it reflexively before answering about clients, domains, patterns,
tools, colleagues, or past work** — do not answer from memory when the wiki
has the answer.

```bash
# from ANY repo — resolves the wiki root from the script's own location
python ~/projects/memory/scripts/wiki-search.py <terms>          # ranked hits
python ~/projects/memory/scripts/wiki-search.py <terms> --full   # + Summary
```

- Read the top matching page(s) in full before you answer.
- Compact catalog / router: `~/projects/memory/index.md` (per-category summaries
  in `~/projects/memory/wiki/{category}/_index.md`).
- When you learn something durable about this project or client, ask the user to
  run `ingest` in the memory repo so it compounds for future sessions.
