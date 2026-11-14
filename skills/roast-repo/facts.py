#!/usr/bin/env python3
"""Gather hard facts about a repository before the roast panel sits down.

Every judge on the panel gets this file, so every jab can point at something
real: file counts, the largest files, TODO/FIXME locations, test and CI
presence, dependency manifests, git history stats and secret-looking strings
(always redacted). Standard library only, no network, read-only.

Usage:
    python3 facts.py [REPO] [--format json|md] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", ".venv", "venv", "env",
    "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "target", ".next", ".nuxt", ".svelte-kit", "coverage", ".idea", ".vscode",
    ".gradle", "bower_components", "roast",
}
DEPENDENCY_DIRS = {"node_modules", "vendor", "bower_components", ".venv", "venv"}

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go",
    ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".rb": "Ruby", ".php": "PHP",
    ".cs": "C#", ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".swift": "Swift", ".m": "Objective-C", ".scala": "Scala", ".sh": "Shell",
    ".bash": "Shell", ".ps1": "PowerShell", ".lua": "Lua", ".dart": "Dart",
    ".ex": "Elixir", ".exs": "Elixir", ".vue": "Vue", ".svelte": "Svelte",
    ".sql": "SQL", ".r": "R", ".jl": "Julia", ".zig": "Zig", ".clj": "Clojure",
    ".hs": "Haskell", ".erl": "Erlang", ".fs": "F#", ".pl": "Perl",
}
TEXT_EXTRA = {
    ".md", ".rst", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".env", ".xml", ".html", ".css", ".scss", ".gradle", ".properties", ".tf",
    ".dockerfile", ".conf",
}

TEST_NAME = re.compile(
    r"(^test_.*\.py$|_test\.py$|_test\.go$|\.(test|spec)\.[cm]?[jt]sx?$|"
    r"Tests?\.(java|kt|cs|swift)$|_spec\.rb$|_test\.exs?$|test\.php$|"
    r"^(test|tests|spec)\.(py|[cm]?[jt]sx?)$)"
)
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "testing"}

# A marker counts only when it opens a comment, so prose or a regex that mentions one does not.
MARKER = re.compile(r"(?:#|//|/\*|<!--|^\s*\*|^\s*--)[ \t]*(TODO|FIXME|HACK|XXX)\b")

SECRET_PATTERNS = [
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("stripe-live-key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{16,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("hardcoded-credential", re.compile(
        r"""(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|auth[_-]?token|"""
        r"""passw(?:or)?d|client[_-]?secret)\b["']?\s*[:=]\s*["']([^"'\s]{8,})["']""")),
]
PLACEHOLDER = re.compile(
    r"(?i)(example|sample|your[_-]?|<|\$\{|\{\{|xxx|dummy|placeholder|changeme|"
    r"redacted|\*\*\*|fake|test|todo|replace)"
)
SENSITIVE_FILE = re.compile(r"(^\.env(\..+)?$|\.pem$|\.key$|\.p12$|\.pfx$|^id_(rsa|dsa|ecdsa|ed25519)$)")
SAFE_ENV_SUFFIX = (".example", ".sample", ".template", ".dist")

CI_FILES = [
    ".gitlab-ci.yml", ".travis.yml", "azure-pipelines.yml", "Jenkinsfile",
    "bitbucket-pipelines.yml", ".drone.yml", "appveyor.yml",
]
LOCKFILES = [
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "poetry.lock",
    "Pipfile.lock", "uv.lock", "Cargo.lock", "go.sum", "Gemfile.lock",
    "composer.lock", "pdm.lock",
]
LAZY_MESSAGE = re.compile(r"(?i)^(wip|fix|fixes|fixed|update|updates|changes|stuff|asdf|test|tmp|minor|misc|\.+)$")

MAX_READ_BYTES = 2_000_000


