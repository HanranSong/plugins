#!/usr/bin/env python3
"""Keep the files this repo copied from other repositories in step with those repositories.

upstream.json, at the repo root, names each source: its repo and branch, the upstream commit its files were last
synced to (`base`), and a mode.

- merge: files copied with few or no edits, such as pstack's skills. Upstream's change to each file, from base to
  the branch's head, is merged into the local file with `git merge-file`, so local edits survive. A skill that
  upstream deleted or renamed, or that is gone locally, is held at the commit it was last synced to and left alone
  until someone decides what to do with it (`retarget`, or removing it from the manifest).
- report: files that were rewritten while porting them, such as the dream cookbook. Nothing is merged: `status`
  writes what changed upstream, and `apply` only moves base once someone has reviewed it.

A skill is a folder holding a SKILL.md, so a folder whose SKILL.md is gone counts as deleted.

Commands, each printing one JSON object:

  status                      Fetch each source and write what changed upstream to the report folder. Changes
                              no file in the repo.
  apply SOURCE --head HEAD [--accept-license]
                              Merge mode: write upstream's changes into the local files, and hold the skills
                              upstream deleted or renamed or that are gone locally. Both modes: move base to the
                              head `status` saw, which must be HEAD, the head of the report the user reviewed.
                              If upstream's license changed, it stops unless --accept-license is given, which
                              also copies the new license.
  adopt SOURCE SKILL          Copy a skill that upstream has at base, and start tracking it.
  retarget SOURCE SKILL NAME  Resume syncing a held skill from upstream's NAME (its new name, or its own name if
                              it came back), merging upstream's change since the hold.
  touched [--clear]           List the paths apply, adopt and retarget wrote or deleted since the list was last
                              cleared, upstream.json included, that still have changes git hasn't committed.
                              --clear empties the list first.

Each source's clone is a bare one under .git/sync-upstream/ that holds commits and folders but fetches a file's
content only when it is read, so later runs download only what is new.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import itertools
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

MANIFEST = "upstream.json"
MAX_COMMITS = 100
REGULAR = ("100644", "100755")  # git's modes for a plain file and an executable one; others are links or submodules


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(args, capture_output=True)
    if check and proc.returncode != 0:
        sys.exit(f"{' '.join(args)} failed:\n{proc.stderr.decode(errors='replace')}")
    return proc


def blob_id(data: bytes) -> str:
    """The id git gives a file with this content, so a local file compares with upstream's without fetching it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class Clone:
    """One source's bare clone."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        return run("git", "--git-dir", str(self.path), *args, check=check)

    def update(self, repo: str, branch: str) -> str:
        """Fetch the branch, and return its head commit."""
        if self.path.exists():
            self.git("remote", "set-url", "origin", repo)
            self.git("fetch", "--quiet", "--filter=blob:none", "origin", f"+refs/heads/{branch}:refs/heads/{branch}")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            run("git", "clone", "--quiet", "--bare", "--filter=blob:none", "--single-branch", "--branch", branch,
                repo, str(self.path))
        return self.id(f"refs/heads/{branch}")

    def id(self, spec: str) -> str | None:
        """The object a revision names, such as `<commit>:<path>`, or None if there is none."""
        proc = self.git("rev-parse", "--verify", "--quiet", spec, check=False)
        return proc.stdout.decode().strip() if proc.returncode == 0 else None

    def history_problem(self, base: str, head: str) -> str | None:
        """Why head can't be treated as base plus upstream's later commits, if it can't."""
        if self.id(f"{base}^{{commit}}") is None:
            return "base isn't in upstream's history any more: upstream rewrote it"
        if self.git("merge-base", "--is-ancestor", base, head, check=False).returncode != 0:
            return "base isn't an ancestor of upstream's head: upstream rewrote its history"
        return None

    def files(self, commit: str, path: str) -> dict[str, tuple[str, str]]:
        """Each file, link or submodule at or under path, by its full path, to its mode and object id."""
        out = self.git("ls-tree", "-r", "-z", commit, "--", path).stdout.decode()
        found = {}
        for entry in filter(None, out.split("\0")):
            meta, name = entry.split("\t", 1)
            mode, _kind, oid = meta.split()
            found[name] = (mode, oid)
        return found

    def skills(self, commit: str, path: str) -> set[str]:
        """The names of the folders directly inside path that hold a SKILL.md."""
        pattern = re.compile(re.escape(path) + r"/([^/]+)/SKILL\.md")
        return {m.group(1) for name in self.files(commit, path) if (m := pattern.fullmatch(name))}

    def read(self, oid: str) -> bytes:
        return self.git("cat-file", "blob", oid).stdout

    def log(self, base: str, head: str, paths: list[str]) -> list[dict[str, str]]:
        """The commits after base, up to head, that touched paths, newest first."""
        out = self.git("log", f"-{MAX_COMMITS}", "--format=%H%x1f%cs%x1f%s", f"{base}..{head}", "--", *paths)
        return [dict(zip(("commit", "date", "subject"), line.split("\x1f"))) for line in out.stdout.decode().splitlines()]

    def renamed_to(self, base: str, head: str, parent: str, name: str, skills_at_head: set[str]) -> str | None:
        """The other skill in parent that git sees parent/name's files move to between base and head, if any."""
        out = self.git("diff", "--name-status", "-z", "-M", base, head, "--", parent + "/").stdout.decode().split("\0")
        moved: dict[str, str] = {}
        i = 0
        while i < len(out) - 1:
            status = out[i]
            if status[:1] in ("R", "C"):
                old, new = out[i + 1], out[i + 2]
                i += 3
                rest = new[len(parent) + 1:]
                if status[0] == "R" and old.startswith(f"{parent}/{name}/") and new.startswith(parent + "/") and "/" in rest:
                    folder = rest.split("/", 1)[0]
                    if folder != name and folder in skills_at_head:
                        moved[old] = folder
            else:
                i += 2
        if f"{parent}/{name}/SKILL.md" in moved:
            return moved[f"{parent}/{name}/SKILL.md"]
        return Counter(moved.values()).most_common(1)[0][0] if moved else None


