"""Compare declared dependency floors against their current upstream releases.

Dependabot follows the *lock*: it proposes the next version one pull request at a time.
It never answers "how far behind is what we *declare*, across every declaration in the
repository?". This reads the declarations this repository owns, asks the package registry
(and, for pinned GitHub Actions, the Releases API) for the current release, and writes a
Markdown report. Nothing here inspects an installed environment or edits a file: a newer
release is a prompt to read the changelog and run the gate, not a merge.

Which declarations are read is the ``ECOSYSTEMS`` tuple below (``pypi``, ``npm``,
``cargo``, ``go``) plus, always, the ``uses:`` pins in ``.github/workflows/*.yml``.

The comparison is made at the precision the declaration commits to:

* ``>=10`` or ``v7.0.1`` compare on exactly the segments written (major, or all three);
* a caret range (``^5.6.1``, Cargo's bare ``"1.10"``) commits to the compatible series, so
  it compares on the major segment, or major.minor while the major is ``0``;
* a tilde range (``~5.8.3``) compares on major.minor;
* Go requirements compare on major.minor.

A declaration with no numeric floor (``*``, ``latest``, a branch such as ``@stable``, a
path or workspace reference) has nothing to compare and is listed as ``NO FLOOR``.

Two honest exits for a red line, neither of which is "raise the floor to go green":

* ``# freshness-hold: <reason>`` on the declaring line (Actions, requirements files,
  TOML manifests, ``go.mod`` with ``//``): a standing policy, never expires.
* ``.github/dependency-deferrals.json`` with ``deferredLatest`` and ``reason``: reviewed
  but not this month. It expires by itself once the registry passes that release.

    python tools/check_dependency_freshness.py --output report.md --github-output
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "agent-session-restore-dependency-freshness"

# Declarations read besides the workflow pins. Keep this in step with the ecosystems in
# `.github/dependabot.yml`, and with the manifests named below.
ECOSYSTEMS: tuple[str, ...] = ("pypi",)
NPM_MANIFESTS: tuple[str, ...] = ()
CARGO_MANIFESTS: tuple[str, ...] = ()
GO_MANIFESTS: tuple[str, ...] = ()

HOLD_MARKER = "freshness-hold:"
DEFERRALS_PATH = REPO_ROOT / ".github" / "dependency-deferrals.json"

_REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*(.*)$")
_MINIMUM_RE = re.compile(r"(>=|>|==|~=)\s*([0-9][0-9A-Za-z.!+_-]*)")
_RELEASE_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*")
_RANGE_RE = re.compile(r"^(\^|~|>=|>|=)?\s*v?([0-9]+(?:\.[0-9]+)*)")
# `owner/repo`, optionally followed by a sub-path: `github/codeql-action/init` is one
# action published from a directory of `github/codeql-action`.
_USES_RE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*([\w.\-]+/[\w.\-]+(?:/[\w.\-]+)*)@([0-9a-fA-F]{40}|\S+)"
    r"(?:[ \t]*#[ \t]*(.*))?[ \t]*$",
    re.MULTILINE,
)
_HOLD_NAME_RE = re.compile(r"^\s*(?:require\s+)?[\"']?([A-Za-z0-9_.@/\-]+)")
LOCAL_SPECS = ("workspace:", "file:", "link:", "portal:")
_PSEUDO_VERSION_RE = re.compile(r"-\d{14}-")

_CRATES_MIN_INTERVAL = 1.05  # crates.io asks crawlers for at most one request a second
_last_crates_request = 0.0


class DependencyCheckError(RuntimeError):
    """Raised when a declaration file cannot be read."""


# --------------------------------------------------------------------------- versions


def release_key(version: str) -> tuple[int, ...] | None:
    """The numeric release segment of a version, or None when it has none.

    Pre-release and local suffixes are dropped, so 7.0.0rc1 and 7.0.0 rank the same.
    That is precise enough to answer "has the declared floor aged?".
    """
    match = _RELEASE_RE.match(version.strip().lstrip("vV"))
    if not match:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def is_newer_version(latest: str, declared: str) -> bool:
    """Is `latest` newer than `declared`, at the precision `declared` states?"""
    latest_key = release_key(latest)
    declared_key = release_key(declared)
    if latest_key is None or declared_key is None:
        return False
    depth = len(declared_key)
    padded = latest_key + (0,) * (depth - len(latest_key))
    return padded[:depth] > declared_key


def range_floor(spec: str, bare_is_caret: bool) -> str:
    """The floor a npm or Cargo version range commits to, truncated to its precision.

    Returns "" when the range names no version to compare (`*`, `latest`, `workspace:*`,
    a path, a git URL, an alias).
    """
    text = spec.strip()
    if not text or text[0] in "*<" or text.lower() in {"latest", "next", "x"}:
        return ""
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text):  # workspace: file: git+ https: npm: ...
        return ""
    match = _RANGE_RE.match(text)
    if not match:
        return ""
    op, number = match.group(1), match.group(2)
    parts = number.split(".")
    caret = op == "^" or (op is None and bare_is_caret)
    if caret:
        depth = 2 if parts[0] == "0" and len(parts) >= 2 else 1
    elif op == "~":
        depth = min(2, len(parts))
    else:
        depth = len(parts)
    return ".".join(parts[:depth])


# ------------------------------------------------------------------- shared plumbing


def scan_holds(text: str) -> dict[str, str]:
    """`{package name: reason}` for every line carrying a `freshness-hold:` comment."""
    holds: dict[str, str] = {}
    for raw_line in text.splitlines():
        if HOLD_MARKER not in raw_line:
            continue
        head, _, reason = raw_line.partition(HOLD_MARKER)
        head = head.rstrip().rstrip("#").rstrip().removesuffix("//").rstrip()
        match = _HOLD_NAME_RE.match(head)
        if match and reason.strip():
            holds[match.group(1).lower()] = reason.strip()
    return holds


def _package(
    name: str, minimum: str, requirement: str, source: str, kind: str, hold: str = ""
) -> dict[str, str]:
    return {
        "name": name,
        "minimum": minimum,
        "requirement": requirement,
        "source": source,
        "hold": hold,
        "kind": kind,
    }


def _merge(packages: list[dict[str, str]]) -> list[dict[str, str]]:
    """One row per distinct declaration; the same one in two files lists both sources."""
    merged: dict[tuple[str, str], dict[str, str]] = {}
    for package in packages:
        key = (package["name"].lower(), package["requirement"])
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(package)
        elif package["source"] not in existing["source"].split(", "):
            existing["source"] += f", {package['source']}"
    return sorted(merged.values(), key=lambda p: (p["name"].lower(), p["requirement"]))


def _load_toml(path: Path) -> dict[str, object]:
    try:
        import tomllib  # Python 3.11+; the freshness workflow runs on 3.12
    except ImportError as exc:  # pragma: no cover - only on an old local interpreter
        raise DependencyCheckError("reading TOML needs Python 3.11 or newer") from exc
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DependencyCheckError(f"cannot read {path.name}: {exc}") from exc


def _existing(root: Path, names: tuple[str, ...], what: str) -> list[Path]:
    found = [root / name for name in names if (root / name).is_file()]
    if not found:
        raise DependencyCheckError(f"missing {what} manifest: {', '.join(names)}")
    return found


# ------------------------------------------------------------------------------ pypi


def parse_requirement(raw: str, source: str, hold: str = "") -> dict[str, str] | None:
    """One PEP 508 line -> a package row, or None for a blank/option line."""
    line = raw.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        return None
    head = line.split(";", 1)[0].strip()
    match = _REQUIREMENT_RE.match(head)
    if not match:
        return None
    name, specifiers = match.groups()
    minimum = _MINIMUM_RE.search(specifiers)
    return _package(name, minimum.group(2) if minimum else "", line, source, "pypi", hold)


def parse_requirements(text: str, source: str) -> list[dict[str, str]]:
    packages = []
    for raw_line in text.splitlines():
        comment = raw_line.split("#", 1)[1].strip() if "#" in raw_line else ""
        hold = comment[len(HOLD_MARKER) :].strip() if comment.startswith(HOLD_MARKER) else ""
        package = parse_requirement(raw_line, source, hold)
        if package:
            packages.append(package)
    return packages


def load_pypi(root: Path = REPO_ROOT) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    files = sorted(root.glob("requirements*.txt"))
    pyproject = root / "pyproject.toml"
    if not files and not pyproject.is_file():
        raise DependencyCheckError("missing Python manifest: requirements*.txt or pyproject.toml")
    for path in files:
        packages += parse_requirements(path.read_text(encoding="utf-8"), path.name)
    if pyproject.is_file():
        data = _load_toml(pyproject)
        holds = scan_holds(pyproject.read_text(encoding="utf-8"))
        project = data.get("project")
        project = project if isinstance(project, dict) else {}
        groups: list[object] = [project.get("dependencies", [])]
        for table in (project.get("optional-dependencies"), data.get("dependency-groups")):
            if isinstance(table, dict):
                groups += list(table.values())
        for group in groups:
            for entry in group if isinstance(group, list) else []:
                if not isinstance(entry, str):
                    continue  # `{include-group = ...}`
                package = parse_requirement(entry, "pyproject.toml")
                if package:
                    package["hold"] = holds.get(package["name"].lower(), "")
                    packages.append(package)
    return _merge(packages)


def fetch_pypi_version(package_name: str, timeout: float = 10.0) -> str | None:
    quoted = urllib.parse.quote(package_name, safe="")
    payload = _get_json(f"https://pypi.org/pypi/{quoted}/json", timeout=timeout)
    info = payload.get("info") if isinstance(payload, dict) else None
    version = info.get("version") if isinstance(info, dict) else None
    return str(version) if version else None


# ------------------------------------------------------------------------------- npm


def load_npm(root: Path = REPO_ROOT) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for path in _existing(root, NPM_MANIFESTS, "npm"):
        rel = path.relative_to(root).as_posix()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DependencyCheckError(f"cannot read {rel}: {exc}") from exc
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            for name, spec in (data.get(section) or {}).items():
                spec = str(spec)
                if spec.startswith(LOCAL_SPECS):
                    continue  # a sibling in this repository, not a registry dependency
                floor = range_floor(spec, False)
                packages.append(_package(name, floor, f"{name}@{spec}", rel, "npm"))
    return _merge(packages)


def fetch_npm_version(package_name: str, timeout: float = 10.0) -> str | None:
    quoted = urllib.parse.quote(package_name, safe="@")
    payload = _get_json(f"https://registry.npmjs.org/{quoted}/latest", timeout=timeout)
    version = payload.get("version") if isinstance(payload, dict) else None
    return str(version) if version else None


# ----------------------------------------------------------------------------- cargo


def load_cargo(root: Path = REPO_ROOT) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for path in _existing(root, CARGO_MANIFESTS, "Cargo"):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        holds = scan_holds(text)
        data = _load_toml(path)
        tables: list[object] = [
            data.get("dependencies"),
            data.get("dev-dependencies"),
            data.get("build-dependencies"),
        ]
        targets = data.get("target")
        if isinstance(targets, dict):
            for target in targets.values():
                if isinstance(target, dict):
                    tables += [target.get("dependencies"), target.get("build-dependencies")]
        for table in tables:
            if not isinstance(table, dict):
                continue
            for name, spec in table.items():
                if isinstance(spec, dict):
                    if "path" in spec or ("git" in spec and "version" not in spec):
                        continue
                    version = spec.get("version")
                    package_name = str(spec.get("package", name))
                else:
                    version, package_name = spec, name
                if not isinstance(version, str):
                    continue
                packages.append(
                    _package(
                        package_name,
                        range_floor(version, True),
                        f"{package_name} = {version}",
                        rel,
                        "cargo",
                        holds.get(str(name).lower(), ""),
                    )
                )
    return _merge(packages)


def fetch_crate_version(crate_name: str, timeout: float = 10.0) -> str | None:
    global _last_crates_request
    wait = _CRATES_MIN_INTERVAL - (time.monotonic() - _last_crates_request)
    if wait > 0:
        time.sleep(wait)
    _last_crates_request = time.monotonic()
    quoted = urllib.parse.quote(crate_name, safe="")
    payload = _get_json(f"https://crates.io/api/v1/crates/{quoted}", timeout=timeout)
    crate = payload.get("crate") if isinstance(payload, dict) else None
    if not isinstance(crate, dict):
        return None
    version = crate.get("max_stable_version") or crate.get("max_version")
    return str(version) if version else None


# ------------------------------------------------------------------------------- go


def load_go(root: Path = REPO_ROOT) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for path in _existing(root, GO_MANIFESTS, "go.mod"):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        holds = scan_holds(text)
        in_block = False
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("require ("):
                in_block = True
                continue
            if in_block and stripped.startswith(")"):
                in_block = False
                continue
            if in_block:
                entry = stripped
            elif stripped.startswith("require "):
                entry = stripped[len("require ") :].strip()
            else:
                continue
            if "// indirect" in entry:
                continue
            match = re.match(r"^([^\s()]+)\s+(v[0-9][^\s]*)", entry)
            if not match:
                continue
            module, version = match.groups()
            floor = "" if _PSEUDO_VERSION_RE.search(version) else version
            key = release_key(floor) if floor else None
            minimum = ".".join(str(part) for part in key[:2]) if key else ""
            hold = holds.get(module.lower(), "")
            packages.append(_package(module, minimum, f"{module} {version}", rel, "go", hold))
    return _merge(packages)


def _go_escape(module: str) -> str:
    """The module proxy protocol writes each upper-case letter as `!` + lower-case."""
    return re.sub(r"[A-Z]", lambda m: "!" + m.group(0).lower(), module)


def fetch_go_version(module: str, timeout: float = 10.0) -> str | None:
    quoted = urllib.parse.quote(_go_escape(module), safe="/")
    payload = _get_json(f"https://proxy.golang.org/{quoted}/@latest", timeout=timeout)
    version = payload.get("Version") if isinstance(payload, dict) else None
    return str(version).lstrip("vV") if version else None


# ------------------------------------------------------------------- GitHub Actions


def parse_workflow_actions(text: str, source: str) -> list[dict[str, str]]:
    """Every `uses: owner/repo@<ref> # vX.Y.Z` declaration in a workflow file."""
    packages = []
    for match in _USES_RE.finditer(text):
        action, ref, comment = match.group(1), match.group(2), (match.group(3) or "").strip()
        hold = comment[len(HOLD_MARKER) :].strip() if comment.startswith(HOLD_MARKER) else ""
        # A pinned SHA carries its declared version in the trailing comment (`# v7.0.1`);
        # a floating tag (`@v6`) carries it in the ref itself.
        version_source = comment if comment else ref
        version_match = _RELEASE_RE.match(version_source.lstrip("vV")) if not hold else None
        minimum = version_match.group(0) if version_match else ""
        packages.append(
            _package(
                action, minimum, f"{action}@{comment or ref[:12]}", source, "github-action", hold
            )
        )
    return packages


def load_workflow_actions(root: Path = REPO_ROOT) -> list[dict[str, str]]:
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        raise DependencyCheckError("missing .github/workflows directory")
    packages: list[dict[str, str]] = []
    for path in sorted([*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml")]):
        packages += parse_workflow_actions(path.read_text(encoding="utf-8"), path.name)
    return _merge(packages)


def action_repository(action_name: str) -> str:
    """The `owner/repo` that publishes an action, dropping any sub-path."""
    owner, _, rest = action_name.partition("/")
    return f"{owner}/{rest.split('/', 1)[0]}" if rest else action_name


def fetch_github_release(action_name: str, timeout: float = 10.0) -> str | None:
    """The latest release tag for an action, or None when it cannot be read.

    The token matters: anonymous api.github.com allows 60 requests an hour per address,
    shared by hosted runners, and past it every lookup returns 403 -- which reads as
    "latest unknown" on every row at once. `GITHUB_TOKEN` or `GH_TOKEN` is sent when set.
    """
    quoted = urllib.parse.quote(action_repository(action_name), safe="/")
    headers = {"Accept": "application/vnd.github+json"}
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = _get_json(
        f"https://api.github.com/repos/{quoted}/releases/latest", headers, timeout
    )
    tag = str(payload.get("tag_name") or "") if isinstance(payload, dict) else ""
    if release_key(tag) is not None:
        return tag.lstrip("vV")
    # `github/codeql-action` tags its latest *release* `codeql-bundle-v2.27.0` (the bundle),
    # while the action moves on `v4.x.y` tags; comparing against the bundle tag reads as a
    # false green. Fall back to the highest version tag.
    tags = _get_json(f"https://api.github.com/repos/{quoted}/tags?per_page=100", headers, timeout)
    if not isinstance(tags, list):
        return None
    best: tuple[tuple[int, ...], str] | None = None
    for entry in tags:
        name = str(entry.get("name") or "") if isinstance(entry, dict) else ""
        key = release_key(name)
        if key is None or not name.lstrip("vV")[:1].isdigit():
            continue
        if best is None or key > best[0]:
            best = (key, name.lstrip("vV"))
    return best[1] if best else None


def _get_json(
    url: str, headers: dict[str, str] | None = None, timeout: float = 10.0
) -> dict[str, object] | list[object] | None:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict | list) else None


# ------------------------------------------------------------------- deferrals, rows


def load_deferrals(path: Path = DEFERRALS_PATH) -> dict[str, tuple[str, str]]:
    """Read reviewed-but-not-now decisions: package -> (reviewed release, reason).

    A deferral says "we looked, and not this month", which must not outlive the release
    it was made against. `deferredLatest` is what makes it expire by itself; an entry
    without it (or without a reason) is ignored.
    """
    try:
        entries = json.loads(path.read_text(encoding="utf-8")).get("deferrals", {})
    except (OSError, ValueError):
        return {}
    deferrals: dict[str, tuple[str, str]] = {}
    for name, entry in (entries or {}).items():
        if not isinstance(entry, dict):
            continue
        latest = str(entry.get("deferredLatest", "")).strip()
        reason = str(entry.get("reason", "")).strip()
        if latest and reason:
            deferrals[name.lower()] = (latest, reason)
    return deferrals


def collect_status(
    packages: list[dict[str, str]],
    fetch: Callable[[str], str | None],
    deferrals: dict[str, tuple[str, str]] | None = None,
) -> list[dict[str, object]]:
    deferrals = deferrals if deferrals is not None else load_deferrals()
    rows: list[dict[str, object]] = []
    for package in packages:
        minimum = package["minimum"]
        if not minimum:
            # Nothing to compare: not a failure, and not worth a registry request.
            rows.append(
                {
                    **package,
                    "latest": "-",
                    "outdated": False,
                    "check_failed": False,
                    "no_floor": True,
                    "deferred_reason": "",
                }
            )
            continue
        latest = fetch(package["name"])
        reviewed, reason = deferrals.get(package["name"].lower(), ("", ""))
        deferred = bool(reviewed and latest and not is_newer_version(latest, reviewed))
        rows.append(
            {
                **package,
                "latest": latest or "unknown",
                "outdated": bool(latest and is_newer_version(latest, minimum)),
                "check_failed": latest is None,
                "no_floor": False,
                "deferred_reason": reason if deferred else "",
            }
        )
    return rows


def needs_review(row: dict[str, object]) -> bool:
    """An aged floor still counts unless a hold or a live deferral covers it."""
    return bool(row["outdated"]) and not row.get("hold") and not row.get("deferred_reason")


def _render_table(rows: list[dict[str, object]], empty_note: str) -> list[str]:
    lines = [
        "| Package | Declared in | Requirement | Latest | Status |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        if row["check_failed"]:
            status = "CHECK FAILED"
        elif row.get("no_floor"):
            status = "NO FLOOR (not compared)"
        elif row.get("hold") and row["outdated"]:
            status = f"HELD: {row['hold']}"
        elif row.get("deferred_reason") and row["outdated"]:
            status = f"DEFERRED at {row['latest']}: {row['deferred_reason']}"
        elif row["outdated"]:
            status = "REVIEW UPDATE"
        else:
            status = "OK"
        lines.append(
            f"| `{row['name']}` | `{row['source']}` | `{row['requirement']}` | "
            f"`{row['latest']}` | {status} |"
        )
    if not rows:
        lines.append(f"| - | - | - | - | {empty_note} |")
    return lines


SECTION_TITLES = {
    "pypi": "Python packages (PyPI)",
    "npm": "npm packages (registry.npmjs.org)",
    "cargo": "Rust crates (crates.io)",
    "go": "Go modules (proxy.golang.org)",
    "github-action": "GitHub Actions (pinned in .github/workflows/)",
}


def render_markdown(
    sections: dict[str, list[dict[str, object]]], error: str | None = None
) -> str:
    lines = ["# Dependency freshness report", ""]
    if error:
        lines.extend(["## Check failed", "", f"```text\n{error}\n```", ""])
        return "\n".join(lines)
    for kind, rows in sections.items():
        lines.extend([f"## {SECTION_TITLES[kind]}", ""])
        lines.extend(_render_table(rows, "none declared"))
        lines.append("")
    lines.extend(
        [
            "Declared floors are compared with the current release at the precision each",
            "declaration states. The installed environment is not inspected and no file is",
            "edited by this check.",
            "",
            "## Review policy",
            "",
            "1. Read the release notes, then run the repository gate before raising anything.",
            "2. Repin a GitHub Action by its new commit SHA with a `# vX.Y.Z` comment; do not",
            "   switch a pinned SHA back to a floating tag.",
            "3. Raising a floor only to silence this report is not an exit: a declaration is a",
            "   compatibility promise. When the floor really must stay, record why:",
            "   `# freshness-hold: <why>` on the declaring line for a standing policy, or an",
            "   entry in `.github/dependency-deferrals.json` with `deferredLatest` for",
            '   "reviewed, not now" (it expires once the registry passes that release).',
            "",
        ]
    )
    return "\n".join(lines)


def write_github_output(sections: dict[str, list[dict[str, object]]], report_path: Path) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    rows = [row for section in sections.values() for row in section]
    outdated = any(needs_review(row) for row in rows)
    check_failed = not rows or any(bool(row["check_failed"]) for row in rows)
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"outdated={'true' if outdated else 'false'}\n")
        output.write(f"check_failed={'true' if check_failed else 'false'}\n")
        output.write(f"needs_attention={'true' if outdated or check_failed else 'false'}\n")
        output.write(f"report_path={report_path.as_posix()}\n")


Loader = Callable[[Path], list[dict[str, str]]]
Fetcher = Callable[[str], str | None]


def collect_sections(root: Path = REPO_ROOT) -> dict[str, list[dict[str, object]]]:
    deferrals = load_deferrals()
    loaders: dict[str, tuple[Loader, Fetcher]] = {
        "pypi": (load_pypi, fetch_pypi_version),
        "npm": (load_npm, fetch_npm_version),
        "cargo": (load_cargo, fetch_crate_version),
        "go": (load_go, fetch_go_version),
    }
    sections: dict[str, list[dict[str, object]]] = {}
    for kind in ECOSYSTEMS:
        load, fetch = loaders[kind]
        sections[kind] = collect_status(load(root), fetch, deferrals)
    sections["github-action"] = collect_status(
        load_workflow_actions(root), fetch_github_release, deferrals
    )
    return sections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dependency-freshness-report.md")
    parser.add_argument(
        "--github-output", action="store_true", help="Write status fields to GITHUB_OUTPUT"
    )
    parser.add_argument(
        "--strict", action="store_true", help="Return non-zero when a declared floor has aged."
    )
    args = parser.parse_args()

    sections: dict[str, list[dict[str, object]]] = {}
    error: str | None = None
    try:
        sections = collect_sections()
    except DependencyCheckError as exc:
        error = str(exc)

    report = render_markdown(sections, error)
    output_path = Path(args.output)
    output_path.write_text(report, encoding="utf-8")
    print(report)

    if args.github_output:
        write_github_output(sections, output_path)
    if error:
        return 2
    rows = [row for section in sections.values() for row in section]
    if args.strict and (not rows or any(needs_review(r) or bool(r["check_failed"]) for r in rows)):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
