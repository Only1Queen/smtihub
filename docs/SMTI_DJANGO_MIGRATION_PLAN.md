# SMTI HUB — Django Migration Plan

**SMTI HUB** — the Security Monitoring and Threat Intelligence Hub. Named a hub rather than
an appraisal tracker because appraisal is the first module, not the whole product. Later
modules live under the same shell, sidebar, account model and audit log. Nothing here
assumes appraisal is the only thing in the Hub — and nothing is built for future modules
either, until one is actually specified.

## Goal

The Security Monitoring & Threat Intelligence manager runs her team's yearly appraisal
(May 2026 – April 2027) and the work that feeds it. She defines the goals, assigns them to
analysts, assigns tasks against them, approves what the team submits, and ends the year
with a per-analyst score she can defend in a review conversation.

Today that runs on an Electron app (`appraisal-tracker/`) with data in each PC's
localStorage and a shared manager PIN. The problems that actually hurt:

- Scores live on one laptop. No backup, no second device, nothing survives a reimage.
- Analysts cannot see their own scores without the manager's machine.
- A PIN is not an identity — the log cannot say who changed a score, or when.
- The goal structure is hardcoded. Changing a KPI means an app rebuild.
- There is no link between the work an analyst did and the mark they were given.

The rebuild fixes those five things. Everything else is secondary.

## Scope

**In scope (v1):**

- One team, one manager, one appraisal year at a time.
- Manager-created goals: title, description, KPIs with marks, assigned to chosen analysts.
- Monthly scoring, manual or derived from approved tasks.
- Tasks assigned to analysts, optionally linked to a KPI with a weight.
- Analyst-submitted task updates that the manager approves or sends back.
- Each analyst sees their own goals, tasks, scores and totals.
- Every score, goal, task and roster change recorded permanently.
- PostgreSQL, backed up, with a verified restore.

**Explicitly not in v1:** self-assessment of scores, calibration workflows, multiple teams
or departments, multiple organizations, 360 feedback, HR integration, mobile app. If one
becomes a real request, it gets planned then.

**Deferred until a stated need exists:** LDAP/LDAPS (Phase 4), MFA, Celery/Redis,
asynchronous exports, PDF generation. Each is listed with the trigger that would justify it.

The Electron app stays as the reference for scoring behaviour. There is no data migration —
the new system starts clean with the SMTI goals seeded as ordinary, editable rows.

## Decisions

- **Users:** the manager plus her direct reports. Single team, no department hierarchy.
- **Authentication:** Django's built-in accounts, created by the manager. LDAPS in Phase 4,
  once the directory schema question below is answered.
- **Roles:** two, via Django groups. *Manager* (staff) creates goals and tasks, scores,
  approves updates, manages accounts. *Analyst* sees their own record and submits task
  updates. A read-only *Auditor* group is added only if someone asks for one.
- **Goals:** created and edited by the manager in the app. The SMTI five ship as seed data
  for the first year, then behave like any other goal. Marks need not total 100.
- **Assignment:** a goal applies only to the analysts assigned to it. An unassigned goal is
  excluded from that analyst's scoring entirely — never counted as a zero.
- **Tasks:** assigned by the manager. Linking a task to a KPI with a weight makes it count
  toward that goal; an unlinked task is operational work that carries no marks.
- **Approval:** nothing self-reported counts. An analyst submits an update; the task only
  changes state when the manager approves it. Both actions audited.
- **Scoring:** two modes per KPI — `manual` (the manager types the mark) or `from_tasks`
  (the mark is computed from approved task weight). Pinned by golden test vectors.
- **Audit:** every score, goal, task, approval and account change appends to one table,
  never updated or deleted. The database role has no `UPDATE`/`DELETE` on it.
- **Email:** Django `send_mail` through the org SMTP relay, sent inline. Reminders from a
  cron'd management command.
