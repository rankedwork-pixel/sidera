#!/usr/bin/env python3
"""Sidera documentation sync validator.

Introspects the codebase to count key metrics (tests, DB methods, workflows,
migrations, MCP tools, connectors, skills, etc.) and compares them against
values documented in CLAUDE.md, README.md, and MEMORY.md.

Usage:
    # Check mode (default) — report mismatches, exit 1 if any found
    python -m scripts.doc_sync

    # Update mode — auto-fix counts in docs
    python -m scripts.doc_sync --update

    # Verbose — show all metrics even if they match
    python -m scripts.doc_sync --verbose

    # JSON output — machine-readable
    python -m scripts.doc_sync --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ── Project root (works from anywhere) ──────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent


# ── Metric definitions ──────────────────────────────────────────────────────


@dataclass
class Metric:
    """A single countable metric in the codebase."""

    name: str
    actual: int = 0
    description: str = ""


@dataclass
class DocReference:
    """A place in a doc file where a metric count appears."""

    file: str  # relative to ROOT
    pattern: str  # regex to find the number
    metric_name: str  # links to Metric.name
    replacement_template: str = ""  # format string for update mode


@dataclass
class SyncResult:
    """Result of comparing actual vs documented values."""

    metric_name: str
    actual: int
    documented: int
    file: str
    line_number: int
    match_text: str
    is_match: bool


# ── Metric counters ────────────────────────────────────────────────────────


def count_test_files() -> int:
    """Count test_*.py files in tests/."""
    return len(list((ROOT / "tests").rglob("test_*.py")))


def count_tests_via_pytest() -> int:
    """Count test items via pytest --collect-only. Falls back to AST counting."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q", "--no-header"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=120,
        )
        # Last non-empty line is like "2354 tests collected"
        for line in reversed(result.stdout.strip().splitlines()):
            m = re.search(r"(\d+) tests? collected", line)
            if m:
                return int(m.group(1))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: count def test_ functions via AST
    return _count_test_functions_ast()


def _count_test_functions_ast() -> int:
    """Count test functions by scanning files (no pytest needed)."""
    import ast

    count = 0
    for test_file in (ROOT / "tests").rglob("test_*.py"):
        try:
            tree = ast.parse(test_file.read_text())
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("test_"):
                        count += 1
        except SyntaxError:
            pass
    return count


def count_db_methods() -> int:
    """Count public async functions in src/db/service.py."""
    service = ROOT / "src" / "db" / "service.py"
    count = 0
    for line in service.read_text().splitlines():
        if re.match(r"^async def [a-z_]", line):
            count += 1
    return count


def count_workflows() -> int:
    """Count @inngest_client.create_function decorators."""
    count = 0
    for py in (ROOT / "src" / "workflows").rglob("*.py"):
        count += len(re.findall(r"@inngest_client\.create_function", py.read_text()))
    return count


def count_migrations() -> int:
    """Count .py files in alembic/versions/."""
    versions = ROOT / "alembic" / "versions"
    if not versions.exists():
        return 0
    return len([f for f in versions.iterdir() if f.suffix == ".py" and f.name != "__init__.py"])


def count_mcp_tools() -> int:
    """Count @tool( decorators in src/mcp_servers/."""
    count = 0
    exclude = {"__init__.py", "write_safety.py", "helpers.py"}
    for py in (ROOT / "src" / "mcp_servers").iterdir():
        if py.suffix == ".py" and py.name not in exclude:
            count += len(re.findall(r"@tool\(", py.read_text()))
    return count


def count_connectors() -> int:
    """Count connector modules (excluding __init__.py and retry.py)."""
    exclude = {"__init__.py", "retry.py"}
    connectors_dir = ROOT / "src" / "connectors"
    return len([
        f for f in connectors_dir.iterdir()
        if f.suffix == ".py" and f.name not in exclude
    ])


def count_skills() -> int:
    """Count skill YAML files (excluding _department.yaml, _role.yaml, _rules.yaml)."""
    library = ROOT / "src" / "skills" / "library"
    count = 0
    for yaml_file in library.rglob("*.yaml"):
        if yaml_file.name.startswith("_"):
            continue
        count += 1
    return count


