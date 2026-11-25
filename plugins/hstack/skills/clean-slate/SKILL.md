---
name: clean-slate
description: Judge a pending decision from a clean slate, not from the current code, earlier choices or what the user seems to want. Name the debt each option carries, recommend one, and change nothing.
argument-hint: "[the decision, or empty for the options just discussed]"
disable-model-invocation: true
---

# Judge a decision from a clean slate

The user is about to make a decision and wants your honest judgment of it. Partway through a task, your options
and recommendations lean toward what is already there: the current code, the decisions made earlier in this
session, and the plan you have been following. An option that means undoing work gets left out or argued weakly,
and "the smallest change from here" passes for "the right answer". You also lean toward whatever the user seems to
prefer. This skill corrects for both.

A clean slate doesn't mean starting over, and honest doesn't mean contrarian. If the current path is the right one,
say so and say why.

This is a decision aid. Don't edit, create or delete files, and don't start on any option until the user picks one.

## 1. Find the decision

The user's note on the decision, if any, is the text between these markers:

<decision>
$ARGUMENTS
</decision>

If it is empty, take the decision that is still pending: the latest options you gave that the user hasn't chosen
between yet, or the latest question still open. If the note names a decision already made, judge that one, since the
user may want it reconsidered. If no decision fits, ask the user what to judge. If more than one fits, whether or not
there is a note, ask which one in a single short question that names them.

Restate the decision as one question about the outcome the user needs, not as a pick between the options on the
table. "How should the dashboard stay fast when the API is slow?" leaves room for answers that "cache layer or retry
wrapper?" rules out.

If you can't tell what outcome the user needs, for example because this session hasn't said, ask in one short
question before you judge. Don't guess it: the goal is the one thing a clean slate keeps.

If judging it needs facts about the code, read the code. Don't rely on what you remember of it.

## 2. Split constraints from inherited choices

Sort what shapes the decision into two lists:

- **Constraints:** what any good answer must respect because something outside this decision fixes it: what the
  user has said they need, systems you don't control, anything already promised to people outside this codebase
  (such as a published API or file format), and limits the user has set on time or scope.
- **Inherited choices:** what is the way it is because someone chose it, and could be chosen again: the current
  structure, abstractions, libraries and workarounds, and every plan or recommendation made earlier in this
  session, including yours.

Something the user has said they need is a constraint, however it came about. "We chose self-hosting, and we need
to keep it" is a constraint, not an inherited choice. If you can't tell whether an earlier choice of the user's is
still a requirement, and the answer would change your recommendation, ask before you judge.

To sort everything else, ask: if it were different, would that be because the world is different, or because we
decided differently? If we decided, it is inherited. "It's already built" and "changing it is work" make something a
cost, not a constraint. Costs come in at step 4.

If a constraint the user set looks like the real source of the problem, say so in your answer, but keep treating it
as a constraint unless the user drops it.

## 3. Choose as if starting today

Using only the goal and the constraints, decide what you would build if you were starting today. Do this before you
look again at the options already on the table, so they don't set the frame. Keep what the current code has taught
you, such as which approaches failed and what the real requirements turned out to be. Drop the investment in it.

Then list the options worth defending, usually two to four: your clean-slate pick, the earlier options that still
hold up, and anything both missed. Look hardest for:

- replacing or removing something that already exists
- a different approach to the outcome, not a variation on the current one
- not doing it at all, if the need is weaker than it looked

Leave out options you wouldn't defend. Then confirm your clean-slate pick, or change it if another option beats it,
and note why.

## 4. Count the cost from here

Now bring the current state back. For each option, weigh two costs:

- **Getting there:** the code to change, callers to migrate, data to move, and the risk of the switch.
- **Staying put:** what the current path keeps charging, such as workarounds every future change will need, bugs it
  invites and work it blocks. This is the cost a recommendation anchored on the current code leaves out.

Work already done counts only for what it still delivers. Effort already spent is not a reason to keep it.

Then recommend the option with the best balance of where it leads and what it costs to get there. It doesn't have to
be your clean-slate pick: a smaller change that removes most of the debt now can beat a better design that costs too
much to reach. Settle on one of three verdicts:

- **Switch:** move to the recommended option now.
- **Stay:** the current path is the best choice. Say whether that's because it is also your clean-slate pick, or
  because every switch costs more than the debt it removes.
- **Stay for now:** no switch is worth it yet, but a specific event would make one worth it, such as a third caller
  or a second data source. Name the event and the option it would favor, so the user can watch for it. If you can't
  name one, the verdict is switch or stay.

## 5. Check your pull

Before you answer, test the recommendation against the two pulls this skill exists to catch:

- **Toward the current path.** If you recommend the option closest to what exists, or the one you proposed earlier,
  point to the cost from step 4 that justifies it. If the only reason is that it's familiar or already started, go
  back to step 3: the list of options may be anchored too.
- **Toward the user.** If the user has leaned one way, check that your reason holds without their lean. If you
  disagree with them, say so plainly. Don't soften it into "both are valid".

Don't overcorrect. Reversing your earlier answer to look independent is the same mistake in the other direction.

## 6. Answer

Lead with the recommendation, and keep the whole answer to about 300 words. Use this shape:

```markdown
**Decision:** the question from step 1

**Recommendation:** the option. Switch | Stay | Stay for now, until <event>

**Why:**
- two to four reasons, strongest first

**Treated as fixed:** the constraints from step 2, one line each, so the user can correct one you got wrong

**Inherited, not required:** the inherited choices that made an option look better or worse than it is, one line each

| Option | From a clean slate | Cost from here |
| --- | --- | --- |

**What would change my mind:** one or two facts, ideally ones the user can check
```

If the recommendation isn't your clean-slate pick, add one line under it on why not. If it reverses something you
said earlier in this session, add one line under it that says so and names what you were anchored on, for example: "Earlier I suggested X. That leaned on Y, which we chose and can
change."

Then stop and wait for the user to choose. If they pick a different option, go with it. You've made your case, so
don't make it again.