- **UI:** Django templates with HTMX for autosave, approvals and inline validation. One
  Chart.js file for the trend view. A committed Tailwind build — no node stage in the
  runtime image. Full-width layout; wide screens get more columns, not longer lines.
- **Deployment:** internal Docker — Django, PostgreSQL, reverse proxy. Three services.

## Domain Model

Nine models.

- `Employee` — one-to-one with `auth.User`; adds `manager` (self-FK, nullable) and
  `active`. The manager relationship is the only access rule the app needs.
- `AppraisalYear` — label, start month, `closed` flag. One row per year.
- `Goal` — belongs to a year. Title, description, order.
- `Kpi` — belongs to a goal. `code` (A1, B2…), `text`, `max_marks`, `quarterly` (bool),
  and `scoring_mode` = `manual` | `from_tasks`.
- `GoalAssignment` — goal × employee, unique together. Which analysts a goal applies to.
- `Score` — `(employee, kpi, month_index)` unique together, plus `value`, `scored_by`,
  `updated_at`, optional `comment`. The year is reached through `kpi.goal.year`, so a
  score can never disagree with its own year.
- `Task` — `title`, `description`, `assignee`, `created_by`, `due_date`, `status`,
  **`scoring_month`** (explicit integer 0–11), and optional `kpi` + `weight`.
- `TaskUpdate` — `task`, `author`, `note`, `proposed_status`, `submitted_at`, `decision`
  (`pending` | `approved` | `returned`), `decided_by`, `decided_at`, `decision_note`.
- `AuditEvent` — `timestamp`, `actor`, `action`, `target`, JSON `detail` with before/after
  values. Append-only.

Notes on why this is the right size:

- No `Organization`/`Department`/`Team`: there is one team, and `Employee.manager` already
  expresses it. Add them when a second team exists — a migration, not a redesign.
- No `RoleAssignment`: two Django groups plus `employee.manager_id == request.user.id`
  covers every access decision.
- No `ScorePeriod`: the month is an integer 0–11, and quarterly eligibility is a property
  of the KPI. Quarter-end months are indices 1, 4, 7, 10 (Jun-26, Sep-26, Dec-26, Mar-27).
- No template-version chain: goals and KPIs hang off the year directly, so a new year's
  goal set cannot alter a previous year's numbers.
- No workflow engine: `TaskUpdate` is an append-only submission/decision record, not a
  general state machine. Two verbs — submit, decide.

## Goals, Tasks and Scoring

This is the part with real rules in it. Each exists because getting it wrong produces an
appraisal number that cannot be defended.

### Eligibility

A KPI counts for an analyst in a month when **both** hold: the KPI's goal is assigned to
that analyst, and the KPI is non-quarterly or the month is a quarter end. Different
analysts therefore have different denominators — this is intended, not a bug.

### Task-derived scores

For a KPI in `from_tasks` mode:

```
value = kpi.max_marks × (approved task weight ÷ total task weight)
        scoped to (assignee, kpi, scoring_month)
```

Four constraints, each fixing a specific way this goes wrong:

1. **Scoped per analyst.** The denominator is that analyst's own tasks for that KPI and
   month. Without this, one analyst's tasks move another's score.
2. **`scoring_month` is explicit**, set when the task is created and defaulted from the due
   date. A task's month never shifts because a due date moved, and "which month does this
   belong to" has exactly one answer.
3. **Only approved tasks count.** Submitted is not done.
4. **The value freezes at month close.** While a month is open the KPI shows a live figure
   computed from approved tasks. When the manager marks the month scored, that number is
   written into `Score` and stops moving. Later task edits, additions or reassignments
   cannot touch a scored month. Correcting it afterwards is an audited revision, exactly
   like correcting a typed score.

Constraint 4 is the one that matters most: without it, adding a task on the 28th silently
lowers a score already earned, because the denominator grew.

Weights need not sum to 100. The score is the proportion of that analyst's own weight
completed, so a single 100%-weight task and three 30/30/40 tasks both behave sensibly.

