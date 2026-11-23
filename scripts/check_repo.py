"""Check the repository against docs/REPOSITORY-STANDARD.md (read-only).

Rules:

1. Folder index: a folder with files has a README.md, or the nearest
   README.md above it names the folder.
2. No one-file folders (a short allowlist covers import-path folders).
3. Index completeness: in INDEXED_FOLDERS, the README.md names every file.
4. Docs header and footer: guides carry a Version and date line at the top
   and a link line at the bottom.
5. Markdown links to files and folders resolve.
6. No internal host names in the public files.

Usage:
    python scripts/check_repo.py          # print problems, exit 1 if any
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Folders whose README.md lists every file by name.
INDEXED_FOLDERS = (
    "deploy",
    "deploy/local",
    "deploy/local/vm-data-api",
    "deploy/local/vm-data-api/tests",
    "eval",
    "eval/datasets",
    "scripts",
    "src/clusters",
    "src/example_server",
    "tests",
)

# Folders with one file that a code import path or a documented location requires.
ONE_FILE_ALLOWED = {
    "src/clusters/eligibility_v0": "package import; the Eligibility tools",
    "src/clusters/sample_directory": "package import; the generic example pattern",
}

# Files never expected in an index.
INDEX_SKIP = {"__init__.py", "conftest.py", ".gitkeep"}

# Markdown that carries its own header convention or is not a guide.
HEADER_SKIP_PREFIXES = ("agents/",)
HEADER_SKIP_FILES = {
    "README.md",
    "AGENTS.md",
    "QUICKSTART.md",
    "COMPONENTS.md",
    "CREDITS.md",
    "CHANGES-v7.1.md",
    "SUBMISSION-NOTE.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "db/seeds/README-submission-seed.md",
    "eval/MAPPING-PROMPT.md",  # pasted verbatim into a model; no header or footer
}

FORBIDDEN_TERMS = ("Node2",)
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".sql",
    ".txt",
    ".csv",
}
LINK = re.compile(r"\]\(([^)#\s]+)(#[^)]*)?\)")
VERSION_LINE = re.compile(r"Version\s+[\w.]+.*\b20\d\d-\d\d-\d\d\b")


def tracked_files(root: Path) -> list[str]:
    """List the files git tracks, or every file when git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split("\n")
        files = [line for line in out if line]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    skip = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv"}
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and not skip & set(p.relative_to(root).parts)
    )


def _folders(files: list[str]) -> dict[str, list[str]]:
    """Map each folder to every file at or below it."""
    below: dict[str, list[str]] = defaultdict(list)
    for name in files:
        parts = name.split("/")
        for depth in range(1, len(parts)):
            below["/".join(parts[:depth])].append(name)
    return below


def check_folder_index(files: list[str]) -> list[str]:
    """Each folder needs its own README.md or a parent index that names it."""
    problems: list[str] = []
    present = set(files)
    for folder, inside in sorted(_folders(files).items()):
        if f"{folder}/README.md" in present:
            continue
        # Walk up to the nearest README and require it to name this folder.
        parent = Path(folder).parent
        covered = False
        while True:
            candidate = f"{parent}/README.md" if str(parent) != "." else "README.md"
            if candidate in present:
                text = (ROOT / candidate).read_text(encoding="utf-8")
                rel = (
                    str(Path(folder).relative_to(parent))
                    if str(parent) != "."
                    else folder
                )
                first = rel.split("/")[0]
                covered = first in text
                break
            if str(parent) == ".":
                break
            parent = parent.parent
        if not covered:
            problems.append(
                f"folder index: {folder}/ has no README.md and no parent index names it"
            )
    return problems


def check_one_file_folders(files: list[str]) -> list[str]:
    """Flag folders that hold a single file, unless allowlisted."""
    problems: list[str] = []
    for folder, inside in sorted(_folders(files).items()):
        content = [f for f in inside if not f.endswith(("README.md", ".gitkeep"))]
        if len(content) == 1 and folder not in ONE_FILE_ALLOWED:
            problems.append(f"one-file folder: {folder}/ holds only {content[0]}")
    return problems


def check_index_complete(files: list[str]) -> list[str]:
    """Each indexed folder README.md must name every file in its folder."""
    problems: list[str] = []
    for folder in INDEXED_FOLDERS:
        readme = ROOT / folder / "README.md"
        if not readme.exists():
            problems.append(f"index missing: {folder}/README.md")
            continue
        text = readme.read_text(encoding="utf-8")
        for name in files:
            if Path(name).parent.as_posix() != folder:
                continue
            base = Path(name).name
            if base in INDEX_SKIP or base == "README.md":
                continue
            if base not in text:
                problems.append(
                    f"index incomplete: {folder}/README.md does not name {base}"
                )
    return problems


def check_headers(files: list[str]) -> list[str]:
    """Guides need a Version and date line at the top and a link footer."""
    problems: list[str] = []
    for name in files:
        if not name.endswith(".md") or name.startswith(HEADER_SKIP_PREFIXES):
            continue
        if name in HEADER_SKIP_FILES:
            continue
        lines = (ROOT / name).read_text(encoding="utf-8").split("\n")
        if not any(VERSION_LINE.search(line) for line in lines[:8]):
            problems.append(f"header: {name} has no Version and date line at the top")
        tail = [line for line in lines if line.strip()][-1] if any(lines) else ""
        if "](" not in tail:
            problems.append(f"footer: {name} does not end with a link line")
    return problems


def check_links(files: list[str]) -> list[str]:
    """Relative Markdown links must resolve to a file or folder."""
    problems: list[str] = []
    for name in files:
        if not name.endswith(".md"):
            continue
        text = (ROOT / name).read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = match.group(1)
            if target.startswith(("http", "mailto")):
                continue
            if not ((ROOT / name).parent / target).resolve().exists():
                problems.append(f"link: {name} points to missing {target}")
    return problems


def check_terms(files: list[str]) -> list[str]:
    """Keep internal host names out of the public files."""
    problems: list[str] = []
    for name in files:
        if name == "scripts/check_repo.py" or Path(name).suffix not in TEXT_SUFFIXES:
            continue
        text = (ROOT / name).read_text(encoding="utf-8", errors="ignore")
        for term in FORBIDDEN_TERMS:
            if term in text:
                problems.append(f"internal term: {name} contains {term}")
    return problems


def run(root: Path = ROOT) -> list[str]:
    """Run every rule and return the list of problems."""
    files = tracked_files(root)
    return (
        check_folder_index(files)
        + check_one_file_folders(files)
        + check_index_complete(files)
        + check_headers(files)
        + check_links(files)
        + check_terms(files)
    )


def main() -> int:
    """Print the problems and return a process exit code."""
    problems = run()
    for line in problems:
        print(line)
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
