# Copilot Instructions

Read these files for full context:

- `AGENT.md` — Project overview, architecture, key paths, workflow rules, and conventions
- `~/projects/memory/wiki/projects/salesforce-meta-tool-id-prop.md` — Past mistakes / lessons (wiki = single source of truth; `.ai/lessons-learned.md` was REMOVED)
- `.ai/project-reference.md` — Technical details
- `~/projects/memory/agent-config/workflow.md` — Global workflow rules
- `~/projects/memory/agent-config/platform.md` — Platform preferences and domain knowledge index


## End-Session Workflow

When the user says "end session", "wrap up", or "done for today":

1. **Project docs** — Check if the wiki project page (`~/projects/memory/wiki/projects/salesforce-meta-tool-id-prop.md`) and `.ai/project-reference.md` need updates from today's work. Propose changes.
2. **Wiki compounding** — If significant lessons or patterns were discovered:
   - Update the project's wiki page at `~/projects/memory/wiki/projects/{project}.md`
   - Update relevant domain pages at `~/projects/memory/wiki/domains/*.md`
   - Add new glossary terms to `~/projects/memory/glossary.md`
   - Append to `~/projects/memory/log.md`
3. **Git check** — Run `git status` and warn about uncommitted changes.
4. **Summary** — Report what was updated.

## Copilot-Specific Tips

- Use `@workspace` to give Copilot full project context
- Pin important files in chat for persistent context
- Use Copilot Edits (Ctrl+Shift+I) for multi-file changes
- Run tests manually — Copilot cannot execute them
- When corrected, record the lesson in the wiki project page (`~/projects/memory/wiki/projects/salesforce-meta-tool-id-prop.md`)