**A `from_tasks` KPI with no tasks in a month leaves the month incomplete** — surfaced in
the UI as "needs tasks or a manual mark". It is never silently dropped from the average,
which would let a KPI quietly stop counting.

### Unlinked tasks

A task with no `kpi` carries no weight and touches no score. It exists so the manager gets
daily visibility of operational work and projects — shift handovers, migrations, vendor
calls. It still goes through submit-and-approve, so the team's day-to-day status is
current without any of it leaking into an appraisal number.

### Validation

- A task may only link to a KPI whose goal is assigned to that task's assignee.
- `weight` is 1–100, required when `kpi` is set, forbidden when it is not.
- `scoring_month` must be within the KPI's year.
- Task status changes only through an approved `TaskUpdate` — never by direct write.

## Scoring Rules

Ported from `appraisal-tracker/src/App.jsx:70-113`. Before writing the Django
implementation, extract golden vectors from the prototype and make them the test fixture.
That is the only way "matches the prototype" is a checkable claim.

- A month's maximum is the sum of `max_marks` for every KPI eligible for that analyst that
  month.
- A month's percentage is `sum(scores) / month maximum × 100`.
- The annual score is the unweighted mean of the percentages of **complete** months.

**Partial months — decided.** The prototype counted a month if *any* KPI was scored while
still dividing by the full maximum, so a half-filled month scored near zero and dragged the
annual mean down. **A month now counts only when every eligible KPI has a value**; until
then the UI shows progress ("6 of 16 scored") rather than a misleading percentage. This is
implemented in the interface preview and is the rule the golden vectors encode.

## Immutability

The system is meant to keep growing, so it matters exactly what is frozen and when.

**Locked once a KPI has scores:** `max_marks`, `quarterly`, and `scoring_mode`. All three
retroactively change the meaning of scores already recorded. Wording, description and
assignment stay editable.

**Locked once a month is scored:** that month's `Score` values and the task-derived
contributions behind them. Adding, editing, reassigning or re-approving a task cannot
change a scored month.

**Locked when the year closes:** everything in it — scores, goals, KPIs, assignments, and
the tasks that fed them.

A closed year can be **reopened** by the manager for a late correction. Reopening and
re-closing are audited and require a reason. Freezing exists to stop history changing
silently, not to stop it changing at all.

**Concurrent edits.** Score saves send the `updated_at` they loaded with; a mismatch
returns a conflict and re-renders the current value. The grid autosaves and may be open in
two tabs.

## Year Rollover

Goals, KPIs, assignments and scores all belong to an appraisal year, so a new year is data
entry, not a code change. `manage.py new_appraisal_year`:

1. Create the `AppraisalYear` row.
2. Copy the previous year's goals, KPIs and assignments, or load a revised set from a
   YAML/JSON file.
3. Print the resulting structure and total marks for the manager to confirm.

Because goals are year-scoped, a reworded KPI or a changed maximum next year cannot alter a
closed year's numbers. That is what a versioning engine would have bought, obtained instead
from one foreign key.

The roster carries over untouched — `Employee` is not year-scoped, so an analyst
accumulates one set of scores per year and the views offer a year selector. Someone who
leaves is deactivated, not deleted, and their history stays.

Rollover is a Phase 3 task: the first year is seeded by migration, and the command is only
needed before May 2027.

## Code Layout

One Django app, not seven. Splitting nine models across `accounts`/`organization`/
`appraisals`/`tasks`/`reports`/`audit` means every feature touches four apps and every
import is cross-app — the boundaries cost more than they organize at this size.

