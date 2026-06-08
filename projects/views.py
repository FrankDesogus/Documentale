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
    folder = get_object_or_404(
        ProjectFolder.objects.select_related('parent', 'root_project'),
        pk=folder_id,
    )
    user = request.user

    # Redirect: se la cartella è una root folder di progetto, vai al project_detail
    if folder.folder_kind == ProjectFolder.FolderKind.PROJECT:
        try:
            linked_project = folder.root_project  # OneToOneField reverse
            return redirect('project_detail', project_id=linked_project.pk)
        except Project.DoesNotExist:
            pass  # Cartella PROJECT orfana: mostra come cartella normale

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

    # ── Explorer unificato: sottocartelle ordinarie + root folder progetti ──

    # Progetti la cui root folder è figlia diretta di questa cartella
    from projects.resolver import has_folder_permission as _has_fperm
    from projects.permissions import _is_global_manager
    # I manager globali (Document Manager, superuser) vedono i progetti in tutte le cartelle
    can_view_projs = (
        _is_global_manager(user)
        or _has_fperm(user, folder, 'view_projects', include_legacy_fallback=True)
    )

    # Root folder dei progetti figli (folder_kind=PROJECT con root_project collegato)
    child_project_roots_qs = folder.subfolders.filter(
        folder_kind=ProjectFolder.FolderKind.PROJECT
    ).order_by('code')

    # IDs delle root folder progetto da escludere dalle sottocartelle ordinarie
    project_root_ids = set(child_project_roots_qs.values_list('pk', flat=True))

    # Sottocartelle ordinarie (escluse le root folder di progetto)
    subfolders = folder.subfolders.exclude(
        pk__in=project_root_ids
    ).order_by('code')

    # Progetti collegati alle root folder figlie (se l'utente ha view_projects)
    if can_view_projs and project_root_ids:
        child_projects_raw = list(
            Project.objects.filter(
                root_folder__in=project_root_ids
            ).select_related('root_folder', 'manager').order_by('code')
        )
        # Associazione root_folder_id → project per il template
        project_by_root = {p.root_folder_id: p for p in child_projects_raw}
    else:
        child_projects_raw = []
        project_by_root = {}

    # Costruisci lista explorer unificata: (kind, subfolder/project)
    explorer_items = []
    for sub in folder.subfolders.order_by('code'):
        if sub.pk in project_root_ids:
            proj = project_by_root.get(sub.pk)
            if proj:
                explorer_items.append(('project', proj))
            else:
                # Root PROJECT orfana: folder_kind=PROJECT ma nessun progetto collegato
                explorer_items.append(('orphan_project_folder', sub))
        else:
            explorer_items.append(('folder', sub))

    # Documenti visibili nella cartella (usati in modalità explorer e come base per ricerca)
    from django.db.models import Exists, OuterRef
    from documents.models import DocumentVersion as _DocVer, Document as _Doc
    base_docs = folder.documents.select_related('current_version', 'owner').order_by('code')
    if user.is_superuser:
        visible_docs_qs = base_docs
    else:
        own_draft_qs = _DocVer.objects.filter(
            document=OuterRef('pk'),
            created_by=user,
            status__in=[_DocVer.Status.DRAFT, _DocVer.Status.REJECTED, _DocVer.Status.IN_APPROVAL],
        )
        visible_docs_qs = base_docs.filter(
            Q(current_version__isnull=False,
              current_version__status=_DocVer.Status.APPROVED)
            | Q(Exists(own_draft_qs))
        )

    # ── Ricerca contestuale ──────────────────────────────────────────────────
    from django.core.paginator import Paginator as _Paginator
    search_q = request.GET.get('q', '').strip()
    recursive = request.GET.get('recursive', '') == '1'
    search_results = None
    search_page_obj = None

    if search_q:
        from projects.services import get_folder_descendants
        from projects.permissions import get_visible_folder_ids, get_navigation_folder_ids

        # Cartelle accessibili all'utente (per filtrare rami negati)
        if user.is_superuser:
            from projects.permissions import _is_privileged
            accessible_folder_ids = None  # nessuna restrizione
        else:
            accessible_folder_ids = set(get_visible_folder_ids(user)) | get_navigation_folder_ids(user)

        def _folder_accessible(f):
            if accessible_folder_ids is None:
                return True
            return f.pk in accessible_folder_ids

        results = []

        if recursive:
            # Scope: questa cartella + tutti i discendenti accessibili
            scope_folders_qs = get_folder_descendants(folder)
            # Includi anche la cartella corrente nei documenti
            all_scope_ids = [folder.pk] + list(scope_folders_qs.values_list('pk', flat=True))
            if accessible_folder_ids is not None:
                all_scope_ids = [pk for pk in all_scope_ids if pk in accessible_folder_ids or pk == folder.pk]

            # Documenti nelle sottocartelle discendenti (esclusa la root che ha già visible_docs_qs)
            if user.is_superuser:
                own_draft_sub = None
                docs_qs = _Doc.objects.filter(
                    project_folder_id__in=all_scope_ids
                ).select_related('current_version', 'owner', 'project_folder').order_by('code')
            else:
                from django.db.models import Exists as _Exists, OuterRef as _OR
                own_draft_sub = _DocVer.objects.filter(
                    document=_OR('pk'), created_by=user,
                    status__in=[_DocVer.Status.DRAFT, _DocVer.Status.REJECTED, _DocVer.Status.IN_APPROVAL],
                )
                docs_qs = _Doc.objects.filter(
                    project_folder_id__in=all_scope_ids
                ).filter(
                    Q(current_version__isnull=False, current_version__status=_DocVer.Status.APPROVED)
                    | Q(_Exists(own_draft_sub))
                ).select_related('current_version', 'owner', 'project_folder').order_by('code')

            # Applica filtro testuale ai documenti
            docs_qs = docs_qs.filter(
                Q(code__icontains=search_q)
                | Q(title__icontains=search_q)
                | Q(description__icontains=search_q)
                | Q(document_type__icontains=search_q)
            )
            for doc in docs_qs:
                results.append(('document', doc))

            # Sottocartelle discendenti che matchano
            desc_qs = scope_folders_qs.filter(
                folder_kind=ProjectFolder.FolderKind.GENERIC
            ).filter(
                Q(code__icontains=search_q) | Q(name__icontains=search_q) | Q(description__icontains=search_q)
            ).order_by('code')
            if accessible_folder_ids is not None:
                desc_qs = desc_qs.filter(pk__in=accessible_folder_ids)
            for sub in desc_qs:
                results.append(('folder', sub))

            # Progetti discendenti
            if can_view_projs:
                proj_root_qs = scope_folders_qs.filter(folder_kind=ProjectFolder.FolderKind.PROJECT)
                if accessible_folder_ids is not None:
                    proj_root_qs = proj_root_qs.filter(pk__in=accessible_folder_ids)
                proj_ids = list(proj_root_qs.values_list('pk', flat=True))
                if proj_ids:
                    projs_qs = Project.objects.filter(
                        root_folder__in=proj_ids
                    ).filter(
                        Q(code__icontains=search_q) | Q(name__icontains=search_q) | Q(description__icontains=search_q)
                    ).select_related('root_folder', 'manager').order_by('code')
                    for proj in projs_qs:
                        results.append(('project', proj))

        else:
            # Scope: solo contenuto immediato della cartella

            # Documenti immediati con filtro testuale
            doc_results = visible_docs_qs.filter(
                Q(code__icontains=search_q)
                | Q(title__icontains=search_q)
                | Q(description__icontains=search_q)
                | Q(document_type__icontains=search_q)
            )
            for doc in doc_results:
                results.append(('document', doc))

            # Sottocartelle ordinarie immediate
            sub_results = subfolders.filter(
                Q(code__icontains=search_q) | Q(name__icontains=search_q) | Q(description__icontains=search_q)
            )
            for sub in sub_results:
                results.append(('folder', sub))

            # Progetti figli immediati
            if can_view_projs and project_root_ids:
                proj_results = [
                    p for p in child_projects_raw
                    if search_q.lower() in p.code.lower()
                    or search_q.lower() in p.name.lower()
                    or search_q.lower() in (p.description or '').lower()
                ]
                for proj in proj_results:
                    results.append(('project', proj))

        # Paginazione risultati ricerca
        search_paginator = _Paginator(results, 20)
        search_page_obj = search_paginator.get_page(request.GET.get('page', 1))
        search_results = search_page_obj

    return render(request, 'projects/folder_detail.html', {
        'folder': folder,
        'explorer_items': explorer_items,
        'subfolders': subfolders,
        'documents': visible_docs_qs,
        'folder_projects': child_projects_raw,  # backward compat per sezione ECN ecc.
        'can_create': _can_create_folder(user),
        'can_manage': can_manage_folder(user, folder),
        'can_create_project': _can_manage_project(user),
        'is_navigation_only': False,
        # ricerca
        'search_q': search_q,
        'recursive': recursive,
        'search_results': search_results,
        'search_page_obj': search_page_obj,
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

    qs = Project.objects.select_related('root_folder', 'root_folder__parent', 'manager').order_by('code')

    if not _can_manage_project(request.user):
        from projects.permissions import get_project_visible_folder_ids
        visible_ids = get_project_visible_folder_ids(request.user)
        qs = qs.filter(
            Q(root_folder__isnull=False) & Q(root_folder_id__in=visible_ids)
        )

    # Ricerca e filtri
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(code__icontains=q) | Q(name__icontains=q) | Q(description__icontains=q)
        )

    folder_id = request.GET.get('folder', '').strip()
    if folder_id:
        try:
            qs = qs.filter(root_folder__parent_id=int(folder_id))
        except (ValueError, TypeError):
            pass

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'projects/project_list.html', {
        'projects': page_obj,
        'page_obj': page_obj,
        'q': q,
        'folder_id': folder_id,
        'can_create': _can_manage_project(request.user),
        'total_count': paginator.count,
    })


