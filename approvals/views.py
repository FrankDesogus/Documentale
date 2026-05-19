import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from approvals.models import ApprovalRequest, ApprovalRequestApprover, ApprovalRequestAttachment
from approvals.services import approve_version, reject_version


def _can_download_attachment(user, attachment):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    from documents.permissions import is_document_manager, is_document_auditor
    if is_document_manager(user) or is_document_auditor(user):
        return True
    ar = attachment.approval_request
    version = ar.document_version
    if version.created_by_id == user.pk:
        return True
    if ar.approvers.filter(approver=user).exists():
        return True
    return False


@login_required
def download_approval_attachment(request, attachment_id):
    attachment = get_object_or_404(ApprovalRequestAttachment, pk=attachment_id)

    if not _can_download_attachment(request.user, attachment):
        raise PermissionDenied

    file_path = attachment.file.path
    if not os.path.exists(file_path):
        raise Http404

    content_type = attachment.mime_type or 'application/octet-stream'
    return FileResponse(
        open(file_path, 'rb'),
        content_type=content_type,
        as_attachment=True,
        filename=attachment.original_filename,
    )


@login_required
def approval_queue(request):
    requests = ApprovalRequest.objects.filter(
        status=ApprovalRequest.Status.PENDING,
        approvers__approver=request.user,
    ).select_related(
        'document_version__document', 'requested_by'
    ).order_by('due_date', '-requested_at')
    return render(request, 'approvals/approval_queue.html', {
        'approval_requests': requests,
    })


@login_required
def approval_detail(request, approval_request_id):
    ar = get_object_or_404(ApprovalRequest, pk=approval_request_id)

    is_assigned = ar.approvers.filter(approver=request.user).exists()
    if not is_assigned and not request.user.is_superuser:
        raise PermissionDenied

    if request.method == 'POST':
        action = request.POST.get('action')
        comment = request.POST.get('comment', '').strip()

        if action == 'approve':
            try:
                approve_version(ar, request.user, comment=comment)
                ar.refresh_from_db()
                if ar.status == ApprovalRequest.Status.APPROVED:
                    messages.success(request, 'Documento approvato.')
                else:
                    messages.info(
                        request,
                        'Approvazione registrata. La richiesta è ancora in attesa degli altri approvatori.',
                    )
                return redirect('approval_queue')
            except (ValidationError, PermissionDenied) as exc:
                error = ' '.join(exc.messages) if hasattr(exc, 'messages') else str(exc)
                messages.error(request, error)

        elif action == 'reject':
            rejection_reason = request.POST.get('rejection_reason', '').strip()
            if not rejection_reason:
                messages.error(request, 'Il motivo del rifiuto è obbligatorio.')
            else:
                try:
                    reject_version(
                        ar, request.user,
                        rejection_reason=rejection_reason,
                        comment=comment,
                    )
                    messages.success(request, 'Revisione rifiutata.')
                    return redirect('approval_queue')
                except (ValidationError, PermissionDenied) as exc:
                    error = ' '.join(exc.messages) if hasattr(exc, 'messages') else str(exc)
                    messages.error(request, error)

    version = ar.document_version
    decisions = ar.decisions.select_related('approver').order_by('decided_at')
    approvers = ar.approvers.select_related('approver').order_by('order')

    # Per SEQUENTIAL: individua il prossimo approvatore atteso
    next_approver = None
    if ar.approval_policy == ApprovalRequest.Policy.SEQUENTIAL and ar.status == ApprovalRequest.Status.PENDING:
        next_slot = approvers.filter(status=ApprovalRequestApprover.ApproverStatus.PENDING).first()
        next_approver = next_slot.approver if next_slot else None

    return render(request, 'approvals/approval_detail.html', {
        'approval_request': ar,
        'version': version,
        'document': version.document,
        'decisions': decisions,
        'approvers': approvers,
        'next_approver': next_approver,
    })
