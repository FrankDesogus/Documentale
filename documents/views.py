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
    can_view_document,
    is_document_auditor,
    is_document_manager,
)
from documents.services import create_document_file, create_new_revision


@login_required
def dashboard(request):
    from approvals.models import ApprovalRequest
    pending_count = ApprovalRequest.objects.filter(
        status=ApprovalRequest.Status.PENDING,
        approvers__approver=request.user,
    ).count()
    draft_count = DocumentVersion.objects.filter(
        created_by=request.user,
        status__in=[DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED],
    ).count()
    return render(request, 'dashboard.html', {
        'pending_count': pending_count,
        'draft_count': draft_count,
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

    if not (user.is_superuser or user.is_staff or is_document_auditor(user) or is_document_manager(user)):
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

    show_history = (
        request.user.is_staff
        or request.user.is_superuser
        or is_document_auditor(request.user)
        or is_document_manager(request.user)
    )
    versions = None
    if show_history:
        versions = doc.versions.select_related(
            'created_by', 'approved_by'
        ).order_by('-revision_number')

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

    return render(request, 'documents/document_detail.html', {
        'document': doc,
        'versions': versions,
        'show_history': show_history,
        'latest_approval_request': latest_approval_request,
        'latest_approval_approvers': latest_approval_approvers,
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

    if not can_create_document(request.user):
        raise PermissionDenied

    if request.method == 'POST':
        form = DocumentCreateForm(request.POST, request.FILES, user=request.user)
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
                return redirect('my_drafts')
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
    else:
        form = DocumentCreateForm(user=request.user)

    return render(request, 'documents/new_document.html', {'form': form})


@login_required
def new_revision(request, document_id):
    from documents.forms import DocumentRevisionCreateForm

    doc = get_object_or_404(Document, pk=document_id)

    if not can_create_revision(request.user, doc):
        raise PermissionDenied

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
        form = SubmitForApprovalForm(request.POST)
        approver_formset = ApproverFormSet(request.POST, prefix='approver')
        if form.is_valid() and approver_formset.is_valid():
            d = form.cleaned_data
            ordered_approvers = [
                f.cleaned_data['approver']
                for f in approver_formset.forms
                if f.cleaned_data and f.cleaned_data.get('approver')
            ]
            try:
                submit_version_for_approval(
                    version=version,
                    requested_by=request.user,
                    approvers=ordered_approvers,
                    due_date=d.get('due_date'),
                    approval_policy=d['approval_policy'],
                )
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
