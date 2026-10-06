# Examples

Reference output for the skills in this repository. Use them to see what the skills produce, to calibrate your own prompts, and as a baseline when you change a skill. The product, "Acme Events", is fictional.

| Example | Skill(s) | What it shows |
|---|---|---|
| [`event-ticketing-platform/ARCHITECTURE.md`](event-ticketing-platform/ARCHITECTURE.md) | `fullstack-architect` | A full architecture proposal for *"Build an event ticketing platform with a Next.js organizer dashboard, FastAPI API, PostgreSQL database, and Expo attendee app"*: stack decisions (including choosing **not** to use PostGIS), data model, API surface, auth, payments, offline check-in, deployment, risks, and delivery slices |
| [`ticket-reservations/`](ticket-reservations) | All five layer skills | One feature slice of that platform across Alembic migrations, SQLAlchemy, FastAPI, tests, Expo checkout and order screens, and the Next.js organizer orders page, with a table mapping each file to the rules it demonstrates |

## Using examples when changing a skill

When you change a rule, check whether these examples still comply. If the rule changes what good output looks like, update the example in the same PR. Reviewers use the examples to judge whether a rule change produces better results.
