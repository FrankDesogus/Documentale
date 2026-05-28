"""Form per l'app ECN / Varianti."""

from django import forms

from ecn.models import ChangeNotice


class ChangeNoticeForm(forms.Form):
    """Form per la creazione di un nuovo ECN (step ECN-B)."""

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


class ChangeNoticeReviewForm(forms.Form):
    """Form CCB per approvare o rifiutare un ECN.

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
        label='Note CCB',
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