```
smtiapp/
├── manage.py
├── docker-compose.yml
├── Dockerfile
├── config/
│   ├── settings.py          env-driven; deploy-safe defaults live here
│   └── urls.py
└── hub/
    ├── models.py            the nine models + their validation rules
    ├── scoring.py           pure functions: eligibility, month %, annual %, task rollup
    ├── audit.py             log_event() — the one way audit rows are written
    ├── permissions.py       who may read/write whose scores, goals and tasks
    ├── views.py             the screens
    ├── forms.py             goal, task, and score entry + validation
    ├── urls.py
    ├── admin.py             roster and account management
    ├── migrations/
    │   └── 0002_seed_2026.py    the SMTI goals/KPIs as ordinary editable rows
    ├── management/commands/
    │   ├── new_appraisal_year.py
    │   └── send_reminders.py
    ├── templates/hub/
    │   ├── base.html        sidebar shell
    │   ├── team.html  goals.html  tasks.html  score_entry.html
    │   └── my_appraisal.html  my_tasks.html  year_summary.html  activity.html
    └── tests/
        ├── test_scoring.py      golden vectors + task rollup
        ├── test_tasks.py        approval flow, freeze rules
        ├── test_permissions.py
        └── test_audit.py
```

`scoring.py` imports nothing from Django. Eligibility, month percentages, the annual mean
and the task rollup are plain functions over plain numbers, so the golden vectors test them
directly with no database and no fixtures. That constraint is what keeps the rules that
actually matter cheap to verify.

### Where a change goes

| Change | Files to touch |
|---|---|
| Fix or adjust the scoring maths | `scoring.py` + `tests/test_scoring.py` (vectors first) |
| Change how task weight rolls into a KPI | `scoring.py` only |
| Add a field to a score or task | `models.py`, a migration, `forms.py`, one template |
| New report or screen | `views.py`, `urls.py`, one template |
| Change what a role may see | `permissions.py` — nothing else should decide access |
| New audited action | call `audit.log_event()` at the write site |
| This year's goals, KPIs, assignments | no code — the Goals screen |
| Next year's goal set | no code — the rollover file |
| Change the seeded 2026 data | a new migration, never by editing `0002_seed_2026.py` |
| New email or reminder | `management/commands/`, wired to cron |
| Second team / manager | `permissions.py` + `Employee.manager` queries; models unchanged |
| LDAP settings, TLS, cookies, secrets | `config/settings.py` only |

Two rules keep that table true: **all access decisions live in `permissions.py`**, and
**all audit writes go through `audit.log_event()`**. When either gets duplicated inline in
a view, the next security or compliance question stops having a single answer.

## What Can Be Changed, and by Whom

**Editable by the manager, no developer:**

- Goals — title, description, KPIs, marks, quarterly flag, scoring mode, assignment.
- Tasks — create, assign, link to a KPI, set weight and scoring month, approve, send back.
- Scores and comments, for any open month.
- The team roster — add, edit, deactivate, reactivate.
- Year label, start month, open/closed state; next year's goal set via the rollover file.
- Their own theme (light / dark / system) and reminder preferences.

**Needs a developer, but is a small change:** new screens and reports, extra fields on a
score or task (evidence link, mid-year note), a second team or manager, new exports. These
are additive because the model is nine plain tables with no configuration layer to unpick.

**Deliberately locked:** see *Immutability* above. Corrections go through reopening, which
is audited and requires a reason — the door exists, it just leaves a record.

The guiding rule: **make the data easy to change and the history hard to change.**

## Screens

Full width throughout; wide viewports get additional columns rather than longer lines.
Dense grids scroll inside their own container with a sticky first column, never sideways
on the page body.

1. **Sign in** — Django login.
2. **Team** (manager) — analysts with year-to-date percentage, months complete, current
   month progress. Add, edit, deactivate, reactivate.
3. **Goals** (manager) — create and edit goals: title, description, KPIs with marks,
   quarterly flag, `manual`/`from_tasks`, and per-analyst assignment. Shows what is locked
   and why once scores exist.
