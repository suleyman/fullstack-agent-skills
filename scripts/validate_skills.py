#!/usr/bin/env python3
"""Validate every skill in skills/ against the Agent Skills spec and this repo's conventions.

Checks:
  - SKILL.md exists and starts with YAML frontmatter
  - `name`: 1-64 chars, lowercase letters/digits/hyphens, no leading/trailing/consecutive hyphens,
    matches the directory name
  - `description`: 1-1024 chars, no angle brackets
  - only portable frontmatter keys (name, description, license, compatibility, metadata, allowed-tools)
  - SKILL.md stays under 500 lines (progressive disclosure: move detail to references/)
  - relative links in every skill markdown file resolve to existing files
  - .claude-plugin/marketplace.json is valid JSON with the required fields

No third-party dependencies; runs on Python 3.9+.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ALLOWED_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
MAX_SKILL_LINES = 500
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def parse_frontmatter(text: str) -> tuple[dict[str, object], int]:
    """Parse the small YAML subset used in SKILL.md frontmatter: scalars and one level of mapping."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with '---' frontmatter")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("frontmatter is not closed with '---'") from None

    data: dict[str, object] = {}
    current_map: dict[str, str] | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith((" ", "\t")):
            if current_map is None:
                raise ValueError(f"unexpected indented line: {raw!r}")
            key, sep, value = raw.strip().partition(":")
            if not sep:
                raise ValueError(f"invalid mapping line: {raw!r}")
            current_map[key.strip()] = _unquote(value.strip())
            continue
        key, sep, value = raw.partition(":")
        if not sep:
            raise ValueError(f"invalid frontmatter line: {raw!r}")
        key, value = key.strip(), value.strip()
        if value in {">", "|", ">-", "|-"}:
            raise ValueError(f"'{key}': block scalars are not supported here; use a single-line value")
        if value == "":
            current_map = {}
            data[key] = current_map
        else:
            current_map = None
            data[key] = _unquote(value)
    return data, end + 1


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def check_links(md_file: Path, errors: list[str]) -> None:
    text = md_file.read_text(encoding="utf-8")
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)  # ignore links inside code blocks
    for target in LINK_RE.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#", 1)[0]
        if path and not (md_file.parent / path).exists():
            errors.append(f"{md_file.relative_to(ROOT)}: broken link -> {target}")


def validate_skill(skill_dir: Path, errors: list[str], warnings: list[str]) -> None:
    rel = skill_dir.relative_to(ROOT)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        errors.append(f"{rel}: missing SKILL.md")
        return

    text = skill_md.read_text(encoding="utf-8")
    try:
        meta, _ = parse_frontmatter(text)
    except ValueError as exc:
        errors.append(f"{rel}/SKILL.md: {exc}")
        return

    unknown = set(meta) - ALLOWED_KEYS
    if unknown:
        warnings.append(f"{rel}/SKILL.md: non-portable frontmatter keys {sorted(unknown)}")

    name = meta.get("name")
    if not isinstance(name, str) or not name:
        errors.append(f"{rel}/SKILL.md: 'name' is required")
    else:
        if len(name) > 64 or not NAME_RE.match(name):
            errors.append(f"{rel}/SKILL.md: invalid name {name!r} (lowercase, digits, single hyphens, <= 64 chars)")
        if name != skill_dir.name:
            errors.append(f"{rel}/SKILL.md: name {name!r} must match directory {skill_dir.name!r}")

    description = meta.get("description")
    if not isinstance(description, str) or not description:
        errors.append(f"{rel}/SKILL.md: 'description' is required")
    else:
        if len(description) > 1024:
            errors.append(f"{rel}/SKILL.md: description is {len(description)} chars (max 1024)")
        if "<" in description or ">" in description:
            errors.append(f"{rel}/SKILL.md: description must not contain angle brackets")

    if "metadata" in meta and not isinstance(meta["metadata"], dict):
        errors.append(f"{rel}/SKILL.md: 'metadata' must be a mapping")

    line_count = len(text.splitlines())
    if line_count > MAX_SKILL_LINES:
        errors.append(f"{rel}/SKILL.md: {line_count} lines (max {MAX_SKILL_LINES}); move detail into references/")

    for md_file in sorted(skill_dir.rglob("*.md")):
        check_links(md_file, errors)


def validate_marketplace(errors: list[str]) -> None:
    if not MARKETPLACE.is_file():
        return
    try:
        data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{MARKETPLACE.relative_to(ROOT)}: invalid JSON ({exc})")
        return
    for field in ("name", "owner", "plugins"):
        if field not in data:
            errors.append(f"{MARKETPLACE.relative_to(ROOT)}: missing '{field}'")
    for i, plugin in enumerate(data.get("plugins", [])):
        for field in ("name", "source"):
            if field not in plugin:
                errors.append(f"{MARKETPLACE.relative_to(ROOT)}: plugins[{i}] missing '{field}'")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    skill_dirs = sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())
    if not skill_dirs:
        errors.append("skills/: no skills found")
    for skill_dir in skill_dirs:
        validate_skill(skill_dir, errors, warnings)
    validate_marketplace(errors)

    for warning in warnings:
        print(f"warning: {warning}")
    for error in errors:
        print(f"error: {error}")
    if errors:
        print(f"\n{len(errors)} error(s) in {len(skill_dirs)} skill(s)")
        return 1
    print(f"OK: {len(skill_dirs)} skill(s) valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
