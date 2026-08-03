# Browser tests

Playwright, run against a **running deployment** (not a test database), so they
exercise the real thing: TLS, the proxy, and PostgreSQL with the restricted role.

```bash
BASE=https://localhost MANAGER_PW='<manager password>' \
  .venv/bin/python tests/browser/test_phase1.py
```

| Variable | Default | Notes |
|---|---|---|
| `BASE` | `https://localhost` | Self-signed certs are accepted deliberately |
| `MANAGER_PW` | — | Required; the suite refuses to guess |
| `HEADED` | `1` | `0` for headless (CI) |
| `SLOW_MO` | `220` | Milliseconds between steps when watching |
| `CHROME_PATH` | auto | Newest cached Playwright Chromium is found automatically |

Each run creates a uniquely-named analyst so it can be run repeatedly. **It
writes to whatever database it points at** — remove the accounts afterwards, or
point it at a scratch deployment.

Screenshots land in `screenshots/`, including on failure.

## Two selector traps this suite documents

- `button[type=submit]` is ambiguous inside the app shell: the topbar's **Sign
  out** is also a submit button and comes first in the DOM, so a bare selector
  signs the test out instead of saving. Use `submit_form(page, "Save")`.
- `table tbody tr` counts the empty-state row too. Use `analyst_rows(page)`.
