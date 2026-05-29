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


class ChangeNoticeCCBConfigForm(forms.Form):
    """Form per la configurazione CCB di un ECN da parte del Responsabile Qualità / Manager.

    Seleziona approvatori e politica di approvazione.
    """

    ccb_policy = forms.ChoiceField(
        choices=ChangeNotice.CCBPolicy.choices,
        initial=ChangeNotice.CCBPolicy.ALL,
        label='Politica approvazione CCB',
        help_text='ANY: basta un approvatore; ALL: tutti devono approvare; SEQUENTIAL: in ordine.',
    )
    approvers = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        label='Approvatori CCB',
        help_text='Seleziona almeno un approvatore CCB. Per SEQUENTIAL l\'ordine di selezione è l\'ordine di approvazione.',
        widget=forms.CheckboxSelectMultiple,
        required=True,
        error_messages={
            'required': 'Seleziona almeno un approvatore CCB.',
        },
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth.models import Group
        from ecn.permissions import GROUP_CCB
        from documents.permissions import GROUP_MANAGERS, GROUP_APPROVERS

        candidate_group_names = [GROUP_CCB, GROUP_MANAGERS, GROUP_APPROVERS]
        qs = User.objects.none()
        for name in candidate_group_names:
            try:
                qs = qs | Group.objects.get(name=name).user_set.all()
            except Group.DoesNotExist:
                pass

        self.fields['approvers'].queryset = qs.distinct().order_by(
            'last_name', 'first_name', 'username'
        )


class ChangeNoticeReviewForm(forms.Form):
    """Form CCB per approvare o rifiutare un ECN (singolo approvatore).

    Validazione cross-field:
      - action == 'approve' → ccb_class obbligatorio
      - action == 'reject'  → ccb_notes obbligatorio
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
    ccb_class = forms.ChoiceField(
        choices=[('', '— seleziona classe —')] + list(ChangeNotice.CCBClass.choices),
        required=False,
        label='Classe variante',
        help_text='Obbligatorio in caso di approvazione.',
    )
    ccb_requirements = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Analisi requisiti',
        help_text='Conformità ai requisiti tecnici e normativi.',
    )
    ccb_technical_impact = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Impatto tecnico',
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
        label='Note CCB / Motivo rifiuto',
        help_text='Obbligatorio in caso di rifiuto: inserire il motivo.',
    )

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get('action')
        ccb_class = cleaned.get('ccb_class')
        ccb_notes = cleaned.get('ccb_notes', '').strip()

        if action == self.ACTION_APPROVE and not ccb_class:
            self.add_error(
                'ccb_class',
                'La classe variante è obbligatoria per approvare l\'ECN.',
            )
        if action == self.ACTION_REJECT and not ccb_notes:
            self.add_error(
                'ccb_notes',
                'Il motivo del rifiuto è obbligatorio.',
            )
        return cleaned


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
