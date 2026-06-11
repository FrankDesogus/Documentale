from django.contrib import admin
from django.utils import timezone

from .models import AuditLog, HistoricalEvidence, HistoricalImportBatch, HistoricalRecord


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


# ---------------------------------------------------------------------------
# Sanatoria storica
# ---------------------------------------------------------------------------

@admin.register(HistoricalImportBatch)
class HistoricalImportBatchAdmin(admin.ModelAdmin):
    list_display = ('code', 'status', 'created_by', 'created_at', 'completed_at')
    list_filter = ('status',)
    search_fields = ('code', 'description', 'notes')
    readonly_fields = ('created_at', 'completed_at', 'created_by', 'completed_by')
    date_hierarchy = 'created_at'

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class HistoricalEvidenceInline(admin.TabularInline):
    model = HistoricalEvidence
    extra = 0
    readonly_fields = ('uploaded_by', 'uploaded_at')
    fields = ('file', 'description', 'uploaded_by', 'uploaded_at')

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)


def _mark_verified(modeladmin, request, queryset):
    """Azione admin: segna i record storici come verificati."""
    from auditlog.permissions import can_verify_historical_records
    if not can_verify_historical_records(request.user):
        modeladmin.message_user(
            request,
            'Non hai i permessi per verificare record storici.',
            level='error',
        )
        return
    now = timezone.now()
    queryset.filter(is_verified=False).update(
        is_verified=True,
        verified_by=request.user,
        verified_at=now,
    )
    modeladmin.message_user(request, f'{queryset.count()} record marcati come verificati.')


_mark_verified.short_description = 'Segna come verificato'


@admin.register(HistoricalRecord)
class HistoricalRecordAdmin(admin.ModelAdmin):
    list_display = (
        'historical_date',
        'get_date_precision_badge',
        'event_type',
        'get_actor_display',
        'target_repr',
        'import_batch',
        'is_verified',
        'recorded_by',
        'recorded_at',
    )
    list_filter = ('event_type', 'date_precision', 'is_verified', 'import_batch')
    search_fields = (
        'historical_actor_name',
        'target_repr',
        'source_description',
        'notes',
    )
    readonly_fields = (
        'recorded_by',
        'recorded_at',
        'verified_by',
        'verified_at',
    )
    date_hierarchy = 'recorded_at'
    inlines = [HistoricalEvidenceInline]
    actions = [_mark_verified]

    fieldsets = (
        ('Evento storico', {
            'fields': (
                'import_batch',
                'event_type',
                'historical_date',
                'date_precision',
            ),
        }),
        ('Attore storico', {
            'fields': (
                'historical_actor_user',
                'historical_actor_name',
            ),
        }),
        ('Oggetto target', {
            'fields': (
                'target_app',
                'target_model',
                'target_id',
                'target_repr',
            ),
            'classes': ('collapse',),
        }),
        ('Fonte e note', {
            'fields': (
                'source_description',
                'notes',
            ),
        }),
        ('Metadati tecnici (sola lettura)', {
            'fields': (
                'recorded_by',
                'recorded_at',
                'is_verified',
                'verified_by',
                'verified_at',
            ),
            'classes': ('collapse',),
        }),
    )

    @admin.display(description='Precisione', ordering='date_precision')
    def get_date_precision_badge(self, obj):
        return obj.get_date_precision_display()

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.recorded_by = request.user
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        from auditlog.permissions import can_use_sanatoria
        return can_use_sanatoria(request.user) or request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        from auditlog.permissions import can_view_historical_records
        return can_view_historical_records(request.user)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(HistoricalEvidence)
class HistoricalEvidenceAdmin(admin.ModelAdmin):
    list_display = ('historical_record', 'description', 'uploaded_by', 'uploaded_at')
    list_filter = ('uploaded_at',)
    search_fields = ('description', 'historical_record__target_repr')
    readonly_fields = ('uploaded_by', 'uploaded_at')

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.uploaded_by = request.user
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        from auditlog.permissions import can_use_sanatoria
        return can_use_sanatoria(request.user) or request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
