from django.conf import settings
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Notification(models.Model):
    CATEGORY_ACTION = 'action_required'
    CATEGORY_INFO = 'information'
    CATEGORY_CHOICES = [
        (CATEGORY_ACTION, 'Azione richiesta'),
        (CATEGORY_INFO, 'Informazione'),
    ]

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='app_notifications',
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    notification_type = models.CharField(max_length=60)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    target_url = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'read_at']),
            models.Index(fields=['recipient', 'created_at']),
        ]

    @property
    def is_read(self):
        return self.read_at is not None

    def mark_as_read(self):
        if not self.read_at:
            self.read_at = timezone.now()
            self.save(update_fields=['read_at'])


class NotificationLog(models.Model):
    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notification_logs',
        verbose_name='Destinatario',
    )
    subject = models.CharField(max_length=255, verbose_name='Oggetto')
    body = models.TextField(verbose_name='Corpo')
    sent_at = models.DateTimeField(auto_now_add=True)
    is_sent = models.BooleanField(default=False, verbose_name='Inviata')
    error_message = models.TextField(blank=True, verbose_name='Errore')
    # FK circolare evitata con stringa per non creare dipendenza circolare
    approval_request = models.ForeignKey(
        'approvals.ApprovalRequest',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name='Richiesta di approvazione',
    )

    class Meta:
        verbose_name = 'Log notifica'
        verbose_name_plural = 'Log notifiche'
        ordering = ['-sent_at']

    def __str__(self):
        status = 'OK' if self.is_sent else 'ERRORE'
        return f"[{status}] {self.recipient.username} – {self.subject}"
