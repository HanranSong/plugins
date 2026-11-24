---
name: design-data
description: Principles for shaping data. Use before writing logic that picks types, data structures or signatures, validates input or handles errors, models state, must survive retries, or lets concurrent workers share state.
user-invocable: false
---

# Design data

Keep this table in mind for the rest of the task. Each time you reach a situation below, Read the file next to it
in full before you act. Part of each rule sits in the file's frontmatter `description`, so read the whole file. The
left column says when a principle applies, not what it says. If several rows match, read each file. If a file's
text is no longer in the conversation, for example after compaction, Read it again.

| Situation | Read |
| --- | --- |
| Before writing logic: choosing core types and data structures, or deciding what to build first | `${CLAUDE_PLUGIN_ROOT}/skills/principle-foundational-thinking/SKILL.md` |
| Stateful logic, code that branches a lot, or the same assumption about a shape repeated across files | `${CLAUDE_PLUGIN_ROOT}/skills/principle-model-the-domain/SKILL.md` |
| Designing types or a function signature in a statically typed language | `${CLAUDE_PLUGIN_ROOT}/skills/principle-type-system-discipline/SKILL.md` |
| The same, in TypeScript: read this as well | `${CLAUDE_PLUGIN_ROOT}/skills/typescript-best-practices/SKILL.md` |
| Wiring validation, error handling, or a framework adapter | `${CLAUDE_PLUGIN_ROOT}/skills/principle-boundary-discipline/SKILL.md` |
| A command, lifecycle step, or loop that must survive crashes, restarts, and retries | `${CLAUDE_PLUGIN_ROOT}/skills/principle-make-operations-idempotent/SKILL.md` |
| Concurrent processes, agents, or workers might write the same file, branch, key, or object | `${CLAUDE_PLUGIN_ROOT}/skills/principle-separate-before-serializing-shared-state/SKILL.md` |
