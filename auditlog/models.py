from django.db import models
from django.contrib.auth.models import User


class AuditLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
        verbose_name='Utente',
    )
    action = models.CharField(max_length=50, verbose_name='Azione')
    app_label = models.CharField(max_length=50, verbose_name='App')
    model_name = models.CharField(max_length=100, verbose_name='Modello')
    object_id = models.CharField(max_length=50, verbose_name='ID oggetto')
    object_repr = models.CharField(max_length=255, verbose_name='Oggetto')
    changes = models.JSONField(null=True, blank=True, verbose_name='Modifiche')
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name='Indirizzo IP')

    class Meta:
        verbose_name = 'Audit log'
        verbose_name_plural = 'Audit log'
        ordering = ['-timestamp']

    def __str__(self):
        username = self.user.username if self.user else 'sistema'
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {username} – {self.action} {self.model_name} #{self.object_id}"