4. **Tasks** (manager) — pending approvals first, then all tasks for the month. Create and
   assign, link to a KPI with a weight, approve or send back with a reason. The weight
   field states its own effect: how much weight that analyst already has on the KPI, and
   what adding this one does to it.
5. **Score entry** (manager) — one analyst, one month. `manual` KPIs take a number;
   `from_tasks` KPIs show the computed value and the tasks behind it. Inline max
   validation, HTMX autosave with a saved/conflict indicator.
6. **My appraisal** (analyst) — own scores by month, expandable goal drill-down showing
   every KPI month by month and the tasks behind task-derived marks, annual figure, trend
   chart. Read-only, with a year selector.
7. **My tasks** (analyst) — own tasks split open/approved. Submit an update with a note;
   see returned items and the reason.
8. **Year summary** (manager) — team × month grid with CSV export.
9. **Activity** (manager) — the audit log, filterable by analyst and date.
10. **Settings** (everyone) — theme; and for the manager, year open/close, rollover,
    reminders, account.

A clickable interface preview of all of these exists and is the reference for layout and
behaviour.

## Implementation Phases

Each phase ends with something deployable and usable. No phase exists only to prepare for
the next one.

### Phase 1 — Working replacement

Django project, PostgreSQL, Docker Compose, environment-based secrets, deploy-safe settings
from day one (`SECURE_HSTS_SECONDS`, secure/HttpOnly/SameSite cookies, CSRF, TLS at the
proxy). Models: `Employee`, `AppraisalYear`, `Goal`, `Kpi`, `GoalAssignment`, `Score`. Seed
migration for the SMTI goals and the 2026–27 year. Goals screen. Screens 1, 2, 3, 5, 6.

Acceptance criteria:

- `manage.py check --deploy` passes clean with production settings.
- Golden scoring vectors pass for monthly and annual figures.
- A goal assigned to a subset of analysts is excluded from the others entirely — not
  scored as zero.
- Quarterly KPIs cannot be scored outside Jun/Sep/Dec/Mar.
- A score above a KPI's max marks is rejected server-side.
- An analyst cannot read another analyst's record or write any score.
- Two concurrent edits to the same cell produce a conflict, not a silent overwrite.
- `max_marks`, `quarterly` and `scoring_mode` are rejected once the KPI has scores.

At the end of this phase the manager can stop using the Electron app.

### Phase 2 — Tasks and approvals

`Task` and `TaskUpdate`. Tasks screen with the approvals queue, My tasks, task-derived
scoring, and the month-close freeze. Task detail surfaced under the analyst's goal
drill-down.

Acceptance criteria:

- Task weight is scoped per analyst: approving one analyst's task leaves every other
  analyst's score unchanged.
- A submitted task contributes nothing until approved.
- A task added, edited or reassigned after a month is scored cannot change that month.
- A `from_tasks` KPI with no tasks for an analyst that month blocks month completion rather
  than being omitted from the average.
- A task cannot link to a KPI whose goal is not assigned to its assignee.
- Unlinked tasks move no score, in any state.
- Task status changes only via an approved `TaskUpdate`.

### Phase 3 — Audit, summary, export, rollover

`AuditEvent` on every score, goal, task, approval and account change. Activity screen. Year
summary and CSV export. Year close and reopen-with-reason. `new_appraisal_year`. Database
grants: the application role gets `INSERT`/`SELECT` on the audit table and nothing else.
Backup and a verified restore.

Acceptance criteria:

- Every change produces an audit row with actor, timestamp, and before/after values,
  including task approvals and the score recomputation they cause.
- No application code path updates or deletes an audit row, and the DB role cannot either.
- Writes to a closed year are rejected for scores, goals, KPIs, assignments and tasks.
- Reopening a year requires a reason and is audited.
- Rollover leaves the previous year's figures byte-identical, including when the new year's
  goal set differs.
- A restore from backup reproduces scores, tasks and audit history exactly.

### Phase 4 — Directory sign-in