def readable(path: str, data: bytes) -> list[str]:
    """The file's lines as a reviewer reads them: a notebook's cells rather than its JSON."""
    raw = data.decode("utf-8", errors="replace")
    if path.endswith(".ipynb"):
        try:
            cells = json.loads(raw).get("cells", [])
        except (ValueError, AttributeError):
            cells = None
        if cells is not None:
            parts = []
            for cell in cells:
                source = cell.get("source", "")
                source = "".join(source) if isinstance(source, list) else source
                parts.append(f"# ---- {cell.get('cell_type', 'unknown')} cell ----\n{source}\n")
            raw = "".join(parts)
    return [line if line.endswith("\n") else line + "\n" for line in raw.splitlines(keepends=True)]


def write_diff(dest: Path, path: str, before: bytes | None, after: bytes | None) -> str:
    lines = difflib.unified_diff(readable(path, before or b""), readable(path, after or b""),
                                 f"base/{path}", f"head/{path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(lines), encoding="utf-8")
    return str(dest)


def frontmatter(data: bytes) -> str | None:
    text = data.decode("utf-8", errors="replace")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 3)
    return text[4:end] if end >= 0 else None


def description(data: bytes) -> str:
    lines = (frontmatter(data) or "").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("description:"):
            value = line[len("description:"):].strip()
            if value in ("", ">", ">-", "|", "|-"):
                value = " ".join(l.strip() for l in itertools.takewhile(lambda l: l[:1] in (" ", "\t"), lines[i + 1:]))
            return value.strip("\"'")
    return ""


def merge(ours: bytes, base: bytes, theirs: bytes, labels: tuple[str, str, str]) -> tuple[bytes, bool]:
    """git merge-file's result, and whether it left conflict markers."""
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, data in zip(("ours", "base", "theirs"), (ours, base, theirs)):
            path = Path(tmp, name)
            path.write_bytes(data)
            paths.append(str(path))
        proc = run("git", "merge-file", "-p", "--diff3", "-L", labels[0], "-L", labels[1], "-L", labels[2], *paths,
                   check=False)
    if not 0 <= proc.returncode <= 127:
        sys.exit(f"git merge-file failed:\n{proc.stderr.decode(errors='replace')}")
    return proc.stdout, proc.returncode > 0


def linked(root: Path, path: Path) -> bool:
    """Whether path, or a folder between it and root, is a symlink, so writing to it could land outside root."""
    return any(p.is_symlink() for p in (path, *path.parents) if p != root and root in p.parents)


def executable(mode: str) -> bool:
    return mode == "100755"


