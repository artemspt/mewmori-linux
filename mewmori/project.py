"""What the user is working on, read off JetBrains' own bookkeeping.

No window poking: the IDE already records every project it has opened, and the
one actually in front of you is the one whose .idea directory was touched last.
That needs nothing but the filesystem — no xdotool, no window titles, no
subprocesses at all.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from . import apps

HOME = Path.home()
CONFIG = HOME / ".config" / "JetBrains"
IDE_NAMES = {"pycharm": "PyCharm", "clion": "CLion"}

# directories that say nothing about what a project *is*
SKIP_DIRS = {
    ".git", ".idea", ".vscode", "__pycache__", "node_modules", "venv", ".venv",
    "env", "build", "dist", "target", ".mypy_cache", ".pytest_cache", ".tox",
    "cmake-build-debug", "cmake-build-release", "site-packages", ".gradle",
}
# most-explanatory first: prose beats a lockfile for working out what a
# project is, and requirements.txt is a last resort
MANIFESTS = (
    "README.md", "README.rst", "README.txt", "readme.md",
    "CLAUDE.md", "INFRASTRUCTURE.md", "ARCHITECTURE.md", "docs/README.md",
    "pyproject.toml", "package.json", "CMakeLists.txt", "Cargo.toml",
    "go.mod", "setup.py", "Makefile", "requirements.txt",
)
CODE_EXT = {
    ".py": "Python", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++",
    ".cc": "C++", ".rs": "Rust", ".go": "Go", ".js": "JavaScript",
    ".ts": "TypeScript", ".java": "Java", ".kt": "Kotlin", ".sh": "shell",
    ".qml": "QML", ".css": "CSS", ".html": "HTML", ".sql": "SQL",
}


@dataclass(frozen=True)
class Project:
    path: Path
    ide: str
    title: str = ""          # JetBrains' own window title, e.g. "proj – main.py"

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def open_file(self) -> str:
        """The file the IDE last had in front — it is right there in the title."""
        for dash in (" – ", " — ", " - "):
            if dash in self.title:
                return self.title.split(dash, 1)[1].strip()
        return ""


def ide_running() -> set[str]:
    """Which JetBrains IDEs have a live process.

    Delegated to apps, which matches the executable rather than the whole
    command line — otherwise merely typing "pycharm" in a shell counts as
    having the IDE open.
    """
    return {k for k in apps.running() if k in IDE_NAMES}


def _recent(ide_key: str):
    """(path, frameTitle, .idea mtime) for every project this IDE remembers."""
    out = []
    for cfg in sorted(CONFIG.glob(f"{IDE_NAMES[ide_key]}*/options/recentProjects.xml")):
        try:
            root = ET.parse(cfg).getroot()
        except (OSError, ET.ParseError):
            continue
        for entry in root.iter("entry"):
            key = entry.get("key") or ""
            if not key.startswith("$USER_HOME$"):
                continue
            path = HOME / key[len("$USER_HOME$/"):]
            meta = entry.find(".//RecentProjectMetaInfo")
            title = (meta.get("frameTitle") or "") if meta is not None else ""
            idea = path / ".idea"
            try:
                mtime = idea.stat().st_mtime
            except OSError:
                continue          # project has been moved or deleted
            out.append((mtime, path, title))
    return out


def current() -> Project | None:
    """The project most plausibly on screen right now, or None."""
    best = None
    for key in ide_running():
        for mtime, path, title in _recent(key):
            if best is None or mtime > best[0]:
                best = (mtime, path, title, key)
    if best is None:
        return None
    return Project(path=best[1], ide=IDE_NAMES[best[3]], title=best[2])


# -- reading the project ----------------------------------------------------
def _count_below(d: Path) -> int:
    try:
        return sum(1 for p in d.rglob("*") if p.is_file())
    except OSError:
        return 0


def tree(root: Path, limit: int = 90, depth: int = 3) -> list[str]:
    """Interesting paths inside the project, relative and capped.

    A directory whose contents fall past the depth limit is annotated with how
    many files are in it. Without that a model reads the bare name and happily
    concludes the folder is empty.
    """
    out = []
    for path in sorted(root.rglob("*")):
        if len(out) >= limit:
            break
        rel = path.relative_to(root)
        if len(rel.parts) > depth or any(p in SKIP_DIRS for p in rel.parts):
            continue
        if path.name.startswith(".") and path.is_file():
            continue
        if path.is_dir():
            n = _count_below(path) if len(rel.parts) == depth else 0
            out.append(str(rel) + "/" + (f"  ({n} файлов)" if n else ""))
        else:
            out.append(str(rel))
    return out


def languages(root: Path, limit: int = 400) -> list[str]:
    """Which languages the project is written in, commonest first."""
    counts: dict[str, int] = {}
    seen = 0
    for path in root.rglob("*"):
        if seen >= limit:
            break
        rel = path.relative_to(root)
        if any(p in SKIP_DIRS for p in rel.parts) or not path.is_file():
            continue
        seen += 1
        lang = CODE_EXT.get(path.suffix)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


def open_source(proj: Project, limit: int = 1200) -> str:
    """The head of the file the owner is actually looking at, if it is code."""
    if not proj.open_file:
        return ""
    matches = [p for p in proj.path.rglob(proj.open_file)
               if not any(part in SKIP_DIRS for part in p.relative_to(proj.path).parts)]
    if not matches:
        return ""
    f = min(matches, key=lambda p: len(p.parts))
    if f.suffix not in CODE_EXT:
        return ""
    try:
        return f.read_text(errors="replace")[:limit]
    except OSError:
        return ""


def digest(proj: Project, budget: int = 2600, full_tree: bool = False) -> str:
    """A compact description of the project, small enough for a short context.

    full_tree spends most of the budget on the file listing: a truncated tree
    invites the model to conclude that folders are empty when they are not.
    """
    root = proj.path
    parts = [f"Проект «{proj.name}» открыт в {proj.ide}, путь {root}."]
    langs = languages(root)
    if langs:
        parts.append("Языки: " + ", ".join(langs[:4]) + ".")
    if proj.open_file:
        parts.append(f"Сейчас открыт файл: {proj.open_file}.")

    listing = tree(root, limit=220 if full_tree else 90)
    if listing:
        joined = "\n".join(listing)
        share = int(budget * (0.72 if full_tree else 0.5))
        cut = joined[:share]
        parts.append("Файлы:\n" + cut
                     + ("\n(список обрезан)" if len(joined) > share else ""))

    src = open_source(proj)
    if src:
        parts.append(f"--- начало открытого файла {proj.open_file} ---\n{src}")

    for name in _doc_candidates(root):
        try:
            text = (root / name).read_text(errors="replace").strip()
        except OSError:
            continue
        if text:
            parts.append(f"--- {name} ---\n{text[:520]}")
            break

    return "\n".join(parts)[:budget]


def _doc_candidates(root: Path):
    """Named manifests first, then any other top-level markdown."""
    for name in MANIFESTS:
        if (root / name).is_file():
            yield name
    for f in sorted(root.glob("*.md")):
        if f.name not in MANIFESTS:
            yield f.name
