import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from documents.models import Document, DocumentVersion
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
    documents = Document.objects.filter(
        status=Document.Status.ACTIVE,
        current_version__isnull=False,
        current_version__status=DocumentVersion.Status.APPROVED,
        current_version__is_current=True,
    ).select_related('current_version', 'owner').order_by('code')
    return render(request, 'documents/document_list.html', {'documents': documents})


@login_required
def document_detail(request, document_id):
    doc = get_object_or_404(Document, pk=document_id)

    if not request.user.is_staff:
        if (
            doc.status != Document.Status.ACTIVE
            or not doc.current_version
            or doc.current_version.status != DocumentVersion.Status.APPROVED
        ):
            raise Http404

    versions = None
    if request.user.is_staff:
        versions = doc.versions.select_related(
            'created_by', 'approved_by'
        ).order_by('-revision_number')

    return render(request, 'documents/document_detail.html', {
        'document': doc,
        'versions': versions,
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

    if request.method == 'POST':
        form = DocumentCreateForm(request.POST, request.FILES)
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
        form = DocumentCreateForm()

    return render(request, 'documents/new_document.html', {'form': form})


@login_required
def new_revision(request, document_id):
    from documents.forms import DocumentRevisionCreateForm

    doc = get_object_or_404(Document, pk=document_id)

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
    from documents.forms import SubmitForApprovalForm
    from documents.services import submit_version_for_approval

    version = get_object_or_404(DocumentVersion, pk=version_id)

    if version.created_by != request.user and not request.user.is_staff:
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
        if form.is_valid():
            d = form.cleaned_data
            try:
                submit_version_for_approval(
                    version=version,
                    requested_by=request.user,
                    approvers=list(d['approvers']),
                    due_date=d.get('due_date'),
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

    return render(request, 'documents/submit_for_approval.html', {
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
