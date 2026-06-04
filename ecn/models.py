from django.contrib.auth.models import User
from django.db import models


class ChangeNotice(models.Model):
    """Richiesta di modifica controllata (ECN / Variante) per un documento emesso."""

    class Status(models.TextChoices):
        DRAFT           = 'draft',           'Bozza'
        CCB_PREPARATION = 'ccb_preparation', 'Istruttoria CCB'
        UNDER_REVIEW    = 'under_review',    'In revisione CCB'
        APPROVED        = 'approved',        'Approvata'
        REJECTED        = 'rejected',        'Rifiutata'
        CLOSED          = 'closed',          'Chiusa'

    class Motivation(models.TextChoices):
        IMPROVEMENT    = 'improvement',    'Miglioramento tecnico'
        CUSTOMER       = 'customer',       'Richiesta cliente'
        NON_CONFORMITY = 'non_conformity', 'Non conformità'
        DESIGN         = 'design',         'Ottimizzazione progettuale'
        REGULATORY     = 'regulatory',     'Adeguamento normativo'
        OTHER          = 'other',          'Altro'

    class CCBClass(models.TextChoices):
        CLASS1 = 'class1', 'Classe 1'
        CLASS2 = 'class2', 'Classe 2'

    class CCBPolicy(models.TextChoices):
        ANY        = 'any',        'Qualsiasi approvatore (ANY)'
        ALL        = 'all',        'Tutti gli approvatori (ALL)'
        SEQUENTIAL = 'sequential', 'Sequenziale (SEQUENTIAL)'

    # ------------------------------------------------------------------
    # Identificazione
    # ------------------------------------------------------------------
    code  = models.CharField(max_length=50, unique=True, verbose_name='Codice ECN')
    title = models.CharField(max_length=255, verbose_name='Titolo')

    # ------------------------------------------------------------------
    # Descrizioni proposta
    # ------------------------------------------------------------------
    description = models.TextField(
        blank=True,
        verbose_name='Descrizione modifica proposta',
    )
    motivation = models.CharField(
        max_length=30,
        choices=Motivation.choices,
        verbose_name='Categoria motivazione',
    )
    motivation_detail = models.TextField(
        blank=True,
        verbose_name='Motivazione (dettaglio)',
        help_text='Descrizione estesa della motivazione della variante.',
    )
    commessa = models.CharField(
        max_length=100,
        blank=True,
        verbose_name='Commessa / ordine',
    )

    # ------------------------------------------------------------------
    # Riferimenti a documento, versione e progetto
    # FK a stringa per evitare dipendenze circolari con le altre app
    # ------------------------------------------------------------------
    document = models.ForeignKey(
        'documents.Document',
        on_delete=models.PROTECT,
        related_name='ecns',
        verbose_name='Documento',
    )
    # Snapshot della versione corrente al momento della proposta ECN.
    # Serve per tracciare su quale revisione si è basata la variante.
    document_version = models.ForeignKey(
        'documents.DocumentVersion',
        on_delete=models.PROTECT,
        related_name='ecns_as_baseline',
        verbose_name='Revisione di riferimento',
        help_text='Versione del documento corrente al momento della proposta.',
    )
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ecns',
        verbose_name='Progetto',
    )

    # ------------------------------------------------------------------
    # Proponente e stato
    # ------------------------------------------------------------------
    proposed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='proposed_ecns',
        verbose_name='Proponente',
    )
    proposed_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Proposto il',
    )
    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Inviato a CCB il',
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name='Stato',
    )

    # ------------------------------------------------------------------
    # Politica di approvazione CCB
    # ------------------------------------------------------------------
    ccb_policy = models.CharField(
        max_length=20,
        choices=CCBPolicy.choices,
        default=CCBPolicy.ALL,
        verbose_name='Politica approvazione CCB',
        help_text='ANY: basta un approvatore; ALL: tutti; SEQUENTIAL: in ordine.',
    )

    # ------------------------------------------------------------------
    # Responsabile istruttoria CCB
    # Compilazione del dossier prima dell'invio ai votanti.
    # ------------------------------------------------------------------
    ccb_coordinator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='coordinating_ecns',
        verbose_name='Responsabile istruttoria CCB',
        help_text='Utente incaricato di compilare il dossier istruttorio prima dell\'invio ai votanti CCB.',
    )

    # ------------------------------------------------------------------
    # Dossier istruttorio CCB
    # Compilato dal responsabile istruttoria prima dell'invio ai votanti.
    # ------------------------------------------------------------------
    ccb_class = models.CharField(
        max_length=10,
        choices=CCBClass.choices,
        null=True,
        blank=True,
        verbose_name='Classe variante',
    )
    ccb_requirements = models.TextField(
        blank=True,
        verbose_name='Analisi requisiti',
        help_text='Conformità ai requisiti tecnici e normativi.',
    )
    ccb_technical_impact = models.TextField(
        blank=True,
        verbose_name='Impatto tecnico',
    )
    ccb_cost_impact = models.TextField(
        blank=True,
        verbose_name='Impatto costi',
    )
    ccb_time_impact = models.TextField(
        blank=True,
        verbose_name='Impatto tempi',
    )
    ccb_quality_impact = models.TextField(
        blank=True,
        verbose_name='Impatto qualità',
    )
    ccb_other_impact = models.TextField(
        blank=True,
        verbose_name='Impatto su altri documenti / parti',
    )
    ccb_notes = models.TextField(
        blank=True,
        verbose_name='Note CCB',
        help_text='Note aggiuntive del revisore CCB, incluse le motivazioni del rifiuto.',
    )
    ccb_reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_ecns',
        verbose_name='Revisore CCB',
    )
    ccb_reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Revisionato il',
    )

    # ------------------------------------------------------------------
    # Esecuzione: nuova revisione del documento collegata all'ECN
    # Popolato quando l'autore/esecutore crea la nuova bozza a valle
    # dell'ECN approvato.
    # ------------------------------------------------------------------
    executed_version = models.ForeignKey(
        'documents.DocumentVersion',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ecns_executed',
        verbose_name='Revisione eseguita',
    )
    executed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Eseguita il',
    )

    # ------------------------------------------------------------------
    # Chiusura: verificata dal Responsabile Qualità
    # ------------------------------------------------------------------
    close_notes = models.TextField(
        blank=True,
        verbose_name='Note chiusura qualità',
        help_text='Note del Responsabile Qualità a chiusura della variante.',
    )
    closed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='closed_ecns',
        verbose_name='Chiuso da',
    )
    closed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Chiuso il',
    )

    # ------------------------------------------------------------------
    # Metadati
    # ------------------------------------------------------------------
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='created_ecns',
        verbose_name='Creato da',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Aggiornato il',
    )

    class Meta:
        verbose_name        = 'ECN / Variante'
        verbose_name_plural = 'ECN / Varianti'
        ordering            = ['-proposed_at']

    def __str__(self):
        return f"{self.code} — {self.title} [{self.get_status_display()}]"


