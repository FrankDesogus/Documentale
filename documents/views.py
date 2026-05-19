from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from documents.models import Document, DocumentVersion


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