def count_roles() -> int:
    """Count _role.yaml files."""
    library = ROOT / "src" / "skills" / "library"
    return len(list(library.rglob("_role.yaml")))


def count_departments() -> int:
    """Count _department.yaml files."""
    library = ROOT / "src" / "skills" / "library"
    return len(list(library.rglob("_department.yaml")))


def count_slack_methods() -> int:
    """Count public methods in SlackConnector."""
    slack = ROOT / "src" / "connectors" / "slack.py"
    count = 0
    for line in slack.read_text().splitlines():
        if re.match(r"    (async )?def [a-z_]", line):
            count += 1
    return count


# ── Collect all metrics ─────────────────────────────────────────────────────


def collect_metrics(skip_pytest: bool = False) -> dict[str, Metric]:
    """Run all counters and return metrics dict."""
    metrics: dict[str, Metric] = {}

    # Fast counters (file-based)
    metrics["db_methods"] = Metric("db_methods", count_db_methods(), "DB service methods")
    metrics["workflows"] = Metric("workflows", count_workflows(), "Inngest workflows")
    metrics["migrations"] = Metric("migrations", count_migrations(), "Alembic migrations")
    metrics["mcp_tools"] = Metric("mcp_tools", count_mcp_tools(), "MCP tools")
    metrics["connectors"] = Metric("connectors", count_connectors(), "API connectors")
    metrics["skills"] = Metric("skills", count_skills(), "YAML skills")
    metrics["roles"] = Metric("roles", count_roles(), "Agent roles")
    metrics["departments"] = Metric("departments", count_departments(), "Departments")
    metrics["slack_methods"] = Metric(
        "slack_methods", count_slack_methods(), "Slack connector methods"
    )
    metrics["test_files"] = Metric("test_files", count_test_files(), "Test files")

    # Slow counter (pytest collection)
    if skip_pytest:
        metrics["tests"] = Metric(
            "tests", _count_test_functions_ast(), "Test functions (AST)"
        )
    else:
        metrics["tests"] = Metric("tests", count_tests_via_pytest(), "Test items (pytest)")

    return metrics


# ── Doc references — where counts appear in docs ───────────────────────────


def build_doc_references() -> list[DocReference]:
    """Define where each metric appears in documentation files."""
    return [
        # ── CLAUDE.md ──
        DocReference(
            "CLAUDE.md",
            r"Database service.*?(\d+) methods",
            "db_methods",
        ),
        DocReference(
            "CLAUDE.md",
            r"(\d+) workflows:",
            "workflows",
        ),
        DocReference(
            "CLAUDE.md",
            r"(\d+) revisions",
            "migrations",
        ),
        DocReference(
            "CLAUDE.md",
            r"(\d+) tools total",
            "mcp_tools",
        ),
        DocReference(
            "CLAUDE.md",
            r"(\d+) connectors \+ retry",
            "connectors",
        ),
        DocReference(
            "CLAUDE.md",
            r"(\d+) skills in \d+ departments",
            "skills",
        ),
        DocReference(
            "CLAUDE.md",
            r"\d+ skills in (\d+) departments",
            "departments",
        ),
        DocReference(
            "CLAUDE.md",
            r"(\d+) unit \+ integration tests",
            "tests",
        ),
        DocReference(
            "CLAUDE.md",
            r"Slack connector.*?(\d+) methods",
            "slack_methods",
        ),
        DocReference(
            "CLAUDE.md",
            r"(\d+)-method CRUD",
            "db_methods",
        ),
        # ── README.md ──
        DocReference(
            "README.md",
            r"(\d+)-method CRUD",
            "db_methods",
        ),
        DocReference(
            "README.md",
            r"(\d+) tools",
            "mcp_tools",
        ),
        DocReference(
            "README.md",
            r"(\d+) workflows:",
            "workflows",
        ),
        DocReference(
            "README.md",
            r"(\d+)\+ unit and integration",
            "tests",
        ),
    ]


# ── Scan docs for actual values ─────────────────────────────────────────────