class ChangeNoticeApprover(models.Model):
    """Approvatore CCB assegnato a un ECN."""

    change_notice = models.ForeignKey(
        ChangeNotice,
        on_delete=models.CASCADE,
        related_name='approvers',
        verbose_name='ECN',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='ecn_approver_roles',
        verbose_name='Approvatore',
    )
    order = models.PositiveSmallIntegerField(
        default=0,
        verbose_name='Ordine',
        help_text='Usato per la politica SEQUENTIAL.',
    )
    is_required = models.BooleanField(
        default=True,
        verbose_name='Approvazione obbligatoria',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Approvatore CCB'
        verbose_name_plural = 'Approvatori CCB'
        unique_together     = [['change_notice', 'user']]
        ordering            = ['order', 'id']

    def __str__(self):
        return f"{self.change_notice.code} — {self.user.username}"


class ChangeNoticeDecision(models.Model):
    """Decisione individuale di un approvatore CCB su un ECN."""

    class Decision(models.TextChoices):
        APPROVE = 'approve', 'Approvato'
        REJECT  = 'reject',  'Rifiutato'

    change_notice = models.ForeignKey(
        ChangeNotice,
        on_delete=models.CASCADE,
        related_name='decisions',
        verbose_name='ECN',
    )
    approver = models.ForeignKey(
        ChangeNoticeApprover,
        on_delete=models.PROTECT,
        related_name='decisions',
        verbose_name='Approvatore',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='ecn_decisions',
        verbose_name='Utente',
    )
    decision = models.CharField(
        max_length=10,
        choices=Decision.choices,
        verbose_name='Decisione',
    )
    comment = models.TextField(
        blank=True,
        verbose_name='Commento',
    )
    decided_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Deciso il',
    )

    class Meta:
        verbose_name        = 'Decisione CCB'
        verbose_name_plural = 'Decisioni CCB'
        unique_together     = [['change_notice', 'approver']]
        ordering            = ['decided_at', 'id']

    def __str__(self):
        return (
            f"{self.change_notice.code} — {self.user.username}: "
            f"{self.get_decision_display()}"
        )


class ChangeNoticeAttachment(models.Model):
    """File allegato a un ECN (specifiche tecniche, disegni, analisi, ecc.)."""

    change_notice = models.ForeignKey(
        ChangeNotice,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name='ECN',
    )
    file = models.FileField(
        upload_to='ecn/attachments/%Y/%m/',
        verbose_name='File',
    )
    # original_filename: nome del file al momento del caricamento,
    # necessario per i download (FileResponse filename) — coerente con
    # ApprovalRequestAttachment e DocumentFile già presenti nel progetto.
    original_filename = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Nome file originale',
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Titolo',
    )
    description = models.TextField(
        blank=True,
        verbose_name='Descrizione',
    )
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='uploaded_ecn_attachments',
        verbose_name='Caricato da',
    )
    uploaded_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Caricato il',
    )
    size = models.PositiveBigIntegerField(
        null=True,
        blank=True,
        verbose_name='Dimensione (byte)',
    )
    extension = models.CharField(
        max_length=20,
        blank=True,
        verbose_name='Estensione',
    )

    class Meta:
        verbose_name        = 'Allegato ECN'
        verbose_name_plural = 'Allegati ECN'
        ordering            = ['uploaded_at']

    def __str__(self):
        name = self.original_filename or self.title or '—'
        return f"{self.change_notice.code} — {name}"
