---
name: second-opinion
description: Write a self-contained prompt that asks another agent for an independent review of the current work, then triage the review the user pastes back and decide whether another round is worth it.
argument-hint: "[what to review]"
disable-model-invocation: true
---

# Get a second opinion

The user wants an independent review from another agent. That agent can read this repository but knows nothing
about this session. You write the prompt, the user pastes it into the reviewer, then pastes the reviewer's reply
back here. You judge the reply and decide whether a follow-up round is worth it. Every review turns up something
new, so the aim is a review that stays on what matters and comes to an end. You start at most 3 rounds on your
own. The user can always ask for more.

## 1. Decide what is under review

The user's note on what to review, if any, is the text between these markers:

<review_target>
$ARGUMENTS
</review_target>

If it is empty, review the uncommitted changes: staged, unstaged, and new files git doesn't track yet. Leave out
untracked files that are clearly not part of the work, such as editor or OS files. If there are no uncommitted
changes, don't guess: ask the user what to review in one short question, naming the likely candidates (for
example the last commit, or a plan from this conversation).

Then collect what the reviewer needs to find the work on its own:

- The absolute path of the repository.
- For code: the exact git command that shows the change and the files that matter most. Use `git diff HEAD` for
  uncommitted work (plain `git diff` skips staged changes), `git diff <base>...HEAD` for a branch and
  `git show <sha>` for a commit. For a branch, `<base>` is the branch it will merge into, which isn't always
  `main`: check it rather than assume. Run the command yourself first to check it shows what you expect. No `git diff`
  shows new files that git doesn't track yet, so name those by path.
- For a plan or design: the file it is in. If it exists only in this conversation, put it in the prompt itself,
  condensed.

## 2. Write the first prompt

Write for a reader with no context, which may be a different AI. Leave out names and shorthand that only this
session knows, and define any project term you need. Keep sections 1 to 5 to about 400 words, not counting a plan
you had to paste in.

Use these sections, in this order:

1. **Role.** One sentence: they are giving an independent second opinion on work they were not part of.
2. **Background.** What the project is, and what the user is trying to achieve and why. Two or three sentences.
3. **Current state.** What has been done or decided, with the reason behind each decision that matters, and any
   constraints. The reasons let the reviewer tell a deliberate choice from an oversight. It may still challenge a
   choice it thinks is wrong.
4. **What to review.** The repository path, the git command or files, and anything that is out of scope.
5. **What to check.** Whether the work achieves its goal, plus any specific worries raised in this session.
6. **Rules.** Copy this text as written:

~~~text
Read only: don't edit, create or delete files, and don't run commands that change anything (no commits, installs
or formatters).

Only report a finding if it would cause a bug, a wrong result, or a design decision the user would regret later.
Don't report style, naming, preferences, or "you could also" alternatives. "Nothing blocking" is a good answer when
it's true. Don't hunt for something to say.

Label each finding:
- blocking: breaks something, gives a wrong result, loses data, opens a security hole, or means the work can't
  reach its goal.
- should-fix: a real problem that won't break anything today, such as an edge case likely to happen, a fragile
  design, or docs that mislead.
- minor: a real but small problem. Not a style point.

Reply in this format, worst first, with no summary of the work and no praise:

    Verdict: nothing blocking | blocking issues found

    1. [blocking] path/to/file:line or plan section - what's wrong
       Why: the concrete scenario where it goes wrong
       Fix: the fix you suggest, briefly
~~~

Show the prompt in one fenced code block, using a fence longer than any run of backticks inside it (four backticks
usually does). Under it, add one line: this is round 1 of 3, so paste it into the reviewer and paste the
reply back here.

## 3. Triage the reply

When the user pastes a reply, check every finding against the code or plan yourself. Don't accept a finding
because it sounds confident, and don't reject one because it's inconvenient. Give each finding one verdict:

- **Accept:** it's real and worth fixing.
- **Reject:** it's wrong, already handled, or below the bar. Give one line on why.
- **Your call:** it's real, but fixing it changes direction or scope, needs a big rewrite, or is a tradeoff the user
  should choose. Lay out the choice in a line or two.

Show the verdicts as a short list in the reviewer's numbering. Then fix the accepted findings right away, and ask
the user about the "your call" ones. When the user answers, a "your call" finding they want fixed counts as
accepted: fix it. One they decline is settled: the problem is real, and the user chose to live with it. A settled
finding is not a rejection.

In a follow-up round, the reviewer answers your fixes and rejections, and may report a new blocking problem that a
fix introduced. Triage a new blocking problem, and a fix it says is not fixed, like any other finding. A rejection
it accepts is closed. If it still disagrees with a rejection, don't argue a second time: the finding is disputed.
Ask the user to decide, with each side's case in one line. If the user sides with the reviewer, the finding is
accepted: fix it. If the user sides with you, the rejection is upheld and closed. An upheld rejection is not a
settled finding: the user agreed with your reason for rejecting it.

## 4. Decide whether to go another round

First wait until the user has answered every "your call" and disputed finding, and apply the fixes they approved.
Then another round is worth it only when one of these is true:

- You fixed a blocking or should-fix finding, and the fix is big or subtle enough that a second look could catch
  a mistake in it.
- You rejected a blocking or should-fix finding, and the reviewer's case was strong enough to deserve a reply.
- The user asks for another round.

Minor findings, settled findings and upheld rejections never justify another round. If none of these is true, stop.
Tell the user in a line or two that the review is finished and why, and list anything still open.

Past round 3, start a round only when the user asks for one. After round 3, and after every round the user asks
for beyond it, stop and list anything still open. If you think more review is needed, say why in one line, and let
the user decide. If the user asks for another round, at any point, write it.

Otherwise, write a follow-up prompt. The user pastes it into the same reviewer session, often after compacting
it, so the reviewer may remember only a summary. Write it to work either way. The follow-up has:

1. One or two lines restating the goal and what is under review, so it stands on its own after compaction.
2. "Round N of 3", or just "Round N" after round 3.
3. Each accepted finding, by the reviewer's number: one line restating the problem and the scenario where it went
   wrong, then what changed and where to see it (files, or a git command). If the work is a plan that exists only
   in this conversation, paste the revised passage instead.
4. Each finding you rejected since the last prompt, by number: one line restating it, why you rejected it, and
   where to see the evidence (files, or a git command). If the evidence is in a plan that exists only in this
   conversation, paste the passage.
5. Each finding the user closed since the last prompt, by number: one line on what the user decided and why.
   Either they chose to keep it as it is (settled), or they agreed with your reason for rejecting it (upheld).
   A rejection the reviewer itself accepted needs no line.
6. The ask. Copy this text as written:

~~~text
Check only the fixes and rejections above. For each fix: is it correct and complete? For each rejection: accept
it, or explain why the problem is still real. Closed findings stay closed: don't reopen them. Don't look for new
issues. The only new finding to report is a blocking problem that the fixes introduced. The read-only rule still
applies.

Reply in this format:

    Fix 1: confirmed | not fixed: why
    Rejection 2: accepted | still disagree: why
    New blocking: none | path/to/file:line or plan section - what's wrong
~~~

Show it the same way as the first prompt, with one line under it saying which round it is. When the reply comes
back, triage it with step 3 and decide again with step 4.
