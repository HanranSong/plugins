---
name: principles-verify-work
description: Principles for checking work. Use when fixing a bug or a failing test, debugging, writing or changing tests, measuring performance or eval results, when fixes keep failing, or before saying work is done.
user-invocable: false
---

# Verify work

Keep this table in mind for the rest of the task. Each time you reach a situation below, Read the file next to it
in full before you act. Part of each rule sits in the file's frontmatter `description`, so read the whole file. The
left column says when a principle applies, not what it says. If several rows match, read each file. If a file's
text is no longer in the conversation, for example after compaction, Read it again.

| Situation | Read |
| --- | --- |
| Debugging: a symptom whose cause you have to find | `${CLAUDE_PLUGIN_ROOT}/skills/principle-fix-root-causes/SKILL.md` |
| Two or more fixes that rest on the same assumption have failed the same check | `${CLAUDE_PLUGIN_ROOT}/skills/principle-attack-the-premise/SKILL.md` |
| Writing, changing, or deciding whether to keep a test | `${CLAUDE_PLUGIN_ROOT}/skills/principle-test-behavior-not-implementation/SKILL.md` |
| Before measuring, and before trusting, reporting, or acting on a number you measured: a speedup, regression, latency, throughput, or eval result | `${CLAUDE_PLUGIN_ROOT}/skills/principle-explain-the-number/SKILL.md` |
| The same, for a performance number: read this as well | `${CLAUDE_PLUGIN_ROOT}/skills/benchmark-checklist/SKILL.md` |
| The work is finished and you are about to say it is done | `${CLAUDE_PLUGIN_ROOT}/skills/principle-prove-it-works/SKILL.md` |
