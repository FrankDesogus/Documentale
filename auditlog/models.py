from django.conf import settings
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


# ---------------------------------------------------------------------------
# Sanatoria storica — disponibile solo con DOCUMENTALE_DEMO_MODE=true
# ---------------------------------------------------------------------------

class HistoricalImportBatch(models.Model):
    """
    Lotto di sanatoria storica.

    Identifica un insieme di HistoricalRecord inseriti in una sessione di backfill.
    Anche quando l'inserimento avviene manualmente dalla UI viene sempre
    associato a un batch per consentire il rollback coerente.
    Disponibile solo con DOCUMENTALE_DEMO_MODE=true.
    """

    class Status(models.TextChoices):
        DRAFT       = 'draft',       'Bozza'
        IN_PROGRESS = 'in_progress', 'In corso'
        COMPLETED   = 'completed',   'Completato'
        ROLLED_BACK = 'rolled_back', 'Annullato'

    code = models.CharField(
        max_length=100,
        unique=True,
        verbose_name='Codice batch',
        help_text='Identificatore univoco del lotto (es. 2026-backfill-01).',
    )
    description = models.TextField(blank=True, verbose_name='Descrizione')
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name='Stato',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_historical_batches',
        verbose_name='Creato da',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creato il')
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='completed_historical_batches',
        verbose_name='Completato da',
    )
    completed_at = models.DateTimeField(
        null=True, blank=True,
        verbose_name='Completato il',
    )
    notes = models.TextField(blank=True, verbose_name='Note')

    class Meta:
        verbose_name = 'Lotto sanatoria'
        verbose_name_plural = 'Lotti sanatoria'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} [{self.get_status_display()}]"


