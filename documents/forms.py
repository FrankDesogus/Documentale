from django import forms
from django.contrib.auth.models import User

from auditlog.historical_forms import SanatoriaFieldsMixin
from documents.models import Document
from documents.versioning import SequenceScheme, normalize_sequence_value, validate_sequence_value
from projects.models import ProjectFolder


class DocumentCreateForm(SanatoriaFieldsMixin, forms.Form):
    code = forms.CharField(
        max_length=50,
        label='Codice documento',
        help_text='Codice univoco (es. QUA-001)',
    )
    title = forms.CharField(max_length=255, label='Titolo')
    description = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Descrizione',
    )
    category = forms.ChoiceField(choices=Document.Category.choices, label='Categoria')
    document_type = forms.CharField(
        max_length=100,
        required=False,
        label='Tipo documento',
        help_text='Es. Procedura, Istruzione operativa, Modulo…',
    )
    project_folder = forms.ModelChoiceField(
        queryset=ProjectFolder.objects.none(),
        required=True,
        label='Cartella',
        empty_label='— seleziona cartella —',
        help_text='La cartella determina dove il documento viene archiviato e quali utenti possono accedervi.',
    )
    revision_scheme = forms.ChoiceField(
        choices=SequenceScheme.choices,
        initial=SequenceScheme.NUMERIC,
        label='Schema revisione',
        help_text='Numerica (00, 01…) o Alfabetica (A, B…). Applicato alle revisioni future.',
    )
    revision_label = forms.CharField(
        max_length=20,
        initial='00',
        label='Etichetta prima revisione',
        help_text='Numerica: 00. Alfabetica: A.',
    )
    revision_number = forms.IntegerField(
        min_value=0,
        initial=0,
        label='Numero revisione',
    )
    change_summary = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Sommario modifiche',
    )
    ecn_exemption = forms.BooleanField(
        required=False,
        initial=False,
        label='Consenti revisioni senza ECN obbligatorio',
        help_text=(
            'Se spuntato, le revisioni successive potranno essere create senza un ECN approvato. '
            'Il normale ciclo di approvazione rimane obbligatorio.'
        ),
    )
    file = forms.FileField(required=False, label='File operativo')

    def __init__(self, *args, user=None, fixed_project_folder=None, current_user=None, **kwargs):
        super().__init__(*args, current_user=current_user, **kwargs)
        if fixed_project_folder is not None:
            self.fields['project_folder'].queryset = ProjectFolder.objects.filter(
                pk=fixed_project_folder.pk
            )
            self.fields['project_folder'].initial = fixed_project_folder
            self.fields['project_folder'].empty_label = None
        elif user is not None and (user.is_superuser or user.is_staff):
            qs = ProjectFolder.objects.filter(status='active').order_by('code')
            self.fields['project_folder'].queryset = qs
        elif user is not None:
            from projects.permissions import get_writable_folder_ids
            writable_ids = get_writable_folder_ids(user)
            qs = ProjectFolder.objects.filter(pk__in=writable_ids, status='active').order_by('code')
            self.fields['project_folder'].queryset = qs

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if Document.objects.filter(code=code).exists():
            raise forms.ValidationError(f'Un documento con codice "{code}" esiste già.')
        return code

    def clean(self):
        cleaned = super().clean()
        scheme = cleaned.get('revision_scheme', SequenceScheme.NUMERIC)
        label = cleaned.get('revision_label', '')
        if label:
            try:
                label = normalize_sequence_value(label, scheme)
                validate_sequence_value(label, scheme)
                cleaned['revision_label'] = label
            except Exception as exc:
                self.add_error('revision_label', str(exc))
        return cleaned


