# SMTI HUB — Install and Use

A plain-English guide to putting SMTI HUB on a Linux server and running it.
No prior Docker or Django knowledge assumed. Copy the commands as they are
written, in order.

`DEPLOYMENT.md` is the same ground in more depth, for whoever maintains it
later. This file is the one to follow the first time.

**What the app is:** an appraisal tracker for the SOC team. A manager sets
goals and KPIs for the year, assigns tasks, and scores each analyst month by
month. Analysts see their own marks, submit tasks for approval, and sign off
the year at the end. Everything anyone does is written to an activity log that
nobody — including the manager, including the database user the app connects
as — can edit or delete afterwards.

**Roughly an hour**, most of it waiting for downloads.

---

# Part 1 — Before you start

## The server

| | |
|---|---|
| Machine | A Linux server: Ubuntu 22.04/24.04 or RHEL/Rocky 9. A virtual machine is fine. |
| Size | 2 CPUs, 4 GB memory, 20 GB disk. That is plenty for one team. |
| Network | On the internal network only. **Do not put this on the public internet.** |
| Access | You can log in over SSH and run `sudo`. |

## What to ask for, before you begin

Get these three things lined up first — everything else you can do yourself.

1. **A hostname** — a name on the internal network pointing at this server,
   e.g. `smti-hub.internal`. Ask whoever runs DNS.
2. **A TLS certificate** for that name (a `.crt` file and a `.key` file). Ask
   whoever runs the internal certificate authority. If there isn't one, Part 4
   shows how to make your own; browsers will show a warning until it is
   trusted.
3. **Optional — email relay details** (server, port, username, password) so the
   app can send month-end reminders. Leave blank and it simply doesn't send
   any; nothing breaks.

Optional and recommended, but you can add it later: **Active Directory**
sign-in, so people use their normal Windows password. See Part 8 for what to
ask the AD administrator.

## Install Docker

Docker is the thing that runs the app. One command installs it:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
```

The second line lets you use Docker without typing `sudo` every time. **Log out
and back in** for it to take effect, then check:

```bash
docker --version && docker compose version
```

Both should print a version number. If `docker compose version` fails, your
Docker is too old — install the Compose plugin:
`sudo apt install docker-compose-plugin` (Ubuntu) or
`sudo dnf install docker-compose-plugin` (RHEL/Rocky).

Also make sure git is present:

```bash
sudo apt install -y git      # Ubuntu/Debian
sudo dnf install -y git      # RHEL/Rocky
```

---

# Part 2 — Get the code

```bash
sudo mkdir -p /opt/smti-hub
sudo chown "$USER" /opt/smti-hub
git clone https://github.com/Only1Queen/smtihub.git /opt/smti-hub
cd /opt/smti-hub
```

Everything from here on is run from `/opt/smti-hub`. If you open a new terminal
later, `cd /opt/smti-hub` first.

---

# Part 3 — Settings and passwords

The app reads its settings from a file called `.env`. Start from the example:

```bash
cp .env.example .env
chmod 600 .env
```

`chmod 600` means only you can read it. It holds passwords, so that matters.

## Generate the secrets

Run this and keep the output on screen:

```bash
python3 - <<'PY'
import secrets
print("DJANGO_SECRET_KEY=" + secrets.token_urlsafe(64))
print("POSTGRES_OWNER_PASSWORD=" + secrets.token_urlsafe(24))
print("POSTGRES_WEB_PASSWORD=" + secrets.token_urlsafe(24))
print("BACKUP_PASSPHRASE=" + secrets.token_urlsafe(24))
PY
```

Now open the settings file:

```bash
nano .env
```

(`nano` saves with `Ctrl+O`, `Enter`, then exits with `Ctrl+X`.)

Replace the values that are in there with the generated ones, and set your
hostname:

```ini
DJANGO_SECRET_KEY=<the long one you just generated>
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=smti-hub.internal
DJANGO_TIME_ZONE=Africa/Lagos

POSTGRES_DB=smti
POSTGRES_OWNER=smti_owner
POSTGRES_OWNER_PASSWORD=<generated>
POSTGRES_WEB_USER=smti_web
POSTGRES_WEB_PASSWORD=<generated>

# Who gets an email if a page crashes. Blank means nobody does.
DJANGO_ADMINS=Your Name <you@organisation.example>

