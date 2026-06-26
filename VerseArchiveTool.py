
from pathlib import Path
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys

REPO = Path(r"E:\github\Fortnite-Verse-Archive")
SOURCE_PROJECT = "FortniteGame"
VERSEPROJECT_ROOT = Path.home() / "AppData" / "Local" / "UnrealEditorFortnite" / "Saved" / "VerseProject"

DIGESTS = {
    "Fortnite": ("Fortnite", "Fortnite.digest.verse"),
    "UnrealEngine": ("UnrealEngine", "UnrealEngine.digest.verse"),
    "Verse": ("Verse", "Verse.digest.verse"),
}

DECL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]*>)?\s*:=\s*(class|interface|struct|enum|module)\b")
BUILD_RE = re.compile(r"\+\+Fortnite\+Release-[0-9.]+-CL-[0-9]+")


def run_git(args, allow_fail=False):
    p = subprocess.run(["git"] + args, cwd=REPO, text=True, capture_output=True)
    if p.stdout:
        print(p.stdout.rstrip())
    if p.stderr:
        print(p.stderr.rstrip())
    if p.returncode != 0 and not allow_fail:
        raise SystemExit(p.returncode)
    return p


def source_paths():
    base = VERSEPROJECT_ROOT / SOURCE_PROJECT
    paths = {}
    for module, (folder, filename) in DIGESTS.items():
        p = base / folder / filename
        if not p.exists():
            raise FileNotFoundError(f"Missing {p}")
        paths[module] = p
    return paths


def detect_build(paths):
    for p in paths.values():
        sample = p.read_text(encoding="utf-8", errors="ignore")[:5000]
        m = BUILD_RE.search(sample)
        if m:
            return m.group(0)
    return "Unknown-Fortnite-Release"


def safe_name(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "unknown"


def clean_dir(path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def split_digest(src, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = src.read_text(encoding="utf-8", errors="ignore").splitlines()
    items = []
    cur_name = None
    cur_kind = None
    cur_lines = []

    def save():
        if cur_name and cur_lines:
            file_name = f"{safe_name(cur_kind)}__{safe_name(cur_name)}.verse"
            (out_dir / file_name).write_text("\n".join(cur_lines) + "\n", encoding="utf-8")
            items.append({"kind": cur_kind, "name": cur_name, "file": file_name, "lines": len(cur_lines)})

    for line in lines:
        m = DECL_RE.match(line)
        if m:
            save()
            cur_name = m.group(1)
            cur_kind = m.group(2)
            cur_lines = [line]
        elif cur_name:
            cur_lines.append(line)
    save()
    return items


def existing_version_dirs():
    versions = REPO / "Versions"
    if not versions.exists():
        return []
    return sorted([p for p in versions.iterdir() if p.is_dir()])


def read_index(version_dir):
    idx = version_dir / "api_index.json"
    if not idx.exists():
        return {}
    try:
        return json.loads(idx.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_change_report(version_dir, previous_dir, current_index):
    reports = version_dir / "Reports"
    reports.mkdir(parents=True, exist_ok=True)
    prev_index = read_index(previous_dir) if previous_dir else {}

    added = []
    removed = []
    changed = []

    for module, items in current_index.items():
        prev_items = {f"{x['kind']}::{x['name']}": x for x in prev_index.get(module, [])}
        cur_items = {f"{x['kind']}::{x['name']}": x for x in items}
        for key in sorted(cur_items.keys() - prev_items.keys()):
            added.append(f"- {module}: {key}")
        for key in sorted(prev_items.keys() - cur_items.keys()):
            removed.append(f"- {module}: {key}")

        if previous_dir:
            for key in sorted(cur_items.keys() & prev_items.keys()):
                cur_file = version_dir / "Parsed" / module / cur_items[key]["file"]
                prev_file = previous_dir / "Parsed" / module / prev_items[key]["file"]
                if cur_file.exists() and prev_file.exists():
                    if cur_file.read_text(encoding="utf-8", errors="ignore") != prev_file.read_text(encoding="utf-8", errors="ignore"):
                        changed.append(f"- {module}: {key}")

    (reports / "Added.md").write_text("# Added API\n\n" + ("\n".join(added) if added else "No added API detected.") + "\n", encoding="utf-8")
    (reports / "Removed.md").write_text("# Removed API\n\n" + ("\n".join(removed) if removed else "No removed API detected.") + "\n", encoding="utf-8")
    (reports / "Changed.md").write_text("# Changed API\n\n" + ("\n".join(changed) if changed else "No changed API detected.") + "\n", encoding="utf-8")

    summary = [
        "# API Change Summary",
        "",
        f"Previous version: `{previous_dir.name if previous_dir else 'None detected'}`",
        f"Added: `{len(added)}`",
        f"Removed: `{len(removed)}`",
        f"Changed: `{len(changed)}`",
        "",
        "See `Added.md`, `Removed.md`, and `Changed.md` for details.",
    ]
    (reports / "Summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Archive Fortnite Verse digest files and split them for GitHub diffs.")
    parser.add_argument("--version", help="Folder name/version. If omitted, detected from digest build string.")
    parser.add_argument("--no-push", action="store_true", help="Commit but do not push.")
    parser.add_argument("--no-commit", action="store_true", help="Create files only.")
    args = parser.parse_args()

    if not (REPO / ".git").exists():
        raise SystemExit(f"Not a Git repo: {REPO}")

    paths = source_paths()
    build = args.version or detect_build(paths)
    version_dir = REPO / "Versions" / build

    previous_dirs = [p for p in existing_version_dirs() if p.name != build]
    previous_dir = previous_dirs[-1] if previous_dirs else None

    clean_dir(version_dir / "Raw")
    clean_dir(version_dir / "Parsed")

    current_index = {}
    for module, src in paths.items():
        dst = version_dir / "Raw" / src.name
        shutil.copy2(src, dst)
        print(f"Copied {src.name}")
        current_index[module] = split_digest(dst, version_dir / "Parsed" / module)

    (version_dir / "api_index.json").write_text(json.dumps(current_index, indent=2), encoding="utf-8")
    (version_dir / "README.md").write_text(
        "# Fortnite Verse API Archive\n\n"
        f"Version/build: `{build}`\n\n"
        "Folders:\n\n"
        "- `Raw/` contains the exact digest files copied from UEFN.\n"
        "- `Parsed/` splits classes, interfaces, structs, enums, and modules into separate files for cleaner GitHub diffs.\n"
        "- `Reports/` contains added, removed, and changed API summaries.\n",
        encoding="utf-8",
    )

    write_change_report(version_dir, previous_dir, current_index)

    print(f"\nArchived to: {version_dir}")

    if args.no_commit:
        print("Created files only. No commit.")
        return

    run_git(["add", "-A"])
    commit_msg = f"Archive Fortnite Verse API {build}"
    commit = run_git(["commit", "-m", commit_msg], allow_fail=True)
    if commit.returncode != 0:
        print("No commit created. This usually means there were no changes.")
        return

    if not args.no_push:
        run_git(["push", "origin", "main"])

    print("Done.")


if __name__ == "__main__":
    main()
