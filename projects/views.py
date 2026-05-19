from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from projects.models import Project, ProjectFolder, ProjectRevision
from projects.permissions import can_manage_folder, can_view_folder


def _can_create_folder(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    from documents.permissions import is_document_manager
    return is_document_manager(user)


@login_required
def folder_list(request):
    user = request.user
    qs = ProjectFolder.objects.filter(
        status=ProjectFolder.Status.ACTIVE,
        parent__isnull=True,
    ).prefetch_related('subfolders').order_by('code')

    if not (user.is_superuser or user.is_staff):
        from documents.permissions import is_document_manager, is_document_auditor
        if not (is_document_manager(user) or is_document_auditor(user)):
            from projects.permissions import get_visible_folder_ids
            visible_ids = get_visible_folder_ids(user)
            qs = qs.filter(pk__in=visible_ids)

    return render(request, 'projects/folder_list.html', {
        'folders': qs,
        'can_create': _can_create_folder(user),
    })


@login_required
def folder_detail(request, folder_id):
    folder = get_object_or_404(ProjectFolder, pk=folder_id)

    if not can_view_folder(request.user, folder):
        raise PermissionDenied

    subfolders = folder.subfolders.order_by('code')
    documents = folder.documents.select_related(
        'current_version', 'owner',
    ).order_by('code')
    return render(request, 'projects/folder_detail.html', {
        'folder': folder,
        'subfolders': subfolders,
        'documents': documents,
        'can_create': _can_create_folder(request.user),
        'can_manage': can_manage_folder(request.user, folder),
    })


@login_required
def folder_create(request):
    from projects.forms import ProjectFolderForm

    if not _can_create_folder(request.user):
        raise PermissionDenied

    if request.method == 'POST':
        form = ProjectFolderForm(request.POST)
        if form.is_valid():
            folder = form.save(commit=False)
            folder.owner = request.user
            folder.created_by = request.user
            folder.save()
            messages.success(request, f'Cartella "{folder.name}" creata.')
            return redirect('folder_detail', folder_id=folder.pk)
    else:
        form = ProjectFolderForm()

    return render(request, 'projects/folder_form.html', {'form': form})


# ---------------------------------------------------------------------------
# Project views
# ---------------------------------------------------------------------------

def _can_manage_project(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    from documents.permissions import is_document_manager
    return is_document_manager(user)


@login_required
def project_list(request):
    qs = Project.objects.select_related('folder', 'manager').order_by('code')

    if not _can_manage_project(request.user):
        from projects.permissions import get_visible_folder_ids
        visible_ids = get_visible_folder_ids(request.user)
        qs = qs.filter(
            Q(folder__isnull=False) & Q(folder_id__in=visible_ids)
        )

    return render(request, 'projects/project_list.html', {
        'projects': qs,
        'can_create': _can_manage_project(request.user),
    })


@login_required
def project_detail(request, project_id):
    project = get_object_or_404(Project, pk=project_id)

    if not _can_manage_project(request.user):
        if project.folder and can_view_folder(request.user, project.folder):
            pass
        else:
            raise PermissionDenied

    documents = []
    subfolders = []
    if project.folder:
        documents = project.folder.documents.select_related(
            'current_version', 'owner'
        ).order_by('code')
        subfolders = project.folder.subfolders.order_by('code')

    revisions = project.revisions.order_by('-revision_number')

    return render(request, 'projects/project_detail.html', {
        'project': project,
        'documents': documents,
        'subfolders': subfolders,
        'revisions': revisions,
        'can_manage': _can_manage_project(request.user),
    })


@login_required
def project_create(request):
    from projects.forms import ProjectForm

    if not _can_manage_project(request.user):
        raise PermissionDenied

    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.created_by = request.user
            project.save()
            messages.success(request, f'Progetto "{project.name}" creato.')
            return redirect('project_detail', project_id=project.pk)
    else:
        form = ProjectForm()

    return render(request, 'projects/project_form.html', {'form': form})


# ---------------------------------------------------------------------------
# ProjectRevision (baseline) views
# ---------------------------------------------------------------------------

@login_required
def project_revision_create(request, project_id):
    from projects.forms import ProjectRevisionForm
    from projects.services import create_project_revision, populate_project_revision_from_current_documents

    project = get_object_or_404(Project, pk=project_id)

    if not _can_manage_project(request.user):
        raise PermissionDenied

    if request.method == 'POST':
        form = ProjectRevisionForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            revision = create_project_revision(
                project=project,
                created_by=request.user,
                revision_label=d['revision_label'],
                revision_number=d['revision_number'],
                title=d['title'],
                description=d['description'],
            )
            added = populate_project_revision_from_current_documents(revision)
            messages.success(
                request,
                f'Baseline {revision.revision_label} creata con {added} documenti.'
            )
            return redirect('project_revision_detail', revision_id=revision.pk)
    else:
        form = ProjectRevisionForm()

    return render(request, 'projects/project_revision_form.html', {
        'form': form,
        'project': project,
    })


@login_required
def project_revision_detail(request, revision_id):
    revision = get_object_or_404(
        ProjectRevision.objects.select_related('project', 'created_by', 'issued_by'),
        pk=revision_id,
    )
    project = revision.project

    if not _can_manage_project(request.user):
        if project and project.folder and can_view_folder(request.user, project.folder):
            pass
        else:
            raise PermissionDenied

    items = revision.items.select_related(
        'document_version', 'document_version__document'
    ).order_by('item_number')

    can_issue = (
        _can_manage_project(request.user)
        and revision.status == ProjectRevision.Status.DRAFT
    )

    return render(request, 'projects/project_revision_detail.html', {
        'revision': revision,
        'project': project,
        'items': items,
        'can_issue': can_issue,
        'can_manage': _can_manage_project(request.user),
    })


@login_required
def project_revision_issue(request, revision_id):
    from projects.services import issue_project_revision

    revision = get_object_or_404(ProjectRevision, pk=revision_id)

    if not _can_manage_project(request.user):
        raise PermissionDenied

    if request.method == 'POST':
        try:
            issue_project_revision(revision, request.user)
            messages.success(
                request,
                f'Baseline {revision.revision_label} emessa.'
            )
        except ValueError as e:
            messages.error(request, str(e))

    return redirect('project_revision_detail', revision_id=revision.pk)
