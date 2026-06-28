import hashlib
import mimetypes
import os

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from documents.models import Document, DocumentFile, DocumentVersion
from documents.versioning import sequence_label_to_number
from auditlog.services import create_audit_log


def create_new_revision(
    document,
    created_by,
    revision_label,
    version_label="",
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
    # approvata E la policy del documento lo richiede (requires_ecn_for_revision).
    # Il bypass è riservato a usi tecnici (admin, test, import).
    # ----------------------------------------------------------------
    if (
        not _bypass_ecn_check
        and document.current_version is not None
        and document.current_version.status == DocumentVersion.Status.APPROVED
        and document.requires_ecn_for_revision
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

    # Calcola automaticamente revision_number dall'etichetta e dallo schema del documento.
    # Se l'etichetta non corrisponde allo schema dichiarato (es. chiamate interne da test),
    # si prova l'altro schema prima di rinunciare.
    try:
        revision_number = sequence_label_to_number(revision_label, document.revision_scheme)
    except Exception:
        from documents.versioning import SequenceScheme as _SS
        _other = _SS.ALPHABETIC if document.revision_scheme == _SS.NUMERIC else _SS.NUMERIC
        try:
            revision_number = sequence_label_to_number(revision_label, _other)
        except Exception:
            revision_number = 0

    # version_label e version_number: rilevanti solo se il documento usa l'asse versione
    uses_version = document.versioning_mode in (
        Document.VersioningMode.VERSION_ONLY,
        Document.VersioningMode.BOTH,
    )
    version_label_clean = str(version_label).strip() if uses_version else ""
    try:
        version_number = (
            sequence_label_to_number(version_label_clean, document.version_scheme)
            if version_label_clean
            else 0
        )
    except Exception:
        version_number = 0

    replaces_version = document.current_version

    version = DocumentVersion.objects.create(
        document=document,
        revision_label=revision_label,
        revision_number=revision_number,
        version_label=version_label_clean,
        version_number=version_number,
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
            'version_label': version_label_clean,
            'version_number': version_number,
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
        # Notifica esecuzione ECN (email)
        try:
            from ecn.notifications import notify_ecn_executed
            notify_ecn_executed(ecn)
        except Exception:
            pass
        # Notifica in-app esecuzione ECN
        try:
            from notifications.inbox import notify_ecn_executed_inapp
            notify_ecn_executed_inapp(ecn)
        except Exception:
            pass

    return version


def submit_version_for_approval(version, requested_by, approvers, due_date=None, approval_policy='all', send_notifications=True):
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
    # In modalità sanatoria le notifiche sono soppresse.
    if not send_notifications:
        return approval_request

    from notifications.services import send_approval_request_email
    from approvals.models import ApprovalRequest as AR
    if approval_policy == AR.Policy.SEQUENTIAL:
        # SEQUENTIAL: notifica solo il primo approvatore (order=1)
        first_slot = (
            approval_request.approvers
            .order_by('order')
            .first()
        )
        if first_slot:
            send_approval_request_email(approval_request, first_slot.approver)
            try:
                from notifications.inbox import notify_approval_requested
                notify_approval_requested(approval_request, first_slot.approver)
            except Exception:
                pass
    else:
        # ANY / ALL: notifica tutti gli approvatori
        for approver in approvers:
            send_approval_request_email(approval_request, approver)
            try:
                from notifications.inbox import notify_approval_requested
                notify_approval_requested(approval_request, approver)
            except Exception:
                pass

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


def update_draft_version(version, user, revision_label, version_label="", change_summary="", new_file=None):
    if version.status not in (DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED):
        raise ValidationError(
            f"Solo le bozze e le revisioni rifiutate possono essere modificate. "
            f"Stato attuale: {version.get_status_display()}"
        )

    was_rejected = version.status == DocumentVersion.Status.REJECTED
    document = version.document

    if revision_label != version.revision_label:
        if DocumentVersion.objects.filter(
            document=document, revision_label=revision_label,
        ).exclude(pk=version.pk).exists():
            raise ValidationError(
                f"Etichetta revisione '{revision_label}' già utilizzata per questo documento."
            )

    try:
        revision_number = sequence_label_to_number(revision_label, document.revision_scheme)
    except Exception:
        from documents.versioning import SequenceScheme as _SS
        _other = _SS.ALPHABETIC if document.revision_scheme == _SS.NUMERIC else _SS.NUMERIC
        try:
            revision_number = sequence_label_to_number(revision_label, _other)
        except Exception:
            revision_number = 0

    uses_version = document.versioning_mode in (
        Document.VersioningMode.VERSION_ONLY,
        Document.VersioningMode.BOTH,
    )
    version_label_clean = str(version_label).strip() if uses_version else ""
    try:
        version_number = (
            sequence_label_to_number(version_label_clean, document.version_scheme)
            if version_label_clean
            else 0
        )
    except Exception:
        version_number = 0

    old_values = {
        'revision_label': version.revision_label,
        'revision_number': version.revision_number,
        'version_label': version.version_label,
        'version_number': version.version_number,
        'change_summary': version.change_summary,
        'status': version.status,
    }

    with transaction.atomic():
        update_fields = ['revision_label', 'revision_number', 'version_label', 'version_number', 'change_summary']

        version.revision_label = revision_label
        version.revision_number = revision_number
        version.version_label = version_label_clean
        version.version_number = version_number
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
                'version_label': version_label_clean,
                'version_number': version_number,
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
