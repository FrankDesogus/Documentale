"""Notifiche email per l'app ECN / Varianti.

Usa _send_and_log() di notifications.services (stesso pattern delle
notifiche di approvazione documenti).
"""


def notify_ecn_submitted(change_notice):
    """Invia email a tutti gli approvatori quando un ECN è inviato alla CCB."""
    from notifications.services import _send_and_log
    for app in change_notice.approvers.select_related('user').order_by('order', 'id'):
        subject = f"[ECN] Richiesta di revisione: {change_notice.code}"
        body = (
            f"Gentile {app.user.get_full_name() or app.user.username},\n\n"
            f"l'ECN {change_notice.code} «{change_notice.title}» è stato inviato alla CCB "
            f"per revisione e ti è stato assegnato come approvatore.\n\n"
            f"Documento: {change_notice.document.code} — {change_notice.document.title}\n"
            f"Motivazione: {change_notice.get_motivation_display()}\n\n"
            f"Accedi al sistema per revisionare e decidere."
        )
        _send_and_log(app.user, subject, body)


def notify_ecn_approved(change_notice):
    """Invia email al proponente quando l'ECN è approvato dalla CCB."""
    from notifications.services import _send_and_log
    ccb_class_label = (
        change_notice.get_ccb_class_display() if change_notice.ccb_class else '—'
    )
    subject = f"[ECN] Approvato: {change_notice.code}"
    body = (
        f"Gentile {change_notice.proposed_by.get_full_name() or change_notice.proposed_by.username},\n\n"
        f"l'ECN {change_notice.code} «{change_notice.title}» è stato approvato dalla CCB.\n\n"
        f"Classe variante: {ccb_class_label}\n\n"
        f"Puoi ora procedere con la creazione della nuova revisione del documento."
    )
    _send_and_log(change_notice.proposed_by, subject, body)


def notify_ecn_rejected(change_notice):
    """Invia email al proponente quando l'ECN è rifiutato dalla CCB."""
    from notifications.services import _send_and_log
    subject = f"[ECN] Rifiutato: {change_notice.code}"
    body = (
        f"Gentile {change_notice.proposed_by.get_full_name() or change_notice.proposed_by.username},\n\n"
        f"l'ECN {change_notice.code} «{change_notice.title}» è stato rifiutato dalla CCB.\n\n"
        f"Motivo: {change_notice.ccb_notes or '—'}\n\n"
        f"Accedi al sistema per i dettagli."
    )
    _send_and_log(change_notice.proposed_by, subject, body)


def notify_ecn_next_approver(change_notice, next_approver_user):
    """Notifica il prossimo approvatore in una catena SEQUENTIAL."""
    from notifications.services import _send_and_log
    subject = f"[ECN] Attesa tua revisione: {change_notice.code}"
    body = (
        f"Gentile {next_approver_user.get_full_name() or next_approver_user.username},\n\n"
        f"l'ECN {change_notice.code} «{change_notice.title}» è pronto per la tua revisione "
        f"(l'approvazione precedente nella catena è completata).\n\n"
        f"Documento: {change_notice.document.code} — {change_notice.document.title}\n\n"
        f"Accedi al sistema per revisionare e decidere."
    )
    _send_and_log(next_approver_user, subject, body)


def notify_ecn_closed(change_notice):
    """Invia email al proponente quando l'ECN è chiuso."""
    from notifications.services import _send_and_log
    subject = f"[ECN] Chiuso: {change_notice.code}"
    body = (
        f"Gentile {change_notice.proposed_by.get_full_name() or change_notice.proposed_by.username},\n\n"
        f"l'ECN {change_notice.code} «{change_notice.title}» è stato chiuso.\n\n"
        f"Note di chiusura: {change_notice.close_notes or '—'}"
    )
    _send_and_log(change_notice.proposed_by, subject, body)
