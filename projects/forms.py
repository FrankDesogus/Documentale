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


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ['code', 'name', 'description', 'status', 'project_type', 'folder', 'manager']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['folder'].queryset = ProjectFolder.objects.filter(
            status=ProjectFolder.Status.ACTIVE
        ).order_by('code')
        self.fields['folder'].required = False
        self.fields['folder'].empty_label = '— nessuna —'
        self.fields['manager'].queryset = User.objects.filter(
            is_active=True
        ).order_by('last_name', 'first_name', 'username')
        self.fields['manager'].required = False
        self.fields['manager'].empty_label = '— nessuno —'


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