LDAPS via `django-auth-ldap`, local accounts retained for the manager and recovery.
Local-only password reset; LDAP users use the directory's own process. Account lockout and
login rate limiting. Sign-in audit events.

**Blocked until answered:** does the directory expose a usable `manager` attribute, do group
memberships map to Manager/Analyst, and do local accounts reconcile with directory
identities by username or by email? Answer before Phase 1 finishes.

Acceptance criteria:

- Directory users sign in over LDAPS; the local manager account still works.
- An LDAP outage does not lock the manager out.
- Sign-in successes, failures and lockouts are audited.

### Phase 5 — Reassess

Month-end and quarter-end reminders, if wanted. Then stop and look at what is actually
being asked for. Build these **only** if a real need has appeared:

- *MFA* — if the Hub becomes reachable off the internal network, or the directory does not
  already enforce it.
- *Celery/Redis* — if a request measurably blocks on email or export.
- *A second team* — if another manager needs the Hub. That is when `Department`/`Team` earn
  their place.
- *A second Hub module* — planned on its own merits, reusing the shell, `permissions.py`
  and `audit.log_event()`.

## Testing

Scoring and tasks:

- Golden scoring vectors, monthly and annual, including quarter ends and partial months.
- Task weight scoped per analyst; approving one analyst's task moves only their score.
- Submitted-but-unapproved tasks contribute zero.
- A task added to a scored month cannot change it.
- A `from_tasks` KPI with no tasks blocks month completion.
- Unlinked tasks move no score even when every one is approved.
- Goal assignment changes eligibility and the month denominator.

Access, integrity and operations:

- Analyst cannot read or write another analyst's record; a deactivated analyst cannot sign
  in; an analyst cannot approve their own task.
- Max-marks and quarterly-eligibility validation, server-side.
- Concurrent score writes produce a conflict.
- Closed-year writes rejected across scores, goals, KPIs, assignments and tasks.
- `max_marks`/`quarterly`/`scoring_mode` rejected once scores exist.
- Rollover with a changed goal set leaves prior-year figures unchanged.
- Audit rows created on every change; cannot be updated or deleted.
- CSV export contents and authorization.
- LDAP success, failure and timeout (Phase 4).
- Playwright: manager creates a goal, assigns a task, approves it, and sees the score move;
  analyst submits an update and sees it reflected read-only.

Verification before each deploy: `manage.py check --deploy`, the test suite, a Docker smoke
test against PostgreSQL, and a dependency scan.

## Open Questions

1. Directory schema for LDAPS. (Blocks Phase 4; answer before Phase 1 ends.)
2. Who administers accounts and runs backups when the manager is away?
3. Should an analyst see the running annual figure mid-year, or only completed months? An
   early, volatile percentage can distract more than it informs.
4. Should returning a task notify the analyst by email, or is seeing it on My tasks enough?
5. When a task is reassigned before its month is scored, does the weight follow the task to
   the new assignee, or stay with the original? Current assumption: it follows the task,
   and both analysts' live figures move accordingly.

## Changes from the original plan

Recorded so the growth in scope is visible rather than implicit.

- Renamed from "Appraisal Tracker" to **SMTI HUB**; appraisal is the first module.
- Goals moved from seeded-and-fixed to **manager-created and assignable**, with the SMTI
  five as ordinary seed rows.
- Added **tasks with approval**, and task-weighted scoring — three new models
  (`GoalAssignment`, `Task`, `TaskUpdate`) and one new field (`Kpi.scoring_mode`).
- Partial-month scoring **decided**: a month counts only when complete.
- Added the **month-close freeze** for task-derived scores, after review found the
  denominator was mutable.
- Phases resequenced from four to five; tasks are now Phase 2, LDAPS moves to Phase 4.
- Layout is full width.

This is roughly double the original model. The minimalism principle still holds — every
addition above traces to a stated requirement, and the deferred list is unchanged.
