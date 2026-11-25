---
name: principles-design-features
description: Principles for deciding what to build. Use when recommending how to support a requested feature, for product, UX, or feature-scope tradeoffs, a new interaction or architecture with no precedent in the codebase, or fitting a new requirement into an existing design.
user-invocable: false
---

# Design features

Keep this table in mind for the rest of the task. Each time you reach a situation below, Read the file next to it
in full before you act. Part of each rule sits in the file's frontmatter `description`, so read the whole file. The
left column says when a principle applies, not what it says. If several rows match, read each file. If a file's
text is no longer in the conversation, for example after compaction, Read it again.

| Situation | Read |
| --- | --- |
| A product, UX, or feature-scope tradeoff, including the experience of a developer who imports your library or API | `${CLAUDE_PLUGIN_ROOT}/skills/principle-experience-first/SKILL.md` |
| A new interaction or architectural decision with no precedent in the codebase | `${CLAUDE_PLUGIN_ROOT}/skills/principle-exhaust-the-design-space/SKILL.md` |
| Integrating a new requirement into an existing design | `${CLAUDE_PLUGIN_ROOT}/skills/principle-redesign-from-first-principles/SKILL.md` |