@login_required
def project_detail(request, project_id):
    project = get_object_or_404(
        Project.objects.select_related('root_folder', 'root_folder__parent', 'manager'),
        pk=project_id,
    )

    if not _can_manage_project(request.user):
        if project.root_folder:
            # Document Auditor globale può vedere il dettaglio progetto (per audit)
            from projects.permissions import _is_privileged
            if not _is_privileged(request.user):
                from projects.resolver import has_folder_permission as _has_fperm
                if not _has_fperm(
                    request.user, project.root_folder, 'view_projects',
                    include_legacy_fallback=True,
                ):
                    raise PermissionDenied
        else:
            raise PermissionDenied

    # ── Ricerca documenti nel progetto ──────────────────────────────────────
    from django.core.paginator import Paginator as _Paginator
    from documents.models import DocumentVersion as _DocVer, Document as _Doc

    doc_q = request.GET.get('q', '').strip()
    doc_type_filter = request.GET.get('document_type', '').strip()
    doc_folder_filter = request.GET.get('folder', '').strip()

    documents = []
    subfolders = []
    project_folder_choices = []
    if project.root_folder:
        subfolders = project.root_folder.subfolders.order_by('code')

        # Tutte le cartelle del progetto (root + discendenti via path)
        from projects.services import get_folder_descendants
        project_folders_qs = ProjectFolder.objects.filter(
            pk=project.root_folder.pk
        ) | get_folder_descendants(project.root_folder)
        project_folder_ids = list(project_folders_qs.values_list('pk', flat=True))

        # Documenti autorizzati nelle cartelle del progetto
        base_docs = _Doc.objects.filter(
            project_folder_id__in=project_folder_ids
        ).select_related('current_version', 'owner', 'project_folder').order_by('code')

        if not _can_manage_project(request.user):
            from django.db.models import Exists, OuterRef
            own_draft_qs = _DocVer.objects.filter(
                document=OuterRef('pk'),
                created_by=request.user,
                status__in=[_DocVer.Status.DRAFT, _DocVer.Status.REJECTED, _DocVer.Status.IN_APPROVAL],
            )
            base_docs = base_docs.filter(
                Q(current_version__isnull=False,
                  current_version__status=_DocVer.Status.APPROVED)
                | Q(Exists(own_draft_qs))
            )

        # Filtri ricerca
        if doc_q:
            base_docs = base_docs.filter(
                Q(code__icontains=doc_q)
                | Q(title__icontains=doc_q)
                | Q(description__icontains=doc_q)
                | Q(document_type__icontains=doc_q)
            )
        if doc_type_filter:
            base_docs = base_docs.filter(document_type=doc_type_filter)
        if doc_folder_filter:
            try:
                _fid = int(doc_folder_filter)
                if _fid in project_folder_ids:
                    base_docs = base_docs.filter(project_folder_id=_fid)
            except (ValueError, TypeError):
                pass

        doc_paginator = _Paginator(base_docs, 20)
        documents = doc_paginator.get_page(request.GET.get('page', 1))

        # Choices cartelle per il filtro (solo cartelle del progetto)
        project_folder_choices = list(
            ProjectFolder.objects.filter(pk__in=project_folder_ids).order_by('code')
        )

        # Tipi documento distinti nelle cartelle del progetto
    doc_type_choices = list(
        _Doc.objects.filter(
            project_folder_id__in=project_folder_ids if project.root_folder else []
        ).exclude(document_type='').values_list('document_type', flat=True).distinct().order_by('document_type')
    ) if project.root_folder else []

    revisions = project.revisions.order_by('-revision_number')

    from projects.services import build_project_baseline_comparison
    current_baseline, comparison_rows = build_project_baseline_comparison(project)

    from projects.permissions import can_create_document_in_folder
    can_create_doc = (
        project.root_folder is not None
        and can_create_document_in_folder(request.user, project.root_folder)
    )

    from documents.permissions import can_view_audit
    show_audit = can_view_audit(request.user, folder=project.root_folder)

    audit_logs = None
    if show_audit:
        from auditlog.models import AuditLog
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
    if project.root_folder:
        from ecn.models import ChangeNotice
        project_ecns = list(
            ChangeNotice.objects
            .filter(document__project_folder=project.root_folder)
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
        # ricerca documenti
        'doc_q': doc_q,
        'doc_type_filter': doc_type_filter,
        'doc_folder_filter': doc_folder_filter,
        'doc_type_choices': doc_type_choices,
        'project_folder_choices': project_folder_choices,
        'doc_page_obj': documents if project.root_folder else None,
    })


