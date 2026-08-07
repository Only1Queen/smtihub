# SMTI HUB — Deployment Guide

Internal deployment on one Linux host: Django + PostgreSQL + Nginx, in Docker.

There is **no SQLite fallback**. PostgreSQL is required everywhere, including
local development and the test suite, because the audit trail's append-only
guarantee is a PostgreSQL `GRANT` (`deploy/grants.sql`). A database that quietly
works in development but cannot enforce that rule is how the rule gets
discovered missing, in the month somebody disputes a mark.

---

## 1. What you need first

| | |
|---|---|
| Host | Linux, 2 vCPU / 4 GB RAM / 20 GB disk is ample for one team |
| Software | Docker Engine 24+ and the Compose plugin |
| Network | Reachable on the internal network only. Do not expose to the internet. |
| DNS | A name, e.g. `smti-hub.internal`, pointing at the host |
| TLS | A certificate and key for that name (internal CA is fine) |
| SMTP | Relay host, port and credentials — or leave blank and mail goes to the log |

Check Docker:

```bash
docker --version && docker compose version
```

---

## 2. Get the code and configure

```bash
sudo mkdir -p /opt/smti-hub && sudo chown "$USER" /opt/smti-hub
cd /opt/smti-hub
# copy or clone the repository here
cp .env.example .env
chmod 600 .env
```

Generate the four secrets:

```bash
python3 - <<'PY'
import secrets
print("DJANGO_SECRET_KEY=" + secrets.token_urlsafe(64))
print("POSTGRES_OWNER_PASSWORD=" + secrets.token_urlsafe(24))
print("POSTGRES_WEB_PASSWORD=" + secrets.token_urlsafe(24))
print("BACKUP_PASSPHRASE=" + secrets.token_urlsafe(24))
PY
```

Paste the first three into `.env` and set `DJANGO_ALLOWED_HOSTS` to your hostname.
**Keep `BACKUP_PASSPHRASE` somewhere else** — a password manager, not this host.
A backup you cannot decrypt after losing the host is not a backup.

`.env` should end up looking like:

```ini
DJANGO_SECRET_KEY=<64 chars>
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=smti-hub.internal
DJANGO_TIME_ZONE=Africa/Lagos

POSTGRES_DB=smti
POSTGRES_OWNER=smti_owner
POSTGRES_OWNER_PASSWORD=<24 chars>
POSTGRES_WEB_USER=smti_web
POSTGRES_WEB_PASSWORD=<24 chars>

EMAIL_HOST=smtp.organisation.example
EMAIL_PORT=587
EMAIL_HOST_USER=smti-hub
EMAIL_HOST_PASSWORD=<relay password>
EMAIL_USE_TLS=1
DEFAULT_FROM_EMAIL=smti-hub@organisation.example
```

### Two database roles, on purpose

| Role | Used by | Can |
|---|---|---|
| `smti_owner` | migrations and admin tasks only | everything |
| `smti_web` | the running application | read/write all tables; **INSERT and SELECT only** on `hub_auditevent` |

The application never connects as the owner. That is what makes "the audit log
cannot be rewritten" a fact about the database rather than a promise about the
code.

---

## 3. TLS certificate

Put the certificate and key where Nginx expects them:

```bash
cp your-cert.crt deploy/certs/smti.crt
cp your-cert.key deploy/certs/smti.key
chmod 600 deploy/certs/smti.key
```

For a self-signed certificate on a closed network:

```bash
openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
  -keyout deploy/certs/smti.key -out deploy/certs/smti.crt \
  -subj "/CN=smti-hub.internal" \
  -addext "subjectAltName=DNS:smti-hub.internal"
chmod 600 deploy/certs/smti.key
```

Browsers will warn until the CA is trusted on client machines. Distribute the
CA rather than teaching people to click through certificate warnings — that
habit is what phishing relies on.

---

## 4. First deployment

