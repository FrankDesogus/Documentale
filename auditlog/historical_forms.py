"""
SAN-2 — Mixin riusabile per la sanatoria storica.

Aggiunge a qualsiasi ModelForm Django i campi opzionali di sanatoria:
  sanatoria (checkbox)
  historical_actor_user
  historical_actor_name
  historical_date
  date_precision
  source_description
  notes_historical

La checkbox è visibile SOLO se can_use_sanatoria(current_user) restituisce True.
Se sanatoria=False (default), i campi storici vengono ignorati completamente
e il comportamento del form non cambia.

Uso:

    class MioForm(SanatoriaFieldsMixin, forms.ModelForm):
        class Meta:
            ...

    # nella view:
    form = MioForm(request.POST, current_user=request.user)
    if form.is_valid():
        obj = form.save()
        form.maybe_create_historical_record(
            event_type=HistoricalRecord.EventType.DOC_CREATED,
            target_instance=obj,
            recorded_by=request.user,
        )
"""
import datetime

from django import forms
from django.contrib.auth.models import User
from django.utils import timezone


class SanatoriaFieldsMixin(forms.BaseForm):
    """
    Mixin da usare con forms.Form o forms.ModelForm.

    Richiede di passare current_user al costruttore:
        form = MioForm(..., current_user=request.user)

    Se current_user non può usare la sanatoria, i campi storici non compaiono
    nel form e qualsiasi POST con sanatoria=true viene rifiutato silenziosamente.
    """

    def __init__(self, *args, current_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_user = current_user
        self._sanatoria_available = self._check_sanatoria_available()

        if self._sanatoria_available:
            self._add_sanatoria_fields()
        # Se non disponibile, i campi storici non vengono aggiunti → sicurezza backend

    def _check_sanatoria_available(self) -> bool:
        if self._current_user is None:
            return False
        from auditlog.permissions import can_use_sanatoria
        return can_use_sanatoria(self._current_user)

    def _add_sanatoria_fields(self):
        """Inserisce i campi sanatoria DOPO i campi normali."""
        from auditlog.models import HistoricalRecord

        self.fields['sanatoria'] = forms.BooleanField(
            required=False,
            initial=False,
            label='Registra come evento storico (sanatoria)',
            help_text=(
                'Seleziona solo se stai registrando retroattivamente un evento avvenuto prima '
                'del sistema. Non saranno inviate notifiche.'
            ),
        )
        self.fields['historical_actor_user'] = forms.ModelChoiceField(
            queryset=User.objects.filter(is_active=True).order_by('last_name', 'first_name'),
            required=False,
            label='Attore storico (utente nel sistema)',
            help_text='Se la persona ha ancora un account attivo.',
        )
        self.fields['historical_actor_name'] = forms.CharField(
            max_length=200,
            required=False,
            label='Attore storico (nome libero)',
            help_text='Nome e cognome. Utile per ex dipendenti senza account.',
        )
        self.fields['historical_date'] = forms.DateField(
            required=False,
            label='Data dichiarata',
            widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            help_text='Data in cui l\'evento è avvenuto secondo la fonte cartacea.',
        )
        self.fields['date_precision'] = forms.ChoiceField(
            choices=HistoricalRecord.DatePrecision.choices,
            initial=HistoricalRecord.DatePrecision.EXACT_DATE,
            required=False,
            label='Precisione data',
        )
        self.fields['source_description'] = forms.CharField(
            max_length=500,
            required=False,
            label='Fonte',
            widget=forms.TextInput(attrs={
                'placeholder': 'Es: "Verbale CCB n. 27 del 2021-03-15"',
            }),
            help_text='Breve descrizione del documento che prova l\'evento.',
        )
        self.fields['notes_historical'] = forms.CharField(
            required=False,
            label='Note storiche',
            widget=forms.Textarea(attrs={'rows': 2}),
        )

    # ------------------------------------------------------------------
    # Validazione
    # ------------------------------------------------------------------

    def clean(self):
        cleaned = super().clean()

        # Sicurezza backend: se l'utente non può usare la sanatoria,
        # ignoriamo qualsiasi campo storico (anche se il POST è forgiato).
        if not self._sanatoria_available:
            cleaned['sanatoria'] = False
            return cleaned

        sanatoria = cleaned.get('sanatoria', False)
        if not sanatoria:
            return cleaned

        # Validazione campi storici obbligatori quando sanatoria=True
        actor_user = cleaned.get('historical_actor_user')
        actor_name = cleaned.get('historical_actor_name', '').strip()
        if not actor_user and not actor_name:
            self.add_error(
                'historical_actor_name',
                'Indicare almeno il nome testuale dell\'attore o selezionare un utente.',
            )

        precision = cleaned.get('date_precision', '')
        historical_date = cleaned.get('historical_date')

        if precision == 'exact_date' and not historical_date:
            self.add_error(
                'historical_date',
                'Inserire la data esatta oppure cambiare la precisione.',
            )

        if historical_date and historical_date > datetime.date.today():
            self.add_error(
                'historical_date',
                'La data storica non può essere nel futuro.',
            )

        return cleaned

    # ------------------------------------------------------------------
    # Helper per la view
    # ------------------------------------------------------------------

    @property
    def is_sanatoria(self) -> bool:
        """True se il form è stato inviato con sanatoria=True e l'utente è autorizzato."""
        if not self._sanatoria_available:
            return False
        return bool(self.cleaned_data.get('sanatoria', False))

    def maybe_create_historical_record(
        self,
        event_type: str,
        recorded_by,
        target_instance=None,
        import_batch=None,
        extra_notes: str = '',
    ):
        """
        Crea un HistoricalRecord se is_sanatoria è True.
        Restituisce l'oggetto creato oppure None.

        - Se import_batch è None, crea o riutilizza un batch demo attivo.
        - Non invia mai notifiche.
        """
        if not self.is_sanatoria:
            return None

        from auditlog.models import HistoricalImportBatch, HistoricalRecord
        from auditlog.services import create_audit_log

        # Batch: recupera quello attivo oppure ne crea uno
        if import_batch is None:
            import_batch = _get_or_create_demo_batch(recorded_by)

        data = self.cleaned_data
        rec = HistoricalRecord.objects.create(
            import_batch=import_batch,
            event_type=event_type,
            historical_actor_user=data.get('historical_actor_user'),
            historical_actor_name=(data.get('historical_actor_name') or '').strip(),
            historical_date=data.get('historical_date'),
            date_precision=data.get('date_precision') or HistoricalRecord.DatePrecision.EXACT_DATE,
            source_description=(data.get('source_description') or '').strip(),
            notes=(data.get('notes_historical') or '').strip() + (
                f'\n{extra_notes}' if extra_notes else ''
            ),
            recorded_by=recorded_by,
            target_app=target_instance._meta.app_label if target_instance else '',
            target_model=target_instance._meta.model_name if target_instance else '',
            target_id=str(target_instance.pk) if target_instance else '',
            target_repr=str(target_instance)[:255] if target_instance else '',
        )

        # AuditLog tecnico per il backfill
        if target_instance:
            create_audit_log(
                user=recorded_by,
                action='historical_backfill',
                instance=target_instance,
                metadata={
                    'event_type': event_type,
                    'historical_record_pk': rec.pk,
                    'historical_actor_name': rec.historical_actor_name,
                    'historical_date': str(rec.historical_date) if rec.historical_date else None,
                },
            )

        return rec


def _get_or_create_demo_batch(user):
    """
    Restituisce il batch demo attivo (IN_PROGRESS o DRAFT) oppure ne crea uno nuovo.
    Usato solo in modalità demo.
    """
    from auditlog.models import HistoricalImportBatch

    active = HistoricalImportBatch.objects.filter(
        created_by=user,
        status__in=[
            HistoricalImportBatch.Status.DRAFT,
            HistoricalImportBatch.Status.IN_PROGRESS,
        ],
    ).order_by('-created_at').first()

    if active:
        return active

    today = timezone.now().strftime('%Y%m%d-%H%M')
    return HistoricalImportBatch.objects.create(
        code=f'demo-{today}',
        description='Batch sanatoria automatico (modalità demo)',
        status=HistoricalImportBatch.Status.IN_PROGRESS,
        created_by=user,
    )


def should_send_notifications(*, sanatoria: bool) -> bool:
    """
    Helper centralizzato per la soppressione notifiche.

    sanatoria=False → comportamento ordinario (notifiche attive)
    sanatoria=True  → nessuna email, nessuna Notification in-app
    """
    return not sanatoria
