import hashlib
import mimetypes
import os

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from documents.models import Document, DocumentFile, DocumentVersion
from auditlog.services import create_audit_log


def create_new_revision(
    document,
    created_by,
    revision_label,
    revision_number,
    file=None,
    change_summary="",
):
    if document.status != Document.Status.ACTIVE:
        raise ValidationError("Il documento non è attivo.")

    if DocumentVersion.objects.filter(document=document, revision_label=revision_label).exists():
        raise ValidationError(
            f"Etichetta revisione '{revision_label}' già utilizzata per questo documento."
        )

    if DocumentVersion.objects.filter(document=document, revision_number=revision_number).exists():
        raise ValidationError(
            f"Numero revisione {revision_number} già utilizzato per questo documento."
        )

    replaces_version = document.current_version

    version = DocumentVersion.objects.create(
        document=document,
        revision_label=revision_label,
        revision_number=revision_number,
        status=DocumentVersion.Status.DRAFT,
        file=file,
        created_by=created_by,
        change_summary=change_summary,
        is_current=False,
        replaces_version=replaces_version,
    )

    create_audit_log(
        user=created_by,
        action='REVISION_CREATED',
        instance=version,
        new_values={
            'revision_label': revision_label,
            'revision_number': revision_number,
            'status': DocumentVersion.Status.DRAFT,
        },
        document=document,
        document_version=version,
    )

    return version


def submit_version_for_approval(version, requested_by, approvers, due_date=None):
    from approvals.models import ApprovalRequest, ApprovalRequestApprover

    if version.status not in (DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED):
        raise ValidationError(
            f"La revisione deve essere in bozza o rifiutata per essere inviata in approvazione. "
            f"Stato attuale: {version.get_status_display()}"
        )

    if not approvers:
        raise ValidationError("È necessario indicare almeno un approvatore.")

    if version.approval_requests.filter(status=ApprovalRequest.Status.PENDING).exists():
        raise ValidationError(
            "Esiste già una richiesta di approvazione pendente per questa revisione."
        )

    with transaction.atomic():
        now = timezone.now()
        version.status = DocumentVersion.Status.IN_APPROVAL
        version.submitted_at = now
        version.save(update_fields=['status', 'submitted_at'])

        approval_request = ApprovalRequest.objects.create(
            document_version=version,
            requested_by=requested_by,
            status=ApprovalRequest.Status.PENDING,
            due_date=due_date,
        )

        for i, approver in enumerate(approvers):
            ApprovalRequestApprover.objects.create(
                approval_request=approval_request,
                approver=approver,
                order=i,
            )

        create_audit_log(
            user=requested_by,
            action='SUBMITTED_FOR_APPROVAL',
            instance=version,
            old_values={'status': DocumentVersion.Status.DRAFT},
            new_values={'status': DocumentVersion.Status.IN_APPROVAL},
            document=version.document,
            document_version=version,
            metadata={'approval_request_id': approval_request.pk},
        )

    # Notifiche fuori dalla transazione: un errore email non deve annullare il workflow.
    from notifications.services import send_approval_request_email
    for approver in approvers:
        send_approval_request_email(approval_request, approver)

    return approval_request


def reopen_rejected_version_as_draft(version, user):
    if version.status != DocumentVersion.Status.REJECTED:
        raise ValidationError(
            f"Solo le revisioni rifiutate possono essere riaperte. "
            f"Stato attuale: {version.get_status_display()}"
        )

    old_values = {
        'status': version.status,
        'rejection_reason': version.rejection_reason,
    }

    version.status = DocumentVersion.Status.DRAFT
    version.submitted_at = None
    version.rejected_at = None
    version.save(update_fields=['status', 'submitted_at', 'rejected_at'])

    create_audit_log(
        user=user,
        action='REOPENED_AS_DRAFT',
        instance=version,
        old_values=old_values,
        new_values={'status': DocumentVersion.Status.DRAFT},
        document=version.document,
        document_version=version,
    )

    return version


def create_document_file(uploaded_file, user):
    """Crea un DocumentFile da un file caricato via form, calcolando i metadati automaticamente."""
    sha256 = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        sha256.update(chunk)
    uploaded_file.seek(0)

    name = uploaded_file.name
    ext = os.path.splitext(name)[1].lstrip('.').lower()
    mime = getattr(uploaded_file, 'content_type', None) or mimetypes.guess_type(name)[0] or ''

    return DocumentFile.objects.create(
        file=uploaded_file,
        original_filename=name,
        extension=ext,
        size=uploaded_file.size,
        mime_type=mime,
        sha256_hash=sha256.hexdigest(),
        uploaded_by=user,
    )