def plan_skill(clone: Clone, *, base: str, base_dir: str, head: str, head_dir: str, root: Path, local_rel: str,
               edits: dict[str, str], diffs: Path | None) -> tuple[list[dict], list[str], list[str]]:
    """What to do to one skill's local files to bring in upstream's change from base_dir at base to head_dir at
    head; and the local edits that have no reason recorded, and the recorded reasons whose edit is gone."""
    before = {p[len(base_dir) + 1:]: e for p, e in clone.files(base, base_dir).items()}
    after = {p[len(head_dir) + 1:]: e for p, e in clone.files(head, head_dir).items()}
    labels = ("local", f"upstream {base[:8]}", f"upstream {head[:8]}")
    actions, unrecorded, stale = [], [], []
    for name in sorted(before.keys() | after.keys()):
        old_entry, new_entry = before.get(name), after.get(name)
        b, t = old_entry and old_entry[1], new_entry and new_entry[1]
        path = root / local_rel / name
        is_linked = linked(root, path)
        plain = path.is_file() and not is_linked
        ours = path.read_bytes() if plain else None
        o = blob_id(ours) if ours is not None else None
        shown = f"{local_rel}/{name}"
        if b and o and o != b and name not in edits:
            unrecorded.append(shown)
        if name in edits and o == b:
            stale.append(shown)
        if old_entry == new_entry:
            continue
        act: dict = {"file": shown}
        if name in edits:
            act["edit_reason"] = edits[name]
        if is_linked or (path.exists() and not plain) or any(e and e[0] not in REGULAR for e in (old_entry, new_entry)):
            act["action"], act["why"] = "keep", "a link, submodule or folder where a plain file was expected"
            actions.append(act)
            continue
        # The execute bit merges like content: upstream's change to it is brought in unless the local bit was
        # changed too, and an unchanged upstream bit leaves the local one alone.
        local_x = plain and bool(path.stat().st_mode & 0o100)
        new_mode = None
        if new_entry and old_entry is None:
            new_mode = new_entry[0]
        elif new_entry and old_entry and old_entry[0] != new_entry[0] and local_x == executable(old_entry[0]):
            new_mode = new_entry[0]
        if o is not None and o == t:
            if new_mode and executable(new_mode) != local_x:
                act["action"], act["_mode"] = "mode", new_mode
                actions.append(act)
            continue
        theirs = clone.read(t) if t else None
        if t is None:
            if ours is None:
                continue
            act["action"] = "delete" if o == b else "keep"
            if o != b:
                act["why"] = "upstream deleted this file, and the local copy has edits"
        elif ours is None:
            act["action"] = "add" if b is None else "keep"
            if b is None:
                act["_write"], act["_mode"] = theirs, new_entry[0]
            else:
                act["why"] = "upstream changed this file, and it isn't here locally, so it isn't brought back"
        elif o == b:
            act["action"], act["_write"], act["_mode"] = "update", theirs, new_mode
        else:
            older = clone.read(b) if b else b""
            if b"\0" in ours + older + theirs:
                act["action"], act["why"] = "keep", "a binary file changed on both sides"
            else:
                merged, conflicted = merge(ours, older, theirs, labels)
                act["action"], act["_write"], act["_mode"] = ("conflict" if conflicted else "merge"), merged, new_mode
        if diffs is not None:
            older = clone.read(b) if b else None
            act["diff"] = write_diff(diffs / (shown + ".diff"), shown, older, theirs)
            if older is not None and theirs is not None and frontmatter(older) != frontmatter(theirs):
                act["frontmatter_changed"] = True
        actions.append(act)
    return actions, unrecorded, stale