```bash
cd /opt/smti-hub

# Read the three database settings out of .env. Do not `. ./.env`: values like
# DJANGO_ADMINS contain spaces and <>, which Compose accepts and bash does not.
OWNER=$(grep -m1 '^POSTGRES_OWNER=' .env | cut -d= -f2-)
DB=$(grep -m1 '^POSTGRES_DB=' .env | cut -d= -f2-)
WEB_PW=$(grep -m1 '^POSTGRES_WEB_PASSWORD=' .env | cut -d= -f2-)

# 1. Build images and start PostgreSQL
docker compose build
docker compose up -d db
docker compose exec db pg_isready -U "$OWNER" -d "$DB"

# 2. Create the restricted application role
docker compose exec -T db psql -U "$OWNER" -d "$DB" \
  -v web_password="'$WEB_PW'" -f /deploy/bootstrap_roles.sql

# 3. Run migrations as the owner (creates tables, seeds FY 2026-27)
docker compose run --rm admin python manage.py migrate

# 4. Lock down the audit table
docker compose exec -T db psql -U "$OWNER" -d "$DB" -f /deploy/grants.sql

# 5. Prove it worked — must print audit_append_only_ok = t
docker compose exec -T db psql -U "$OWNER" -d "$DB" -f /deploy/verify_grants.sql

# 6. Start the application and proxy
docker compose up -d web proxy
```

If step 5 does not print `t`, stop and fix it before anyone signs in.

### Create the manager account

```bash
docker compose run --rm admin python manage.py createsuperuser
```

Then link that user to an employee record and give them the Manager group:

```bash
docker compose run --rm admin python manage.py shell -c "
from django.contrib.auth.models import User
from hub.models import Employee
from hub.forms import ensure_manager_group
u = User.objects.get(username='<the username you just made>')
u.first_name, u.last_name = 'Firstname', 'Lastname'; u.save()
ensure_manager_group(u)
Employee.objects.get_or_create(user=u, defaults={'job_title': 'SMTI Manager'})
print('manager ready:', u.get_full_name())
"
```

Visit `https://smti-hub.internal/` and sign in.

### Add the analysts

Through the UI: **Team → + Add analyst**. That creates the login and the
employee record together, but leaves the password unusable. Set it from the same
screen with **Set password**, or from the command line:

```bash
docker compose run --rm admin python manage.py changepassword <username>
```

With Active Directory configured (next section) you do not do this at all —
people appear the first time they sign in.

Tick **Manager** on that form to give someone the manager role: scoring,
approvals and the activity log. It is recorded in the audit trail as
`role.granted`. Under AD, the tick is overwritten by group membership at their
next sign-in — grant it in AD, not here.

Then assign goals: **Goals → Edit** on each goal, tick the analysts it applies
to. A goal not assigned to someone is excluded from their scoring entirely — it
is never counted against them as a zero.

---

## 4a. Active Directory sign-in

Optional but recommended. With `AUTH_LDAP_SERVER_URI` set, AD decides who can
sign in and who is a manager, and this application stops holding passwords for
those people at all — password policy, expiry, MFA at the edge and disabling a
leaver all become the directory's job, which is where they were always enforced
properly.

Leave `AUTH_LDAP_SERVER_URI` blank and everything stays local; nothing else
changes.

### What you need from whoever runs AD

| | |
|---|---|
| A read-only service account | DN and password. No privileges beyond reading users and groups. |
| The user search base | e.g. `OU=Users,DC=corp,DC=example` |
| The group search base | e.g. `OU=Groups,DC=corp,DC=example` |
| A managers group | e.g. `CN=SMTI-Managers,OU=Groups,DC=corp,DC=example` |
| Optionally, an access group | If set, only its members may sign in at all. |
| LDAPS reachable from this host | Port 636. Port 389 is used with StartTLS if that is all there is. |

Fill in the `--- Active Directory ---` block in `.env`, then rebuild and
restart:

```bash
docker compose build web && docker compose up -d web
```