# Email relay. Leave EMAIL_HOST blank and mail goes to the log instead.
EMAIL_HOST=
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=1
DEFAULT_FROM_EMAIL=smti-hub@organisation.example
```

Leave the `AUTH_LDAP_*` lines alone for now — blank `AUTH_LDAP_SERVER_URI`
means "local accounts only", which is what you want until the app is up.

**`BACKUP_PASSPHRASE` does not go in this file.** Put it in a password manager,
or on paper in a safe — somewhere that is not this server. It is what decrypts
your backups, and a backup you can't decrypt after losing the server is not a
backup.

> **Why two database passwords?** The app runs as a restricted database user
> (`smti_web`) that is allowed to *add* rows to the activity log but not change
> or delete them. The powerful user (`smti_owner`) is only used by you, by hand,
> for upgrades. That is what makes "the audit log can't be edited" a fact rather
> than a promise.

---

# Part 4 — The certificate

The app is served over HTTPS. Put your certificate and key where it expects
them:

```bash
cp /path/to/your-cert.crt deploy/certs/smti.crt
cp /path/to/your-cert.key deploy/certs/smti.key
chmod 600 deploy/certs/smti.key
```

**No certificate from IT?** Make your own (fine for a closed internal network —
browsers will warn until someone installs it as trusted):

```bash
openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
  -keyout deploy/certs/smti.key -out deploy/certs/smti.crt \
  -subj "/CN=smti-hub.internal" \
  -addext "subjectAltName=DNS:smti-hub.internal"
chmod 600 deploy/certs/smti.key
```

Replace `smti-hub.internal` with your real hostname in both places.

---

# Part 5 — Start it up

```bash
cd /opt/smti-hub
docker compose up -d --build
```

That is the whole install. The build takes a few minutes the first time. Compose
then works through the steps in order and stops at the first one that fails:

1. **db** — starts PostgreSQL and waits until it is accepting connections.
2. **roles** — creates the restricted database user the app signs in as, with
   the password from your `.env`.
3. **migrate** — creates the tables and the first appraisal year.
4. **grants** — locks the activity log so it can be added to but never edited,
   then checks the lock actually took. If it did not, the deploy stops here and
   the app is never started.
5. **web** and **proxy** — the app itself.

Confirm it is alive:

```bash
docker compose ps
```

You want `db` and `web` both showing `healthy`, `proxy` showing `Up`, and
`roles`, `migrate` and `grants` showing `Exited (0)`. Give it about thirty
seconds after starting.

---

# Part 6 — Create the first manager

Nobody can sign in yet. Make yourself an account:

```bash
docker compose run --rm admin python manage.py createsuperuser
```

It asks for a username, an email (optional — press Enter to skip), and a
password twice. **The password must be at least 12 characters.** Write down the
username you chose.

Now give that account a name and the manager role:

```bash
docker compose run --rm admin python manage.py shell -c "
from django.contrib.auth.models import User
from hub.models import Employee
from hub.forms import ensure_manager_group
u = User.objects.get(username='CHANGE_ME')
u.first_name, u.last_name = 'Firstname', 'Lastname'; u.save()
ensure_manager_group(u)
Employee.objects.get_or_create(user=u, defaults={'job_title': 'SMTI Manager'})
print('manager ready:', u.get_full_name())
"
```

Replace `CHANGE_ME` with your username and put your real name in. It should
print `manager ready: Your Name`.

Keep this account even after you turn on Active Directory. It is how you get
back in when the domain controller is unreachable — store its password with the
backup passphrase.

---

# Part 7 — Sign in

Open `https://smti-hub.internal/` in a browser on the internal network and sign
in with the account you just made.

If you get a certificate warning, that is expected with a self-made
certificate — the proper fix is to have the certificate trusted on the client
machines, not to teach people to click through warnings.

**Quick check that everything is wired up:** type a wrong password once on
purpose, sign in properly, then open **Activity** in the sidebar. You should
see `auth.login_failed` and `auth.login`. That is the audit trail working.

---

# Part 8 — Active Directory sign-in (optional)

With this on, people sign in with their normal Windows username and password,
and the app never holds their password at all. Skip it if you don't need it;
everything works with local accounts.

### Ask the AD administrator for

| | |
|---|---|
| A read-only service account | Its full DN and password. It needs no privileges beyond reading users and groups. |
| The user search base | e.g. `OU=Users,DC=corp,DC=example` |
| The group search base | e.g. `OU=Groups,DC=corp,DC=example` |
| A managers group | e.g. `CN=SMTI-Managers,OU=Groups,DC=corp,DC=example` — whoever is in it gets the manager role |
| The server address | `ldaps://dc01.corp.example` (port 636), reachable from this server |

