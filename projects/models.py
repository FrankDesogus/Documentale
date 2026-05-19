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


class ProjectFolderMembership(models.Model):

    class Role(models.TextChoices):
        READER = 'reader', 'Lettore'
        AUTHOR = 'author', 'Autore'
        APPROVER = 'approver', 'Approvatore'
        AUDITOR = 'auditor', 'Revisore'
        MANAGER = 'manager', 'Gestore'

    folder = models.ForeignKey(
        ProjectFolder,
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name='Cartella',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='folder_memberships',
        verbose_name='Utente',
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        verbose_name='Ruolo',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_memberships',
        verbose_name='Creato da',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Permesso cartella'
        verbose_name_plural = 'Permessi cartella'
        unique_together = [('folder', 'user')]
        ordering = ['folder', 'user']

    def __str__(self):
        return f"{self.folder.code} – {self.user.username} ({self.get_role_display()})"


class Project(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Bozza'
        ACTIVE = 'active', 'In corso'
        SUSPENDED = 'suspended', 'Sospeso'
        CLOSED = 'closed', 'Chiuso'
        ARCHIVED = 'archived', 'Archiviato'

    class ProjectType(models.TextChoices):
        CUSTOMER = 'customer', 'Cliente'
        INTERNAL = 'internal', 'Interno'
        ENGINEERING = 'engineering', 'Ingegneria'
        QUALITY = 'quality', 'Qualità'
        OTHER = 'other', 'Altro'

    code = models.CharField(max_length=50, unique=True, verbose_name='Codice')
    name = models.CharField(max_length=255, verbose_name='Nome')
    description = models.TextField(blank=True, verbose_name='Descrizione')
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name='Stato',
    )
    project_type = models.CharField(
        max_length=20,
        choices=ProjectType.choices,
        default=ProjectType.OTHER,
        verbose_name='Tipo',
    )
    folder = models.ForeignKey(
        ProjectFolder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='projects',
        verbose_name='Cartella documentale',
    )
    manager = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_projects',
        verbose_name='Responsabile',
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_projects',
        verbose_name='Creato da',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Progetto'
        verbose_name_plural = 'Progetti'
        ordering = ['code']

    def __str__(self):
        return f"{self.code} – {self.name}"


class ProjectRevision(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Bozza'
        ISSUED = 'issued', 'Emessa'
        SUPERSEDED = 'superseded', 'Superata'
        ARCHIVED = 'archived', 'Archiviata'

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='revisions',
        verbose_name='Progetto',
    )
    revision_label = models.CharField(max_length=20, verbose_name='Etichetta revisione')
    revision_number = models.PositiveIntegerField(default=0, verbose_name='Numero revisione')
    title = models.CharField(max_length=255, verbose_name='Titolo')
    description = models.TextField(blank=True, verbose_name='Descrizione')
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name='Stato',
    )
    is_current = models.BooleanField(default=False, verbose_name='Corrente')
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_project_revisions',
        verbose_name='Creato da',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    issued_at = models.DateTimeField(null=True, blank=True, verbose_name='Emessa il')
    issued_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issued_project_revisions',
        verbose_name='Emessa da',
    )
    replaces_revision = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replaced_by',
        verbose_name='Sostituisce',
    )
    notes = models.TextField(blank=True, verbose_name='Note')

    class Meta:
        verbose_name = 'Baseline progetto'
        verbose_name_plural = 'Baseline progetto'
        ordering = ['project', '-revision_number']
        unique_together = [('project', 'revision_label'), ('project', 'revision_number')]

    def __str__(self):
        project_code = self.project.code if self.project else '—'
        return f"{project_code} baseline rev.{self.revision_label} – {self.title}"


class ProjectRevisionItem(models.Model):
    revision = models.ForeignKey(
        ProjectRevision,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Baseline',
    )
    item_number = models.PositiveSmallIntegerField(verbose_name='Numero voce')
    document_version = models.ForeignKey(
        'documents.DocumentVersion',
        on_delete=models.PROTECT,
        related_name='project_revision_items',
        verbose_name='Revisione documento',
    )
    description = models.CharField(max_length=255, blank=True, verbose_name='Descrizione')
    notes = models.TextField(blank=True, verbose_name='Note')

    class Meta:
        verbose_name = 'Voce baseline progetto'
        verbose_name_plural = 'Voci baseline progetto'
        ordering = ['revision', 'item_number']
        unique_together = [('revision', 'item_number'), ('revision', 'document_version')]

    def __str__(self):
        return f"{self.revision} – voce {self.item_number}: {self.document_version}"
