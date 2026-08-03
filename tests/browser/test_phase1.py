#!/usr/bin/env python
"""Phase 1 browser tests — SMTI HUB.

Covers the three journeys that make Phase 1 usable:
  1. Sign in (and the failure path, and the access boundary)
  2. Create a team member, set their password, and have them sign in
  3. Assign a goal to that member and confirm it changes what is scored

Runs against the Docker deployment over TLS with a self-signed certificate, so
the context ignores HTTPS errors — that is the deployment's documented state,
not something the test papers over.

    BASE=https://localhost MANAGER_PW=... .venv/bin/python tests/browser/test_phase1.py
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "https://localhost")
MANAGER_USER = os.environ.get("MANAGER_USER", "manager")
MANAGER_PW = os.environ.get("MANAGER_PW", "")
SLOW_MO = int(os.environ.get("SLOW_MO", "220"))
HEADED = os.environ.get("HEADED", "1") != "0"

SHOTS = Path(__file__).parent / "screenshots"
SHOTS.mkdir(exist_ok=True)


def _find_chrome():
    """Newest cached Playwright Chromium, or CHROME_PATH if set."""
    explicit = os.environ.get("CHROME_PATH")
    if explicit:
        return explicit
    cache = Path.home() / ".cache" / "ms-playwright"
    builds = sorted(cache.glob("chromium-*/chrome-linux*/chrome"),
                    key=lambda p: int(re.search(r"chromium-(\d+)", str(p)).group(1)))
    return str(builds[-1]) if builds else None


CHROME_PATH = _find_chrome()

# Unique per run so re-running does not collide with an existing account.
STAMP = datetime.now().strftime("%H%M%S")
NEW_USER = f"k.okonkwo{STAMP}"
NEW_NAME = f"K Okonkwo{STAMP}"
NEW_PW = "shift-handover-4471"


class Results:
    def __init__(self):
        self.rows = []

    def check(self, ok, name, detail=""):
        self.rows.append((bool(ok), name, detail))
        mark = "PASS" if ok else "FAIL"
        line = f"  [{mark}] {name}"
        if detail:
            line += f"  — {detail}"
        print(line, flush=True)
        return bool(ok)

    def shot(self, page, label):
        path = SHOTS / f"{label}.png"
        try:
            page.screenshot(path=str(path), full_page=True)
        except Exception:
            pass
        return path

    @property
    def failed(self):
        return [r for r in self.rows if not r[0]]

    def summary(self):
        passed = len(self.rows) - len(self.failed)
        print("\n" + "=" * 62)
        print(f"  {passed} passed, {len(self.failed)} failed, {len(self.rows)} total")
        if self.failed:
            print("\n  Failures:")
            for _, name, detail in self.failed:
                print(f"    - {name}  {detail}")
        print("=" * 62)
        return 1 if self.failed else 0


R = Results()


def sign_in(page, username, password):
    page.goto(f"{BASE}/accounts/login/", wait_until="domcontentloaded")
    page.wait_for_selector("input[name='username']")
    page.fill("input[name='username']", username)
    page.fill("input[name='password']", password)
    with page.expect_navigation(wait_until="domcontentloaded"):
        page.locator(".auth-form button[type='submit']").click()


def submit_form(page, text):
    """Click a form's submit button by its label.

    Never use a bare button[type=submit] selector inside the app shell: the
    topbar's "Sign out" is also a submit button and precedes the form in the
    DOM, so the test signs itself out instead of saving.
    """
    btn = page.locator(f"form button[type='submit']:has-text('{text}')")
    with page.expect_navigation(wait_until="domcontentloaded"):
        btn.first.click()


def analyst_rows(page):
    """Roster rows only — the empty state is also a <tr>."""
    return page.locator("table tbody tr:has(a[href*='/score/'])")


# ── 1. Login ────────────────────────────────────────────────────────────────

def test_login(page):
    print("\n1. LOGIN")

    page.goto(f"{BASE}/accounts/login/", wait_until="domcontentloaded")
    R.check(page.locator(".auth-card").is_visible(), "login card renders")
    R.check(page.title().startswith("Sign in"), "page title", page.title())

    # Every input carries a label — a screen reader must be able to name them.
    labelled = page.evaluate("""() => {
        const ins = [...document.querySelectorAll('.auth-form input:not([type=hidden])')];
        return ins.every(i => document.querySelector(`label[for="${i.id}"]`)
                              || i.getAttribute('aria-label'));
    }""")
    R.check(labelled, "every visible input has a label")

    autocomplete = page.evaluate("""() => ({
        user: document.getElementById('id_username')?.autocomplete,
        pass: document.getElementById('id_password')?.autocomplete,
    })""")
    R.check(autocomplete["user"] == "username" and autocomplete["pass"] == "current-password",
            "autocomplete set so password managers work", str(autocomplete))

    focused = page.evaluate("document.activeElement && document.activeElement.id")
    R.check(focused == "id_username", "username is autofocused", f"got {focused!r}")

    # Wrong password must fail closed and say so.
    sign_in(page, MANAGER_USER, "definitely-not-the-password")
    err = page.locator("[role='alert']")
    R.check(err.count() > 0 and err.first.is_visible(), "bad password shows an error")
    R.check("/accounts/login/" in page.url, "bad password does not sign in", page.url)
    R.shot(page, "01-login-error")

    # Anonymous users are bounced to the login page.
    page.goto(f"{BASE}/goals/", wait_until="domcontentloaded")
    R.check("/accounts/login/" in page.url, "anonymous request redirects to login", page.url)

    # The real thing.
    sign_in(page, MANAGER_USER, MANAGER_PW)
    R.check(page.url.rstrip("/") == BASE.rstrip("/"), "manager lands on Team", page.url)
    R.check(page.locator("h1:has-text('Team')").is_visible(), "Team heading visible")
    R.check(page.locator("nav button:has-text('Goals')").count() > 0, "manager nav shows Goals")
    R.shot(page, "02-team-signed-in")


# ── 2. Create a team member ─────────────────────────────────────────────────

def test_create_member(page):
    print("\n2. CREATE TEAM MEMBER")

    page.goto(f"{BASE}/", wait_until="domcontentloaded")
    before = analyst_rows(page).count()

    page.click("a[href$='/people/new/'] button, a[href$='/people/new/']")
    page.wait_for_url("**/people/new/")
    R.check(page.locator("h1").inner_text().strip() == "Add analyst", "add-analyst form opens")

    page.fill("input[name='full_name']", NEW_NAME)
    page.fill("input[name='job_title']", "SOC Analyst II")
    page.fill("input[name='email']", f"{NEW_USER}@example.org")
    page.fill("input[name='username']", NEW_USER)
    submit_form(page, "Save")

    R.check(page.url.rstrip("/") == BASE.rstrip("/"), "redirects back to Team", page.url)
    row = page.locator(f"table tbody tr:has-text('{NEW_NAME}')")
    R.check(row.count() == 1, "new analyst appears in the roster")
    after = analyst_rows(page).count()
    R.check(after == before + 1, "roster grew by exactly one", f"{before} -> {after}")
    R.shot(page, "03-member-created")

    # Creating an account is an audited act.
    page.goto(f"{BASE}/activity/", wait_until="domcontentloaded")
    R.check(page.locator(f"tr:has-text('employee.create'):has-text('{NEW_NAME}')").count() > 0,
            "account creation is in the audit log")

    # A new account has no usable password until the manager sets one.
    page.goto(f"{BASE}/", wait_until="domcontentloaded")
    row = page.locator(f"table tbody tr:has-text('{NEW_NAME}')")
    row.locator("a[href*='/password/']").first.click()
    page.wait_for_url("**/password/")
    R.check(page.locator("h1").inner_text().startswith("Set password"), "password form opens")

    # Weak passwords are refused.
    page.fill("input[name='password1']", "short")
    page.fill("input[name='password2']", "short")
    submit_form(page, "Save")
    R.check("/password/" in page.url, "weak password is rejected", page.url)

    # Mismatched pair is refused.
    page.fill("input[name='password1']", NEW_PW)
    page.fill("input[name='password2']", NEW_PW + "-different")
    submit_form(page, "Save")
    R.check("/password/" in page.url, "mismatched confirmation is rejected", page.url)
    R.shot(page, "04-password-validation")

    # Correct pair is accepted.
    page.fill("input[name='password1']", NEW_PW)
    page.fill("input[name='password2']", NEW_PW)
    submit_form(page, "Save")
    R.check(page.url.rstrip("/") == BASE.rstrip("/"), "password set, back on Team", page.url)


def test_member_can_sign_in(browser):
    print("\n3. THE NEW MEMBER CAN SIGN IN")

    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    sign_in(page, NEW_USER, NEW_PW)
    R.check("/accounts/login/" not in page.url, "new analyst signs in", page.url)
    # Signing in must land somewhere they are allowed to be, not on a 403.
    R.check(page.url.rstrip("/").endswith("/me"),
            "analyst lands on their own record, not a forbidden page", page.url)
    R.check(page.locator("nav button:has-text('My appraisal')").count() > 0,
            "analyst sees their own nav")
    R.check(page.locator("nav button:has-text('Goals')").count() == 0,
            "analyst does NOT see manager-only Goals")
    R.shot(page, "05-analyst-view")

    # The access boundary, not just the hidden link.
    for path, label in [("/goals/", "Goals"), ("/tasks/", "Tasks"),
                        ("/summary/", "Year summary"), ("/activity/", "Activity")]:
        resp = page.goto(f"{BASE}{path}", wait_until="domcontentloaded")
        R.check(resp.status == 403, f"analyst is 403 on {label}", f"status {resp.status}")

    ctx.close()
    return True


# ── 4. Goal assignment ──────────────────────────────────────────────────────

def test_goal_assignment(page, browser):
    print("\n4. GOAL ASSIGNMENT")

    page.goto(f"{BASE}/goals/", wait_until="domcontentloaded")
    R.check(page.locator(".goal-card").count() == 5, "five seeded goals render",
            f"{page.locator('.goal-card').count()} cards")

    # The new member starts unassigned everywhere.
    unassigned = page.locator(f".chip.off:has-text('{NEW_NAME}')").count()
    R.check(unassigned == 5, "new analyst starts unassigned on all five goals",
            f"{unassigned} greyed chips")
    R.shot(page, "06-goals-before")

    # Assign goal A only — a partial assignment is what proves exclusion works.
    page.locator(".goal-card:has(.gc-id:text-is('A')) a[href*='/edit/']").first.click()
    page.wait_for_url("**/edit/**")
    R.check(page.locator("h1").inner_text().startswith("Edit goal A"), "goal A edit form opens")

    box = page.locator(f"label:has-text('{NEW_NAME}') input[type=checkbox]")
    if box.count() == 0:
        box = page.locator("input[name='assignees']").nth(-1)
    R.check(box.count() > 0, "assignee checkbox present for the new analyst")
    box.first.check()
    R.check(box.first.is_checked(), "checkbox ticked")
    R.shot(page, "07-assigning")

    submit_form(page, "Save goal")
    R.check(page.url.endswith("/goals/"), "redirects back to Goals", page.url)

    # Assignment decides whose appraisal a goal counts toward, so the log must
    # record it. It previously wrote before == after, making the change invisible.
    page.goto(f"{BASE}/activity/", wait_until="domcontentloaded")
    audit_row = page.locator("tr:has-text('goal.update')").first
    R.check(audit_row.count() > 0, "goal edit is audited")
    audit_text = audit_row.inner_text() if audit_row.count() else ""
    R.check(NEW_NAME in audit_text,
            "audit records WHO the goal was assigned to",
            audit_text.replace("\n", " ")[:110])
    R.check("→" in audit_text, "audit shows the assignment change as before → after")

    page.goto(f"{BASE}/goals/", wait_until="domcontentloaded")
    card_a = page.locator(".goal-card:has(.gc-id:text-is('A'))")
    R.check(card_a.locator(f".chip:not(.off):has-text('{NEW_NAME}')").count() == 1,
            "goal A now shows the analyst as assigned")
    still_off = page.locator(f".chip.off:has-text('{NEW_NAME}')").count()
    R.check(still_off == 4, "the other four goals remain unassigned", f"{still_off} greyed")
    R.shot(page, "08-goals-after")

    # Assignment must change what is actually scored, not just a chip.
    page.goto(f"{BASE}/", wait_until="domcontentloaded")
    row = page.locator(f"table tbody tr:has-text('{NEW_NAME}')")
    row.locator("a[href*='/score/']").first.click()
    page.wait_for_url("**/score/**")

    codes = page.locator(".goal-code").all_inner_texts()
    R.check([c.strip() for c in codes] == ["A"],
            "score entry shows ONLY goal A for this analyst", f"got {codes}")

    kpis = page.locator("td .mono[style*='--accent']").all_inner_texts()
    a_kpis = [k.strip() for k in kpis if k.strip().startswith("A")]
    R.check(len(a_kpis) == 3, "all three A KPIs are scorable", f"{a_kpis}")
    R.check(not any(k.strip().startswith(("B", "C", "D", "E")) for k in kpis),
            "no KPI from an unassigned goal appears")

    progress = page.locator("#saveline").inner_text()
    m = re.search(r"of (\d+) scored", progress)
    R.check(m and m.group(1) == "3", "denominator is 3, not 18 — unassigned goals excluded",
            progress.strip().replace("\n", " ")[:80])
    R.shot(page, "09-score-entry-scoped")

    # And the analyst sees the goal on their own record.
    ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    p2 = ctx.new_page()
    sign_in(p2, NEW_USER, NEW_PW)
    p2.goto(f"{BASE}/me/", wait_until="domcontentloaded")
    R.check(p2.locator(".goal-toggle").count() == 1,
            "analyst sees exactly the one goal assigned to them",
            f"{p2.locator('.goal-toggle').count()} goal rows")
    R.check("Operational Tasks" in p2.content(), "and it is goal A")
    R.shot(p2, "10-analyst-sees-goal")
    ctx.close()


# ── UX checks ───────────────────────────────────────────────────────────────

def test_ux(page):
    print("\n5. UX CHECKS")

    for label, url in [("login", "/accounts/login/"), ("team", "/"), ("goals", "/goals/")]:
        page.goto(f"{BASE}{url}", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        no_hscroll = page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        R.check(no_hscroll, f"{label}: no horizontal page scroll")

    # Icon-only controls need an accessible name.
    page.goto(f"{BASE}/settings/", wait_until="domcontentloaded")
    nameless = page.evaluate("""() => [...document.querySelectorAll('button')]
        .filter(b => !b.textContent.trim() && !b.getAttribute('aria-label'))
        .map(b => b.outerHTML.slice(0, 70))""")
    R.check(len(nameless) == 0, "no unlabelled icon-only buttons", str(nameless[:2]))

    # Focus must be visible for keyboard users. Tab rather than .focus() so
    # :focus-visible applies, and wait out the CSS transition — reading the
    # computed style immediately returns the ring at zero and passes falsely.
    page.goto(f"{BASE}/accounts/login/", wait_until="domcontentloaded")
    page.keyboard.press("Tab")
    page.wait_for_timeout(400)
    ring = page.evaluate("""() => {
        const s = getComputedStyle(document.activeElement);
        const shadow = s.boxShadow || "none";
        // A ring counts only if it has non-zero size and is not transparent.
        const sized = /[1-9]\d*(\.\d+)?px/.test(shadow);
        const transparent = /\/\s*0\s*\)|rgba\([^)]*,\s*0\s*\)/.test(shadow);
        return {el: document.activeElement.id || document.activeElement.tagName,
                outline: s.outlineStyle, outlineWidth: s.outlineWidth,
                shadow, visible: (s.outlineStyle !== "none" && s.outlineWidth !== "0px")
                                 || (sized && !transparent)};
    }""")
    R.check(ring["visible"], "focused control has a genuinely visible focus ring",
            f"{ring['el']}: outline={ring['outline']} {ring['outlineWidth']}, shadow={ring['shadow'][:60]}")

    # Responsive: the shell must not force sideways scrolling on a phone.
    for w, h, name in [(375, 667, "mobile"), (768, 1024, "tablet"), (1440, 900, "desktop")]:
        page.set_viewport_size({"width": w, "height": h})
        page.goto(f"{BASE}/accounts/login/", wait_until="domcontentloaded")
        ok = page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        R.check(ok, f"login has no horizontal scroll at {name} ({w}px)")
    page.set_viewport_size({"width": 1440, "height": 900})


def main():
    if not MANAGER_PW:
        print("MANAGER_PW is not set. Refusing to guess.")
        return 2

    print(f"SMTI HUB — Phase 1 browser tests\n  target: {BASE}\n  new account: {NEW_USER}")

    with sync_playwright() as pw:
        launch = {"headless": not HEADED, "slow_mo": SLOW_MO}
        # Fall back to an already-downloaded Chromium when the pinned build is
        # missing — a 177 MB download is not a prerequisite for running tests.
        if CHROME_PATH:
            launch["executable_path"] = CHROME_PATH
        browser = pw.chromium.launch(**launch)
        # Self-signed certificate: the documented state of this deployment.
        ctx = browser.new_context(ignore_https_errors=True,
                                  viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            test_login(page)
            test_create_member(page)
            test_member_can_sign_in(browser)
            test_goal_assignment(page, browser)
            test_ux(page)
        except Exception as exc:
            R.check(False, "unhandled error", f"{type(exc).__name__}: {exc}")
            R.shot(page, "99-crash")
            raise
        finally:
            code = R.summary()
            print(f"\n  screenshots: {SHOTS}")
            ctx.close()
            browser.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