### Fill it in

`nano .env`, find the `--- Active Directory ---` block, and complete it:

```ini
AUTH_LDAP_SERVER_URI=ldaps://dc01.corp.example
AUTH_LDAP_BIND_DN=CN=svc-smti-hub,OU=Service Accounts,DC=corp,DC=example
AUTH_LDAP_BIND_PASSWORD=<the service account password>
AUTH_LDAP_USER_SEARCH_BASE=OU=Users,DC=corp,DC=example
AUTH_LDAP_GROUP_SEARCH_BASE=OU=Groups,DC=corp,DC=example
AUTH_LDAP_MANAGER_GROUP_DN=CN=SMTI-Managers,OU=Groups,DC=corp,DC=example
```

Then restart:

```bash
docker compose up -d web
```

### Test it before telling anyone

```bash
docker compose run --rm admin python manage.py shell -c "
from django_auth_ldap.backend import LDAPBackend
u = LDAPBackend().populate_user('SOMEONES_USERNAME')
print('found:', u, '| groups:', sorted(u.ldap_user.group_dns) if u else 'NOT FOUND')
"
```

`NOT FOUND` for somebody who definitely exists means the service account
password is wrong, the search base is wrong, or the account is disabled. Set
`LDAP_LOG_LEVEL=DEBUG` in `.env`, `docker compose up -d web`, and
`docker compose logs -f web` will tell you which while you try again.

Once it works: people appear in the app automatically the first time they sign
in. You do not create accounts by hand any more, and the manager role comes
from the AD group at every sign-in — grant it in AD, not in the app.

---

# Part 9 — Using the app

The sidebar shows only what your role allows. Analysts see the last two
entries; managers see everything.

## For the manager

**Team** — everyone you appraise, with their year-to-date percentage and a
month-by-month grid.

- **+ Add analyst** creates their login and their record together. If you are
  not using AD, use **Set password** on the same screen afterwards — until then
  they cannot sign in.
- Tick **Manager** on that form to give someone the manager role. (With AD on,
  that tick is overwritten by the AD group at their next sign-in.)
- **Deactivate** for a leaver — never delete. They keep their place in the
  year's history.

**Goals** — what the team is measured on this year. Each goal holds KPIs, and
each KPI has a mark out of some maximum.

- A KPI can be scored **by hand** (you type the mark) or **from tasks** (the
  mark comes from the tasks approved against it — you never type it).
- **Quarterly** KPIs are only scored in Jun, Sep, Dec and Mar.
- Editing a goal lets you tick which analysts it applies to. **Somebody not
  ticked is excluded from that goal entirely** — it is not counted against them
  as a zero.

**Tasks** — work assigned to people.

- **+ New task**: pick who, when it's due, and which month it scores in. Link it
  to a KPI under **Counts toward** and it carries marks; leave that empty and it
  is operational work that still needs your approval but earns nothing.
- When someone submits a task you get it under **Tasks** to **Approve** or
  **Send back** with a reason. Approving a KPI-linked task moves their mark
  immediately.

**Scoring a month** — from **Team**, click the month cell for a person. Type
each mark; the grid saves as you go. When the month is agreed, **Close month**
freezes it so later task changes cannot alter marks already earned. Reopening
is allowed but asks for a reason and is recorded.

**Year summary** — everyone, every month, in one table, with CSV export.

**Activity** — everything anyone did, filterable, with **Export CSV** giving the
whole filtered set rather than the page on screen. This is the file to hand over
when somebody asks for the history of one account.

**Settings** — close the appraisal year when it is done (this freezes
everything), or reopen it with a reason.

## For the analyst

**My appraisal** — your goals, KPIs and marks, month by month, with your
year-to-date percentage. Once the manager closes the year you are asked to
**acknowledge** it: a tick that you have seen it, plus an optional comment.

Acknowledging is *not* agreeing. If you disagree, say so in the comment — it is
kept permanently, word for word, next to the mark, and the percentage is frozen
at what it was when you signed, so a later correction cannot rewrite what you
acknowledged.

**My tasks** — what you have been assigned. When a task is done, **Submit for
approval** with a note. It stays "awaiting approval" until the manager confirms
it; only then does it count toward a KPI.

**Settings** — change your own password. (If you sign in with Active Directory
it says "Active Directory" instead — your password is changed in Windows, as
usual.)

## Things that surprise people

