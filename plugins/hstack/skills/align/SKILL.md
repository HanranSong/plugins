---
name: align
description: Before building, play back what you understand the user wants (the outcome, what they'll get, what it changes, what's left out and what you assumed), then wait for their go. Change nothing until they approve.
argument-hint: "[what you want built, or empty for your latest request]"
disable-model-invocation: true
---

# Align before building

The user wants to check that you understand what they want before you build it. The failure this prevents is
building something good that isn't what they asked for. That usually comes from two places: gaps in the request
that you filled in on your own, and extras you added because they seemed better. This skill shows the user both
while they are still cheap to correct.

This is a check, not a plan. It covers what the user will get, not how you'll build it, so leave out file lists and
steps. Don't edit, create or delete files until the user approves.

## 1. Find the task

The user's request, if any, is the text between these markers:

<task>
$ARGUMENTS
</task>

If it is empty, take the latest request in this conversation that hasn't been built yet. If there is none, ask the
user what they want built. Either way, use everything the user has said about it in this session, not only the
latest message.

## 2. Read what you need

Read enough of the project to describe the task accurately: the parts it adds to or changes, and anything the user
pointed to. Don't rely on what you remember of the code. Stop there. You're reading to understand the request, not
to plan the implementation.

## 3. Sort what was said from what you filled in

Go through everything your understanding rests on and sort each piece:

- **Said:** the user stated it, in this request or earlier in the session.
- **Filled in:** you inferred it, picked a default, or carried it over from how similar work is usually done.

Everything filled in goes in the playback, as an assumption or as a question. Make it a question when a wrong guess
would change what you build in a way that's costly to undo. Otherwise state it as an assumption the user can correct
in a word.

Don't invent a reason. If the user didn't say why they want this and the session doesn't show it, the reason is an
assumption or a question too.

If you think what the user asked for won't get them the outcome they want, say so as a question, with what you'd do
instead and why. Don't quietly build your better version.

## 4. Name what you'd leave out

List the extras you'd be tempted to add that the user didn't ask for, such as options, modes, tests, docs,
generalizing for future cases, or tidying nearby code. They stay out unless the user asks for them. List only the
ones you were actually tempted by. Don't pad the list to look thorough.

## 5. Play it back

Keep it to about 200 words, in the user's terms rather than yours. Use this shape:

```markdown
**What you want:** the outcome and why, in one or two sentences
**What you'll get:** what it does and how the user uses it once it's done
**Existing things it changes:** the parts of the project you'd modify, such as a skill, the README or a hook, or "none, all new"
**Not doing:** the extras from step 4, one line each
**I assumed:** each filled-in piece from step 3 that isn't a question, one line each
**Questions:** at most three, each as "<topic>: I'd <your pick>, because <why>. Or: <the other option>"
```

Write each question as your pick and the alternative, never as a yes-or-no question. After "Should it include
archived items?", a bare "yes" could mean "include them" or "go with your pick". After "Archived items: I'd leave
them out, because … Or: include them", it can only mean "go with your pick".

If more than three filled-in pieces need to be questions, ask the three that would cost most to get wrong, and end
the section with "Still to settle:" and the topics of the rest.

Write "none" for a section with nothing in it. Name existing parts by what they are, not by file path. You don't
know every file yet, and a guessed list looks more precise than it is.

Then stop and wait.

## 6. Handle the reply

- **Approval** ("yes", "go ahead", "build it"): build what was agreed, including any corrections in the same reply.
  A plain yes also accepts your picks for the questions. If questions are still to settle, ask them first, in the
  same shape, and wait again.
- **A reply that could mean more than one thing:** ask which, in one short question, before you build.
- **Corrections or answers without a go:** reply with only what changed, a line or two each, and wait again. Don't
  repeat the whole playback.
- **The user picks something you argued against:** go with it. You've made your case, so don't make it again.

## 7. While building

Build what was agreed, and nothing from "Not doing" unless the user asked for it.

If the work forces a change from what was agreed, stop before carrying on and tell the user what changed, why, and
what you suggest. That includes behavior that differs from "What you'll get", an existing part that wasn't listed,
and anything agreed that turns out not to work. Inside the parts you listed, details that don't change what the
user gets don't need a stop. Changing a part you didn't list always does, however small the change.
