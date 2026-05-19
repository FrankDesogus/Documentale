import logging

from django.core.mail import send_mail

from notifications.models import NotificationLog

logger = logging.getLogger(__name__)


def _send_and_log(recipient, subject, body, approval_request=None):
    """Crea NotificationLog, invia email, registra esito."""
    log = NotificationLog.objects.create(
        recipient=recipient,
        subject=subject,
        body=body,
        approval_request=approval_request,
        is_sent=False,
    )

    if not recipient.email:
        log.error_message = "L'utente non ha un indirizzo email configurato."
        log.save(update_fields=['error_message'])
        logger.warning(
            'Email non inviata: utente %s senza indirizzo email (NotificationLog #%s).',
            recipient.username, log.pk,
        )
        return log

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=None,  # usa DEFAULT_FROM_EMAIL
            recipient_list=[recipient.email],
            fail_silently=False,
        )
        log.is_sent = True
        log.save(update_fields=['is_sent'])
    except Exception as exc:
        error_msg = str(exc)
        log.error_message = error_msg
        log.save(update_fields=['error_message'])
        logger.error(
            'Invio email fallito per %s (NotificationLog #%s): %s',
            recipient.email, log.pk, error_msg,
        )

    return log


def send_approval_request_email(approval_request, approver):
    """Notifica a un approvatore che è richiesta la sua approvazione."""
    version = approval_request.document_version
    document = version.document
    requester = approval_request.requested_by

    due_line = (
        f"\n  Scadenza approvazione : {approval_request.due_date:%d/%m/%Y}"
        if approval_request.due_date
        else ""
    )

    subject = (
        f"[Documentale] Approvazione richiesta: "
        f"{document.code} rev.{version.revision_label}"
    )
    body = (
        f"Gentile {approver.get_full_name() or approver.username},\n\n"
        f"è richiesta la tua approvazione per la seguente revisione documentale:\n\n"
        f"  Codice documento      : {document.code}\n"
        f"  Titolo                : {document.title}\n"
        f"  Revisione             : {version.revision_label} (n. {version.revision_number})\n"
        f"  Richiedente           : {requester.get_full_name() or requester.username}"
        f"{due_line}\n\n"
        f"Accedi al sistema documentale per esaminare e approvare la revisione.\n\n"
        f"Questo messaggio è generato automaticamente, non rispondere a questa email."
    )
    return _send_and_log(approver, subject, body, approval_request=approval_request)


def send_version_approved_email(approval_request):
    """Notifica all'autore che la sua revisione è stata approvata."""
    version = approval_request.document_version
    document = version.document
    author = version.created_by

    subject = (
        f"[Documentale] Revisione approvata: "
        f"{document.code} rev.{version.revision_label}"
    )
    body = (
        f"Gentile {author.get_full_name() or author.username},\n\n"
        f"la revisione {version.revision_label} del documento {document.code} "
        f"è stata approvata.\n\n"
        f"  Codice documento  : {document.code}\n"
        f"  Titolo            : {document.title}\n"
        f"  Revisione         : {version.revision_label} (n. {version.revision_number})\n"
        f"  Approvato da      : "
        f"{version.approved_by.get_full_name() or version.approved_by.username}\n"
        f"  Data approvazione : {version.approved_at:%d/%m/%Y %H:%M}\n\n"
        f"La revisione è ora la versione corrente del documento.\n\n"
        f"Questo messaggio è generato automaticamente, non rispondere a questa email."
    )
    return _send_and_log(author, subject, body, approval_request=approval_request)


def send_version_rejected_email(approval_request, rejection_reason):
    """Notifica all'autore che la sua revisione è stata rifiutata."""
    version = approval_request.document_version
    document = version.document
    author = version.created_by

    subject = (
        f"[Documentale] Revisione rifiutata: "
        f"{document.code} rev.{version.revision_label}"
    )
    body = (
        f"Gentile {author.get_full_name() or author.username},\n\n"
        f"la revisione {version.revision_label} del documento {document.code} "
        f"è stata rifiutata.\n\n"
        f"  Codice documento : {document.code}\n"
        f"  Titolo           : {document.title}\n"
        f"  Revisione        : {version.revision_label} (n. {version.revision_number})\n\n"
        f"Motivo del rifiuto:\n{rejection_reason}\n\n"
        f"Accedi al sistema documentale per modificare la revisione "
        f"e reinviarla in approvazione.\n\n"
        f"Questo messaggio è generato automaticamente, non rispondere a questa email."
    )
    return _send_and_log(author, subject, body, approval_request=approval_request)
