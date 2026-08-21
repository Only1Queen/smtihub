from django.contrib import admin

from hub.models import (Announcement, AppraisalYear, AuditEvent, Employee, Goal,
                        GoalAssignment, Kpi, Score, ScoredMonth, Task, TaskUpdate)


class KpiInline(admin.TabularInline):
    model = Kpi
    extra = 0


@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "year", "total_marks")
    list_filter = ("year",)
    inlines = [KpiInline]


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "job_title", "manager", "active")
    list_filter = ("active",)


@admin.register(AppraisalYear)
class AppraisalYearAdmin(admin.ModelAdmin):
    list_display = ("label", "start_year", "closed")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "assignee", "month_label", "kpi", "weight", "status")
    list_filter = ("status", "scoring_month")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    """Read-only in the admin too — the table is append-only everywhere."""

    list_display = ("timestamp", "actor_label", "action", "target")
    list_filter = ("action",)
    search_fields = ("target", "actor_label")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register([Announcement, GoalAssignment, Score, ScoredMonth, TaskUpdate])
admin.site.site_header = "SMTI HUB administration"
admin.site.site_title = "SMTI HUB"
