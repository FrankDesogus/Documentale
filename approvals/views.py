import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from approvals.models import ApprovalRequest, ApprovalRequestApprover, ApprovalRequestAttachment
from approvals.services import approve_version, reject_version


def _can_download_attachment(user, attachment):
    """
    Download allegato richiesta di approvazione:
      - superuser
      - autore della versione in approvazione
      - approvatori assegnati alla specifica richiesta
    MB1: rimosso bypass is_staff, Document Manager, Document Auditor.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
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
    from django.core.paginator import Paginator
    from django.db.models import Q
    from django.utils import timezone

    user = request.user

    # ── Queryset autorizzato: richieste assegnate a me + create da me ──
    qs = ApprovalRequest.objects.filter(
        Q(approvers__approver=user) | Q(requested_by=user)
    ).select_related(
        'document_version__document', 'requested_by'
    ).distinct()

    # ── Filtri GET ──
    status_filter = request.GET.get('status', 'PENDING')
    if status_filter and status_filter in [s.value for s in ApprovalRequest.Status]:
        qs = qs.filter(status=status_filter)
    elif status_filter == '':
        pass  # tutti gli stati
    else:
        status_filter = 'PENDING'
        qs = qs.filter(status=ApprovalRequest.Status.PENDING)

    mine_filter = request.GET.get('mine', '')
    if mine_filter == 'to_approve':
        qs = qs.filter(approvers__approver=user)
    elif mine_filter == 'created':
        qs = qs.filter(requested_by=user)

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(document_version__document__code__icontains=q)
            | Q(document_version__document__title__icontains=q)
            | Q(requested_by__username__icontains=q)
            | Q(requested_by__first_name__icontains=q)
            | Q(requested_by__last_name__icontains=q)
        )

    qs = qs.order_by('due_date', '-requested_at')

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'approvals/approval_queue.html', {
        'approval_requests': page_obj,
        'page_obj': page_obj,
        'q': q,
        'status_filter': status_filter,
        'mine_filter': mine_filter,
        'status_choices': [('', 'Tutti')] + list(ApprovalRequest.Status.choices),
        'today': timezone.now().date(),
        'total_count': paginator.count,
    })


@login_required
def approval_detail(request, approval_request_id):
    ar = get_object_or_404(ApprovalRequest, pk=approval_request_id)

    is_assigned = ar.approvers.filter(approver=request.user).exists()
    if not is_assigned and not request.user.is_superuser:
        raise PermissionDenied

    # Sanatoria: legge i campi storici opzionali dal POST
    from auditlog.historical_forms import SanatoriaStandaloneForm, should_send_notifications
    from auditlog.permissions import can_use_sanatoria
    sanatoria_form = None

    if request.method == 'POST':
        # Istanzia il form sanatoria standalone (fa il gate backend automaticamente)
        sanatoria_form = SanatoriaStandaloneForm(request.POST, current_user=request.user)
        sanatoria_form.is_valid()  # valida senza propagare errori al flusso principale
        is_sanatoria = sanatoria_form.is_sanatoria

        action = request.POST.get('action')
        comment = request.POST.get('comment', '').strip()

        if action == 'approve':
            try:
                approve_version(
                    ar, request.user,
                    comment=comment,
                    send_notifications=should_send_notifications(sanatoria=is_sanatoria),
                )
                ar.refresh_from_db()
                # Sanatoria: crea HistoricalRecord
                from auditlog.models import HistoricalRecord
                sanatoria_form.maybe_create_historical_record(
                    event_type=HistoricalRecord.EventType.DOC_APPROVED,
                    recorded_by=request.user,
                    target_instance=ar.document_version,
                )
                if ar.status == ApprovalRequest.Status.APPROVED:
                    san_suffix = ' [sanatoria]' if is_sanatoria else ''
                    messages.success(request, f'Documento approvato.{san_suffix}')
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
                        send_notifications=should_send_notifications(sanatoria=is_sanatoria),
                    )
                    # Sanatoria: crea HistoricalRecord
                    from auditlog.models import HistoricalRecord
                    sanatoria_form.maybe_create_historical_record(
                        event_type=HistoricalRecord.EventType.DOC_REJECTED,
                        recorded_by=request.user,
                        target_instance=ar.document_version,
                    )
                    san_suffix = ' [sanatoria]' if is_sanatoria else ''
                    messages.success(request, f'Revisione rifiutata.{san_suffix}')
                    return redirect('approval_queue')
                except (ValidationError, PermissionDenied) as exc:
                    error = ' '.join(exc.messages) if hasattr(exc, 'messages') else str(exc)
                    messages.error(request, error)
    else:
        sanatoria_form = SanatoriaStandaloneForm(current_user=request.user)

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
        'form': sanatoria_form,
    })