def run_git(repo: Path, *args: str) -> str | None:
    """Run a read-only git command; return stdout or None if git is unavailable."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def list_files(repo: Path) -> tuple[list[str], int, bool]:
    """Return (relative posix paths, tracked dependency files skipped, is_git)."""
    top = run_git(repo, "rev-parse", "--show-toplevel")
    out = run_git(repo, "ls-files", "-z") if top is not None else None
    skipped_deps = 0
    files: list[str] = []
    if out is not None:
        for rel in out.split("\0"):
            if not rel:
                continue
            parts = rel.split("/")
            if any(p in DEPENDENCY_DIRS for p in parts[:-1]):
                skipped_deps += 1
                continue
            if any(p in SKIP_DIRS for p in parts[:-1]):
                continue
            if (repo / rel).is_file():
                files.append(rel)
        return sorted(files), skipped_deps, True
    for path in repo.rglob("*"):
        rel_parts = path.relative_to(repo).parts
        if any(p in SKIP_DIRS for p in rel_parts[:-1]) or not path.is_file():
            continue
        if path.name in SKIP_DIRS:
            continue
        files.append("/".join(rel_parts))
    return sorted(files), 0, False


def read_text(path: Path) -> str | None:
    """Read a file as text; None for binaries and very large files."""
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def is_test_file(rel: str) -> bool:
    parts = rel.split("/")
    if any(p.lower() in TEST_DIRS for p in parts[:-1]):
        return True
    return bool(TEST_NAME.search(parts[-1]))


def redact(line: str, start: int, end: int) -> str:
    """Replace the matched secret with a short redacted stub."""
    secret = line[start:end]
    stub = secret[:4] + "...[redacted]"
    preview = (line[:start] + stub + line[end:]).strip()
    return preview[:160]


def scan_secrets(rel: str, text: str) -> list[dict]:
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line_hit = False
        for kind, pattern in SECRET_PATTERNS:
            for m in pattern.finditer(line):
                if kind == "hardcoded-credential":
                    if line_hit:  # a specific pattern already flagged this line
                        continue
                    value = m.group(1)
                    if PLACEHOLDER.search(value):
                        continue
                    start, end = m.start(1), m.end(1)
                else:
                    start, end = m.start(), m.end()
                hits.append({"kind": kind, "location": f"{rel}:{lineno}",
                             "preview": redact(line, start, end)})
                line_hit = True
    return hits


def parse_requirements(text: str) -> dict:
    deps = unpinned = 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-r", "-c", "-e", "--")):
            continue
        deps += 1
        if "==" not in line and "@" not in line and "===" not in line:
            unpinned += 1
    return {"dependencies": deps, "unpinned": unpinned}


def parse_package_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        return {"error": "package.json is not valid JSON"}
    scripts = data.get("scripts") or {}
    test_script = scripts.get("test", "")
    return {
        "dependencies": len(data.get("dependencies") or {}),
        "devDependencies": len(data.get("devDependencies") or {}),
        "scripts": sorted(scripts),
        "has_real_test_script": bool(test_script) and "no test specified" not in test_script,
        "engines": data.get("engines") or {},
    }


def parse_go_mod(text: str) -> dict:
    count, in_block = 0, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("require ("):
            in_block = True
        elif in_block and s == ")":
            in_block = False
        elif in_block and s and not s.startswith("//"):
            count += 1
        elif s.startswith("require ") and "(" not in s:
            count += 1
    return {"dependencies": count}


def dependency_facts(repo: Path, files: list[str]) -> dict:
    result: dict = {"manifests": [], "lockfiles": sorted(f for f in files if f.rsplit("/", 1)[-1] in LOCKFILES)}
    for rel in files:
        base = rel.rsplit("/", 1)[-1]
        if rel.count("/") > 2:
            continue
        text = None
        if base == "package.json":
            text = read_text(repo / rel)
            result["manifests"].append({"path": rel, "type": "npm", **parse_package_json(text or "")})
        elif re.match(r"requirements.*\.txt$", base):
            text = read_text(repo / rel)
            result["manifests"].append({"path": rel, "type": "pip", **parse_requirements(text or "")})
        elif base == "go.mod":
            text = read_text(repo / rel)
            result["manifests"].append({"path": rel, "type": "go", **parse_go_mod(text or "")})
        elif base in {"pyproject.toml", "Cargo.toml", "Gemfile", "composer.json", "pom.xml",
                      "build.gradle", "build.gradle.kts", "Pipfile", "setup.py", "mix.exs"}:
            result["manifests"].append({"path": rel, "type": base})
    result["has_lockfile"] = bool(result["lockfiles"])
    result["manifest_without_lockfile"] = bool(result["manifests"]) and not result["lockfiles"] and \
        not any(m["type"] == "pip" and m.get("unpinned") == 0 for m in result["manifests"])
    return result


def git_facts(repo: Path) -> dict | None:
    if run_git(repo, "rev-parse", "--show-toplevel") is None:
        return None
    if run_git(repo, "rev-list", "--count", "HEAD") is None:
        return {"commits": 0, "note": "repository has no commits yet"}
    count = run_git(repo, "rev-list", "--count", "HEAD", "--", ".")
    facts: dict = {"commits": int((count or "0").strip() or 0)}
    shortlog = run_git(repo, "shortlog", "-sn", "HEAD", "--", ".") or ""
    facts["contributors"] = len([ln for ln in shortlog.splitlines() if ln.strip()])
    first_dates = (run_git(repo, "log", "--reverse", "--format=%cs", "HEAD", "--", ".") or "").splitlines()
    if first_dates:
        facts["first_commit"] = first_dates[0].strip()
    facts["last_commit"] = (run_git(repo, "log", "-1", "--format=%cs", "HEAD", "--", ".") or "").strip()
    recent = run_git(repo, "rev-list", "--count", "--since=90.days", "HEAD", "--", ".")
    facts["commits_last_90_days"] = int((recent or "0").strip() or 0)
    subjects = (run_git(repo, "log", "-n", "200", "--format=%s", "HEAD", "--", ".") or "").splitlines()
    lazy = [s for s in subjects if LAZY_MESSAGE.match(s.strip()) or len(s.strip()) < 4]
    facts["lazy_commit_messages"] = {"count": len(lazy), "of_last": len(subjects),
                                     "examples": sorted(set(lazy))[:5]}
    churn = Counter(
        f for f in (run_git(repo, "log", "-n", "500", "--format=", "--name-only", "--relative", "HEAD", "--", ".") or "").splitlines() if f
    )
    facts["churn_hotspots"] = [{"path": p, "changes": n} for p, n in churn.most_common(10)]
    status = run_git(repo, "status", "--porcelain", "--", ".") or ""
    facts["uncommitted_changes"] = len([ln for ln in status.splitlines() if ln.strip()])
    return facts


def gather(repo: Path) -> dict:
    repo = repo.resolve()
    files, skipped_deps, is_git = list_files(repo)
    languages: Counter = Counter()
    source_lines: dict[str, int] = {}
    total_lines = 0
    markers: Counter = Counter()
    marker_locations: list[str] = []
    secrets: list[dict] = []
    sensitive_files: list[str] = []
    test_files: list[str] = []
    binary_files = 0

    for rel in files:
        base = rel.rsplit("/", 1)[-1]
        ext = ("." + base.rsplit(".", 1)[-1].lower()) if "." in base else ""
        if SENSITIVE_FILE.search(base) and not base.endswith(SAFE_ENV_SUFFIX):
            sensitive_files.append(rel)
        is_source = ext in LANGUAGES
        if not is_source and ext not in TEXT_EXTRA and base not in {"Dockerfile", "Makefile", "Jenkinsfile"} \
                and not base.startswith(".env"):
            continue
        text = read_text(repo / rel)
        if text is None:
            binary_files += 1
            continue
        lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        total_lines += lines
        secrets.extend(scan_secrets(rel, text))
        if is_source:
            languages[LANGUAGES[ext]] += 1
            source_lines[rel] = lines
            if is_test_file(rel):
                test_files.append(rel)
            for lineno, line in enumerate(text.splitlines(), 1):
                for m in MARKER.finditer(line):
                    markers[m.group(1)] += 1
                    marker_locations.append(f"{rel}:{lineno}")

    names_lower = {f.lower(): f for f in files}

    def find_doc(*candidates: str) -> str | None:
        for cand in candidates:
            if cand.lower() in names_lower:
                return names_lower[cand.lower()]
        return None

    readme = find_doc("README.md", "README.rst", "README.txt", "README", "readme.markdown")
    readme_lines = None
    if readme:
        text = read_text(repo / readme) or ""
        readme_lines = len(text.splitlines())

    workflows = [f for f in files if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))]
    ci_files = workflows + [f for f in files if f in CI_FILES or f.startswith(".circleci/")]
    ci_runs_tests = False
    for f in ci_files:
        text = (read_text(repo / f) or "").lower()
        if re.search(r"\b(test|pytest|jest|vitest|mocha|unittest|go test|cargo test|rspec)\b", text):
            ci_runs_tests = True
        # Travis runs a language's default test command (e.g. `npm test`) when no script: is given.
        elif (f == ".travis.yml" and re.search(r"^language:\s*(node_js|python|ruby|go|rust|java|scala|clojure|groovy|php|perl|elixir|dart)\b",
                                                text, re.M)
              and not re.search(r"^script:", text, re.M)):
            ci_runs_tests = True

    non_test_source = [f for f in source_lines if f not in set(test_files)]
    largest = sorted(source_lines.items(), key=lambda kv: (-kv[1], kv[0]))[:10]

    facts = {
        "schema": 1,
        "repo": repo.name,
        "is_git_repo": is_git,
        "files": {
            "total": len(files),
            "source": len(source_lines),
            "binary_or_huge": binary_files,
            "total_text_lines": total_lines,
            "tracked_dependency_files": skipped_deps,
        },
        "languages": dict(languages.most_common()),
        "largest_source_files": [{"path": p, "lines": n} for p, n in largest],
        "docs": {
            "readme": readme,
            "readme_lines": readme_lines,
            "license": find_doc("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"),
            "contributing": find_doc("CONTRIBUTING.md", ".github/CONTRIBUTING.md", "docs/CONTRIBUTING.md"),
            "changelog": find_doc("CHANGELOG.md", "CHANGES.md", "HISTORY.md", "CHANGELOG"),
            "security_policy": find_doc("SECURITY.md", ".github/SECURITY.md"),
            "env_example": find_doc(".env.example", ".env.sample", ".env.template"),
        },
        "tests": {
            "test_files": len(test_files),
            "source_files_excluding_tests": len(non_test_source),
            "ratio": round(len(test_files) / len(non_test_source), 2) if non_test_source else None,
            "examples": test_files[:5],
        },
        "ci": {"files": ci_files, "runs_tests": ci_runs_tests},
        "tooling": {
            "gitignore": ".gitignore" in files,
            "dockerfile": find_doc("Dockerfile"),
            "makefile": find_doc("Makefile"),
            "editorconfig": ".editorconfig" in files,
            "linter_configs": sorted(f for f in files if re.search(
                r"(^|/)(\.eslintrc[^/]*|eslint\.config\.[cm]?js|\.flake8|ruff\.toml|\.ruff\.toml|"
                r"\.pylintrc|\.prettierrc[^/]*|biome\.json|\.golangci\.ya?ml|rustfmt\.toml|"
                r"\.rubocop\.yml|\.pre-commit-config\.yaml)$", f)),
        },
        "dependencies": dependency_facts(repo, files),
        "markers": {"counts": dict(markers), "total": sum(markers.values()),
                    "locations": marker_locations[:25]},
        "secrets": {"suspects": secrets[:30], "total": len(secrets),
                    "sensitive_files_tracked": sensitive_files},
        "git": git_facts(repo) if is_git else None,
    }
    return facts


def to_markdown(f: dict) -> str:
    out = [f"# Facts for {f['repo']}", ""]
    files = f["files"]
    out.append(f"- Files: {files['total']} tracked, {files['source']} source, "
               f"{files['total_text_lines']} text lines")
    if f["languages"]:
        out.append("- Languages: " + ", ".join(f"{k} ({v})" for k, v in f["languages"].items()))
    t = f["tests"]
    out.append(f"- Tests: {t['test_files']} test files for {t['source_files_excluding_tests']} source files"
               + (f" (ratio {t['ratio']})" if t["ratio"] is not None else ""))
    ci = f["ci"]
    out.append(f"- CI: {', '.join(ci['files']) if ci['files'] else 'none found'}"
               + (" (runs tests)" if ci["runs_tests"] else ""))
    d = f["docs"]
    out.append(f"- README: {d['readme'] or 'MISSING'}" + (f" ({d['readme_lines']} lines)" if d["readme"] else ""))
    out.append(f"- LICENSE: {d['license'] or 'MISSING'}; CONTRIBUTING: {d['contributing'] or 'missing'}; "
               f"CHANGELOG: {d['changelog'] or 'missing'}")
    m = f["markers"]
    out.append(f"- TODO/FIXME/HACK/XXX: {m['total']} " + (str(m["counts"]) if m["counts"] else ""))
    s = f["secrets"]
    out.append(f"- Secret-looking strings: {s['total']}; sensitive files tracked: "
               f"{', '.join(s['sensitive_files_tracked']) or 'none'}")
    deps = f["dependencies"]
    out.append(f"- Manifests: {', '.join(m['path'] for m in deps['manifests']) or 'none'}; "
               f"lockfiles: {', '.join(deps['lockfiles']) or 'none'}")
    if f["largest_source_files"]:
        out.append("- Largest source files: " + ", ".join(
            f"{x['path']} ({x['lines']})" for x in f["largest_source_files"][:5]))
    g = f.get("git")
    if g:
        out.append(f"- Git: {g.get('commits', 0)} commits, {g.get('contributors', 0)} contributors, "
                   f"{g.get('commits_last_90_days', 0)} in the last 90 days, last commit {g.get('last_commit', '?')}")
        lazy = g.get("lazy_commit_messages")
        if lazy:
            out.append(f"- Low-effort commit messages: {lazy['count']} of the last {lazy['of_last']}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gather hard facts about a repo for the roast panel (read-only).")
    parser.add_argument("repo", nargs="?", default=".", help="path to the repository (default: current dir)")
    parser.add_argument("--format", choices=["json", "md"], default="json", help="output format")
    parser.add_argument("--out", help="write to this file instead of stdout")
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return 2
    facts = gather(repo)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    text = json.dumps(facts, indent=2) + "\n" if args.format == "json" else to_markdown(facts)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
