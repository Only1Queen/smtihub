from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.templatetags.static import static
from django.urls import include, path, reverse_lazy
from django.views.generic.base import RedirectView

from hub.views import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    # Analysts are not staff, so the admin's password form redirects them to a
    # login they cannot pass. These are the same views, outside the admin.
    #
    # There is deliberately no password_reset flow: accounts are not
    # self-service (the sign-in page says so), and for an AD account the
    # password does not live here at all — it is reset in Windows.
    path("accounts/password/", auth_views.PasswordChangeView.as_view(
        # Not "registration/...": django.contrib.admin ships a template of that
        # exact name and, sitting above hub in INSTALLED_APPS, wins the lookup.
        template_name="hub/password_change_form.html",
        success_url=reverse_lazy("password_change_done")), name="password_change"),
    path("accounts/password/done/", auth_views.PasswordChangeDoneView.as_view(
        template_name="hub/password_change_done.html"), name="password_change_done"),
    # Unauthenticated on purpose: the container healthcheck has no session.
    path("healthz", healthz, name="healthz"),
    # Browsers probe /favicon.ico regardless of the <link> tag; without this
    # every page load logs a 404 warning.
    path("favicon.ico", RedirectView.as_view(
        url=static("hub/favicon.svg"), permanent=True)),
    path("", include("hub.urls")),
]
