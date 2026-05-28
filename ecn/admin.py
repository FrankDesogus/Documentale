from django.contrib import admin

from .models import ChangeNotice, ChangeNoticeApprover, ChangeNoticeAttachment, ChangeNoticeDecision


class ChangeNoticeApproverInline(admin.TabularInline):
    model = ChangeNoticeApprover
    extra = 0
    fields = ('user', 'order', 'is_required', 'created_at')
    readonly_fields = ('created_at',)


class ChangeNoticeDecisionInline(admin.TabularInline):
    model = ChangeNoticeDecision
    extra = 0
    fields = ('user', 'approver', 'decision', 'comment', 'decided_at')
    readonly_fields = ('user', 'approver', 'decision', 'comment', 'decided_at')

    def has_add_permission(self, request, obj=None):
        return False


class ChangeNoticeAttachmentInline(admin.TabularInline):
    model = ChangeNoticeAttachment
    extra = 0
    fields = ('title', 'original_filename', 'extension', 'size', 'uploaded_by', 'uploaded_at')
    readonly_fields = ('original_filename', 'extension', 'size', 'uploaded_by', 'uploaded_at')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ChangeNotice)
class ChangeNoticeAdmin(admin.ModelAdmin):
    list_display = (
        'code', 'title', 'document', 'status', 'ccb_policy', 'motivation',
        'ccb_class', 'proposed_by', 'proposed_at', 'ccb_reviewed_at', 'closed_at',
    )
    list_filter = ('status', 'ccb_policy', 'motivation', 'ccb_class', 'proposed_at')
    search_fields = (
        'code', 'title',
        'document__code', 'document__title',
        'proposed_by__username', 'proposed_by__last_name',
    )
    readonly_fields = (
        'proposed_at', 'submitted_at', 'ccb_reviewed_at',
        'executed_at', 'closed_at', 'updated_at',
        'proposed_by', 'created_by',
    )
    fieldsets = (
        ('Identificazione', {
            'fields': ('code', 'title', 'status'),
        }),
        ('Riferimenti', {
            'fields': ('document', 'document_version', 'project', 'commessa'),
        }),
        ('Proposta', {
            'fields': ('description', 'motivation', 'motivation_detail'),
        }),
        ('Proponente', {
            'fields': ('proposed_by', 'proposed_at', 'submitted_at', 'created_by'),
        }),
        ('Politica CCB', {
            'fields': ('ccb_policy',),
        }),
        ('Valutazione CCB', {
            'fields': (
                'ccb_class',
                'ccb_requirements',
                'ccb_technical_impact',
                'ccb_cost_impact',
                'ccb_time_impact',
                'ccb_quality_impact',
                'ccb_other_impact',
                'ccb_notes',
                'ccb_reviewed_by',
                'ccb_reviewed_at',
            ),
            'classes': ('collapse',),
        }),
        ('Esecuzione', {
            'fields': ('executed_version', 'executed_at'),
            'classes': ('collapse',),
        }),
        ('Chiusura', {
            'fields': ('close_notes', 'closed_by', 'closed_at'),
            'classes': ('collapse',),
        }),
        ('Metadati', {
            'fields': ('updated_at',),
            'classes': ('collapse',),
        }),
    )
    inlines = [ChangeNoticeApproverInline, ChangeNoticeDecisionInline, ChangeNoticeAttachmentInline]


@admin.register(ChangeNoticeAttachment)
class ChangeNoticeAttachmentAdmin(admin.ModelAdmin):
    list_display = (
        'change_notice', 'title', 'original_filename',
        'extension', 'size', 'uploaded_by', 'uploaded_at',
    )
    list_filter = ('uploaded_at',)
    search_fields = (
        'original_filename', 'title',
        'change_notice__code', 'change_notice__title',
    )
    readonly_fields = ('original_filename', 'extension', 'size', 'uploaded_by', 'uploaded_at')