### Prove it before telling anyone to use it

```bash
# 1. The service account can bind and find a person
docker compose run --rm admin python manage.py shell -c "
from django_auth_ldap.backend import LDAPBackend
u = LDAPBackend().populate_user('<a real username>')
print('found:', u, '| groups:', sorted(u.ldap_user.group_dns) if u else 'NOT FOUND')
"
```

`NOT FOUND` for someone who definitely exists means the bind failed, the search
base is wrong, or the filter excluded them. Set `LDAP_LOG_LEVEL=DEBUG` in `.env`,
restart `web`, and `docker compose logs -f web` will say which.

2. Sign in as that person in the browser. On first sign-in the hub creates their
   employee record automatically (`employee.provisioned` in the activity log)
   and gives them the manager role if they are in the managers group.

3. Check the roles landed: **Activity**, filter on `role.granted`.

### How it behaves

- **Passwords** are never stored or changed here. Settings shows "Active
  Directory" instead of a change-password button.
- **The manager role** is re-read at every sign-in. Removing someone from the AD
  group removes their access to team screens the next time they sign in, and it
  is audited as `role.revoked`. Nested groups are followed.
- **Disabled AD accounts** cannot sign in immediately: the user filter excludes
  them. They are deactivated in the hub by the nightly `sync_ad` job, which is
  what removes them from the team list and the reminder emails.
- **The local superuser still works.** Keep it: it is how you get in when the
  domain controller is unreachable, and it is the only account that can then fix
  the configuration. Give it a long password and store it with the backup
  passphrase.

### Closing leavers' accounts

```bash
docker compose run --rm admin python manage.py sync_ad --dry-run   # report only
docker compose run --rm admin python manage.py sync_ad             # deactivate
```

Run nightly from cron (see **Scheduled jobs**). It only ever touches accounts
that came from AD, never the local break-glass one, and it refuses to run if
more than half the team looks missing — an unreachable domain controller and a
team that has all left look identical over LDAP, and only one of them should
deactivate everybody.

It closes accounts; it never reopens them. Someone deactivated here stays
deactivated until a manager turns them back on, because they may have been
deactivated for a reason of the hub's own.

---

## 5. Routine operations

### Deploying a new version

```bash
cd /opt/smti-hub
OWNER=$(grep -m1 '^POSTGRES_OWNER=' .env | cut -d= -f2-)
DB=$(grep -m1 '^POSTGRES_DB=' .env | cut -d= -f2-)

BACKUP_PASSPHRASE=<yours> ./deploy/backup.sh        # always back up first
git pull                                            # or copy the new code in
docker compose build
docker compose run --rm admin python manage.py migrate
docker compose exec -T db psql -U "$OWNER" -d "$DB" -f /deploy/grants.sql
docker compose up -d web proxy
docker compose exec -T db psql -U "$OWNER" -d "$DB" -f /deploy/verify_grants.sql
```

**Re-run `grants.sql` after every migrate.** A migration that creates a table
grants privileges on it afresh, so a new table would otherwise arrive
unprotected. The `verify_grants.sql` step at the end is how you find out.

### Backups

```bash
BACKUP_PASSPHRASE=<yours> ./deploy/backup.sh
```

Writes `backups/smti-YYYY-MM-DD-HHMM.sql.gz.gpg`, AES256-encrypted, and prunes
anything older than 30 days (`BACKUP_KEEP_DAYS` to change).

Nightly, via the deploying user's crontab:

```cron
15 2 * * * cd /opt/smti-hub && BACKUP_PASSPHRASE=<yours> ./deploy/backup.sh >> /var/log/smti-backup.log 2>&1
```

Copy `backups/` to a different machine. A backup on the host it protects is
only a defence against software failure, not against losing the host.

### Restoring

```bash
BACKUP_PASSPHRASE=<yours> ./deploy/restore.sh backups/smti-2026-10-14-0200.sql.gz.gpg
```

