from django.db import models
from django.contrib.auth.models import User


class ApprovalRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'In attesa'
        APPROVED = 'APPROVED', 'Approvato'
        REJECTED = 'REJECTED', 'Rifiutato'
        CANCELLED = 'CANCELLED', 'Annullato'

    # FK a stringa per evitare importazione circolare con l'app documents
    document_version = models.ForeignKey(
        'documents.DocumentVersion',
        on_delete=models.CASCADE,
        related_name='approval_requests',
        verbose_name='Revisione documento',
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='submitted_approval_requests',
        verbose_name='Richiesto da',
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name='Stato',
    )
    due_date = models.DateField(null=True, blank=True, verbose_name='Scadenza approvazione')
    notes = models.TextField(blank=True, verbose_name='Note')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Completato il')

    class Meta:
        verbose_name = 'Richiesta di approvazione'
        verbose_name_plural = 'Richieste di approvazione'
        ordering = ['-requested_at']

    def __str__(self):
        return f"Approvazione {self.document_version} [{self.get_status_display()}]"


class ApprovalRequestApprover(models.Model):
    approval_request = models.ForeignKey(
        ApprovalRequest,
        on_delete=models.CASCADE,
        related_name='approvers',
        verbose_name='Richiesta',
    )
    approver = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='approval_assignments',
        verbose_name='Approvatore',
    )
    order = models.PositiveSmallIntegerField(default=0, verbose_name='Ordine')
    notified_at = models.DateTimeField(null=True, blank=True, verbose_name='Notificato il')

    class Meta:
        verbose_name = 'Approvatore'
        verbose_name_plural = 'Approvatori'
        ordering = ['approval_request', 'order']
        unique_together = [('approval_request', 'approver')]

    def __str__(self):
        return f"{self.approver.get_full_name() or self.approver.username} → {self.approval_request}"


class ApprovalDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVED = 'APPROVED', 'Approvato'
        REJECTED = 'REJECTED', 'Rifiutato'

    approval_request = models.ForeignKey(
        ApprovalRequest,
        on_delete=models.CASCADE,
        related_name='decisions',
        verbose_name='Richiesta',
    )
    approver = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='approval_decisions',
        verbose_name='Approvatore',
    )
    decision = models.CharField(max_length=20, choices=Decision.choices, verbose_name='Decisione')
    decided_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, verbose_name='Note')

    class Meta:
        verbose_name = 'Decisione di approvazione'
        verbose_name_plural = 'Decisioni di approvazione'
        ordering = ['-decided_at']
        unique_together = [('approval_request', 'approver')]

    def __str__(self):
        return f"{self.approver.get_full_name() or self.approver.username}: {self.get_decision_display()}"
