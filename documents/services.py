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
    ecn=None,
    _bypass_ecn_check=False,
):
    """
    Crea una nuova DocumentVersion in stato DRAFT.

    Parametri:
      ecn              — istanza ChangeNotice approvata che autorizza la revisione.
                         Obbligatoria quando il documento ha già una current_version
                         approvata (gate ECN-C), salvo bypass esplicito.
      _bypass_ecn_check — True solo per script di admin, comandi di gestione e test
                         che testano logiche indipendenti dall'ECN. Non esposto nella UI.
    """
    if document.status != Document.Status.ACTIVE:
        raise ValidationError("Il documento non è attivo.")

    # ----------------------------------------------------------------
    # Gate ECN: obbligatorio quando il documento ha una versione corrente
    # approvata. Garantisce che ogni nuova revisione sia tracciata da un ECN.
    # Il bypass è riservato a usi tecnici (admin, test, import).
    # ----------------------------------------------------------------
    if (
        not _bypass_ecn_check
        and document.current_version is not None
        and document.current_version.status == DocumentVersion.Status.APPROVED
    ):
        if ecn is None:
            raise ValidationError(
                "Per creare una nuova revisione di un documento approvato è necessario "
                "un ECN approvato. Richiedere prima una variante ECN."
            )
        # Import locale per evitare import circolari (ecn → documents)
        from ecn.models import ChangeNotice
        if ecn.document_id != document.pk:
            raise ValidationError(
                "L'ECN indicato non è relativo a questo documento."
            )
        if ecn.status != ChangeNotice.Status.APPROVED:
            raise ValidationError(
                f"L'ECN deve essere approvato per creare una nuova revisione. "
                f"Stato attuale: {ecn.get_status_display()}."
            )
        if ecn.executed_version_id is not None:
            raise ValidationError(
                "L'ECN è già stato utilizzato per creare una revisione del documento."
            )

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

    # Collega l'ECN alla nuova revisione (esecuzione)
    if ecn is not None:
        from django.utils import timezone as tz
        ecn.executed_version = version
        ecn.executed_at = tz.now()
        ecn.save(update_fields=['executed_version', 'executed_at'])
        try:
            from ecn.services import _write_audit
            _write_audit(
                actor=created_by,
                action='ECN_EXECUTED',
                ecn=ecn,
                old_status=ecn.status,
                new_status=ecn.status,
            )
        except Exception:
            pass

    return version


def submit_version_for_approval(version, requested_by, approvers, due_date=None, approval_policy='all'):
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
            approval_policy=approval_policy,
        )

        for i, approver in enumerate(approvers, start=1):
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


def update_draft_version(version, user, revision_label, revision_number, change_summary, new_file=None):
    if version.status not in (DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED):
        raise ValidationError(
            f"Solo le bozze e le revisioni rifiutate possono essere modificate. "
            f"Stato attuale: {version.get_status_display()}"
        )

    was_rejected = version.status == DocumentVersion.Status.REJECTED

    if revision_label != version.revision_label:
        if DocumentVersion.objects.filter(
            document=version.document, revision_label=revision_label,
        ).exclude(pk=version.pk).exists():
            raise ValidationError(
                f"Etichetta revisione '{revision_label}' già utilizzata per questo documento."
            )

    if revision_number != version.revision_number:
        if DocumentVersion.objects.filter(
            document=version.document, revision_number=revision_number,
        ).exclude(pk=version.pk).exists():
            raise ValidationError(
                f"Numero revisione {revision_number} già utilizzato per questo documento."
            )

    old_values = {
        'revision_label': version.revision_label,
        'revision_number': version.revision_number,
        'change_summary': version.change_summary,
        'status': version.status,
    }

    with transaction.atomic():
        update_fields = ['revision_label', 'revision_number', 'change_summary']

        version.revision_label = revision_label
        version.revision_number = revision_number
        version.change_summary = change_summary

        if new_file is not None:
            version.file = new_file
            update_fields.append('file')

        if was_rejected:
            version.status = DocumentVersion.Status.DRAFT
            version.submitted_at = None
            version.rejected_at = None
            update_fields += ['status', 'submitted_at', 'rejected_at']

        version.save(update_fields=update_fields)

        metadata = {}
        if new_file is not None:
            metadata['new_file'] = new_file.original_filename

        create_audit_log(
            user=user,
            action='REOPENED_AS_DRAFT' if was_rejected else 'VERSION_UPDATED',
            instance=version,
            old_values=old_values,
            new_values={
                'revision_label': revision_label,
                'revision_number': revision_number,
                'change_summary': change_summary,
                'status': version.status,
            },
            document=version.document,
            document_version=version,
            metadata=metadata if metadata else None,
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
