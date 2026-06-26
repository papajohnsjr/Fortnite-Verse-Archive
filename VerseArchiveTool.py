
from pathlib import Path
import argparse, difflib, json, re, shutil, subprocess, sys

REPO = Path(r"E:\github\Fortnite-Verse-Archive")
SOURCE_PROJECT = "FortniteGame"
SOURCE_ROOT = Path.home() / "AppData" / "Local" / "UnrealEditorFortnite" / "Saved" / "VerseProject" / SOURCE_PROJECT
DIGESTS = {
    "Fortnite": SOURCE_ROOT / "Fortnite" / "Fortnite.digest.verse",
    "UnrealEngine": SOURCE_ROOT / "UnrealEngine" / "UnrealEngine.digest.verse",
    "Verse": SOURCE_ROOT / "Verse" / "Verse.digest.verse",
}
BUILD_RE = re.compile(r"\+\+Fortnite\+Release-[0-9.]+-CL-[0-9]+")
DECL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:<[^>]*>)?\s*:=\s*(class|interface|struct|enum|module)\b")
FUNC_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*<.*?>?\s*\(")


def read(p):
    return p.read_text(encoding="utf-8", errors="ignore")


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def run_git(args, allow_fail=False):
    p = subprocess.run(["git"] + args, cwd=REPO, text=True, capture_output=True)
    if p.stdout: print(p.stdout.rstrip())
    if p.stderr: print(p.stderr.rstrip())
    if p.returncode and not allow_fail: raise SystemExit(p.returncode)
    return p


def detect_build():
    for p in DIGESTS.values():
        if p.exists():
            m = BUILD_RE.search(read(p)[:8000])
            if m: return m.group(0)
    return "Unknown-Fortnite-Release"


def parse_api(text):
    items = {}
    current = None
    current_key = None
    for line in text.splitlines():
        m = DECL_RE.match(line)
        if m:
            name, kind = m.group(1), m.group(2)
            current_key = f"{kind}::{name}"
            current = {"kind": kind, "name": name, "header": line.strip(), "functions": [], "text": [line]}
            items[current_key] = current
            continue
        if current:
            current["text"].append(line)
            fm = FUNC_RE.match(line)
            if fm:
                current["functions"].append(line.strip())
    for v in items.values():
        v["text"] = "\n".join(v["text"]).strip()
    return items


def find_previous(current_build):
    versions = REPO / "Versions"
    if not versions.exists(): return None
    dirs = [p for p in versions.iterdir() if p.is_dir() and p.name != current_build]
    dirs.sort(key=lambda p: p.stat().st_mtime)
    return dirs[-1] if dirs else None


def previous_raw(prev_dir, module):
    if not prev_dir: return None
    p = prev_dir / "Raw" / f"{module}.digest.verse"
    if p.exists(): return p
    p = prev_dir / f"{module}.digest.verse"
    return p if p.exists() else None


def make_reports(version_dir, build, prev_dir):
    reports = version_dir / "Reports"
    diffs = version_dir / "Diffs"
    reports.mkdir(parents=True, exist_ok=True)
    diffs.mkdir(parents=True, exist_ok=True)
    totals = {"added":0, "removed":0, "changed":0}
    all_added=[]; all_removed=[]; all_changed=[]
    api_index = {}

    for module, cur_path in DIGESTS.items():
        cur_text = read(cur_path)
        cur_api = parse_api(cur_text)
        api_index[module] = {k:{"kind":v["kind"],"name":v["name"],"header":v["header"],"functions":v["functions"]} for k,v in cur_api.items()}

        prev_path = previous_raw(prev_dir, module)
        if prev_path:
            prev_text = read(prev_path)
            prev_api = parse_api(prev_text)
            diff = difflib.unified_diff(prev_text.splitlines(), cur_text.splitlines(), fromfile=str(prev_path.name), tofile=str(cur_path.name), lineterm="")
            write(diffs / f"{module}.diff", "\n".join(diff) + "\n")
        else:
            prev_api = {}
            write(diffs / f"{module}.diff", "No previous raw file found.\n")

        added = sorted(set(cur_api)-set(prev_api))
        removed = sorted(set(prev_api)-set(cur_api))
        changed = sorted(k for k in set(cur_api)&set(prev_api) if cur_api[k]["text"] != prev_api[k]["text"])
        totals["added"] += len(added); totals["removed"] += len(removed); totals["changed"] += len(changed)
        all_added += [f"- {module}: {x}" for x in added]
        all_removed += [f"- {module}: {x}" for x in removed]
        all_changed += [f"- {module}: {x}" for x in changed]

    write(reports / "Added.md", "# Added API\n\n" + ("\n".join(all_added) or "No added API detected.") + "\n")
    write(reports / "Removed.md", "# Removed API\n\n" + ("\n".join(all_removed) or "No removed API detected.") + "\n")
    write(reports / "Changed.md", "# Changed API\n\n" + ("\n".join(all_changed) or "No changed API detected.") + "\n")
    write(version_dir / "api_index.json", json.dumps(api_index, indent=2))
    summary = f"""# Fortnite Verse API {build}

Previous version compared: `{prev_dir.name if prev_dir else 'none found'}`

## Counts

- Added API blocks: `{totals['added']}`
- Removed API blocks: `{totals['removed']}`
- Changed API blocks: `{totals['changed']}`

## Folders

- `Raw/` exact digest files from UEFN
- `Reports/` readable API summaries
- `Diffs/` unified diffs for GitHub viewing
- `api_index.json` machine-readable API index
"""
    write(version_dir / "README.md", summary)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version")
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    build = args.version or detect_build()
    version_dir = REPO / "Versions" / build
    raw_dir = version_dir / "Raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    for module, src in DIGESTS.items():
        if not src.exists(): raise SystemExit(f"Missing: {src}")
        shutil.copy2(src, raw_dir / src.name)
        print(f"Copied {src.name}")

    prev_dir = find_previous(build)
    make_reports(version_dir, build, prev_dir)
    print(f"Archived to: {version_dir}")

    if args.no_commit:
        print("No commit mode complete.")
        return

    run_git(["add", "-A", "VerseArchiveTool.py", str(version_dir.relative_to(REPO))])
    c = run_git(["commit", "-m", f"Update Verse archive {build}"], allow_fail=True)
    if c.returncode != 0:
        print("No commit created, probably no changes.")
        return
    if not args.no_push:
        run_git(["push", "origin", "main"])
    print("Done.")

if __name__ == "__main__":
    main()
