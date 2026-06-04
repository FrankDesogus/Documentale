"""Notifiche email per l'app ECN / Varianti.

Usa _send_and_log() di notifications.services (stesso pattern delle
notifiche di approvazione documenti).
"""


def notify_ecn_submitted(change_notice):
    """
    Invia email ai membri CCB quando un ECN è inviato per votazione.

    Policy:
    - ANY / ALL: notifica tutti i membri contemporaneamente.
    - SEQUENTIAL: notifica soltanto il primo membro (order=1);
      i successivi verranno notificati uno alla volta da notify_ecn_next_approver.
    """
    from ecn.models import ChangeNotice
    approvers = list(change_notice.approvers.select_related('user').order_by('order', 'id'))
    if not approvers:
        return

    if change_notice.ccb_policy == ChangeNotice.CCBPolicy.SEQUENTIAL:
        # Solo il primo membro riceve la notifica ora
        _notify_ccb_member(change_notice, approvers[0].user, is_first=True)
    else:
        # ANY / ALL: notifica tutti
        for app in approvers:
            _notify_ccb_member(change_notice, app.user, is_first=False)


def _notify_ccb_member(change_notice, user, is_first=False):
    """Invia email a un singolo membro CCB."""
    from notifications.services import _send_and_log
    subject = f"[ECN] Richiesta di decisione CCB: {change_notice.code}"
    body = (
        f"Gentile {user.get_full_name() or user.username},\n\n"
        f"l'ECN {change_notice.code} «{change_notice.title}» è pronto per la tua decisione CCB.\n\n"
        f"Documento: {change_notice.document.code} — {change_notice.document.title}\n"
        f"Proponente: {change_notice.proposed_by.get_full_name() or change_notice.proposed_by.username}\n"
        f"Motivazione: {change_notice.get_motivation_display()}\n"
        f"Policy CCB: {change_notice.get_ccb_policy_display()}\n\n"
        f"Accedi al sistema per leggere il dossier istruttorio e esprimere la tua decisione."
    )
    _send_and_log(user, subject, body)


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
    """Notifica il prossimo membro CCB in una catena SEQUENTIAL."""
    _notify_ccb_member(change_notice, next_approver_user, is_first=False)


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