def scan_doc_values(references: list[DocReference]) -> list[SyncResult]:
    """Scan doc files and extract current documented values."""
    results: list[SyncResult] = []

    for ref in references:
        filepath = ROOT / ref.file
        if not filepath.exists():
            continue

        lines = filepath.read_text().splitlines()
        found = False
        for i, line in enumerate(lines, 1):
            m = re.search(ref.pattern, line)
            if m:
                documented = int(m.group(1))
                results.append(SyncResult(
                    metric_name=ref.metric_name,
                    actual=0,  # filled in later
                    documented=documented,
                    file=ref.file,
                    line_number=i,
                    match_text=line.strip(),
                    is_match=False,  # filled in later
                ))
                if not found:
                    found = True
                    continue  # keep scanning for more occurrences of same pattern

    return results


# ── Compare and report ──────────────────────────────────────────────────────


def compare(
    metrics: dict[str, Metric],
    doc_results: list[SyncResult],
    skip_pytest: bool = False,
) -> list[SyncResult]:
    """Fill in actual values and compare.

    When skip_pytest=True, test count comparisons are skipped because AST
    counting undercounts (misses parametrize expansions). Only structural
    metrics are checked in fast mode.
    """
    for result in doc_results:
        if result.metric_name in metrics:
            result.actual = metrics[result.metric_name].actual
            if skip_pytest and result.metric_name == "tests":
                # AST counting is inaccurate — skip test count checks in fast mode
                result.is_match = True
            else:
                result.is_match = result.actual == result.documented
    return doc_results


def update_docs(doc_results: list[SyncResult]) -> list[str]:
    """Update doc files with actual values. Returns list of files changed."""
    changed_files: set[str] = set()

    # Group by file
    by_file: dict[str, list[SyncResult]] = {}
    for r in doc_results:
        if not r.is_match:
            by_file.setdefault(r.file, []).append(r)

    for filename, mismatches in by_file.items():
        filepath = ROOT / filename
        lines = filepath.read_text().splitlines()

        for r in mismatches:
            # Replace on the exact line number
            idx = r.line_number - 1  # 0-based
            if idx < len(lines):
                old_line = lines[idx]
                # Find the matching ref pattern
                refs = [
                    ref for ref in build_doc_references()
                    if ref.file == filename and ref.metric_name == r.metric_name
                ]
                for ref in refs:
                    new_line = re.sub(
                        ref.pattern,
                        lambda m, d=r.documented, a=r.actual: m.group(0).replace(
                            str(d), str(a)
                        ),
                        old_line,
                        count=1,
                    )
                    if old_line != new_line:
                        lines[idx] = new_line
                        changed_files.add(filename)
                        break

        filepath.write_text("\n".join(lines) + "\n")

    return sorted(changed_files)


# ── Additional checks ───────────────────────────────────────────────────────