@login_required
def project_edit(request, project_id):
    from projects.forms import ProjectUpdateForm
    from projects.services import update_project_metadata

    project = get_object_or_404(
        Project.objects.select_related('root_folder', 'root_folder__parent', 'manager'),
        pk=project_id,
    )

    if not _can_manage_project(request.user):
        raise PermissionDenied

    if request.method == 'POST':
        form = ProjectUpdateForm(request.POST, instance=project)
        if form.is_valid():
            d = form.cleaned_data
            try:
                update_project_metadata(
                    project=project,
                    name=d['name'],
                    description=d.get('description', ''),
                    manager=d.get('manager'),
                    revision_scheme=d.get('revision_scheme'),
                    revision=d.get('revision'),
                    updated_by=request.user,
                )
                messages.success(request, f'Progetto "{project.name}" aggiornato.')
                return redirect('project_detail', project_id=project.pk)
            except Exception as exc:
                messages.error(request, f'Errore: {exc}')
    else:
        form = ProjectUpdateForm(instance=project)

    return render(request, 'projects/project_edit.html', {
        'form': form,
        'project': project,
    })


@login_required
def project_create(request):
    from projects.forms import ProjectCreateForm
    from projects.services import create_project_with_root_folder

    if not _can_manage_project(request.user):
        raise PermissionDenied

    # Supporto ?parent_folder=<pk> o ?folder=<pk> (retrocompat) per precompilare la cartella padre
    prefill_folder = None
    parent_id = (
        request.GET.get('parent_folder')
        or request.GET.get('folder')
        or request.POST.get('_parent_prefill')
    )
    if parent_id:
        try:
            prefill_folder = ProjectFolder.objects.get(pk=int(parent_id))
        except (ProjectFolder.DoesNotExist, ValueError, TypeError):
            prefill_folder = None

    # Cartelle selezionabili come parent (solo cartelle ordinarie attive)
    allowed_parents = ProjectFolder.objects.filter(
        status=ProjectFolder.Status.ACTIVE,
    ).exclude(
        folder_kind=ProjectFolder.FolderKind.PROJECT,
    ).order_by('code')

    if request.method == 'POST':
        form = ProjectCreateForm(request.POST, allowed_parent_folders=allowed_parents)
        if form.is_valid():
            d = form.cleaned_data
            try:
                from django.core.exceptions import ValidationError as DjVE
                project = create_project_with_root_folder(
                    parent_folder=d['parent_folder'],
                    code=d['code'],
                    name=d['name'],
                    description=d.get('description', ''),
                    project_type=d['project_type'],
                    manager=d.get('manager'),
                    created_by=request.user,
                    revision_scheme=d.get('revision_scheme', 'numeric'),
                    revision=d.get('revision', '00'),
                )
                messages.success(request, f'Progetto "{project.name}" creato.')
                return redirect('project_detail', project_id=project.pk)
            except (DjVE, Exception) as exc:
                err = getattr(exc, 'message', str(exc))
                messages.error(request, f'Errore creazione progetto: {err}')
    else:
        initial = {}
        if prefill_folder:
            initial['parent_folder'] = prefill_folder.pk
        form = ProjectCreateForm(initial=initial, allowed_parent_folders=allowed_parents)

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
        if project and project.root_folder:
            from projects.permissions import _is_privileged
            if not _is_privileged(request.user):
                from projects.resolver import has_folder_permission as _has_fperm
                if not _has_fperm(
                    request.user, project.root_folder, 'view_projects',
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
