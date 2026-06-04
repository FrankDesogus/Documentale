from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from projects.models import Project, ProjectFolder, ProjectRevision
from projects.permissions import can_manage_folder, can_view_folder


def _can_create_folder(user):
    """MB1: is_staff NON concede creazione cartelle."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from documents.permissions import is_document_manager
    return is_document_manager(user)


@login_required
def folder_list(request):
    from django.core.paginator import Paginator

    user = request.user
    qs = ProjectFolder.objects.filter(
        status=ProjectFolder.Status.ACTIVE,
        parent__isnull=True,
    ).prefetch_related('subfolders').order_by('code')

    # is_staff NON concede visibilità globale cartelle (MB1)
    nav_ids_set = set()
    if not user.is_superuser:
        from documents.permissions import is_document_manager, is_document_auditor
        if not (is_document_manager(user) or is_document_auditor(user)):
            from projects.permissions import get_visible_folder_ids, get_navigation_folder_ids
            visible_ids = set(get_visible_folder_ids(user))
            nav_ids_set = get_navigation_folder_ids(user)
            qs = qs.filter(pk__in=visible_ids | nav_ids_set)

    # Ricerca
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))

    paginator = Paginator(qs, 24)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'projects/folder_list.html', {
        'folders': page_obj,
        'page_obj': page_obj,
        'q': q,
        'can_create': _can_create_folder(user),
        'total_count': paginator.count,
    })


@login_required
def folder_detail(request, folder_id):
    folder = get_object_or_404(ProjectFolder, pk=folder_id)
    user = request.user

    is_readable = can_view_folder(user, folder)
    is_navigation_only = False

    if not is_readable:
        if user.is_superuser:
            is_readable = True
        else:
            from projects.permissions import get_navigation_folder_ids
            if folder.pk in get_navigation_folder_ids(user):
                is_navigation_only = True
            else:
                raise PermissionDenied

    # Cartella navigation-only: solo contenitore, nessun contenuto operativo
    if is_navigation_only:
        from projects.permissions import get_visible_folder_ids, get_navigation_folder_ids
        accessible_ids = set(get_visible_folder_ids(user)) | get_navigation_folder_ids(user)
        subfolders = folder.subfolders.filter(pk__in=accessible_ids).order_by('code')
        return render(request, 'projects/folder_detail.html', {
            'folder': folder,
            'subfolders': subfolders,
            'documents': [],
            'folder_projects': [],
            'can_create': False,
            'can_manage': False,
            'can_create_project': False,
            'is_navigation_only': True,
        })

    subfolders = folder.subfolders.order_by('code')

    # Documenti visibili nella cartella:
    # - documenti con versione corrente approvata
    # - documenti per cui l'utente è autore di almeno una bozza privata
    # Le bozze altrui non vengono esposte nella navigazione (MB1)
    from django.db.models import Exists, OuterRef
    from documents.models import DocumentVersion as _DocVer
    base_docs = folder.documents.select_related('current_version', 'owner').order_by('code')
    if user.is_superuser:
        documents = base_docs
    else:
        own_draft_qs = _DocVer.objects.filter(
            document=OuterRef('pk'),
            created_by=user,
            status__in=[_DocVer.Status.DRAFT, _DocVer.Status.REJECTED, _DocVer.Status.IN_APPROVAL],
        )
        documents = base_docs.filter(
            Q(current_version__isnull=False,
              current_version__status=_DocVer.Status.APPROVED)
            | Q(Exists(own_draft_qs))
        )

    # Progetti: visibili solo se l'utente ha view_projects sulla cartella
    from projects.resolver import has_folder_permission as _has_fperm
    can_view_projs = _has_fperm(user, folder, 'view_projects', include_legacy_fallback=True)
    folder_projects = (
        Project.objects.filter(folder=folder).select_related('manager').order_by('code')
        if can_view_projs else
        Project.objects.none()
    )

    return render(request, 'projects/folder_detail.html', {
        'folder': folder,
        'subfolders': subfolders,
        'documents': documents,
        'folder_projects': folder_projects,
        'can_create': _can_create_folder(user),
        'can_manage': can_manage_folder(user, folder),
        'can_create_project': _can_manage_project(user),
        'is_navigation_only': False,
    })


@login_required
def folder_create(request):
    from projects.forms import ProjectFolderForm

    if not _can_create_folder(request.user):
        raise PermissionDenied

    # Supporto ?parent=<folder_id> per precompilare la cartella padre
    parent_folder = None
    parent_id = request.GET.get('parent') or request.POST.get('_parent_prefill')
    if parent_id:
        try:
            parent_folder = ProjectFolder.objects.get(pk=int(parent_id))
        except (ProjectFolder.DoesNotExist, ValueError, TypeError):
            parent_folder = None

    if request.method == 'POST':
        form = ProjectFolderForm(request.POST)
        if form.is_valid():
            folder = form.save(commit=False)
            folder.owner = request.user
            folder.created_by = request.user
            folder.save()
            messages.success(request, f'Cartella "{folder.name}" creata.')
            # Torna alla cartella padre se disponibile, altrimenti alla cartella creata
            if folder.parent_id:
                return redirect('folder_detail', folder_id=folder.parent_id)
            return redirect('folder_detail', folder_id=folder.pk)
    else:
        initial = {}
        if parent_folder:
            initial['parent'] = parent_folder.pk
        form = ProjectFolderForm(initial=initial)

    return render(request, 'projects/folder_form.html', {
        'form': form,
        'parent_folder': parent_folder,
    })


# ---------------------------------------------------------------------------
# Project views
# ---------------------------------------------------------------------------

def _can_manage_project(user):
    """MB1: is_staff NON concede gestione progetti."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from documents.permissions import is_document_manager
    return is_document_manager(user)