Restores into `smti_restore` rather than over the live database, and prints the
rename commands once you have checked it. **Test a restore before you need
one** — an untested backup is a guess. Do it quarterly.

### Closing and reopening the appraisal year

Closing freezes every score, goal, KPI, assignment and task in the year.
Reopening is audited and asks for a reason. Both are done from **Settings**, or
from the Django admin at `/admin/hub/appraisalyear/`.

Once the year is closed, each analyst is asked on **My appraisal** to
acknowledge it: a tick that they have seen it, with an optional comment kept
word for word. It records the percentage as it stood at that moment, so a later
correction cannot change what they signed. Acknowledging is not agreeing — the
comment box is where disagreement goes, and it is permanent. Nobody can
acknowledge on somebody else's behalf; there is no route that accepts another
person's id.

Who has and has not acknowledged shows on each person's appraisal page.

### Starting the next year

```bash
docker compose run --rm admin python manage.py new_appraisal_year "FY 2027-28" 2027
```

Copies this year's goals, KPIs and assignments forward and prints the structure
for you to check. To change the goals for the new year, edit them on the Goals
screen afterwards, or load a revised set:

```bash
docker compose run --rm admin python manage.py new_appraisal_year "FY 2027-28" 2027 \
  --goals-file /app/deploy/goals-2027.json
```

Because goals are year-scoped, nothing you do to next year can alter last
year's numbers.

### Scheduled jobs

Month-end reminders, the AD sync and backups all run from cron. Nothing is
scheduled until you install it:

```bash
sudo cp deploy/smti-hub.cron /etc/cron.d/smti-hub
sudo chown root:root /etc/cron.d/smti-hub && sudo chmod 644 /etc/cron.d/smti-hub
```

Edit it if the paths differ. Output goes to syslog — `journalctl -t smti-hub` is
where a failed run shows up. Check the reminder by hand first:

```bash
docker compose run --rm admin python manage.py send_reminders --dry-run
```

### Clearing a lockout

Five failed sign-ins lock that username, and that address, for fifteen minutes.
The lock clears itself; to clear it immediately:

```bash
docker compose run --rm admin python manage.py axes_reset_username <username>
docker compose run --rm admin python manage.py axes_reset_ip <address>
docker compose run --rm admin python manage.py axes_reset          # everything
```

Look at **Activity** first, filtered on `auth.lockout` and `auth.login_failed`.
A lockout on an account whose owner is on leave is worth a question before it is
cleared.

### The activity log

**Activity** is paginated and filterable, and **Export CSV** gives the whole
filtered set rather than the page on screen — that is the file to hand over when
somebody asks for everything on one account.

Retention is not automatic. Audit rows are kept forever until someone decides
otherwise, and the decision belongs with whoever owns HR retention:

```bash
docker compose run --rm admin python manage.py prune_audit --years 7        # counts only
docker compose run --rm admin python manage.py prune_audit --years 7 --yes  # deletes
```

It runs as the owner role on purpose. The application role cannot delete an
audit row and must not be able to; this is the one place that deliberately can,
and it is a person typing a command, not a job.

### Health

`https://<host>/healthz` returns `ok` (200) when the app can reach the database
and `db unavailable` (503) when it cannot. It needs no sign-in, and the `web`
container healthcheck uses it — `docker compose ps` showing `healthy` means the
app answered, not just that the process is alive.

---

## 5a. Seeing the app without the certificate warning

For a quick local look, publish the app port to loopback instead of clicking
through a TLS warning:

```bash
cat > docker-compose.override.yml <<'EOF'
services:
  web:
    ports: ["127.0.0.1:8000:8000"]
    environment:
      DJANGO_SECURE_SSL_REDIRECT: "0"
EOF
docker compose up -d web       # then open http://127.0.0.1:8000/
```

**Delete the override before real use** — `rm docker-compose.override.yml &&
docker compose up -d web`. Traffic must arrive through the proxy over TLS, and
`docker compose ps` should show no published port on `web`.

