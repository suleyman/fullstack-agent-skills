# Contributing

These skills are only as useful as their rules are precise. This guide describes what a good rule looks like, how skills are structured, and how to check a change before you open a PR.

## What belongs here

- Rules you've seen matter in production: incidents, review comments you keep repeating, mistakes agents keep making.
- Corrections, when a rule is wrong, outdated, or too broad.
- Version updates, when a framework changes an API or a default.
- Missing debugging, security, or testing guidance for the existing stack.

Not a fit: generic advice that applies to any codebase, tutorials, tool comparisons without a decision, and new stacks outside the scope of the six skills (open an issue first if you think the scope should grow).

## The bar for a rule

Every rule must be:

1. **Concrete.** A reviewer, or the agent itself, can check compliance by reading the code.
2. **Justified.** It says why, in one clause. The reason is what lets an agent apply the rule correctly in situations the rule didn't anticipate.
3. **Scoped.** It says when it applies, and its exceptions if it has any.
4. **Current.** It is verified against current documentation. A version-specific rule names the version.

| Rejected | Accepted |
|---|---|
| Write clean, maintainable components. | Keep business logic out of React components. Frontend-only logic lives in pure functions in `features/*/lib/` with unit tests. |
| Optimize database performance. | Add indexes from actual query patterns (`pg_stat_statements`, `EXPLAIN`). Every index names the query or constraint it serves. |
| Handle errors properly. | Every error, including validation and unhandled exceptions, returns the same Problem Details body with a stable `code` and the `requestId`. |
| Be careful with migrations. | Never run migrations on application startup. Replicas race, and a failure crash-loops every instance. Run them as a release step. |
| Use secure storage on mobile. | Store refresh tokens only in `expo-secure-store` with `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY`. Never AsyncStorage, MMKV, or persisted Zustand. |

If two rules conflict across skills, the PR must resolve the conflict. The skills have to agree with each other: casing on the wire, the error format, token storage, pagination.

## Skill structure

```
skills/<skill-name>/
├── SKILL.md          # required: frontmatter + core instructions
└── references/       # optional: detailed material the agent reads on demand
```

### Frontmatter

```yaml
---
name: skill-name            # lowercase, digits, single hyphens; must match the directory
description: What the skill covers and when to use it, with the keywords that should trigger it.
license: MIT
metadata:
  author: <github-handle>
  version: "1.0.0"
---
```

- Use only portable fields (`name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`). Agent-specific fields break other agents.
- The `description` is what agents use to decide whether to load the skill. Name the technologies and the tasks ("Use when creating or reviewing...") in under 1024 characters. Write it in third person, without angle brackets.

### Body

Keep this section order, so readers and agents can navigate every skill the same way:

1. Purpose (opening paragraph) and **When to use**, including hand-offs to other skills
2. **Core rules**: the non-negotiables, first, because agents may keep only the beginning of a long skill in context
3. **Architecture**: layout and boundaries
4. **Preferred patterns**, with short code
5. **Patterns to avoid**, as a table with the alternative
6. **Debugging**
7. **Performance**
8. **Security**
9. **Testing**
10. **Production-readiness checklist**

### Size

- `SKILL.md` stays **under 500 lines**, and ideally around 5,000 tokens. The whole file loads when the skill activates.
- Move long code samples and deep dives into `references/<topic>.md`, linked from `SKILL.md` with a relative path. Keep references one level deep: a reference doesn't send the agent to another reference.
- Skills may be installed individually, so link to other skills **by name** ("see the `postgresql` skill"), never by relative path.

## Checking a change

```bash
# Frontmatter, naming, size, links, marketplace manifest
python3 scripts/validate_skills.py

# Optional: the reference validator from the Agent Skills project
# https://github.com/agentskills/agentskills/tree/main/skills-ref
skills-ref validate skills/<skill-name>

# Optional: the Claude Code plugin manifest
claude plugin validate .
```

Then test behavior, not only syntax:

1. Install your branch locally, for example by copying `skills/<skill-name>` into `~/.claude/skills/` or your agent's skills directory.
2. Run prompts that exercise the rule you changed. The example prompts in the README are a good start.
3. Compare the agent's output with and without the change. Mention the prompts you used, and what changed, in the PR description.
4. If the change affects what good output looks like, update `examples/` in the same PR.

## Pull requests

- One topic per PR. A rule change and an unrelated wording cleanup are two PRs.
- Explain the production context: what went wrong, or what kept being corrected in review.
- Link the documentation that confirms version-specific claims.
- Bump `metadata.version` in the skill's frontmatter for behavior changes: minor for new or changed rules, patch for corrections and wording.

## Reporting problems

Open an issue with the prompt you used, what the agent did, and what it should have done. Transcripts showing a skill steering an agent wrong are the most useful bug reports.
