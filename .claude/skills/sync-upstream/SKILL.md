---
name: sync-upstream
description: Bring upstream changes into the skills hstack copied from pstack, keeping this repo's edits to them, and report what changed in the Anthropic cookbook that /hstack:dream ports. Run by hand about once a week.
disable-model-invocation: true
allowed-tools:
  - Bash(python3 "${CLAUDE_SKILL_DIR}/scripts/sync.py" *)
---

# Sync with upstream

hstack carries files from two other repositories, and both keep changing. `upstream.json`, at the repo root,
lists them:

- **pstack**, poteto's, in `cursor/plugins`: 26 skills copied with a few small edits. You merge upstream's
  changes into them on a branch, keeping this repo's edits.
- **claude-cookbooks**: the "Dreaming with a dynamic workflow" cookbook, which `/hstack:dream` ports to Claude
  Code. The port rewrote too much to merge, so you report what changed upstream and suggest edits, and nothing is
  applied until the user agrees.

Each source's `base` is the upstream commit its files were last synced to. Comparing base with upstream's head
shows what upstream changed. Comparing base with the local file shows what this repo changed. The script does
the comparing and merging. You do what needs judgment, and you tell the user about anything that stands out.
Don't push and don't open a pull request.

Run the script from the repo root. Each command prints one JSON object, and each one reaches github.com.

## 1. Check for changes

Start from a clean `main`. If you're on a `sync-upstream/` branch, first run
`python3 "${CLAUDE_SKILL_DIR}/scripts/sync.py" touched`: it lists the paths the script changed that aren't
committed yet, new untracked files included. If it lists any, or `git status --porcelain --untracked-files=no`
prints anything, an earlier sync didn't finish. Tell the user, read `git diff HEAD` (it shows staged changes
too) and the listed paths, and run status below. Where `git diff HEAD -- upstream.json` changes a source's
`base`, its apply already ran: carry on from step 3's check for conflict markers. Otherwise carry on from the
start of step 3, keeping any reasons already recorded.

Anywhere else, `git status --porcelain --untracked-files=no` must print nothing. If it prints something, ask the
user to commit or stash that work, and stop. If you're on a branch other than `main`, note its name and
`git switch main`. Then:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/sync.py" status
```

It changes no file in the repo. Diffs of what changed upstream are under its `report_folder`, and each changed
file's `diff` gives the path.

If `nothing_new` is true, tell the user in one line that nothing changed upstream since the last sync. Add any
`held` skills still waiting for their call, and any `unrecorded_edits` or `stale_edit_notes` (step 4). Switch back
to the branch you noted, if any, and stop.

If a source has `error` or `path_missing`, upstream rewrote its history or moved or removed the whole folder this
repo copies from. Don't apply that source. Put it first in the report, with the commits `path_missing` lists, and
go on with the others.

## 2. Start a branch

Before the first change you make:

```bash
git switch -c sync-upstream/<today, as YYYY-MM-DD> main
python3 "${CLAUDE_SKILL_DIR}/scripts/sync.py" touched --clear
```

The second command starts the script's list of the paths it changes afresh for this sync.

## 3. pstack: apply and resolve

Read the source's `commits`: their subjects say why upstream changed things. Read the `diff` of each entry in
`files`.

Settle two things with the user before you apply:

- **License.** If `license_changed` is there, show the user its diff and wait for their answer. Taking upstream's
  newer files means taking them under the new license. If they agree, add `--accept-license` to the apply
  command below, which also copies the new license over the local one. If they don't, stop syncing this source
  and tell them what's left undone.
- **Edits with no reason.** If a file's `action` is `merge` or `conflict` and it has no `edit_reason`, its local
  edit isn't recorded, so you can't tell what the edit is for. Ask the user, and record their answer under that
  skill's `edits` in `upstream.json` first.

Then, with the `head` that status reported for pstack:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/sync.py" apply pstack --head <head>
```

Each file's `action` says what apply did:

- `update`, `add`, `delete`: the file had no local edits, so it now matches upstream.
- `mode`: upstream changed only whether the file is executable.
- `merge`: upstream's change and the local edit didn't overlap, and both are in the file.
- `conflict`: they overlapped. The file has diff3 markers: `<<<<<<< local`, then `||||||| upstream <base>`,
  `=======`, `>>>>>>> upstream <head>`. Resolve each one so the file takes upstream's change and still does what
  its `edit_reason` says. If both can't hold, keep the edit's purpose and say which part of upstream's change you
  left out. A conflict with no `edit_reason`, from apply or `retarget`, waits until the user says what the local
  edit is for: record their answer under that skill's `edits`, then resolve it.
- `keep`: nothing written. `why` says what happened. Each one goes under "Needs your call" in the report.

After any apply or `retarget`, check that
`grep -rnE '^(<<<<<<<|\|\|\|\|\|\|\||>>>>>>>)' plugins/` prints nothing.

