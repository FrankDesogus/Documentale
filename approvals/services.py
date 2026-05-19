from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from approvals.models import ApprovalDecision, ApprovalRequest
from auditlog.services import create_audit_log


def approve_version(approval_request, approved_by, comment=""):
    from documents.models import DocumentVersion

    is_assigned = approval_request.approvers.filter(approver=approved_by).exists()
    if not is_assigned and not approved_by.is_superuser:
        raise PermissionDenied("L'utente non è tra gli approvatori assegnati.")

    if approval_request.status != ApprovalRequest.Status.PENDING:
        raise ValidationError(
            f"La richiesta non è in attesa. Stato attuale: {approval_request.get_status_display()}"
        )

    version = approval_request.document_version
    if version.status != DocumentVersion.Status.IN_APPROVAL:
        raise ValidationError(
            f"La revisione non è in approvazione. Stato attuale: {version.get_status_display()}"
        )

    with transaction.atomic():
        now = timezone.now()

        ApprovalDecision.objects.create(
            approval_request=approval_request,
            approver=approved_by,
            decision=ApprovalDecision.Decision.APPROVED,
            notes=comment,
        )

        approval_request.status = ApprovalRequest.Status.APPROVED
        approval_request.completed_at = now
        approval_request.save(update_fields=['status', 'completed_at'])

        document = version.document
        old_current = document.current_version

        if old_current is not None and old_current.pk != version.pk:
            DocumentVersion.objects.filter(pk=old_current.pk).update(
                status=DocumentVersion.Status.SUPERSEDED,
                is_current=False,
            )
            create_audit_log(
                user=approved_by,
                action='SUPERSEDED',
                instance=old_current,
                old_values={'status': old_current.status, 'is_current': True},
                new_values={'status': DocumentVersion.Status.SUPERSEDED, 'is_current': False},
                document=document,
                document_version=old_current,
            )

        version.status = DocumentVersion.Status.APPROVED
        version.is_current = True
        version.approved_at = now
        version.approved_by = approved_by
        version.save(update_fields=['status', 'is_current', 'approved_at', 'approved_by'])

        document.current_version = version
        document.save(update_fields=['current_version'])

        create_audit_log(
            user=approved_by,
            action='APPROVED',
            instance=version,
            old_values={'status': DocumentVersion.Status.IN_APPROVAL},
            new_values={
                'status': DocumentVersion.Status.APPROVED,
                'is_current': True,
                'approved_by_id': approved_by.pk,
            },
            document=document,
            document_version=version,
        )

    return approval_request


def reject_version(approval_request, rejected_by, rejection_reason, comment=""):
    from documents.models import DocumentVersion

    if not rejection_reason or not rejection_reason.strip():
        raise ValidationError("Il motivo del rifiuto è obbligatorio.")

    is_assigned = approval_request.approvers.filter(approver=rejected_by).exists()
    if not is_assigned and not rejected_by.is_superuser:
        raise PermissionDenied("L'utente non è tra gli approvatori assegnati.")

    if approval_request.status != ApprovalRequest.Status.PENDING:
        raise ValidationError(
            f"La richiesta non è in attesa. Stato attuale: {approval_request.get_status_display()}"
        )

    version = approval_request.document_version
    if version.status != DocumentVersion.Status.IN_APPROVAL:
        raise ValidationError(
            f"La revisione non è in approvazione. Stato attuale: {version.get_status_display()}"
        )

    with transaction.atomic():
        now = timezone.now()

        ApprovalDecision.objects.create(
            approval_request=approval_request,
            approver=rejected_by,
            decision=ApprovalDecision.Decision.REJECTED,
            notes=comment,
        )

        approval_request.status = ApprovalRequest.Status.REJECTED
        approval_request.completed_at = now
        approval_request.save(update_fields=['status', 'completed_at'])

        version.status = DocumentVersion.Status.REJECTED
        version.rejected_at = now
        version.rejection_reason = rejection_reason
        version.save(update_fields=['status', 'rejected_at', 'rejection_reason'])

        create_audit_log(
            user=rejected_by,
            action='REJECTED',
            instance=version,
            old_values={'status': DocumentVersion.Status.IN_APPROVAL},
            new_values={
                'status': DocumentVersion.Status.REJECTED,
                'rejection_reason': rejection_reason,
            },
            document=version.document,
            document_version=version,
        )

    return approval_request
