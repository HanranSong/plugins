---
name: refactor-code
description: Principles for changing existing code. Use when refactoring or simplifying, sizing a diff, tempted to add an abstraction or layer, untangling code that is hard to trace, or replacing an internal API that still has callers.
user-invocable: false
---

# Refactor code

Keep this table in mind for the rest of the task. Each time you reach a situation below, Read the file next to it
in full before you act. Part of each rule sits in the file's frontmatter `description`, so read the whole file. The
left column says when a principle applies, not what it says. If several rows match, read each file. If a file's
text is no longer in the conversation, for example after compaction, Read it again.

| Situation | Read |
| --- | --- |
| Sizing a diff, or tempted to add an abstraction, a layer, or a flag passed down through many layers | `${CLAUDE_PLUGIN_ROOT}/skills/principle-laziness-protocol/SKILL.md` |
| Deciding the order of an addition, refactor, or rewrite | `${CLAUDE_PLUGIN_ROOT}/skills/principle-subtract-before-you-add/SKILL.md` |
| Code that is hard to trace: too many layers, or hidden or mutable state to keep in mind | `${CLAUDE_PLUGIN_ROOT}/skills/principle-minimize-reader-load/SKILL.md` |
| Introducing a new internal API while callers of the old one still exist | `${CLAUDE_PLUGIN_ROOT}/skills/principle-migrate-callers-then-delete-legacy-apis/SKILL.md` |
