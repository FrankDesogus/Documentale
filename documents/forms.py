from django import forms
from django.contrib.auth.models import User

from documents.models import Document
from projects.models import ProjectFolder


class DocumentCreateForm(forms.Form):
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
        required=False,
        label='Cartella',
        empty_label='— nessuna —',
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and (user.is_superuser or user.is_staff):
            qs = ProjectFolder.objects.filter(status='active').order_by('code')
        elif user is not None:
            from projects.permissions import get_writable_folder_ids
            writable_ids = get_writable_folder_ids(user)
            qs = ProjectFolder.objects.filter(pk__in=writable_ids, status='active').order_by('code')
        else:
            qs = ProjectFolder.objects.none()
        self.fields['project_folder'].queryset = qs
    revision_label = forms.CharField(
        max_length=20,
        initial='00',
        label='Etichetta revisione',
        help_text='Es. 00, A, Rev.00',
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
    file = forms.FileField(required=False, label='File operativo')

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if Document.objects.filter(code=code).exists():
            raise forms.ValidationError(f'Un documento con codice "{code}" esiste già.')
        return code


class DocumentRevisionCreateForm(forms.Form):
    revision_label = forms.CharField(max_length=20, label='Etichetta revisione')
    revision_number = forms.IntegerField(min_value=0, label='Numero revisione')
    change_summary = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Sommario modifiche',
    )
    file = forms.FileField(required=False, label='File operativo')


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


class SubmitForApprovalForm(forms.Form):
    approvers = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(is_active=True).order_by('last_name', 'first_name', 'username'),
        widget=forms.CheckboxSelectMultiple,
        label='Approvatori',
    )
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
