# hstack

My agent workflows, as a Claude Code plugin.

| Command | What it does |
| --- | --- |
| `/hstack:second-opinion` | Writes a self-contained prompt asking another agent to review your uncommitted changes, or whatever you name after the command. Paste the reviewer's reply back, and Claude triages it, fixes what holds up and decides whether another round is worth it. It won't start more than 3 rounds on its own. |
| `/hstack:dream` | Distills this project's recent Claude Code sessions into Markdown memory files in `.dreaming/memory/`, as Anthropic's "Dreaming with a dynamic workflow" cookbook does. See [skills/dream/README.md](skills/dream/README.md). |

`/hstack:dream` also adds two agents (`transcript-analyzer` and `memory-organizer`), one workflow
(`dream-workflow`) and one hook (`hooks/dream_guard.py`). Only `/hstack:dream` uses them. The hook stays silent
for every other agent's tool calls.
