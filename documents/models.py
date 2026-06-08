from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.contrib.auth.models import User

from documents.versioning import SequenceScheme


class DocumentFile(models.Model):
    file = models.FileField(upload_to='documents/files/%Y/%m/', verbose_name='File')
    original_filename = models.CharField(max_length=255, verbose_name='Nome file originale')
    mime_type = models.CharField(max_length=100, blank=True, verbose_name='Tipo MIME')
    extension = models.CharField(max_length=20, blank=True, verbose_name='Estensione')
    size = models.PositiveIntegerField(null=True, blank=True, verbose_name='Dimensione (byte)')
    sha256_hash = models.CharField(max_length=64, blank=True, verbose_name='Hash SHA-256')
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='uploaded_files',
        verbose_name='Caricato da',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'File documento'
        verbose_name_plural = 'File documenti'
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.original_filename}"


class DocumentVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Bozza'
        IN_APPROVAL = 'in_approval', 'In approvazione'
        APPROVED = 'approved', 'Approvato'
        REJECTED = 'rejected', 'Rifiutato'
        SUPERSEDED = 'superseded', 'Sostituito'
        ARCHIVED = 'archived', 'Archiviato'

    # FK a stringa per evitare dipendenza circolare con Document
    document = models.ForeignKey(
        'Document',
        on_delete=models.CASCADE,
        related_name='versions',
        verbose_name='Documento',
    )
    revision_label = models.CharField(max_length=20, verbose_name='Etichetta revisione')
    revision_number = models.PositiveIntegerField(verbose_name='Numero revisione')
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name='Stato',
    )
    file = models.ForeignKey(
        DocumentFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='versions',
        verbose_name='File operativo',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='created_versions',
        verbose_name='Creato da',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name='Inviato in approvazione il')
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name='Approvato il')
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_versions',
        verbose_name='Approvato da',
    )
    rejected_at = models.DateTimeField(null=True, blank=True, verbose_name='Rifiutato il')
    rejection_reason = models.TextField(blank=True, verbose_name='Motivo rifiuto')
    change_summary = models.TextField(blank=True, verbose_name='Sommario modifiche')
    is_current = models.BooleanField(default=False, verbose_name='È la revisione corrente')
    replaces_version = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replaced_by',
        verbose_name='Sostituisce la revisione',
    )

    class Meta:
        verbose_name = 'Revisione documento'
        verbose_name_plural = 'Revisioni documento'
        ordering = ['document', '-revision_number']
        unique_together = [('document', 'revision_label')]
        constraints = [
            models.UniqueConstraint(
                fields=['document', 'revision_number'],
                name='unique_revision_number_per_document',
            ),
            models.UniqueConstraint(
                fields=['document'],
                condition=Q(is_current=True),
                name='unique_current_version_per_document',
            ),
        ]

    def __str__(self):
        return f"{self.document.code} rev.{self.revision_label} [{self.get_status_display()}]"


class Document(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Attivo'
        OBSOLETE = 'obsolete', 'Obsoleto'
        ARCHIVED = 'archived', 'Archiviato'

    class Category(models.TextChoices):
        QUALITY = 'QUALITY', 'Documento di qualità'
        PROJECT = 'PROJECT', 'Documento di progetto'

    revision_scheme = models.CharField(
        max_length=20,
        choices=SequenceScheme.choices,
        default=SequenceScheme.NUMERIC,
        verbose_name='Schema revisione',
        help_text='Schema usato per le etichette di revisione future. Non modifica lo storico.',
    )
    code = models.CharField(max_length=50, unique=True, verbose_name='Codice')
    title = models.CharField(max_length=255, verbose_name='Titolo')
    description = models.TextField(blank=True, verbose_name='Descrizione')
    category = models.CharField(max_length=20, choices=Category.choices, verbose_name='Categoria')
    document_type = models.CharField(max_length=100, blank=True, verbose_name='Tipo documento')
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name='Stato',
    )
    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='owned_documents',
        verbose_name='Responsabile',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='created_documents',
        verbose_name='Creato da',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    current_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='current_for_document',
        verbose_name='Revisione corrente',
    )
    # FK a stringa per evitare importazione circolare con l'app projects
    project_folder = models.ForeignKey(
        'projects.ProjectFolder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents',
        verbose_name='Cartella progetto',
    )

    class Meta:
        verbose_name = 'Documento'
        verbose_name_plural = 'Documenti'
        ordering = ['code']

    _OPEN_STATUSES = {'draft', 'in_approval'}

    def clean(self):
        if self.pk:
            old = Document.objects.filter(pk=self.pk).values('revision_scheme').first()
            if old and old['revision_scheme'] != self.revision_scheme:
                has_open = self.versions.filter(status__in=self._OPEN_STATUSES).exists()
                if has_open:
                    raise ValidationError({
                        'revision_scheme': (
                            'Non è possibile modificare lo schema di revisione mentre esiste '
                            'una revisione aperta o in approvazione. '
                            'Gestire prima la revisione corrente.'
                        )
                    })

    def __str__(self):
        return f"{self.code} – {self.title}"
