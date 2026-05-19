from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = (
        'timestamp', 'user', 'action',
        'app_label', 'model_name', 'object_repr',
    )
    list_filter = ('action', 'app_label', 'model_name', 'timestamp')
    search_fields = ('action', 'object_repr', 'model_name', 'app_label')
    date_hierarchy = 'timestamp'
    readonly_fields = (
        'timestamp', 'user', 'action',
        'app_label', 'model_name', 'object_id', 'object_repr',
        'changes', 'ip_address',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
