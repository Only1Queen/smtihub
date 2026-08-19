# Deploying the August 2026 release

Everything in PR #1 (`feature/task-tracking-and-year-scoring`). Follow this
top to bottom on the production host. It takes about ten minutes, and the
service is down for roughly thirty seconds of that.

`DEPLOYMENT.md` section 5 is the general procedure; this file is that
procedure with the specifics of this release filled in, plus the two things
that are particular to it: five migrations and a change in what an unassigned
goal means.

---

## What is in it

| Area | Change |
|---|---|
| Scoring | Whole-year score sheet per analyst, reached by clicking a name on Team |
| Scoring | **A goal with nobody assigned now counts for everyone** |
| Tasks | One form assigns to several analysts at once |
| Tasks | Analyst status ladder: picked up → in progress → on track → sent for review → (manager) completed |
| Tasks | Task detail page with the full trail and a daily update box |
| Tasks | Manager can grade work that carries no KPI, out of 100 |
| Reporting | Daily-update dashboard for analysts and managers |
| Accounts | An analyst's password is set when the account is created |
| UI | Card alignment, page footers removed, live countdown to year end |

### The one behaviour change to tell the team about

A goal with **no analysts ticked into it** used to count for nobody. It now
counts for **everybody**. This is what fixes the empty score sheet: production
has five goals and no assignments, so every analyst was being measured on
nothing.

If any goal is meant for only some people, tick those names on **Goals**
before or after deploying — assignment still narrows a goal to exactly the
people listed. Nothing you have already ticked changes meaning.

---

## Before you start

```bash
cd /opt/smti-hub          # wherever the checkout lives on the host
git status                # must be clean; this deploy pulls
docker compose ps         # web and db healthy, proxy up
```

Pick a quiet moment. Nobody loses work if they are mid-page — the restart
drops in-flight requests, and a browser retry is all it takes.

---

## 1. Back up, and prove the backup exists

`./deploy.sh` in step 3 does this for you. To take one now anyway:

```bash
./deploy/backup.sh
ls -lh backups/ | tail -3
```

You should see a new `smti-YYYY-MM-DD-HHMM.sql.gz.gpg` a few kilobytes in
size. **Do not continue without it.** Migration `0007` rewrites rows; the
backup is how you undo that.

Copy it off the host now if your routine does not already:

```bash
scp backups/smti-*.sql.gz.gpg <somewhere-else>:
```

## 2. Get the code

Merge PR #1 on GitHub first, then:

```bash
git pull
git log --oneline -1     # expect: "Year score sheet, team-wide goals, ..."
```

Or take the branch directly without merging:

```bash
git fetch origin
git checkout feature/task-tracking-and-year-scoring
```

## 3. Deploy

```bash
./deploy.sh
```

It backs up first (see step 1 — this is now automatic), then Compose runs the
whole chain and stops at the first failure: `roles`, then
`migrate`, then `grants` (which re-applies `grants.sql` and fails the deploy if
the audit table came back rewritable), then `web` and `proxy`.

Expect all five migrations to apply:

```bash
docker compose logs migrate
```

```
  Applying hub.0004_task_grade... OK
  Applying hub.0005_alter_taskupdate_decision... OK
  Applying hub.0006_alter_task_status_alter_taskupdate_proposed_status... OK
  Applying hub.0007_status_on_track... OK
  Applying hub.0008_daily_update_blank_status... OK
```

What they do: `0004` adds a grade to a task, `0005` and `0006` widen the status
lists, `0007` renames the status `near_done` to `on_track` **and rewrites
existing rows** (reversible), `0008` lets a daily update carry no status at all.
None of them drop anything.

## 4. Verify — do not skip this

```bash
docker compose logs grants             # ends in audit_append_only_ok = t
docker compose run --rm admin python manage.py check --deploy
```

```bash
docker compose ps                      # web healthy
curl -sk https://<host>/healthz        # ok
docker compose logs --tail=40 web      # no tracebacks
```

Then sign in and walk these five, which is every new surface:

1. **Team** → click an analyst → the year sheet fills with goals and months,
   and typing a mark shows "Saved" under the grid.
2. **Tasks** → New task → tick two analysts → both get their own row.
3. Sign in as an analyst → **My tasks** → click the task → post a daily
   update → it appears in Progress tagged "Daily update".
4. Same analyst → Change status → **Completed** → it lands in the manager's
   review queue.
5. **Daily updates** in the sidebar → today's square is green for whoever
   posted.

## 5. Tell the team

Two things they will notice immediately, and one they will not:

- Tasks now have their own page, a status they set themselves, and a daily
  update box. Statuses are picked up / in progress / on track / sent for
  review, and only the manager marks a task completed.
- The score sheet is no longer empty (see the behaviour change above).
- Analysts can change their own password under **Settings**.

---

## If it goes wrong

**Roll back the code, keep the data.** The new migrations do not delete
anything, and the old code ignores the new columns — except `0007`, which
renames a status value the old code does not know. So roll the code back
first, then the one migration:

```bash
git checkout master                     # or the previous tag
docker compose build
docker compose run --rm admin python manage.py migrate hub 0006
./deploy.sh
```

`migrate hub 0006` reverses `0007` and `0008`, putting `on_track` back to
`near_done`. Tasks graded under `0004` keep their grades; the old code simply
does not show them.

**Roll everything back**, if the database itself is wrong:

```bash
./deploy/restore.sh backups/smti-<the-one-from-step-1>.sql.gz.gpg
```

That discards anything entered since step 1. Which is why step 1 comes first.