---

## 6. Local development

Still PostgreSQL — just a throwaway one.

```bash
docker run -d --name smti-dev-db -p 5432:5432 \
  -e POSTGRES_DB=smti -e POSTGRES_USER=smti_owner -e POSTGRES_PASSWORD=devpw \
  postgres:16-alpine

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export DATABASE_URL=postgres://smti_owner:devpw@localhost:5432/smti
export DJANGO_SECRET_KEY=dev-only
export DJANGO_DEBUG=1

.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Run the tests (they create and drop their own database):

```bash
.venv/bin/python manage.py test hub --noinput
```

62 tests, about two minutes. They cover the scoring golden vectors, the
task-approval and month-freeze rules, access control, and audit immutability.

---

## 7. Troubleshooting

**`ImproperlyConfigured: DATABASE_URL is not set`**
The environment did not reach the process. Check `env_file: .env` is present in
`docker-compose.yml` and that `.env` sits beside it. Run `manage.py` through
`docker compose run --rm admin` rather than on the host, so Compose supplies the
environment — `.env` cannot be sourced into a shell, several values contain
spaces and `<>`.

**`password authentication failed for user "smti_web"` (or `smti_owner`)**
The role's password and `.env` have diverged. Print what the app actually
sends, which is not always what the file says:

```bash
docker compose exec -T web python -c "import os;from urllib.parse import urlparse;print(repr(urlparse(os.environ['DATABASE_URL']).password))"
```

If that differs from `grep POSTGRES_WEB_PASSWORD .env`, the password contains a
character that does not survive the trip into `DATABASE_URL`: Compose truncates
at `;`, and `urlparse` returns `None` when it contains `/` or `#`. Replace it
with `secrets.token_urlsafe(24)` output, which is `[A-Za-z0-9_-]` only.

If it matches, the database simply holds a different password — `.env` was
changed after the role was created, or step 2 never ran. Either way re-run
`bootstrap_roles.sql` (it is idempotent) and `docker compose up -d web`.

Note that `POSTGRES_OWNER_PASSWORD` only reaches PostgreSQL when the data
directory is first initialized. Changing it in `.env` afterwards changes what
clients send, never what the server expects; correct the server with
`ALTER ROLE smti_owner PASSWORD '...'`.

**`DATABASE_URL must be a postgres:// URL`**
Deliberate. Only PostgreSQL is supported; see the top of this guide.

**`permission denied for table hub_auditevent`**
Correct behaviour if something tried to `UPDATE` or `DELETE` an audit row. If it
appears on a normal action, a code path is trying to rewrite history — treat it
as a bug, not a permissions problem to grant away.

**Web container restarts, log says the database is not ready**
`docker compose ps` — the `db` healthcheck should read `healthy`. Check
`docker compose logs db`.

**`Missing staticfiles manifest entry`**
`collectstatic` did not run. It is in the Dockerfile, so rebuild:
`docker compose build web`.

**Build fails on `collectstatic` with `DATABASE_URL is not set`**
Already handled: the Dockerfile passes a placeholder `DATABASE_URL` for that one
step. `collectstatic` never opens a connection, but `settings.py` requires the
variable to exist because there is no SQLite fallback. The real URL arrives at
runtime.

**Browser certificate warning**
Expected until the internal CA is trusted on the client. Distribute the CA
certificate; do not train people to click through the warning.

**A score will not save, message about the month being scored**
Working as designed. A scored month is frozen so task changes cannot alter marks
already earned. Reopen the year in the admin to record a correction; the reopen
is audited.

**`ImproperlyConfigured: DJANGO_SECRET_KEY is not set`**
Deliberate, and it only fires with `DJANGO_DEBUG=0`. Every session cookie is
signed with that key; shipping the placeholder would mean anyone who has read
this repository can forge one. Generate one into `.env`:
`python3 -c "import secrets; print(secrets.token_urlsafe(64))"`.

