# hstack

My agent workflows, as a Claude Code plugin.

| Command | What it does |
| --- | --- |
| `/hstack:hello` | Greets you. |
| `/hstack:dream` | Distills this project's recent Claude Code sessions into Markdown memory files in `.dreaming/memory/`, as Anthropic's "Dreaming with a dynamic workflow" cookbook does. See [skills/dream/README.md](skills/dream/README.md). |

`/hstack:dream` also adds two agents (`transcript-analyzer` and `memory-organizer`), one workflow
(`dream-workflow`) and one hook (`hooks/dream_guard.py`). Only `/hstack:dream` uses them. The hook stays silent
for every other agent's tool calls.
