"""Form per l'app ECN / Varianti."""

from django import forms
from django.contrib.auth.models import User

from ecn.models import ChangeNotice


class ChangeNoticeForm(forms.Form):
    """Form per la creazione di un nuovo ECN da parte del proponente.

    Non include la selezione degli approvatori CCB: quella è responsabilità
    del Responsabile Qualità / Document Manager, tramite ChangeNoticeCCBConfigForm.
    """

    title = forms.CharField(
        max_length=255,
        label='Titolo',
        help_text='Titolo breve e descrittivo della variante proposta.',
    )
    motivation = forms.ChoiceField(
        choices=ChangeNotice.Motivation.choices,
        label='Categoria motivazione',
    )
    motivation_detail = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Motivazione (dettaglio)',
        help_text='Descrizione estesa della motivazione della variante.',
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}),
        required=False,
        label='Descrizione modifica proposta',
        help_text='Descrizione della modifica tecnica proposta.',
    )
    commessa = forms.CharField(
        max_length=100,
        required=False,
        label='Commessa / ordine',
    )
    project = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput,
    )


def _ccb_candidate_queryset(current_user=None):
    """
    Restituisce il queryset degli utenti selezionabili come componenti CCB.
    In demo mode con supervisor_demo: solo il supervisore stesso.
    In normale: unione di CCB + Document Managers + Document Approvers.
    """
    from config.demo_utils import is_demo_supervisor
    if current_user and is_demo_supervisor(current_user):
        return User.objects.filter(pk=current_user.pk)

    from django.contrib.auth.models import Group
    from ecn.permissions import GROUP_CCB
    from documents.permissions import GROUP_MANAGERS, GROUP_APPROVERS

    qs = User.objects.none()
    for name in [GROUP_CCB, GROUP_MANAGERS, GROUP_APPROVERS]:
        try:
            qs = qs | Group.objects.get(name=name).user_set.all()
        except Group.DoesNotExist:
            pass
    return qs.distinct().order_by('last_name', 'first_name', 'username')


# ---------------------------------------------------------------------------
# Formset ordinato per componenti CCB (STEP I)
# Pattern identico ad ApproverFormSet in documents/forms.py
# ---------------------------------------------------------------------------

class CCBMemberRowForm(forms.Form):
    """Una riga del formset componenti CCB: seleziona utente."""

    user = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True).order_by('last_name', 'first_name'),
        label='Componente CCB',
        empty_label='— seleziona componente —',
        required=False,
    )

    def __init__(self, *args, current_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = _ccb_candidate_queryset(current_user)


class BaseCCBMemberFormSet(forms.BaseFormSet):
    def __init__(self, *args, current_user=None, **kwargs):
        self.current_user = current_user
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs['current_user'] = self.current_user
        return kwargs

    def clean(self):
        if any(self.errors):
            return
        selected = []
        for form in self.forms:
            if form.cleaned_data and form.cleaned_data.get('user'):
                user = form.cleaned_data['user']
                if user in selected:
                    raise forms.ValidationError(
                        f"L'utente '{user}' è selezionato più di una volta."
                    )
                selected.append(user)
        if not selected:
            raise forms.ValidationError('Devi selezionare almeno un componente CCB.')


CCBMemberFormSet = forms.formset_factory(
    CCBMemberRowForm,
    formset=BaseCCBMemberFormSet,
    extra=0,
)


# ---------------------------------------------------------------------------
# Form configurazione CCB (mantiene la versione legacy per backward compat)
# ---------------------------------------------------------------------------

class ChangeNoticeCCBConfigForm(forms.Form):
    """Form principale per la configurazione CCB: policy + coordinator.

    I componenti CCB sono gestiti da CCBMemberFormSet.
    Mantenuto per backward compatibility (usato da DemoSupervisorTests
    che testano il campo 'approvers'). Nelle nuove view viene usato
    il formset + questo form per policy/coordinator.
    """

    ccb_policy = forms.ChoiceField(
        choices=ChangeNotice.CCBPolicy.choices,
        initial=ChangeNotice.CCBPolicy.ALL,
        label='Politica approvazione CCB',
        help_text='ANY: basta un approvatore; ALL: tutti devono approvare; SEQUENTIAL: in ordine.',
    )
    coordinator = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True).order_by('last_name', 'first_name'),
        label='Responsabile istruttoria CCB',
        empty_label='— seleziona responsabile —',
        required=False,
        help_text='Utente incaricato di compilare il dossier prima dell\'invio ai votanti.',
    )
    # Campo legacy (multiselect) mantenuto per backward compat dei test esistenti
    approvers = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        label='Approvatori CCB (legacy)',
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    def __init__(self, *args, current_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        candidate_qs = _ccb_candidate_queryset(current_user)
        self.fields['approvers'].queryset = candidate_qs
        self.fields['coordinator'].queryset = candidate_qs


class ChangeNoticeReviewForm(forms.Form):
    """
    Form di decisione individuale del membro CCB.

    STEP I: Separato dal dossier istruttorio.
    Il membro vede il dossier in sola lettura e compila solo:
      - decisione (approva / rifiuta)
      - commento personale
      - motivazione rifiuto (obbligatoria se rifiuta)

    La classificazione variante e i campi di impatto vengono compilati
    nel dossier istruttorio (/ecn/<pk>/ccb-dossier/) PRIMA dell'invio
    ai votanti.
    """

    ACTION_APPROVE = 'approve'
    ACTION_REJECT  = 'reject'
    ACTION_CHOICES = [
        (ACTION_APPROVE, 'Approva'),
        (ACTION_REJECT,  'Rifiuta'),
    ]

    action = forms.ChoiceField(
        choices=ACTION_CHOICES,
        label='Decisione',
        widget=forms.RadioSelect,
    )
    comment = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Commento personale',
        help_text='Commento individuale allegato alla tua decisione.',
    )
    ccb_notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Motivazione rifiuto',
        help_text='Obbligatorio se Rifiuta: inserisci il motivo del rifiuto.',
    )

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get('action')
        ccb_notes = cleaned.get('ccb_notes', '').strip()

        if action == self.ACTION_REJECT and not ccb_notes:
            self.add_error(
                'ccb_notes',
                'Il motivo del rifiuto è obbligatorio.',
            )
        return cleaned


