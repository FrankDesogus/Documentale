from django.contrib import admin

from .models import NotificationLog


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'subject', 'is_sent', 'sent_at', 'approval_request')
    list_filter = ('is_sent',)
    search_fields = ('recipient__username', 'subject')
    readonly_fields = ('sent_at',)
