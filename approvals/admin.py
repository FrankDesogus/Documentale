from django.contrib import admin

from .models import ApprovalRequest, ApprovalRequestApprover, ApprovalDecision


class ApprovalRequestApproverInline(admin.TabularInline):
    model = ApprovalRequestApprover
    extra = 0
    fields = ('approver', 'order', 'notified_at')


class ApprovalDecisionInline(admin.TabularInline):
    model = ApprovalDecision
    extra = 0
    fields = ('approver', 'decision', 'decided_at', 'notes')
    readonly_fields = ('decided_at',)


@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'document_version', 'requested_by', 'status', 'requested_at', 'due_date', 'completed_at')
    list_filter = ('status', 'due_date')
    search_fields = ('document_version__document__code',)
    inlines = [ApprovalRequestApproverInline, ApprovalDecisionInline]


@admin.register(ApprovalRequestApprover)
class ApprovalRequestApproverAdmin(admin.ModelAdmin):
    list_display = ('approver', 'approval_request', 'order', 'notified_at')
    search_fields = ('approver__username',)


@admin.register(ApprovalDecision)
class ApprovalDecisionAdmin(admin.ModelAdmin):
    list_display = ('approver', 'approval_request', 'decision', 'decided_at')
    list_filter = ('decision',)
    search_fields = ('approver__username',)
