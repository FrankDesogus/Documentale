from django.contrib.auth.models import User
from django.db import models


class ProjectFolder(models.Model):

    class Status(models.TextChoices):
        ACTIVE = 'active', 'Attiva'
        ARCHIVED = 'archived', 'Archiviata'
        OBSOLETE = 'obsolete', 'Obsoleta'

    class FolderKind(models.TextChoices):
        DEPARTMENT = 'department', 'Reparto'
        GENERIC = 'generic', 'Generica'
        ARCHIVE = 'archive', 'Archivio'

    code = models.CharField(max_length=50, unique=True, verbose_name='Codice')
    name = models.CharField(max_length=255, verbose_name='Nome')
    description = models.TextField(blank=True, verbose_name='Descrizione')
    folder_kind = models.CharField(
        max_length=20,
        choices=FolderKind.choices,
        default=FolderKind.GENERIC,
        verbose_name='Tipo cartella',
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subfolders',
        verbose_name='Cartella padre',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        verbose_name='Stato',
    )
    # owner mantenuto per compatibilità con dati esistenti
    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='owned_projects',
        verbose_name='Responsabile',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_folders',
        verbose_name='Creato da',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, verbose_name='Attivo')

    class Meta:
        verbose_name = 'Cartella'
        verbose_name_plural = 'Cartelle'
        ordering = ['code']

    def __str__(self):
        return f"{self.code} – {self.name}"


class ProjectRevision(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Bozza'
        ISSUED = 'ISSUED', 'Emessa'
        SUPERSEDED = 'SUPERSEDED', 'Superata'

    project_folder = models.ForeignKey(
        ProjectFolder,
        on_delete=models.CASCADE,
        related_name='revisions',
        verbose_name='Cartella',
    )
    revision_code = models.CharField(max_length=20, verbose_name='Codice revisione')
    title = models.CharField(max_length=255, verbose_name='Titolo')
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name='Stato',
    )
    author = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='authored_project_revisions',
        verbose_name='Autore',
    )
    notes = models.TextField(blank=True, verbose_name='Note')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Revisione cartella'
        verbose_name_plural = 'Revisioni cartella'
        ordering = ['project_folder', '-created_at']
        unique_together = [('project_folder', 'revision_code')]

    def __str__(self):
        return f"{self.project_folder.code} rev.{self.revision_code} – {self.title}"


class ProjectRevisionItem(models.Model):
    revision = models.ForeignKey(
        ProjectRevision,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Revisione cartella',
    )
    item_number = models.PositiveSmallIntegerField(verbose_name='Numero voce')
    # FK a stringa per evitare importazione circolare con l'app documents
    document_version = models.ForeignKey(
        'documents.DocumentVersion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='project_revision_items',
        verbose_name='Revisione documento collegata',
    )
    description = models.CharField(max_length=255, verbose_name='Descrizione')
    notes = models.TextField(blank=True, verbose_name='Note')

    class Meta:
        verbose_name = 'Voce revisione cartella'
        verbose_name_plural = 'Voci revisione cartella'
        ordering = ['revision', 'item_number']
        unique_together = [('revision', 'item_number')]

    def __str__(self):
        return f"{self.revision} – voce {self.item_number}: {self.description}"
