from django.db import models
from django.contrib.auth.models import User


class ApprovalRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'In attesa'
        APPROVED = 'APPROVED', 'Approvato'
        REJECTED = 'REJECTED', 'Rifiutato'
        CANCELLED = 'CANCELLED', 'Annullato'

    class Policy(models.TextChoices):
        ANY = 'any', 'Basta un approvatore'
        ALL = 'all', 'Devono approvare tutti'
        SEQUENTIAL = 'sequential', 'Approvazione sequenziale'

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
    approval_policy = models.CharField(
        max_length=20,
        choices=Policy.choices,
        default=Policy.ALL,
        verbose_name='Modalità approvazione',
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
    class ApproverStatus(models.TextChoices):
        PENDING = 'pending', 'In attesa'
        APPROVED = 'approved', 'Approvato'
        REJECTED = 'rejected', 'Rifiutato'

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
    status = models.CharField(
        max_length=20,
        choices=ApproverStatus.choices,
        default=ApproverStatus.PENDING,
        verbose_name='Stato',
    )
    notified_at = models.DateTimeField(null=True, blank=True, verbose_name='Notificato il')
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name='Deciso il')

    class Meta:
        verbose_name = 'Approvatore'
        verbose_name_plural = 'Approvatori'
        ordering = ['approval_request', 'order']
        unique_together = [('approval_request', 'approver')]

    def __str__(self):
        return f"{self.approver.get_full_name() or self.approver.username} → {self.approval_request}"


class ApprovalRequestAttachment(models.Model):
    class AttachmentType(models.TextChoices):
        SIGNATURE_TEMPLATE = 'signature_template', 'Modello da firmare'

    approval_request = models.ForeignKey(
        ApprovalRequest,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='Richiesta di approvazione',
    )
    file = models.FileField(
        upload_to='approval_attachments/%Y/%m/',
        verbose_name='File',
    )
    attachment_type = models.CharField(
        max_length=30,
        choices=AttachmentType.choices,
        default=AttachmentType.SIGNATURE_TEMPLATE,
        verbose_name='Tipo allegato',
    )
    original_filename = models.CharField(max_length=255, verbose_name='Nome file originale')
    mime_type = models.CharField(max_length=100, blank=True, verbose_name='Tipo MIME')
    extension = models.CharField(max_length=20, blank=True, verbose_name='Estensione')
    size = models.PositiveIntegerField(null=True, blank=True, verbose_name='Dimensione (byte)')
    sha256_hash = models.CharField(max_length=64, blank=True, verbose_name='Hash SHA-256')
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_approval_attachments',
        verbose_name='Caricato da',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name='Caricato il')

    class Meta:
        verbose_name = 'Allegato richiesta approvazione'
        verbose_name_plural = 'Allegati richiesta approvazione'
        ordering = ['uploaded_at']

    def __str__(self):
        return f"{self.get_attachment_type_display()} — {self.original_filename}"


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
