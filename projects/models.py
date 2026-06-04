from django.conf import settings
from django.contrib.auth.models import Group, User
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
        PROJECT = 'project', 'Progetto'

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

    # Materialized path — formato /pk/ oppure /parent_pk/child_pk/
    # Valorizzato dai service, non in .save().
    path = models.CharField(
        max_length=1000,
        blank=True,
        db_index=True,
        verbose_name='Path materializzato',
    )

    class Meta:
        verbose_name = 'Cartella'
        verbose_name_plural = 'Cartelle'
        ordering = ['code']

    def __str__(self):
        return f"{self.code} – {self.name}"


class FolderPermissionGrant(models.Model):
    """
    Grant modulare di permesso per cartella.

    Supporta:
      - grant a utente singolo oppure a gruppo (esattamente uno dei due)
      - effetto allow/deny
      - ereditarietà alle sottocartelle (inherit_to_children)
      - scadenza opzionale (expires_at)
      - permission code granulare

    NON sostituisce ProjectFolderMembership: coesiste con il sistema legacy.
    Il resolver (projects/resolver.py) lavorerà su questo modello.
    """

    class PermissionCode(models.TextChoices):
        READ_PUBLISHED         = 'read_published',           'Leggi documenti pubblicati'
        VIEW_HISTORY           = 'view_history',             'Vedi storico versioni'
        VIEW_OBSOLETE_DOCUMENTS= 'view_obsolete_documents',  'Vedi documenti obsoleti'
        VIEW_PROJECTS          = 'view_projects',            'Vedi progetti nella cartella'
        VIEW_FOLDER_ECNS       = 'view_folder_ecns',         'Vedi ECN della cartella'
        CREATE_DRAFT           = 'create_draft',             'Crea bozze'
        SUBMIT_FOR_APPROVAL    = 'submit_for_approval',      'Invia in approvazione'
        ELIGIBLE_APPROVER      = 'eligible_document_approver','Eleggibile come approvatore'
        MANAGE_REJECTED_DRAFTS = 'manage_rejected_drafts',   'Gestisci bozze rifiutate'
        MANAGE_PROJECT_DOCUMENTS='manage_project_documents', 'Gestisci documenti progetto'
        REQUEST_ECN            = 'request_ecn',              'Richiedi variante ECN'
        MANAGE_FOLDER          = 'manage_folder',            'Gestisci cartella'

    class Effect(models.TextChoices):
        ALLOW = 'allow', 'Consenti'
        DENY  = 'deny',  'Nega'

    folder = models.ForeignKey(
        'ProjectFolder',
        on_delete=models.CASCADE,
        related_name='permission_grants',
        verbose_name='Cartella',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='folder_permission_grants',
        verbose_name='Utente',
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='folder_permission_grants',
        verbose_name='Gruppo',
    )
    permission_code = models.CharField(
        max_length=60,
        choices=PermissionCode.choices,
        verbose_name='Permesso',
    )
    effect = models.CharField(
        max_length=10,
        choices=Effect.choices,
        default=Effect.ALLOW,
        verbose_name='Effetto',
    )
    inherit_to_children = models.BooleanField(
        default=True,
        verbose_name='Ereditato dalle sottocartelle',
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Scadenza',
    )
    notes = models.TextField(blank=True, verbose_name='Note')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
        verbose_name='Creato da',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Grant permesso cartella'
        verbose_name_plural = 'Grant permessi cartella'
        indexes = [
            models.Index(fields=['folder', 'permission_code']),
            models.Index(fields=['user', 'permission_code']),
            models.Index(fields=['group', 'permission_code']),
            models.Index(fields=['expires_at']),
        ]
        constraints = [
            # Esattamente uno tra user e group deve essere valorizzato
            models.CheckConstraint(
                name='fpg_user_or_group_not_both',
                check=(
                    models.Q(user__isnull=False, group__isnull=True)
                    | models.Q(user__isnull=True, group__isnull=False)
                ),
            ),
            # Unicità per user grant
            models.UniqueConstraint(
                fields=['folder', 'user', 'permission_code'],
                condition=models.Q(user__isnull=False),
                name='fpg_unique_user_grant',
            ),
            # Unicità per group grant
            models.UniqueConstraint(
                fields=['folder', 'group', 'permission_code'],
                condition=models.Q(group__isnull=False),
                name='fpg_unique_group_grant',
            ),
        ]

    def __str__(self):
        subject = f"user:{self.user}" if self.user_id else f"group:{self.group}"
        return (
            f"{self.folder.code} | {subject} | "
            f"{self.permission_code} → {self.effect}"
        )


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
    class ProjectType(models.TextChoices):
        CUSTOMER = 'customer', 'Cliente'
        INTERNAL = 'internal', 'Interno'
        ENGINEERING = 'engineering', 'Ingegneria'
        QUALITY = 'quality', 'Qualità'
        OTHER = 'other', 'Altro'

    code = models.CharField(max_length=50, unique=True, verbose_name='Codice')
    name = models.CharField(max_length=255, verbose_name='Nome')
    description = models.TextField(blank=True, verbose_name='Descrizione')
    project_type = models.CharField(
        max_length=20,
        choices=ProjectType.choices,
        default=ProjectType.OTHER,
        verbose_name='Tipo',
    )
    root_folder = models.OneToOneField(
        ProjectFolder,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='root_project',
        verbose_name='Cartella radice progetto',
        help_text='Cartella dedicata che contiene tutti i documenti e sottocartelle del progetto.',
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

    def clean(self):
        """Valida coerenza root_folder: se impostata deve avere folder_kind=PROJECT."""
        from django.core.exceptions import ValidationError
        if self.root_folder_id is not None:
            if self.root_folder.folder_kind != ProjectFolder.FolderKind.PROJECT:
                raise ValidationError({
                    'root_folder': (
                        f"La cartella '{self.root_folder.code}' non è di tipo Progetto "
                        f"(tipo attuale: {self.root_folder.get_folder_kind_display()}). "
                        "Usa il servizio di creazione progetto."
                    )
                })


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
