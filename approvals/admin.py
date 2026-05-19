from django.contrib import admin

from .models import ApprovalDecision, ApprovalRequest, ApprovalRequestApprover, ApprovalRequestAttachment


class ApprovalRequestAttachmentInline(admin.TabularInline):
    model = ApprovalRequestAttachment
    extra = 0
    fields = ('attachment_type', 'original_filename', 'uploaded_by', 'uploaded_at')
    readonly_fields = ('original_filename', 'uploaded_by', 'uploaded_at')

    def has_add_permission(self, request, obj=None):
        return False


class ApprovalRequestApproverInline(admin.TabularInline):
    model = ApprovalRequestApprover
    extra = 0
    fields = ('approver', 'order', 'notified_at')
    readonly_fields = ('notified_at',)


class ApprovalDecisionInline(admin.TabularInline):
    model = ApprovalDecision
    extra = 0
    fields = ('approver', 'decision', 'decided_at', 'notes')
    readonly_fields = ('approver', 'decision', 'decided_at', 'notes')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    list_display = (
        'document_version', 'requested_by', 'status',
        'due_date', 'requested_at', 'completed_at',
    )
    list_filter = ('status', 'due_date', 'requested_at')
    search_fields = (
        'document_version__document__code',
        'document_version__document__title',
    )
    readonly_fields = ('requested_at', 'completed_at')
    inlines = [ApprovalRequestApproverInline, ApprovalDecisionInline, ApprovalRequestAttachmentInline]


@admin.register(ApprovalRequestApprover)
class ApprovalRequestApproverAdmin(admin.ModelAdmin):
    list_display = ('approver', 'approval_request', 'order', 'notified_at')
    search_fields = ('approver__username',)


@admin.register(ApprovalRequestAttachment)
class ApprovalRequestAttachmentAdmin(admin.ModelAdmin):
    list_display = ('approval_request', 'attachment_type', 'original_filename', 'uploaded_by', 'uploaded_at')
    list_filter = ('attachment_type',)
    search_fields = ('original_filename', 'approval_request__document_version__document__code')
    readonly_fields = ('original_filename', 'mime_type', 'extension', 'size', 'sha256_hash', 'uploaded_by', 'uploaded_at')


@admin.register(ApprovalDecision)
class ApprovalDecisionAdmin(admin.ModelAdmin):
    list_display = ('approval_request', 'approver', 'decision', 'decided_at')
    list_filter = ('decision', 'decided_at')
    search_fields = ('approver__username',)
    readonly_fields = ('decided_at',)