class HistoricalRecord(models.Model):
    """
    Registro di un singolo evento storico dichiarato nell'ambito di una sanatoria.

    Distinto da AuditLog (tecnico, auto_now_add, inalterabile).
    Questo modello contiene:
      - recorded_by / recorded_at : chi ha inserito il dato oggi e quando
      - historical_actor_*         : a chi attribuire l'azione storicamente
      - historical_date            : data dichiarata (da documento cartaceo, verbale, ecc.)
      - source_description         : descrizione della fonte probatoria

    NON mostrare in UI "Firmato da X" — il sistema non ha firma digitale.
    """

    class EventType(models.TextChoices):
        # Documenti
        DOC_CREATED          = 'doc_created',          'Documento creato'
        DOC_DRAFT_CREATED    = 'doc_draft_created',    'Prima bozza creata'
        DOC_REVISION_CREATED = 'doc_revision_created', 'Nuova revisione creata'
        DOC_SUBMITTED        = 'doc_submitted',        'Inviato in approvazione'
        DOC_APPROVED         = 'doc_approved',         'Revisione approvata'
        DOC_REJECTED         = 'doc_rejected',         'Revisione rifiutata'
        DOC_PUBLISHED        = 'doc_published',        'Documento pubblicato'
        # ECN
        ECN_CREATED          = 'ecn_created',          'ECN creata'
        ECN_CCB_CONFIGURED   = 'ecn_ccb_configured',   'CCB configurata'
        ECN_DOSSIER_COMPILED = 'ecn_dossier_compiled', 'Dossier compilato'
        ECN_SUBMITTED        = 'ecn_submitted',        'ECN inviata alla CCB'
        ECN_VOTE_APPROVED    = 'ecn_vote_approved',    'Voto CCB: approvato'
        ECN_VOTE_REJECTED    = 'ecn_vote_rejected',    'Voto CCB: rifiutato'
        ECN_APPROVED         = 'ecn_approved',         'ECN approvata dalla CCB'
        ECN_REJECTED         = 'ecn_rejected',         'ECN rifiutata dalla CCB'
        ECN_EXECUTED         = 'ecn_executed',         'ECN eseguita'
        ECN_CLOSED           = 'ecn_closed',           'ECN chiusa'
        # Progetti
        PROJECT_CREATED          = 'project_created',          'Progetto creato'
        PROJECT_METADATA_UPDATED = 'project_metadata_updated', 'Metadati progetto aggiornati'
        PROJECT_VERSION_SAVED    = 'project_version_saved',    'Versione progetto salvata'
        PROJECT_REVISION_SAVED   = 'project_revision_saved',   'Revisione progetto salvata'
        PROJECT_SNAPSHOT_ISSUED  = 'project_snapshot_issued',  'Snapshot progetto emesso'
        # Generico
        GENERIC = 'generic', 'Evento generico'

    class DatePrecision(models.TextChoices):
        EXACT_DATE = 'exact_date', 'Data esatta'
        MONTH      = 'month',      'Mese (giorno non noto)'
        YEAR       = 'year',       'Anno (mese non noto)'
        UNKNOWN    = 'unknown',    'Data sconosciuta'

    import_batch = models.ForeignKey(
        HistoricalImportBatch,
        on_delete=models.PROTECT,
        related_name='records',
        verbose_name='Lotto sanatoria',
    )
    event_type = models.CharField(
        max_length=30,
        choices=EventType.choices,
        verbose_name='Tipo evento',
    )
    # Attore storico
    historical_actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='historical_records_as_actor',
        verbose_name='Attore (utente collegato)',
        help_text='Se l\'attore ha ancora un account nel sistema.',
    )
    historical_actor_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Attore (nome testuale)',
        help_text='Nome e cognome. Obbligatorio se non è collegato un utente.',
    )
    # Data storica dichiarata
    historical_date = models.DateField(
        null=True,
        blank=True,
        verbose_name='Data dichiarata',
        help_text='Data in cui l\'evento è avvenuto, secondo la fonte.',
    )
    date_precision = models.CharField(
        max_length=20,
        choices=DatePrecision.choices,
        default=DatePrecision.EXACT_DATE,
        verbose_name='Precisione data',
    )
    # Oggetto target
    target_app = models.CharField(max_length=50, blank=True, verbose_name='App target')
    target_model = models.CharField(max_length=100, blank=True, verbose_name='Modello target')
    target_id = models.CharField(max_length=50, blank=True, verbose_name='ID oggetto')
    target_repr = models.CharField(max_length=255, blank=True, verbose_name='Descrizione oggetto')
    # Fonte probatoria
    source_description = models.TextField(
        blank=True,
        verbose_name='Fonte',
        help_text=(
            'Es: "Verbale riunione QM del 2021-03-15", '
            '"Timbro copia controllata n. 3".'
        ),
    )
    notes = models.TextField(blank=True, verbose_name='Note aggiuntive')
    # Metadati tecnici del backfill
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recorded_historical_records',
        verbose_name='Registrato da',
    )
    recorded_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Registrato il',
    )
    # Verifica qualità
    is_verified = models.BooleanField(
        default=False,
        verbose_name='Verificato',
        help_text='Il responsabile qualità ha verificato la fonte.',
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_historical_records',
        verbose_name='Verificato da',
    )
    verified_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Verificato il',
    )

    class Meta:
        verbose_name = 'Record storico'
        verbose_name_plural = 'Registro storico'
        ordering = ['historical_date', 'recorded_at']
        indexes = [
            models.Index(fields=['target_app', 'target_model', 'target_id']),
            models.Index(fields=['import_batch', 'event_type']),
            models.Index(fields=['historical_date']),
            models.Index(fields=['is_verified']),
        ]

    def __str__(self):
        actor = (
            self.historical_actor_name
            or (self.historical_actor_user.get_full_name() if self.historical_actor_user else '—')
        )
        date_str = str(self.historical_date) if self.historical_date else '(data sconosciuta)'
        return f"[{self.get_event_type_display()}] {actor} – {date_str}"

    def get_actor_display(self):
        """Stringa leggibile dell'attore storico."""
        if self.historical_actor_user:
            full = self.historical_actor_user.get_full_name()
            return full or self.historical_actor_user.username
        return self.historical_actor_name or '—'

    def get_date_display(self):
        """Stringa data con precisione."""
        if not self.historical_date:
            return 'Data sconosciuta'
        if self.date_precision == self.DatePrecision.YEAR:
            return str(self.historical_date.year)
        if self.date_precision == self.DatePrecision.MONTH:
            return self.historical_date.strftime('%m/%Y')
        return self.historical_date.strftime('%d/%m/%Y')


class HistoricalEvidence(models.Model):
    """
    Allegato probatorio a supporto di un HistoricalRecord.

    Mantiene separati:
      - file probatorio (verbale, scan timbro, email d'epoca)
      - file ufficiale del documento (DocumentFile)
    """

    historical_record = models.ForeignKey(
        HistoricalRecord,
        on_delete=models.CASCADE,
        related_name='evidence_files',
        verbose_name='Record storico',
    )
    file = models.FileField(
        upload_to='historical_evidence/%Y/',
        verbose_name='File allegato',
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Descrizione',
        help_text='Es: "Verbale riunione CCB pag. 3", "Scan timbro copia controllata".',
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='uploaded_historical_evidence',
        verbose_name='Caricato da',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True, verbose_name='Caricato il')

    class Meta:
        verbose_name = 'Allegato probatorio'
        verbose_name_plural = 'Allegati probatori'
        ordering = ['historical_record', 'uploaded_at']

    def __str__(self):
        return f"{self.historical_record} — {self.description or self.file.name}"