def plan_merge(clone: Clone, src: dict, head: str, root: Path, diffs: Path | None) -> dict:
    udir, ldir = src["upstream_dir"], src["local_dir"]
    if clone.id(f"{head}:{udir}") is None:
        return {"path_missing": {"path": udir, "commits": clone.log(src["base"], head, [udir])[:3]}}
    at_base, at_head = clone.skills(src["base"], udir), clone.skills(head, udir)
    result: dict = {"files": [], "skills": [], "held": [], "new_skills": [], "unrecorded_edits": [],
                    "stale_edit_notes": []}
    for skill, entry in src["skills"].items():
        name = entry.get("upstream", skill)
        held = "held_at" in entry
        since = entry.get("held_at", src["base"])
        local_rel = f"{ldir}/{skill}"
        event: dict = {"skill": skill}
        if not (root / local_rel).is_dir():
            event["event"] = "missing-locally"
        elif name not in at_head:
            to = clone.renamed_to(since, head, udir, name, at_head)
            event.update({"event": "renamed", "to": to} if to else {"event": "deleted"})
            event["commits"] = clone.log(since, head, [f"{udir}/{name}"])[:3]
        elif held:
            event["event"] = "back"  # both the local folder and upstream's are here again
        else:
            actions, unrecorded, stale = plan_skill(clone, base=since, base_dir=f"{udir}/{name}", head=head,
                                                    head_dir=f"{udir}/{name}", root=root, local_rel=local_rel,
                                                    edits=entry.get("edits", {}), diffs=diffs)
            result["files"] += actions
            result["unrecorded_edits"] += unrecorded
            result["stale_edit_notes"] += stale
            continue
        result["held" if held else "skills"].append(event)
    renamed = {e["to"] for e in result["skills"] + result["held"] if e.get("to")}
    known = {e.get("upstream", s) for s, e in src["skills"].items()} | renamed
    for name in sorted(at_head - at_base - known):
        oid = clone.id(f"{head}:{udir}/{name}/SKILL.md")
        result["new_skills"].append({"skill": name, "description": description(clone.read(oid))})
    # A rename target counts as tracked, so a commit that renames a held skill again still moves base on apply,
    # which retarget needs: it looks for the target at base.
    tracked = [f"{udir}/{n}" for n in sorted(known)]
    result["commits"] = clone.log(src["base"], head, tracked + ([src["license"]["upstream"]] if "license" in src else []))
    return result


def plan_report(clone: Clone, src: dict, head: str, diffs: Path) -> dict:
    before: dict[str, tuple[str, str]] = {}
    after: dict[str, tuple[str, str]] = {}
    for path in src["paths"]:
        before |= clone.files(src["base"], path)
        after |= clone.files(head, path)
    files = []
    for path in sorted(before.keys() | after.keys()):
        b, t = before.get(path), after.get(path)
        if b == t:
            continue
        change = "added" if b is None else "deleted" if t is None else "changed"
        diff = write_diff(diffs / (path + ".diff"), path, content(clone, b), content(clone, t))
        files.append({"path": path, "change": change, "diff": diff})
    return {"files": files, "commits": clone.log(src["base"], head, src["paths"])}


def content(clone: Clone, entry: tuple[str, str] | None) -> bytes | None:
    """An entry's content for a diff: a file's text, or a link's target. A submodule has none."""
    return clone.read(entry[1]) if entry and entry[0] != "160000" else None


def license_entries(clone: Clone, src: dict, head: str) -> tuple[tuple[str, str] | None, ...] | None:
    """The upstream license's mode and id at base and at head, or None if the source names no license."""
    lic = src.get("license")
    if not lic:
        return None
    return tuple(clone.files(commit, lic["upstream"]).get(lic["upstream"]) for commit in (src["base"], head))


def public(value):
    """value without the keys that start with an underscore, which hold file contents and modes."""
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def write_file(path: Path, data: bytes, mode: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mode is not None:
        current = path.stat().st_mode
        path.chmod(current | 0o111 if mode == "100755" else current & ~0o111)


def write_files(root: Path, actions: list[dict]) -> None:
    for act in actions:
        path = root / act["file"]
        if "_write" in act:
            write_file(path, act["_write"], act.get("_mode"))
        elif act["action"] == "delete":
            path.unlink()
        elif act["action"] == "mode":
            current = path.stat().st_mode
            path.chmod(current | 0o111 if executable(act["_mode"]) else current & ~0o111)


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        common = run("git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir")
        self.cache = Path(common.stdout.decode().strip()) / "sync-upstream"
        self.manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))

    def source(self, name: str) -> dict:
        if name not in self.manifest["sources"]:
            sys.exit(f"No source named {name!r} in {MANIFEST}. Sources: {', '.join(self.manifest['sources'])}")
        return self.manifest["sources"][name]

    def clone(self, name: str) -> Clone:
        return Clone(self.cache / f"{name}.git")

    def save(self) -> None:
        self.record([MANIFEST])
        (self.root / MANIFEST).write_text(json.dumps(self.manifest, indent=2) + "\n", encoding="utf-8")

    # Every path apply, adopt and retarget write or delete, recorded before the write and kept until the next
    # sync starts, so a sync that was cut off can still stage exactly what it changed, new untracked files included.
    def touched(self) -> list[str]:
        path = self.cache / "touched.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def pending(self) -> list[str]:
        """The touched paths git reports as changed and not yet committed: what this sync still has to stage."""
        paths = self.touched()
        if not paths:
            return []
        out = run("git", "--literal-pathspecs", "-C", str(self.root), "status", "--porcelain", "-z",
                  "--untracked-files=all", "--", *paths).stdout.decode().split("\0")
        found, i = set(), 0
        while i < len(out):
            entry = out[i]
            if entry:
                found.add(entry[3:])
                if "R" in entry[:2] or "C" in entry[:2]:  # a rename's or copy's old path follows, unprefixed
                    i += 1
                    found.add(out[i])
            i += 1
        return sorted(found & set(paths))

    def record(self, paths: list[str]) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        merged = sorted(set(self.touched()) | set(paths))
        (self.cache / "touched.json").write_text(json.dumps(merged, indent=1), encoding="utf-8")

    def clear_touched(self) -> None:
        (self.cache / "touched.json").unlink(missing_ok=True)


