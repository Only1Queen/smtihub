"""Authentication events, audited, and Active Directory provisioning.

Two things happen here, both on the way in:

  * every sign-in, sign-out, failed attempt and lockout becomes an AuditEvent —
    without this the audit trail covers what people did but not who got in, and
    "who was in the system that night" has no answer;
  * an AD account arriving for the first time gets its Employee record and its
    role, so nobody has to be added by hand in two places.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.auth.signals import (user_logged_in, user_logged_out,
                                         user_login_failed)
from django.dispatch import receiver

from hub.audit import log_event
from hub.permissions import MANAGER_GROUP

log = logging.getLogger(__name__)

# Membership marks an account as sourced from AD, so sync_ad knows which
# accounts it owns and leaves the break-glass local ones alone.
LDAP_GROUP = "LDAP"


def client_ip(request):
    """One proxy in front of us (nginx), so the client is the first hop."""
    if request is None:
        return ""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.META.get("REMOTE_ADDR", "")[:45]


def in_group(name):
    return Group.objects.get_or_create(name=name)[0]


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    provision(user)
    log_event(user, "auth.login", user.get_username(), ip=client_ip(request),
              source="ad" if hasattr(user, "ldap_user") else "local")


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_event(user, "auth.logout", user.get_username(), ip=client_ip(request))


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    """Actor is None on purpose: nobody has proved who they are yet. The
    username tried is recorded as the target, never the password."""
    log_event(None, "auth.login_failed", credentials.get("username", "(none)"),
              ip=client_ip(request))


def on_locked_out(sender, request, username=None, **kwargs):
    log_event(None, "auth.lockout", username or "(none)", ip=client_ip(request),
              limit=settings.AXES_FAILURE_LIMIT)


def provision(user):
    """Give an AD account its Employee record and role on the way in.

    Runs for local accounts too, where it is a no-op after the first login —
    an account without an Employee row cannot open a single page in the app.
    """
    ldap_user = getattr(user, "ldap_user", None)
    if ldap_user is not None:
        sync_ldap_groups(user, ldap_user)

    from hub.models import Employee  # imported late: signals load before apps are ready

    employee, created = Employee.objects.get_or_create(
        user=user, defaults={"job_title": ldap_attr(ldap_user, "title") or "Analyst"})
    if created:
        log_event(None, "employee.provisioned", employee.name,
                  after="active", source="ad" if ldap_user else "local")


def sync_ldap_groups(user, ldap_user):
    """AD is the source of truth for who is a manager. Losing the group in AD
    removes the role here at the next sign-in, which is the point of using AD."""
    user.groups.add(in_group(LDAP_GROUP))

    manager_dn = (settings.AUTH_LDAP_MANAGER_GROUP_DN or "").lower()
    if not manager_dn:
        return
    try:
        is_manager = manager_dn in {dn.lower() for dn in ldap_user.group_dns}
    except Exception:  # group search misconfigured or the DC is unhappy
        log.exception("LDAP: group lookup failed for %s; leaving role unchanged",
                      user.get_username())
        return

    group = in_group(MANAGER_GROUP)
    had = user.groups.filter(pk=group.pk).exists()
    if is_manager and not had:
        user.groups.add(group)
        log_event(None, "role.granted", user.get_username(), after=MANAGER_GROUP, source="ad")
    elif had and not is_manager:
        user.groups.remove(group)
        log_event(None, "role.revoked", user.get_username(), before=MANAGER_GROUP, source="ad")


def ldap_attr(ldap_user, name):
    if ldap_user is None:
        return ""
    try:
        return (ldap_user.attrs.get(name) or [""])[0]
    except Exception:
        return ""


def connect_axes():
    """Wired in AppConfig.ready(); axes is not importable at module scope during
    some management commands."""
    try:
        from axes.signals import user_locked_out
    except ImportError:  # pragma: no cover - axes is a hard dependency
        return
    user_locked_out.connect(on_locked_out, dispatch_uid="hub.on_locked_out")
