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
    # Progetti associati a questa cartella (per mostrare link e bottone "Crea progetto")
    folder_projects = Project.objects.filter(folder=folder).select_related('manager').order_by('code')
    return render(request, 'projects/folder_detail.html', {
        'folder': folder,
        'subfolders': subfolders,
        'documents': documents,
        'folder_projects': folder_projects,
        'can_create': _can_create_folder(request.user),
        'can_manage': can_manage_folder(request.user, folder),
        'can_create_project': _can_manage_project(request.user),
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

    from projects.services import build_project_baseline_comparison
    current_baseline, comparison_rows = build_project_baseline_comparison(project)

    from projects.permissions import can_create_document_in_folder
    can_create_doc = (
        project.folder is not None
        and can_create_document_in_folder(request.user, project.folder)
    )

    from documents.permissions import can_view_audit
    show_audit = can_view_audit(request.user, folder=project.folder)

    audit_logs = None
    if show_audit:
        from auditlog.models import AuditLog
        from django.db.models import Q
        from documents.models import Document as _Doc
        from projects.services import get_project_document_folders
        _folders = get_project_document_folders(project)
        _doc_ids = []
        if _folders:
            _doc_ids = list(_Doc.objects.filter(
                project_folder__in=_folders
            ).values_list('pk', flat=True))
        audit_logs = AuditLog.objects.filter(
            Q(changes__project_id=project.pk)
            | Q(changes__document_id__in=_doc_ids)
        ).select_related('user').order_by('-timestamp')[:20]

    # ECN collegati ai documenti nelle cartelle del progetto
    project_ecns = []
    if project.folder:
        from ecn.models import ChangeNotice
        project_ecns = list(
            ChangeNotice.objects
            .filter(document__project_folder=project.folder)
            .select_related('document', 'proposed_by')
            .order_by('-proposed_at')[:20]
        )

    return render(request, 'projects/project_detail.html', {
        'project': project,
        'documents': documents,
        'subfolders': subfolders,
        'revisions': revisions,
        'can_manage': _can_manage_project(request.user),
        'can_create_doc': can_create_doc,
        'current_baseline': current_baseline,
        'comparison_rows': comparison_rows,
        'show_audit': show_audit,
        'audit_logs': audit_logs,
        'project_ecns': project_ecns,
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
    from django.db import transaction
    from projects.forms import ProjectRevisionForm
    from projects.services import create_project_revision, populate_project_revision_from_current_documents

    project = get_object_or_404(Project, pk=project_id)

    if not _can_manage_project(request.user):
        raise PermissionDenied

    last = project.revisions.order_by('-revision_number').first()
    next_number = (last.revision_number + 1) if last else 0
    next_label = f'{next_number:02d}'

    if request.method == 'POST':
        form = ProjectRevisionForm(request.POST, project=project)
        if form.is_valid():
            d = form.cleaned_data
            with transaction.atomic():
                revision = create_project_revision(
                    project=project,
                    created_by=request.user,
                    revision_label=d['revision_label'],
                    revision_number=d['revision_number'],
                    title=d['title'],
                    description=d['description'],
                )
                added = populate_project_revision_from_current_documents(revision)
            if added == 0:
                messages.warning(
                    request,
                    f'Baseline {revision.revision_label} creata senza documenti: '
                    'nessun documento approvato trovato nelle cartelle del progetto.',
                )
            else:
                messages.success(
                    request,
                    f'Baseline {revision.revision_label} creata con {added} documenti.',
                )
            return redirect('project_revision_detail', revision_id=revision.pk)
    else:
        form = ProjectRevisionForm(
            project=project,
            initial={'revision_number': next_number, 'revision_label': next_label},
        )

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