def changed_paths(actions: list[dict]) -> list[str]:
    return [act["file"] for act in actions if act["action"] != "keep"]


def status(repo: Repo) -> dict:
    report = repo.cache / "report"
    shutil.rmtree(report, ignore_errors=True)
    seen, sources = {}, []
    for name, src in repo.manifest["sources"].items():
        clone = repo.clone(name)
        head = clone.update(src["repo"], src["branch"])
        result: dict = {"source": name, "mode": src["mode"], "base": src["base"], "head": head}
        if problem := clone.history_problem(src["base"], head):
            result["error"] = problem
        else:
            diffs = report / name
            if src["mode"] == "merge":
                result |= plan_merge(clone, src, head, repo.root, diffs)
            else:
                result |= plan_report(clone, src, head, diffs)
            entries = license_entries(clone, src, head)
            if "path_missing" not in result and entries and entries[0] != entries[1]:
                lic = src["license"]
                diff = write_diff(diffs / "LICENSE.diff", lic["upstream"], content(clone, entries[0]),
                                  content(clone, entries[1]))
                result["license_changed"] = {"upstream": lic["upstream"], "local": lic["local"], "diff": diff}
        news = ("error", "path_missing", "files", "skills", "new_skills", "license_changed", "commits")
        result["nothing_new"] = not any(result.get(key) for key in news)
        seen[name] = {"base": src["base"], "head": head}
        sources.append(public(result))
    repo.cache.mkdir(parents=True, exist_ok=True)
    (repo.cache / "seen.json").write_text(json.dumps(seen, indent=2), encoding="utf-8")
    return {"nothing_new": all(s["nothing_new"] for s in sources), "report_folder": str(report), "sources": sources}


def apply(repo: Repo, name: str, reviewed_head: str, accept_license: bool) -> dict:
    src = repo.source(name)
    seen_file = repo.cache / "seen.json"
    seen = json.loads(seen_file.read_text(encoding="utf-8")).get(name) if seen_file.exists() else None
    if not seen or seen["base"] != src["base"]:
        sys.exit(f"Run status first: apply brings in what the last status reported for {name}.")
    head, clone = seen["head"], repo.clone(name)
    if reviewed_head != head:
        sys.exit(f"The last status saw {name} at {head[:8]}, not the reviewed {reviewed_head[:8]}: status ran again "
                 "since that review. Show the user the latest status's report, and apply its head once they agree.")
    if problem := clone.history_problem(src["base"], head):
        sys.exit(f"{problem}. Settle it with the user before applying.")
    entries = license_entries(clone, src, head)
    license_changed = bool(entries) and entries[0] != entries[1]
    if license_changed:
        if not accept_license:
            sys.exit("Upstream's license changed since base. Show the user its diff (license_changed in status), "
                     "and run apply again with --accept-license once they agree.")
        local = repo.root / src["license"]["local"]
        if entries[1] is None or entries[1][0] not in REGULAR:
            sys.exit("Upstream's license isn't a plain file at head. Settle the license with the user by hand.")
        if linked(repo.root, local) or (local.exists() and not local.is_file()):
            sys.exit(f"{local} is a link or not a plain file. Settle the license with the user by hand.")
    result: dict = {"source": name, "previous_base": src["base"], "base": head}
    if src["mode"] == "merge":
        plan = plan_merge(clone, src, head, repo.root, diffs=None)
        if "path_missing" in plan:
            sys.exit(f"{src['upstream_dir']} isn't upstream at {head[:8]}. Settle where it went before applying.")
        repo.record(changed_paths(plan["files"]))
        write_files(repo.root, plan["files"])
        holds = [e["skill"] for e in plan["skills"] if e["event"] in ("deleted", "renamed", "missing-locally")]
        for skill in holds:
            src["skills"][skill]["held_at"] = src["base"]
        result["files"] = public(plan["files"])
        result["newly_held"] = holds
    if license_changed:
        repo.record([src["license"]["local"]])
        write_file(repo.root / src["license"]["local"], clone.read(entries[1][1]), None)
        result["license_updated"] = src["license"]["local"]
    src["base"] = head
    repo.save()
    return result


