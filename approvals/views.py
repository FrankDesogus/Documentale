from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from approvals.models import ApprovalRequest
from approvals.services import approve_version, reject_version


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
                messages.success(request, 'Revisione approvata con successo.')
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
    return render(request, 'approvals/approval_detail.html', {
        'approval_request': ar,
        'version': version,
        'document': version.document,
        'decisions': decisions,
    })