class DocumentRevisionCreateForm(SanatoriaFieldsMixin, forms.Form):
    revision_label = forms.CharField(max_length=20, label='Etichetta revisione')
    revision_number = forms.IntegerField(min_value=0, label='Numero revisione')
    change_summary = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Sommario modifiche',
    )
    file = forms.FileField(required=False, label='File operativo')

    def __init__(self, *args, revision_scheme=None, current_user=None, **kwargs):
        super().__init__(*args, current_user=current_user, **kwargs)
        self._revision_scheme = revision_scheme

    def clean_revision_label(self):
        label = self.cleaned_data.get('revision_label', '')
        if self._revision_scheme and label:
            from django.core.exceptions import ValidationError as DjVE
            try:
                label = normalize_sequence_value(label, self._revision_scheme)
                validate_sequence_value(label, self._revision_scheme)
            except DjVE as exc:
                raise forms.ValidationError(str(exc))
        return label


class DocumentVersionEditForm(forms.Form):
    revision_label = forms.CharField(max_length=20, label='Etichetta revisione')
    revision_number = forms.IntegerField(min_value=0, label='Numero revisione')
    change_summary = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Sommario modifiche',
    )
    file = forms.FileField(
        required=False,
        label='Sostituisci file operativo',
        help_text='Lascia vuoto per mantenere il file esistente.',
    )

    def __init__(self, *args, revision_scheme=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._revision_scheme = revision_scheme

    def clean_revision_label(self):
        label = self.cleaned_data.get('revision_label', '')
        if self._revision_scheme and label:
            from django.core.exceptions import ValidationError as DjVE
            try:
                label = normalize_sequence_value(label, self._revision_scheme)
                validate_sequence_value(label, self._revision_scheme)
            except DjVE as exc:
                raise forms.ValidationError(str(exc))
        return label


class DocumentMetadataEditForm(forms.ModelForm):
    revision_scheme = forms.ChoiceField(
        choices=SequenceScheme.choices,
        label='Schema revisione',
        help_text=(
            'Il cambio dello schema non modifica le revisioni storiche. '
            'La prima revisione successiva dovrà essere inserita manualmente.'
        ),
    )

    class Meta:
        model = Document
        fields = ['title', 'description', 'revision_scheme']
        labels = {
            'title': 'Titolo',
            'description': 'Descrizione',
        }
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class ApproverRowForm(forms.Form):
    approver = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True).order_by('last_name', 'first_name', 'username'),
        label='Approvatore',
        empty_label='— seleziona utente —',
        required=False,
    )

    def __init__(self, *args, current_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from config.demo_utils import is_demo_supervisor
        if current_user and is_demo_supervisor(current_user):
            # Demo mode: l'unico approvatore selezionabile è il supervisore stesso
            self.fields['approver'].queryset = User.objects.filter(pk=current_user.pk)
        else:
            self.fields['approver'].queryset = User.objects.filter(
                is_active=True
            ).order_by('last_name', 'first_name', 'username')


class BaseApproverFormSet(forms.BaseFormSet):
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
            if form.cleaned_data and form.cleaned_data.get('approver'):
                approver = form.cleaned_data['approver']
                if approver in selected:
                    raise forms.ValidationError(
                        f"L'utente '{approver}' è selezionato più di una volta."
                    )
                selected.append(approver)
        if not selected:
            raise forms.ValidationError("Devi selezionare almeno un approvatore.")


ApproverFormSet = forms.formset_factory(
    ApproverRowForm,
    formset=BaseApproverFormSet,
    extra=0,
)


class SubmitForApprovalForm(SanatoriaFieldsMixin, forms.Form):
    approval_policy = forms.ChoiceField(
        choices=[
            ('any', 'Basta un approvatore'),
            ('all', 'Devono approvare tutti'),
            ('sequential', 'Approvazione sequenziale'),
        ],
        initial='all',
        label='Modalità approvazione',
    )
    due_date = forms.DateField(
        required=False,
        label='Scadenza approvazione',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    signature_template_file = forms.FileField(
        required=False,
        label='Modello da firmare',
        help_text='Allegato opzionale consultabile dagli approvatori.',
    )
