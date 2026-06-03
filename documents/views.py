import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from documents.models import Document, DocumentVersion
from documents.permissions import (
    can_create_document,
    can_create_revision,
    can_edit_version,
    can_submit_for_approval,
    can_view_audit,
    can_view_document,
    can_view_version,
    is_document_auditor,
    is_document_manager,
    is_quality_manager,
    is_quality_operator,
)
from documents.services import create_document_file, create_new_revision


@login_required
def dashboard(request):
    from approvals.models import ApprovalRequest
    from django.db.models import Q

    user = request.user

    pending_count = ApprovalRequest.objects.filter(
        status=ApprovalRequest.Status.PENDING,
        approvers__approver=user,
    ).count()
    draft_count = DocumentVersion.objects.filter(
        created_by=user,
        status__in=[DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED],
    ).count()

    # ECN personali (proposti o creati dall'utente) ancora aperti
    from ecn.models import ChangeNotice
    my_ecn_count = ChangeNotice.objects.filter(
        Q(proposed_by=user) | Q(created_by=user),
        status__in=[ChangeNotice.Status.DRAFT, ChangeNotice.Status.UNDER_REVIEW, ChangeNotice.Status.APPROVED],
    ).distinct().count()

    # Decisioni CCB in attesa (solo se l'utente è un approvatore assegnato)
    from ecn.models import ChangeNoticeApprover, ChangeNoticeDecision
    decided_ids = set(
        ChangeNoticeDecision.objects.filter(user=user).values_list('approver_id', flat=True)
    )
    pending_ccb_count = (
        ChangeNoticeApprover.objects
        .filter(user=user, change_notice__status=ChangeNotice.Status.UNDER_REVIEW)
        .exclude(pk__in=decided_ids)
        .count()
    )

    return render(request, 'dashboard.html', {
        'pending_count': pending_count,
        'draft_count': draft_count,
        'my_ecn_count': my_ecn_count,
        'pending_ccb_count': pending_ccb_count,
    })


@login_required
def workspace_my_work(request):
    """Workspace personale: bozze, approvazioni pendenti, decisioni CCB, ECN aperti."""
    from approvals.models import ApprovalRequest
    from ecn.models import ChangeNotice, ChangeNoticeApprover, ChangeNoticeDecision

    user = request.user

    # Bozze e rifiutate create dall'utente
    my_drafts_qs = DocumentVersion.objects.filter(
        created_by=user,
        status__in=[DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED],
    ).select_related('document').order_by('-created_at')

    # Approvazioni pendenti
    pending_approvals_qs = ApprovalRequest.objects.filter(
        status=ApprovalRequest.Status.PENDING,
        approvers__approver=user,
    ).select_related('document_version__document').distinct().order_by('-requested_at')

    # Decisioni CCB pendenti
    decided_ids = set(
        ChangeNoticeDecision.objects.filter(user=user).values_list('approver_id', flat=True)
    )
    pending_ccb_qs = (
        ChangeNoticeApprover.objects
        .filter(user=user, change_notice__status=ChangeNotice.Status.UNDER_REVIEW)
        .exclude(pk__in=decided_ids)
        .select_related('change_notice')
        .order_by('change_notice__code')
    )

    # ECN aperti proposti dall'utente
    my_ecn_qs = ChangeNotice.objects.filter(
        Q(proposed_by=user) | Q(created_by=user),
        status__in=[
            ChangeNotice.Status.DRAFT,
            ChangeNotice.Status.UNDER_REVIEW,
            ChangeNotice.Status.APPROVED,
        ],
    ).distinct().order_by('-proposed_at')

    return render(request, 'workspace/my_work.html', {
        'my_drafts': my_drafts_qs,
        'pending_approvals': pending_approvals_qs,
        'pending_ccb': pending_ccb_qs,
        'my_ecn': my_ecn_qs,
    })


