from django.db import models
from django.contrib.auth.models import User


def document_file_upload_path(instance, filename):
    return f"documents/{instance.version.document.pk}/{instance.version.pk}/{filename}"


class Document(models.Model):
    class Category(models.TextChoices):
        QUALITY = 'QUALITY', 'Documento di qualità'
        PROJECT = 'PROJECT', 'Documento di progetto'

    code = models.CharField(max_length=50, unique=True, verbose_name='Codice')
    title = models.CharField(max_length=255, verbose_name='Titolo')
    category = models.CharField(max_length=20, choices=Category.choices, verbose_name='Categoria')
    description = models.TextField(blank=True, verbose_name='Descrizione')
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
    is_active = models.BooleanField(default=True, verbose_name='Attivo')

    class Meta:
        verbose_name = 'Documento'
        verbose_name_plural = 'Documenti'
        ordering = ['code']

    def __str__(self):
        return f"{self.code} – {self.title}"


class DocumentVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Bozza'
        UNDER_REVIEW = 'UNDER_REVIEW', 'In approvazione'
        APPROVED = 'APPROVED', 'Approvato'
        REJECTED = 'REJECTED', 'Rifiutato'
        OBSOLETE = 'OBSOLETE', 'Obsoleto'

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='versions',
        verbose_name='Documento',
    )
    version_number = models.CharField(max_length=20, verbose_name='Numero revisione')
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name='Stato',
    )
    author = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='authored_versions',
        verbose_name='Autore',
    )
    change_summary = models.TextField(blank=True, verbose_name='Sommario modifiche')
    effective_date = models.DateField(null=True, blank=True, verbose_name='Data efficacia')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Revisione documento'
        verbose_name_plural = 'Revisioni documento'
        ordering = ['document', '-created_at']
        unique_together = [('document', 'version_number')]

    def __str__(self):
        return f"{self.document.code} rev.{self.version_number} [{self.get_status_display()}]"


class DocumentFile(models.Model):
    version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.CASCADE,
        related_name='files',
        verbose_name='Revisione',
    )
    file = models.FileField(upload_to=document_file_upload_path, verbose_name='File')
    original_filename = models.CharField(max_length=255, verbose_name='Nome file originale')
    file_size = models.PositiveIntegerField(null=True, blank=True, verbose_name='Dimensione (byte)')
    mime_type = models.CharField(max_length=100, blank=True, verbose_name='Tipo MIME')
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
        return f"{self.original_filename} ({self.version})"
