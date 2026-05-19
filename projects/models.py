from django.db import models
from django.contrib.auth.models import User


class ProjectFolder(models.Model):
    code = models.CharField(max_length=50, unique=True, verbose_name='Codice progetto')
    name = models.CharField(max_length=255, verbose_name='Nome')
    description = models.TextField(blank=True, verbose_name='Descrizione')
    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='owned_projects',
        verbose_name='Responsabile',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, verbose_name='Attivo')

    class Meta:
        verbose_name = 'Cartella progetto'
        verbose_name_plural = 'Cartelle progetto'
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
        verbose_name='Progetto',
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
        verbose_name = 'Revisione progetto'
        verbose_name_plural = 'Revisioni progetto'
        ordering = ['project_folder', '-created_at']
        unique_together = [('project_folder', 'revision_code')]

    def __str__(self):
        return f"{self.project_folder.code} rev.{self.revision_code} – {self.title}"


class ProjectRevisionItem(models.Model):
    revision = models.ForeignKey(
        ProjectRevision,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Revisione progetto',
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
        verbose_name = 'Voce revisione progetto'
        verbose_name_plural = 'Voci revisione progetto'
        ordering = ['revision', 'item_number']
        unique_together = [('revision', 'item_number')]

    def __str__(self):
        return f"{self.revision} – voce {self.item_number}: {self.description}"