@login_required
def workspace_quality(request):
    """Workspace Qualità: visibile solo a manager, auditor e staff."""
    from approvals.models import ApprovalRequest
    from ecn.models import ChangeNotice, ChangeNoticeApprover, ChangeNoticeDecision

    user = request.user
    # is_staff NON concede accesso (MB1)
    if not (user.is_superuser
            or is_quality_manager(user)
            or is_quality_operator(user)
            or is_document_auditor(user)):
        raise PermissionDenied

    # ECN DRAFT senza CCB configurata
    draft_ids = ChangeNotice.objects.filter(
        status=ChangeNotice.Status.DRAFT
    ).values_list('pk', flat=True)
    configured_ids = ChangeNoticeApprover.objects.filter(
        change_notice_id__in=draft_ids
    ).values_list('change_notice_id', flat=True).distinct()
    ecn_to_review_qs = ChangeNotice.objects.filter(
        status=ChangeNotice.Status.DRAFT,
    ).exclude(pk__in=configured_ids).order_by('proposed_at')

    # ECN UNDER_REVIEW: decisioni CCB assegnate all'utente e non ancora espresse
    decided_ids = set(
        ChangeNoticeDecision.objects.filter(user=user).values_list('approver_id', flat=True)
    )
    pending_ccb_qs = (
        ChangeNoticeApprover.objects
        .filter(user=user, change_notice__status=ChangeNotice.Status.UNDER_REVIEW)
        .exclude(pk__in=decided_ids)
        .select_related('change_notice')
        .order_by('change_notice__code')
    )

    # ECN APPROVED con revisione eseguita (da chiudere)
    ecn_to_close_qs = ChangeNotice.objects.filter(
        status=ChangeNotice.Status.APPROVED,
        executed_version__isnull=False,
    ).select_related('executed_version__document').order_by('code')

    # Approvazioni documento pendenti (per tutto il sistema - vista manager)
    all_pending_approvals_qs = ApprovalRequest.objects.filter(
        status=ApprovalRequest.Status.PENDING,
    ).select_related('document_version__document').order_by('-requested_at')

    return render(request, 'workspace/quality.html', {
        'ecn_to_review': ecn_to_review_qs,
        'pending_ccb': pending_ccb_qs,
        'ecn_to_close': ecn_to_close_qs,
        'all_pending_approvals': all_pending_approvals_qs,
    })


@login_required
def document_list(request):
    user = request.user
    qs = Document.objects.filter(
        status=Document.Status.ACTIVE,
        current_version__isnull=False,
        current_version__status=DocumentVersion.Status.APPROVED,
        current_version__is_current=True,
    ).select_related('current_version', 'owner').order_by('code')

    # is_staff NON concede visibilità globale (MB1)
    if not (user.is_superuser or is_document_auditor(user) or is_document_manager(user)):
        from projects.permissions import get_visible_folder_ids
        visible_ids = get_visible_folder_ids(user)
        qs = qs.filter(
            Q(project_folder__isnull=True) | Q(project_folder_id__in=visible_ids)
        )

    return render(request, 'documents/document_list.html', {'documents': qs})


@login_required
def document_detail(request, document_id):
    doc = get_object_or_404(Document, pk=document_id)

    if not can_view_document(request.user, doc):
        raise Http404

    show_history = can_view_audit(request.user, folder=doc.project_folder)

    versions = None
    audit_logs = None
    if show_history:
        all_versions = doc.versions.select_related(
            'created_by', 'approved_by'
        ).order_by('-revision_number')
        # Filtra le versioni a quelle visibili all'utente (bozze private escluse)
        versions = [v for v in all_versions if can_view_version(request.user, v)]
        from auditlog.models import AuditLog
        audit_logs = AuditLog.objects.filter(
            changes__document_id=doc.pk
        ).select_related('user').order_by('-timestamp')[:20]

    latest_approval_request = None
    latest_approval_approvers = []
    if doc.current_version:
        from approvals.models import ApprovalRequest
        latest_approval_request = (
            doc.current_version.approval_requests
            .filter(status=ApprovalRequest.Status.APPROVED)
            .order_by('-completed_at')
            .first()
        )
        if latest_approval_request:
            latest_approval_approvers = list(
                latest_approval_request.approvers
                .select_related('approver')
                .order_by('order')
            )

    latest_approval_attachments = (
        list(latest_approval_request.attachments.all())
        if latest_approval_request else []
    )

    # ECN collegati al documento (visibili all'utente)
    doc_ecns = []
    show_create_ecn = False
    try:
        from ecn.permissions import can_create_ecn, can_view_ecn
        raw_ecns = doc.ecns.select_related('proposed_by').order_by('-proposed_at')
        doc_ecns = [e for e in raw_ecns if can_view_ecn(request.user, e)]
        show_create_ecn = (
            doc.current_version is not None
            and can_create_ecn(request.user, doc)
        )
    except Exception:
        pass

    return render(request, 'documents/document_detail.html', {
        'document': doc,
        'versions': versions,
        'show_history': show_history,
        'audit_logs': audit_logs,
        'latest_approval_request': latest_approval_request,
        'latest_approval_approvers': latest_approval_approvers,
        'latest_approval_attachments': latest_approval_attachments,
        'doc_ecns': doc_ecns,
        'show_create_ecn': show_create_ecn,
    })


