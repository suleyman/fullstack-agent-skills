## What changed

<!-- Which skill(s), which rules. One topic per PR. -->

## Why

<!-- The production context: an incident, a repeated review comment, an agent mistake, a framework change. Link docs for version-specific claims. -->

## How it was tested

<!-- Prompts you ran, and how the agent's output differed before and after the change. -->

## Checklist

- [ ] Each new or changed rule is concrete, justified, scoped, and current (see CONTRIBUTING.md)
- [ ] No conflict with rules in other skills (wire casing, error format, token storage, pagination)
- [ ] `python3 scripts/validate_skills.py` passes
- [ ] `SKILL.md` stays under 500 lines, and long material is in `references/`
- [ ] `metadata.version` bumped for behavior changes
- [ ] `examples/` updated if the change affects what good output looks like