- **A closed month won't accept scores.** That is deliberate. Reopen it if there
  is a genuine correction; the reopen is recorded.
- **Five wrong passwords locks the account for fifteen minutes.** The right
  password won't work either during that time — it is a lockout, not a
  rejection. It clears itself.
- **Nothing is ever deleted from Activity.** Not by the manager, not by the app.

---

# Part 10 — Keeping it running

## Backups — set this up on day one

```bash
BACKUP_PASSPHRASE=<yours> ./deploy/backup.sh
```

Writes an encrypted copy into `backups/`. To run it nightly, plus the month-end
reminders and (if you use AD) the leaver sync:

```bash
sudo cp deploy/smti-hub.cron /etc/cron.d/smti-hub
sudo chown root:root /etc/cron.d/smti-hub && sudo chmod 644 /etc/cron.d/smti-hub
```

**Copy `backups/` to another machine.** A backup sitting on the server it
protects only survives software failure, not the loss of the server.

Rehearse a restore once a quarter — an untested backup is a guess:

```bash
BACKUP_PASSPHRASE=<yours> ./deploy/restore.sh backups/smti-2026-10-14-0200.sql.gz.gpg
```

It restores into a *separate* database so it cannot damage the live one.

## Installing an update

```bash
cd /opt/smti-hub
BACKUP_PASSPHRASE=<yours> ./deploy/backup.sh     # always first
git pull
docker compose up -d --build
```

Same steps as the install: the new tables are created, the activity log is
re-locked and checked, and the new code only starts if all of that worked.

## Starting the next appraisal year

```bash
docker compose run --rm admin python manage.py new_appraisal_year "FY 2027-28" 2027
```

Copies this year's goals and KPIs forward. Last year's numbers cannot be changed
by anything you do to the new one.

## Day-to-day commands

```bash
docker compose ps                    # is everything up and healthy?
docker compose logs -f web           # watch the app's log (Ctrl+C to stop)
docker compose restart web           # restart the app
docker compose stop                  # stop everything (data is kept)
docker compose up -d db web proxy    # start it again
```

---

# Part 11 — When something goes wrong

| What you see | What to do |
|---|---|
| Browser says the site can't be reached | `docker compose ps`. If `proxy` isn't `Up`, `docker compose up -d proxy` and check `docker compose logs proxy` — usually the certificate files are missing from `deploy/certs/`. |
| Certificate warning | Expected with a self-made certificate. Have it trusted on the client machines; don't train people to click through. |
| `web` shows `unhealthy` | `curl -k https://localhost/healthz`. `db unavailable` means the database is down — check `docker compose logs db`. No answer at all: `docker compose restart web`. |
| "Too many attempts" and the password is right | It's the fifteen-minute lockout. Wait, or clear it: `docker compose run --rm admin python manage.py axes_reset_username <username>`. |
| Somebody forgot their password (no AD) | `docker compose run --rm admin python manage.py changepassword <username>`, or use **Set password** on the Team screen. |
| Nobody can sign in after turning on AD | Use your local manager account — it still works. Then set `LDAP_LOG_LEVEL=DEBUG` in `.env`, `docker compose up -d web`, and read `docker compose logs -f web` while trying again. |
| `ImproperlyConfigured: DATABASE_URL is not set` | Run `manage.py` through `docker compose run --rm admin`, not on the host — Compose is what supplies the settings. |
| `password authentication failed for user "smti_web"` or `smti_owner` | The database holds a different password than the app sends. First check the app is sending what you think: `docker compose exec -T web python -c "import os;from urllib.parse import urlparse;print(repr(urlparse(os.environ['DATABASE_URL']).password))"`. If it is shorter than what is in `.env`, the password has a `;`, `/` or `#` in it — those do not survive being put into a URL. Use a password generated by `secrets.token_urlsafe(24)` and nothing else. If it matches `.env`, re-run Part 5 step 2, then `docker compose up -d web`. |
| The `grants` step failed, or `docker compose logs grants` does not end in `t` | The activity log is not locked and `web` was not started. Re-run `docker compose up -d --build` and read the `grants` output. |
| A score won't save, message about the month being scored | Working as designed — the month is closed. Reopen it if the correction is genuine. |
| No emails arriving | With `EMAIL_HOST` blank they are written to the log instead: `docker compose logs web`. Fill in the relay details in `.env` and `docker compose up -d web`. |

Anything not covered here is in `DEPLOYMENT.md`, which has the full
troubleshooting list and a go-live security checklist worth walking through
before the team starts using it.
