from django.db import models
from django.contrib.auth.models import User


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
