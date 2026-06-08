from django import forms
from django.contrib.auth.models import User

from projects.models import Project, ProjectFolder, ProjectRevision


class ProjectFolderForm(forms.ModelForm):
    class Meta:
        model = ProjectFolder
        fields = ['code', 'name', 'description', 'folder_kind', 'parent', 'status']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Nei parent mostra solo cartelle attive; escludi se stesso in edit
        qs = ProjectFolder.objects.filter(status=ProjectFolder.Status.ACTIVE).order_by('code')
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        self.fields['parent'].queryset = qs
        self.fields['parent'].empty_label = '— nessuna (cartella radice) —'
        self.fields['parent'].required = False


class ProjectUpdateForm(forms.ModelForm):
    """
    Form per la modifica dei metadati di un progetto esistente.
    Codice, root folder e parent non sono modificabili.
    """
    class Meta:
        model = Project
        fields = ['name', 'description', 'manager', 'version', 'revision']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['manager'].queryset = User.objects.filter(
            is_active=True
        ).order_by('last_name', 'first_name', 'username')
        self.fields['manager'].required = False
        self.fields['manager'].empty_label = '— nessuno —'

    def clean_version(self):
        v = self.cleaned_data.get('version', '').strip()
        if not v:
            raise forms.ValidationError('La versione non può essere vuota.')
        return v

    def clean_revision(self):
        r = self.cleaned_data.get('revision', '').strip()
        if not r:
            raise forms.ValidationError('La revisione non può essere vuota.')
        return r


# Mantenuto per compatibilità: usato dai test legacy che importano ProjectForm
class ProjectForm(ProjectUpdateForm):
    pass


class ProjectCreateForm(forms.Form):
    """
    Form per la CREAZIONE di un nuovo progetto con root folder atomica.
    NON include root_folder: viene creata automaticamente dal service.
    """
    parent_folder = forms.ModelChoiceField(
        queryset=ProjectFolder.objects.none(),
        label='Cartella padre',
        help_text='La root folder del progetto verrà creata come sottocartella di questa cartella.',
        empty_label='— seleziona cartella —',
    )
    code = forms.CharField(
        max_length=50,
        label='Codice progetto',
        help_text='Codice univoco. Sarà usato anche per la root folder.',
    )
    name = forms.CharField(max_length=255, label='Nome progetto')
    description = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3}),
        required=False,
        label='Descrizione',
    )
    project_type = forms.ChoiceField(
        choices=Project.ProjectType.choices,
        initial=Project.ProjectType.OTHER,
        label='Tipo',
    )
    manager = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True).order_by('last_name', 'first_name'),
        required=False,
        label='Responsabile',
        empty_label='— nessuno —',
    )
    version = forms.CharField(
        max_length=32,
        initial='0',
        required=True,
        label='Versione',
        help_text='Es. 0, 1, 2.1, A — modificabile manualmente in qualsiasi momento.',
    )
    revision = forms.CharField(
        max_length=32,
        initial='0',
        required=True,
        label='Revisione',
        help_text='Es. 0, 1, A, Rev. C — modificabile manualmente in qualsiasi momento.',
    )

    def __init__(self, *args, allowed_parent_folders=None, **kwargs):
        super().__init__(*args, **kwargs)
        if allowed_parent_folders is not None:
            self.fields['parent_folder'].queryset = allowed_parent_folders
        else:
            self.fields['parent_folder'].queryset = ProjectFolder.objects.filter(
                status=ProjectFolder.Status.ACTIVE,
            ).order_by('code')

    def clean_code(self):
        code = self.cleaned_data['code'].strip()
        if Project.objects.filter(code=code).exists():
            raise forms.ValidationError(f"Esiste già un progetto con codice '{code}'.")
        if ProjectFolder.objects.filter(code=code).exists():
            raise forms.ValidationError(
                f"Esiste già una cartella con codice '{code}'. "
                "Il codice deve essere unico anche tra le cartelle."
            )
        return code

    def clean_version(self):
        v = self.cleaned_data.get('version', '').strip()
        if not v:
            raise forms.ValidationError('La versione non può essere vuota.')
        return v

    def clean_revision(self):
        r = self.cleaned_data.get('revision', '').strip()
        if not r:
            raise forms.ValidationError('La revisione non può essere vuota.')
        return r


class ProjectRevisionForm(forms.ModelForm):
    class Meta:
        model = ProjectRevision
        fields = ['revision_label', 'revision_number', 'title', 'description']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'revision_label': 'Etichetta (es. 00, 01, A, B)',
            'revision_number': 'Numero progressivo',
        }

    def __init__(self, *args, project=None, **kwargs):
        self._project = project
        super().__init__(*args, **kwargs)

    def clean_revision_label(self):
        label = self.cleaned_data.get('revision_label')
        if self._project and label:
            qs = ProjectRevision.objects.filter(
                project=self._project, revision_label=label
            )
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    f'Esiste già una baseline con etichetta "{label}" per questo progetto.'
                )
        return label

    def clean_revision_number(self):
        number = self.cleaned_data.get('revision_number')
        if self._project and number is not None:
            qs = ProjectRevision.objects.filter(
                project=self._project, revision_number=number
            )
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError(
                    f'Esiste già una baseline con numero progressivo {number} per questo progetto.'
                )
        return number