def check_orphaned_pycache() -> list[str]:
    """Find __pycache__ dirs that are tracked by git (not just normal runtime caches)."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "**/__pycache__"],
            capture_output=True, text=True, cwd=ROOT,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()
    except FileNotFoundError:
        pass
    return []


def check_empty_dirs() -> list[str]:
    """Find empty directories (excluding .git and __pycache__)."""
    empties = []
    for p in ROOT.rglob("*"):
        if not p.is_dir():
            continue
        if any(skip in str(p) for skip in [".git", "__pycache__", ".venv", "venv", "node_modules"]):
            continue
        if not any(p.iterdir()):
            empties.append(str(p.relative_to(ROOT)))
    return empties


def check_init_files() -> list[str]:
    """Check that all src/ packages have __init__.py (excludes templates/)."""
    exclude = {"templates"}
    missing = []
    for p in (ROOT / "src").rglob("*"):
        if p.is_dir() and p.name not in exclude:
            if any(f.suffix == ".py" for f in p.iterdir()):
                if not (p / "__init__.py").exists():
                    missing.append(str(p.relative_to(ROOT)))
    return missing


# ── Output formatting ──────────────────────────────────────────────────────


RESET = "\033[0m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
DIM = "\033[2m"


def print_report(
    metrics: dict[str, Metric],
    results: list[SyncResult],
    verbose: bool = False,
) -> int:
    """Print human-readable report. Returns exit code (0 = clean, 1 = mismatches)."""
    mismatches = [r for r in results if not r.is_match]
    matches = [r for r in results if r.is_match]

    print(f"\n{BOLD}Sidera Doc Sync Report{RESET}")
    print("=" * 60)

    # Metrics summary
    print(f"\n{BOLD}Codebase Metrics:{RESET}")
    for m in sorted(metrics.values(), key=lambda x: x.name):
        print(f"  {m.description:.<40} {BOLD}{m.actual}{RESET}")

    # Doc check results
    if mismatches:
        print(f"\n{RED}{BOLD}Mismatches ({len(mismatches)}):{RESET}")
        for r in mismatches:
            print(
                f"  {RED}MISMATCH{RESET} {r.file}:{r.line_number} "
                f"— {r.metric_name}: documented={r.documented}, actual={r.actual}"
            )
            print(f"  {DIM}  {r.match_text}{RESET}")

    if verbose and matches:
        print(f"\n{GREEN}Matches ({len(matches)}):{RESET}")
        for r in matches:
            print(f"  {GREEN}OK{RESET} {r.file}:{r.line_number} — {r.metric_name}={r.actual}")

    # Additional checks
    orphans = check_orphaned_pycache()
    if orphans:
        print(f"\n{YELLOW}Orphaned __pycache__ dirs ({len(orphans)}):{RESET}")
        for o in orphans:
            print(f"  {YELLOW}!{RESET} {o}")

    empties = check_empty_dirs()
    if empties:
        print(f"\n{YELLOW}Empty directories ({len(empties)}):{RESET}")
        for e in empties:
            print(f"  {YELLOW}!{RESET} {e}")

    missing_init = check_init_files()
    if missing_init:
        print(f"\n{YELLOW}Missing __init__.py ({len(missing_init)}):{RESET}")
        for m in missing_init:
            print(f"  {YELLOW}!{RESET} {m}")

    # Summary
    total_checks = len(results) + len(orphans) + len(empties) + len(missing_init)
    issues = len(mismatches) + len(orphans) + len(empties) + len(missing_init)
    print(f"\n{'=' * 60}")
    if issues == 0:
        print(f"{GREEN}{BOLD}All {total_checks} checks passed.{RESET}")
        return 0
    else:
        print(f"{RED}{BOLD}{issues} issue(s) found across {total_checks} checks.{RESET}")
        if mismatches:
            print(f"{DIM}Run with --update to auto-fix doc counts.{RESET}")
        return 1


def print_json(
    metrics: dict[str, Metric],
    results: list[SyncResult],
) -> int:
    """Print JSON report. Returns exit code."""
    mismatches = [r for r in results if not r.is_match]
    output = {
        "metrics": {
            k: {"value": v.actual, "description": v.description}
            for k, v in metrics.items()
        },
        "doc_checks": [
            {
                "metric": r.metric_name,
                "file": r.file,
                "line": r.line_number,
                "documented": r.documented,
                "actual": r.actual,
                "match": r.is_match,
            }
            for r in results
        ],
        "orphaned_pycache": check_orphaned_pycache(),
        "empty_dirs": check_empty_dirs(),
        "missing_init": check_init_files(),
        "clean": len(mismatches) == 0,
    }
    print(json.dumps(output, indent=2))
    return 0 if output["clean"] else 1


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="Sidera documentation sync validator")
    parser.add_argument(
        "--update", action="store_true",
        help="Auto-update doc files with actual counts",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show all checks, not just mismatches",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output as JSON",
    )
    parser.add_argument(
        "--skip-pytest", action="store_true",
        help="Use AST counting instead of pytest (faster, less accurate)",
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="Only check for mismatches, skip additional checks (for pre-commit)",
    )
    args = parser.parse_args()

    # Collect metrics
    metrics = collect_metrics(skip_pytest=args.skip_pytest)

    # Scan docs
    references = build_doc_references()
    doc_results = scan_doc_values(references)

    # Compare
    results = compare(metrics, doc_results, skip_pytest=args.skip_pytest)

    # Update mode
    if args.update:
        changed = update_docs(results)
        if changed:
            print(f"Updated: {', '.join(changed)}")
            # Re-scan after update to verify
            doc_results = scan_doc_values(references)
            results = compare(metrics, doc_results, skip_pytest=args.skip_pytest)
        else:
            print("No updates needed — docs are in sync.")

    # Report
    if args.json_output:
        return print_json(metrics, results)
    else:
        return print_report(metrics, results, verbose=args.verbose)


if __name__ == "__main__":
    # Ensure we can import from project root
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.exit(main())