@login_required
def my_drafts(request):
    versions = DocumentVersion.objects.filter(
        created_by=request.user,
        status__in=[DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED],
    ).select_related('document').order_by('-created_at')
    return render(request, 'documents/my_drafts.html', {'versions': versions})


@login_required
def new_document(request):
    from documents.forms import DocumentCreateForm
    from projects.models import Project
    from projects.permissions import can_create_document_in_folder

    # Contesto progetto opzionale: ?project=<id>
    from_project = None
    fixed_folder = None
    project_id_param = request.GET.get('project')
    if project_id_param:
        try:
            from_project = get_object_or_404(Project, pk=int(project_id_param))
        except (ValueError, TypeError):
            raise PermissionDenied
        if from_project.folder is None or not can_create_document_in_folder(request.user, from_project.folder):
            raise PermissionDenied
        fixed_folder = from_project.folder
    elif not can_create_document(request.user):
        raise PermissionDenied

    if request.method == 'POST':
        form = DocumentCreateForm(request.POST, request.FILES, user=request.user, fixed_project_folder=fixed_folder)
        if form.is_valid():
            d = form.cleaned_data
            try:
                with transaction.atomic():
                    doc = Document.objects.create(
                        code=d['code'],
                        title=d['title'],
                        description=d['description'],
                        category=d['category'],
                        document_type=d['document_type'],
                        project_folder=d['project_folder'],
                        owner=request.user,
                        created_by=request.user,
                    )
                    doc_file = None
                    if d.get('file'):
                        doc_file = create_document_file(d['file'], request.user)
                    create_new_revision(
                        document=doc,
                        created_by=request.user,
                        revision_label=d['revision_label'],
                        revision_number=d['revision_number'],
                        file=doc_file,
                        change_summary=d['change_summary'],
                    )
                messages.success(
                    request,
                    f'Documento {doc.code} creato con prima bozza Rev. {d["revision_label"]}.',
                )
                if from_project:
                    return redirect('document_detail', document_id=doc.pk)
                return redirect('my_drafts')
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
    else:
        form = DocumentCreateForm(user=request.user, fixed_project_folder=fixed_folder)
        if fixed_folder is None and not form.fields['project_folder'].queryset.exists():
            messages.warning(
                request,
                'Non hai accesso in scrittura a nessuna cartella. '
                'Richiedi i permessi necessari a un amministratore.',
            )

    return render(request, 'documents/new_document.html', {
        'form': form,
        'from_project': from_project,
    })


@login_required
def new_revision(request, document_id):
    from documents.forms import DocumentRevisionCreateForm

    doc = get_object_or_404(Document, pk=document_id)

    if not can_create_revision(request.user, doc):
        raise PermissionDenied

    # Gate ECN: se il documento ha una versione corrente approvata è necessario un ECN.
    needs_ecn = (
        doc.current_version is not None
        and doc.current_version.status == DocumentVersion.Status.APPROVED
    )

    ecn = None
    if needs_ecn:
        # L'ECN pk può arrivare come GET param o come hidden field nel POST
        ecn_pk = request.GET.get('ecn') or request.POST.get('ecn_id')
        if not ecn_pk:
            # Mostra pagina informativa con gli ECN disponibili
            from ecn.models import ChangeNotice
            available_ecns = ChangeNotice.objects.filter(
                document=doc,
                status=ChangeNotice.Status.APPROVED,
                executed_version__isnull=True,
            ).order_by('-proposed_at')
            return render(request, 'documents/new_revision.html', {
                'document': doc,
                'needs_ecn': True,
                'available_ecns': available_ecns,
                'form': None,
            })

        try:
            ecn_pk = int(ecn_pk)
        except (ValueError, TypeError):
            messages.error(request, "Parametro ECN non valido.")
            return redirect('document_new_revision', document_id=doc.pk)

        from ecn.models import ChangeNotice
        try:
            ecn = ChangeNotice.objects.get(
                pk=ecn_pk,
                document=doc,
                status=ChangeNotice.Status.APPROVED,
                executed_version__isnull=True,
            )
        except ChangeNotice.DoesNotExist:
            messages.error(
                request,
                "ECN non trovato, non approvato, non relativo a questo documento "
                "o già utilizzato per creare una revisione.",
            )
            return redirect('document_new_revision', document_id=doc.pk)

    last_version = doc.versions.order_by('-revision_number').first()
    if last_version:
        next_number = last_version.revision_number + 1
        next_label = str(next_number).zfill(2)
    else:
        next_number = 0
        next_label = '00'

    if request.method == 'POST':
        form = DocumentRevisionCreateForm(request.POST, request.FILES)
        if form.is_valid():
            d = form.cleaned_data
            try:
                with transaction.atomic():
                    doc_file = None
                    if d.get('file'):
                        doc_file = create_document_file(d['file'], request.user)
                    version = create_new_revision(
                        document=doc,
                        created_by=request.user,
                        revision_label=d['revision_label'],
                        revision_number=d['revision_number'],
                        file=doc_file,
                        change_summary=d['change_summary'],
                        ecn=ecn,
                    )
                messages.success(
                    request,
                    f'Revisione Rev. {version.revision_label} creata come bozza.',
                )
                return redirect('my_drafts')
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
    else:
        form = DocumentRevisionCreateForm(initial={
            'revision_label': next_label,
            'revision_number': next_number,
        })

    return render(request, 'documents/new_revision.html', {
        'form': form,
        'document': doc,
        'ecn': ecn,
        'needs_ecn': needs_ecn,
    })


