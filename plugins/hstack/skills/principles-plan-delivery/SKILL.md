---
name: principles-plan-delivery
description: Principles for multi-step work. Use when planning a phased rewrite or migration, a sweep of similar edits, how to stack commits or PRs, non-trivial work a script or codemod could do, or a correction you have now given twice.
user-invocable: false
---

# Plan delivery

Keep this table in mind for the rest of the task. Each time you reach a situation below, Read the file next to it
in full before you act. Part of each rule sits in the file's frontmatter `description`, so read the whole file. The
left column says when a principle applies, not what it says. If several rows match, read each file. If a file's
text is no longer in the conversation, for example after compaction, Read it again.

| Situation | Read |
| --- | --- |
| A planned rewrite or migration with explicit phase boundaries | `${CLAUDE_PLUGIN_ROOT}/skills/principle-outcome-oriented-execution/SKILL.md` |
| Multi-step work, such as a sweep, a migration, or a run of similar edits, or deciding how to stack commits and PRs | `${CLAUDE_PLUGIN_ROOT}/skills/principle-sequence-verifiable-units/SKILL.md` |
| Any non-trivial edit, migration, analysis, or check you would otherwise do by hand | `${CLAUDE_PLUGIN_ROOT}/skills/principle-build-the-lever/SKILL.md` |
| Writing the same instruction a second time, or giving or getting the same correction again | `${CLAUDE_PLUGIN_ROOT}/skills/principle-encode-lessons-in-structure/SKILL.md` |