# ---------------------------------------------------------------------------
# Form dossier istruttorio CCB (STEP I)
# ---------------------------------------------------------------------------

class ChangeNoticeDossierForm(forms.Form):
    """
    Form per la compilazione del dossier istruttorio CCB.
    Compilato dal responsabile istruttoria (ccb_coordinator) prima dell'invio ai votanti.

    Campi obbligatori prima dell'invio:
      - ccb_class (classificazione variante)
      - ccb_requirements (analisi requisiti)
      - ccb_technical_impact (impatto tecnico)
    """

    ccb_class = forms.ChoiceField(
        choices=[('', '— seleziona classe —')] + list(ChangeNotice.CCBClass.choices),
        required=False,
        label='Classificazione variante',
        help_text='Obbligatoria prima dell\'invio alla CCB.',
    )
    ccb_requirements = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Analisi requisiti',
        help_text='Conformità ai requisiti tecnici e normativi. Obbligatorio prima dell\'invio.',
    )
    ccb_technical_impact = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Impatto tecnico',
        help_text='Obbligatorio prima dell\'invio.',
    )
    ccb_cost_impact = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
        label='Impatto costi',
    )
    ccb_time_impact = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
        label='Impatto tempi',
    )
    ccb_quality_impact = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
        label='Impatto qualità',
    )
    ccb_other_impact = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
        label='Impatto su altri documenti / parti',
    )
    ccb_notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Note generali CCB',
    )

    def validate_for_submit(self):
        """Validazione aggiuntiva per l'invio alla CCB (non per il salvataggio bozza)."""
        errors = {}
        if not self.cleaned_data.get('ccb_class'):
            errors['ccb_class'] = 'La classificazione variante è obbligatoria prima dell\'invio.'
        if not (self.cleaned_data.get('ccb_requirements') or '').strip():
            errors['ccb_requirements'] = 'L\'analisi requisiti è obbligatoria prima dell\'invio.'
        if not (self.cleaned_data.get('ccb_technical_impact') or '').strip():
            errors['ccb_technical_impact'] = 'L\'impatto tecnico è obbligatorio prima dell\'invio.'
        if errors:
            raise forms.ValidationError(errors)


class ChangeNoticeCloseForm(forms.Form):
    """Form per la chiusura di un ECN approvato (Responsabile Qualità)."""

    close_notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}),
        required=False,
        label='Note di chiusura',
        help_text='Note del Responsabile Qualità a chiusura della variante.',
    )


class ChangeNoticeEditForm(forms.Form):
    """Form per la modifica dei dati base di un ECN in bozza.

    Identici campi di ChangeNoticeForm, usato per la view /ecn/<pk>/edit/.
    """

    title = forms.CharField(
        max_length=255,
        label='Titolo',
        help_text='Titolo breve e descrittivo della variante proposta.',
    )
    motivation = forms.ChoiceField(
        choices=ChangeNotice.Motivation.choices,
        label='Categoria motivazione',
    )
    motivation_detail = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Motivazione (dettaglio)',
        help_text='Descrizione estesa della motivazione della variante.',
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 4}),
        required=False,
        label='Descrizione modifica proposta',
        help_text='Descrizione della modifica tecnica proposta.',
    )
    commessa = forms.CharField(
        max_length=100,
        required=False,
        label='Commessa / ordine',
    )
    project = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label='Progetto',
        empty_label='— nessun progetto —',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from projects.models import Project
        self.fields['project'].queryset = Project.objects.order_by('code')


class ChangeNoticeReopenCCBForm(forms.Form):
    """Form per la riapertura della configurazione CCB di un ECN in revisione.

    La riapertura cancella tutte le decisioni esistenti e riporta l'ECN a DRAFT,
    consentendo di riconfigurare gli approvatori e la policy prima di reinviare.
    """

    reason = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Motivo della riapertura',
        help_text=(
            'Opzionale: spiega perché si riapre la configurazione CCB '
            '(es. approvatore errato, policy da cambiare).'
        ),
    )


class ChangeNoticeAttachmentForm(forms.Form):
    """Form per allegare un file a un ECN."""

    file = forms.FileField(
        label='File',
    )
    title = forms.CharField(
        max_length=255,
        required=False,
        label='Titolo',
        help_text='Titolo descrittivo del file (opzionale).',
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 2}),
        required=False,
        label='Descrizione',
    )