def adopt(repo: Repo, name: str, skill: str) -> dict:
    src = repo.source(name)
    if src["mode"] != "merge":
        sys.exit(f"{name} is a report source; only merge sources copy skills.")
    if skill in src["skills"]:
        sys.exit(f"{skill} is already tracked.")
    clone, udir = repo.clone(name), src["upstream_dir"]
    if skill not in clone.skills(src["base"], udir):
        sys.exit(f"{udir}/{skill}/SKILL.md isn't upstream at base {src['base'][:8]}. If it is new, run status and "
                 "apply first.")
    dest = repo.root / src["local_dir"] / skill
    if dest.exists() or dest.is_symlink() or linked(repo.root, dest):
        sys.exit(f"{dest} already exists, or a folder on its way is a link.")
    upstream = f"{udir}/{skill}"
    entries = clone.files(src["base"], upstream)
    # Read every file before writing any, so a failed fetch leaves nothing half copied.
    contents = {path[len(upstream) + 1:]: (mode, clone.read(oid)) for path, (mode, oid) in entries.items()
                if mode in REGULAR}
    repo.record([str(dest.relative_to(repo.root) / rel) for rel in contents])
    for rel, (mode, data) in contents.items():
        write_file(dest / rel, data, mode)
    src["skills"][skill] = {}
    src["skills"] = dict(sorted(src["skills"].items()))
    repo.save()
    local = dest.relative_to(repo.root)
    return {"adopted": skill, "files": sorted(str(local / rel) for rel in contents),
            "not_copied": sorted(p for p, (mode, _) in entries.items() if mode not in REGULAR)}


def retarget(repo: Repo, name: str, skill: str, upstream_name: str) -> dict:
    src = repo.source(name)
    entry = src["skills"].get(skill)
    if entry is None or "held_at" not in entry:
        sys.exit(f"{skill} isn't a held skill of {name}.")
    udir, clone = src["upstream_dir"], repo.clone(name)
    if upstream_name not in clone.skills(src["base"], udir):
        sys.exit(f"{udir}/{upstream_name}/SKILL.md isn't upstream at base {src['base'][:8]}.")
    actions, _, _ = plan_skill(clone, base=entry["held_at"], base_dir=f"{udir}/{entry.get('upstream', skill)}",
                               head=src["base"], head_dir=f"{udir}/{upstream_name}", root=repo.root,
                               local_rel=f"{src['local_dir']}/{skill}", edits=entry.get("edits", {}), diffs=None)
    repo.record(changed_paths(actions))
    write_files(repo.root, actions)
    del entry["held_at"]
    if upstream_name == skill:
        entry.pop("upstream", None)
    else:
        entry["upstream"] = upstream_name
    repo.save()
    return {"skill": skill, "upstream": upstream_name, "files": public(actions)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync files copied from upstream repositories.")
    parser.add_argument("--root", type=Path, help="the repo root (default: the repo holding the current folder)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("source")
    apply_parser.add_argument("--head", required=True, help="the head commit of the status the user reviewed")
    apply_parser.add_argument("--accept-license", action="store_true")
    for command, extra in (("adopt", ()), ("retarget", ("upstream_name",))):
        sub = commands.add_parser(command)
        for arg in ("source", "skill", *extra):
            sub.add_argument(arg)
    commands.add_parser("touched").add_argument("--clear", action="store_true")
    args = parser.parse_args()
    root = args.root or Path(run("git", "rev-parse", "--show-toplevel").stdout.decode().strip())
    repo = Repo(root.resolve())
    if args.command == "status":
        out = status(repo)
    elif args.command == "apply":
        out = apply(repo, args.source, args.head, args.accept_license)
    elif args.command == "adopt":
        out = adopt(repo, args.source, args.skill)
    elif args.command == "touched":
        if args.clear:
            repo.clear_touched()
        out = {"paths": repo.pending()}
    else:
        out = retarget(repo, args.source, args.skill, args.upstream_name)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