A file with `frontmatter_changed` had its frontmatter changed upstream, and apply brought that in. Show the user
the change. If a principle's `description` changed, compare it with the row that names the principle in the
`principles-*` routers (`plugins/hstack/skills/principles-*/SKILL.md`). If the row no longer fits, suggest new
wording, but leave the router as it is. If upstream removed `disable-model-invocation`, say so first: the
principle's description would then load into every session, which the routers exist to avoid.

Apply also moves base to upstream's head, and holds the skills listed in `skills`, leaving their files as they
are. Stage exactly what the sync touched, never a whole folder, since untracked work of the user's may sit there:
the paths `touched` prints (every file apply, adopt and retarget changed that isn't committed yet, the license
and `upstream.json` included), and each file you edited by hand, with `git add -A -- <paths>`. Then commit:

```
Sync pstack to <head, 8 characters>

<one line per upstream commit: its subject>

<what was applied, merged, resolved and held>
```

## 4. What waits for the user

Don't decide these yourself. Put each in the report with what you'd suggest, and act once the user answers, on
the same branch. Stage and commit each answer the same way, with `upstream.json` in the same commit.

- **Deleted upstream** (`event: deleted`). Keep it as hstack's own: remove its entry from `upstream.json` and
  leave the files. Or drop it: delete its folder, remove its entry, and fix what names it (below).
- **Renamed upstream** (`event: renamed`, with `to`). To keep syncing it under its local name, run
  `retarget pstack <skill> <to>`, which merges what changed since the hold. If the user wants the local name
  changed too, also `git mv` its folder, rename its key in `upstream.json`, remove its `upstream` field, and fix
  what names it.
- **Gone locally** (`event: missing-locally`). Ask whether to stop tracking it (remove its entry) or bring it back.
  To bring it back, restore its folder from git history, then run `retarget pstack <skill> <upstream name>`.
- **Back** (`event: back`): a held skill whose local folder and upstream folder are both there again. Run
  `retarget pstack <skill> <upstream name>` to resume syncing it.

A skill's upstream name is its entry's `upstream` field in `upstream.json`, or its own name if it has none.
- **New upstream skills** (`new_skills`). List each, one line with its description. The next status won't list
  them again. If the user wants one, run `adopt pstack <skill>`. A new principle also needs a row in one of the
  routers and in the README's tables, so suggest which router.
- **Unrecorded edits** (`unrecorded_edits`): a copied file differs from upstream at base, but `upstream.json`
  gives no reason for it. Ask the user why, and record the answer under that skill's `edits`. Without a reason,
  you can't keep the edit's purpose when upstream rewrites around it. A stale note (`stale_edit_notes`) is a
  reason whose file now matches upstream again. Remove it.
- **A change the user rejects.** Restore the local text, and record why under that skill's `edits`. The base has
  already moved past upstream's change, so it won't come back.

Held skills are listed under `held` in every status until they are settled. Remind the user each time.

The names of copied skills appear in the routers, the hstack README (its tables, diagrams and count of
principles), `plugins/hstack/hooks/premise_cue.py` and its tests, and `typescript-best-practices`. Find them with
`grep -rn "<skill>" plugins/ README.md`. If you change the hook or its tests, run
`python3 -m unittest discover -s hooks/tests -v` from `plugins/hstack`.

## 5. The cookbook: report and suggest

For claude-cookbooks, read its `commits` and each `diff`. A notebook's diff shows its cells' text rather than its
JSON. Then read `plugins/hstack/skills/dream/MIGRATION.md`. It maps each part of the cookbook to the plugin,
lists every change the port made, and lists what it left out. For each upstream change, say what it is and
whether the port should follow it, and if so, which file you'd change and how. Leave out changes to parts the
port doesn't use. If `license_changed` is there, show its diff too. Change nothing yet.

Once the user answers, make the changes they approved and add them to MIGRATION.md where its lists call for it.
A change they decline is now a deliberate difference, so add it to MIGRATION.md's changes with their reason.
Update MIGRATION.md's **Source** line to the new head commit and its date. Then run
`apply claude-cookbooks --head <the head of the report the user reviewed>`, with `--accept-license` if they
agreed to a changed license, to move base. If it says status ran again since, show the user the latest report
before going on. Stage and commit as in step 3. If the user wants to decide later, leave base where it is, and the next status reports
the same changes again.

## 6. Report

Lead with anything that stops a source (`error`, `path_missing`), then:

```markdown
**Branch:** sync-upstream/<date>, not pushed

**pstack** <base, 8 characters> → <head, 8 characters>, <n> upstream commits
- Applied: <file>: <what changed, in a few words>
- Merged with your edits: <file>: <what changed>
- Needs your call: conflicts you resolved and how, `keep` files, deleted, renamed or missing skills, frontmatter
  changes, license
- New upstream: `<skill>`: <description>

**claude-cookbooks** <base> → <head>
- <each change>: <your suggestion>
```

Write "nothing new" for a source with no changes, and leave out empty lines.