@login_required
def project_list(request):
    from django.core.paginator import Paginator

    qs = Project.objects.select_related('folder', 'manager').order_by('code')

    if not _can_manage_project(request.user):
        from projects.permissions import get_project_visible_folder_ids
        visible_ids = get_project_visible_folder_ids(request.user)
        qs = qs.filter(
            Q(folder__isnull=False) & Q(folder_id__in=visible_ids)
        )

    # Ricerca e filtri
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(code__icontains=q) | Q(name__icontains=q) | Q(description__icontains=q)
        )

    status_filter = request.GET.get('status', '').strip()
    if status_filter and status_filter in [s.value for s in Project.Status]:
        qs = qs.filter(status=status_filter)

    folder_id = request.GET.get('folder', '').strip()
    if folder_id:
        try:
            qs = qs.filter(folder_id=int(folder_id))
        except (ValueError, TypeError):
            pass

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'projects/project_list.html', {
        'projects': page_obj,
        'page_obj': page_obj,
        'q': q,
        'status_filter': status_filter,
        'folder_id': folder_id,
        'status_choices': Project.Status.choices,
        'can_create': _can_manage_project(request.user),
        'total_count': paginator.count,
    })


@login_required
def project_detail(request, project_id):
    project = get_object_or_404(Project, pk=project_id)

    if not _can_manage_project(request.user):
        if project.folder:
            # Document Auditor globale può vedere il dettaglio progetto (per audit)
            from projects.permissions import _is_privileged
            if not _is_privileged(request.user):
                from projects.resolver import has_folder_permission as _has_fperm
                if not _has_fperm(
                    request.user, project.folder, 'view_projects',
                    include_legacy_fallback=True,
                ):
                    raise PermissionDenied
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

    # Supporto ?folder=<folder_id> per precompilare la cartella documentale
    prefill_folder = None
    folder_id = request.GET.get('folder') or request.POST.get('_folder_prefill')
    if folder_id:
        try:
            prefill_folder = ProjectFolder.objects.get(pk=int(folder_id))
        except (ProjectFolder.DoesNotExist, ValueError, TypeError):
            prefill_folder = None

    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.created_by = request.user
            project.save()
            messages.success(request, f'Progetto "{project.name}" creato.')
            return redirect('project_detail', project_id=project.pk)
    else:
        initial = {}
        if prefill_folder:
            initial['folder'] = prefill_folder.pk
        form = ProjectForm(initial=initial)

    return render(request, 'projects/project_form.html', {
        'form': form,
        'prefill_folder': prefill_folder,
    })


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
        if project and project.folder:
            from projects.permissions import _is_privileged
            if not _is_privileged(request.user):
                from projects.resolver import has_folder_permission as _has_fperm
                if not _has_fperm(
                    request.user, project.folder, 'view_projects',
                    include_legacy_fallback=True,
                ):
                    raise PermissionDenied
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