**Nobody can sign in after enabling AD**
Try the local superuser — it still works, and it is how you get in to fix this.
Then `LDAP_LOG_LEVEL=DEBUG` in `.env`, restart `web`, and read
`docker compose logs -f web` while attempting a sign-in. The usual causes are a
wrong bind password, a search base that does not contain the person, or
`AUTH_LDAP_ACCESS_GROUP_DN` naming a group they are not in.

**Someone signs in but every page says their account is not linked**
That should no longer happen — an employee record is created on first sign-in.
If it does, the audit log will not have an `employee.provisioned` row for them:
check `docker compose logs web` for an error in the login signal.

**"Too many attempts" and the password is definitely right**
It is a lockout, not a rejected password. Wait fifteen minutes or clear it — see
**Clearing a lockout**. If it happens repeatedly to one person with no failed
attempts of their own, look at `auth.login_failed` in Activity for their
username: somebody else is trying it.

**`docker compose ps` shows `web` as unhealthy**
`curl -k https://<host>/healthz`. A 503 means the app cannot reach PostgreSQL —
check `db`. No answer at all means gunicorn is wedged: `docker compose logs web`,
then `docker compose restart web`.

**Mail is not arriving**
With `EMAIL_HOST` blank, mail is written to the container log instead of sent:
`docker compose logs web`. Set the relay details in `.env` and restart `web`.

---

## 8. Security checklist before go-live

- [ ] `.env` is `chmod 600` and not in version control
- [ ] `DJANGO_DEBUG=0` and `DJANGO_ALLOWED_HOSTS` names the real host
- [ ] `docker compose run --rm admin python manage.py check --deploy` is clean
- [ ] `verify_grants.sql` prints `audit_append_only_ok = t`
- [ ] The app connects as `smti_web`, never `smti_owner` (check `docker compose config`)
- [ ] PostgreSQL is not published to the host — no `ports:` on the `db` service
- [ ] TLS works and HTTP redirects to HTTPS
- [ ] A backup has been taken **and a restore rehearsed**
- [ ] `BACKUP_PASSPHRASE` is stored off this host
- [ ] Every account has a real password; no shared logins
- [ ] `DJANGO_SECRET_KEY` is a generated value, not the placeholder
- [ ] A failed sign-in appears in **Activity** as `auth.login_failed`, and five
      of them lock the account out
- [ ] `DJANGO_ADMINS` is set, so a 500 reaches a person and not only the log
- [ ] If AD is enabled: the bind account is read-only, LDAPS (or StartTLS) is in
      use, and the local break-glass superuser has been tested **and its password
      stored off this host**
- [ ] If AD is enabled: `sync_ad --dry-run` names only people who have actually left
- [ ] `docker compose ps` shows `web` as `healthy`
- [ ] Cron is installed (`/etc/cron.d/smti-hub`) and a reminder run has been
      tested with `--dry-run`
- [ ] Somebody has decided how long audit rows are kept (`prune_audit`)
- [ ] The host is reachable from the internal network only
- [ ] `.env` is absent from the image (`docker compose run --rm admin ls /app/.env`
      must say "No such file") — `.dockerignore` keeps it out

---

## 9. Not yet built

- **The reporting line is not read from AD.** Sign-in, the manager role and
  leaver deactivation all come from the directory, but who reports to whom is
  still set in the hub. The `manager` attribute is populated inconsistently in
  most directories, and getting it wrong silently moves whose appraisal a goal
  counts toward. Set it on the Team screen.
- **Self-service password reset** is not implemented and is not planned. Under
  AD the password does not live here; without AD, accounts are deliberately not
  self-service and a manager sets the password. Adding a reset-by-email flow
  would make a mailbox the way into somebody's appraisal record.
- **MFA** is not implemented. On a portal reachable only from the internal
  network, with the directory or VPN enforcing it at the edge, that is a
  reasonable position — revisit it if the Hub is ever exposed more widely.