@login_required
def submit_for_approval(request, version_id):
    from documents.forms import ApproverFormSet, SubmitForApprovalForm
    from documents.services import submit_version_for_approval

    version = get_object_or_404(DocumentVersion, pk=version_id)

    if not can_submit_for_approval(request.user, version):
        raise PermissionDenied

    if version.status not in (DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED):
        messages.error(
            request,
            f'Questa revisione non può essere inviata in approvazione '
            f'(stato: {version.get_status_display()}).',
        )
        return redirect('my_drafts')

    if request.method == 'POST':
        form = SubmitForApprovalForm(request.POST, request.FILES)
        approver_formset = ApproverFormSet(request.POST, prefix='approver')
        if form.is_valid() and approver_formset.is_valid():
            d = form.cleaned_data
            ordered_approvers = [
                f.cleaned_data['approver']
                for f in approver_formset.forms
                if f.cleaned_data and f.cleaned_data.get('approver')
            ]
            try:
                approval_request = submit_version_for_approval(
                    version=version,
                    requested_by=request.user,
                    approvers=ordered_approvers,
                    due_date=d.get('due_date'),
                    approval_policy=d['approval_policy'],
                )
                sig_file = d.get('signature_template_file')
                if sig_file:
                    from approvals.services import create_approval_request_attachment
                    create_approval_request_attachment(approval_request, sig_file, request.user)
                messages.success(
                    request,
                    f'Rev. {version.revision_label} di {version.document.code} '
                    f'inviata in approvazione.',
                )
                return redirect('dashboard')
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
    else:
        form = SubmitForApprovalForm()
        approver_formset = ApproverFormSet(prefix='approver', initial=[{}])

    return render(request, 'documents/submit_for_approval.html', {
        'form': form,
        'approver_formset': approver_formset,
        'version': version,
        'document': version.document,
    })


@login_required
def edit_version(request, version_id):
    from documents.forms import DocumentVersionEditForm
    from documents.services import update_draft_version

    version = get_object_or_404(DocumentVersion, pk=version_id)

    if not can_edit_version(request.user, version):
        raise PermissionDenied

    if request.method == 'POST':
        form = DocumentVersionEditForm(request.POST, request.FILES)
        if form.is_valid():
            d = form.cleaned_data
            try:
                new_file = None
                if d.get('file'):
                    new_file = create_document_file(d['file'], request.user)
                update_draft_version(
                    version=version,
                    user=request.user,
                    revision_label=d['revision_label'],
                    revision_number=d['revision_number'],
                    change_summary=d['change_summary'],
                    new_file=new_file,
                )
                messages.success(
                    request,
                    f'Rev. {version.revision_label} di {version.document.code} aggiornata.',
                )
                return redirect('my_drafts')
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
    else:
        form = DocumentVersionEditForm(initial={
            'revision_label': version.revision_label,
            'revision_number': version.revision_number,
            'change_summary': version.change_summary,
        })

    return render(request, 'documents/edit_version.html', {
        'form': form,
        'version': version,
        'document': version.document,
    })


@login_required
def download_version_file(request, version_id):
    from documents.permissions import can_download_version_file

    version = get_object_or_404(DocumentVersion, pk=version_id)

    if not version.file:
        raise Http404

    if not can_download_version_file(request.user, version):
        raise PermissionDenied

    file_path = version.file.file.path
    if not os.path.exists(file_path):
        raise Http404

    content_type = version.file.mime_type or 'application/octet-stream'
    return FileResponse(
        open(file_path, 'rb'),
        content_type=content_type,
        as_attachment=True,
        filename=version.file.original_filename,
    )
