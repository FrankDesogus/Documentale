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


def notify_ecn_created(change_notice):
    """
    Email a owner documento e Quality Manager quando ECN viene creata.
    Non notifica il proponente (è chi ha creato l'ECN).
    """
    from django.contrib.auth.models import User
    from notifications.services import _send_and_log

    recipients = set()
    doc_owner = change_notice.document.owner
    if doc_owner and doc_owner.pk != change_notice.proposed_by.pk:
        recipients.add(doc_owner)

    qm_users = User.objects.filter(groups__name='Quality Manager', is_active=True)
    for u in qm_users:
        if u.pk != change_notice.proposed_by.pk:
            recipients.add(u)

    for recipient in recipients:
        subject = f"[ECN] Nuova proposta di variante: {change_notice.code}"
        body = (
            f"Gentile {recipient.get_full_name() or recipient.username},\n\n"
            f"È stata proposta una nuova variante di controllo (ECN) sul documento che ti riguarda.\n\n"
            f"Codice ECN      : {change_notice.code}\n"
            f"Titolo          : {change_notice.title}\n"
            f"Documento       : {change_notice.document.code} — {change_notice.document.title}\n"
            f"Proponente      : {change_notice.proposed_by.get_full_name() or change_notice.proposed_by.username}\n"
            f"Motivazione     : {change_notice.get_motivation_display()}\n\n"
            f"Descrizione:\n{change_notice.description}\n\n"
            f"Puoi consultare il dettaglio della pratica nel sistema documentale.\n\n"
            f"Questo messaggio è generato automaticamente, non rispondere a questa email."
        )
        _send_and_log(recipient, subject, body)


def notify_ecn_coordinator_assigned(change_notice):
    """Email al coordinatore quando viene assegnato al dossier CCB."""
    from notifications.services import _send_and_log

    coordinator = change_notice.ccb_coordinator
    if not coordinator:
        return

    subject = f"[ECN] {change_notice.code} — Dossier CCB da compilare"
    body = (
        f"Gentile {coordinator.get_full_name() or coordinator.username},\n\n"
        f"sei stato assegnato come Responsabile Istruttoria CCB per la seguente variante.\n\n"
        f"Codice ECN  : {change_notice.code}\n"
        f"Titolo      : {change_notice.title}\n"
        f"Documento   : {change_notice.document.code} — {change_notice.document.title}\n"
        f"Proponente  : {change_notice.proposed_by.get_full_name() or change_notice.proposed_by.username}\n"
        f"Motivazione : {change_notice.get_motivation_display()}\n\n"
        f"Il tuo compito è compilare il dossier istruttorio (classificazione, analisi requisiti, "
        f"impatti tecnici/costi/tempi/qualità) prima dell'invio ai votanti CCB.\n\n"
        f"Accedi al sistema documentale per procedere.\n\n"
        f"Questo messaggio è generato automaticamente, non rispondere a questa email."
    )
    _send_and_log(coordinator, subject, body)


def notify_ecn_vote_cast(change_notice, voter, decision, comment=''):
    """
    Dopo ogni voto CCB:
    - tutti i membri CCB ricevono aggiornamento informativo
    - SEQUENTIAL e pratica ancora aperta: il prossimo membro riceve anche
      un messaggio che indica che è il suo turno.
    """
    from ecn.models import ChangeNotice, ChangeNoticeDecision
    from notifications.services import _send_and_log

    all_approvers = list(change_notice.approvers.select_related('user').order_by('order', 'id'))
    if not all_approvers:
        return

    # Trova il prossimo approvatore SEQUENTIAL (se applicabile)
    next_approver_user = None
    if (
        change_notice.ccb_policy == ChangeNotice.CCBPolicy.SEQUENTIAL
        and change_notice.status == ChangeNotice.Status.UNDER_REVIEW
    ):
        decided_ids = set(
            ChangeNoticeDecision.objects.filter(change_notice=change_notice)
            .values_list('approver_id', flat=True)
        )
        pending = [
            a for a in all_approvers
            if a.pk not in decided_ids
        ]
        if pending:
            next_approver_user = pending[0].user

    decision_label = 'Approvato' if decision == 'approve' else 'Rifiutato'
    voter_name = voter.get_full_name() or voter.username

    for approver_rel in all_approvers:
        recipient = approver_rel.user
        is_next = (next_approver_user is not None and recipient.pk == next_approver_user.pk)

        if is_next:
            next_block = (
                f"\nÈ ora il tuo turno: accedi al sistema per esprimere la tua decisione su {change_notice.code}.\n"
            )
        else:
            next_block = ""

        body = (
            f"Gentile {recipient.get_full_name() or recipient.username},\n\n"
            f"è stato espresso un voto sull'ECN {change_notice.code} «{change_notice.title}».\n\n"
            f"Votante   : {voter_name}\n"
            f"Decisione : {decision_label}\n"
        )
        if comment:
            body += f"Commento  : {comment}\n"
        body += (
            f"\nDocumento : {change_notice.document.code} — {change_notice.document.title}\n"
            f"Policy CCB: {change_notice.get_ccb_policy_display()}\n"
            f"Stato ECN : {change_notice.get_status_display()}\n"
        )
        body += next_block
        body += (
            f"\nAccedi al sistema documentale per vedere lo stato completo della pratica.\n\n"
            f"Questo messaggio è generato automaticamente, non rispondere a questa email."
        )

        subject = f"[ECN] Voto espresso su {change_notice.code}"
        _send_and_log(recipient, subject, body)


def notify_ecn_approved(change_notice):
    """
    Invia email quando l'ECN è approvato dalla CCB.
    Destinatari: proponente, owner documento (se diverso), ccb_coordinator,
    tutti i membri CCB, Quality Manager. Deduplica per pk.
    """
    from django.contrib.auth.models import User
    from notifications.services import _send_and_log

    ccb_class_label = (
        change_notice.get_ccb_class_display() if change_notice.ccb_class else '—'
    )
    subject = f"[ECN] Approvato: {change_notice.code}"

    recipients = _collect_ecn_outcome_recipients(change_notice)

    for recipient in recipients:
        # Messaggio speciale per owner/autore documento
        doc_owner = change_notice.document.owner
        is_doc_owner = (doc_owner and recipient.pk == doc_owner.pk)
        is_proposer = (recipient.pk == change_notice.proposed_by.pk)

        body = (
            f"Gentile {recipient.get_full_name() or recipient.username},\n\n"
            f"l'ECN {change_notice.code} «{change_notice.title}» è stato approvato dalla CCB.\n\n"
            f"Documento      : {change_notice.document.code} — {change_notice.document.title}\n"
            f"Proponente     : {change_notice.proposed_by.get_full_name() or change_notice.proposed_by.username}\n"
            f"Classe variante: {ccb_class_label}\n\n"
        )
        if is_proposer or is_doc_owner:
            body += (
                "La ECN è stata approvata. "
                "È ora possibile creare la nuova revisione autorizzata del documento.\n\n"
            )
        body += (
            "Accedi al sistema documentale per i dettagli.\n\n"
            "Questo messaggio è generato automaticamente, non rispondere a questa email."
        )
        _send_and_log(recipient, subject, body)


def notify_ecn_rejected(change_notice):
    """
    Invia email quando l'ECN è rifiutato dalla CCB.
    Destinatari: proponente, owner documento (se diverso), ccb_coordinator,
    tutti i membri CCB, Quality Manager. Deduplica per pk.
    """
    from notifications.services import _send_and_log

    subject = f"[ECN] Rifiutato: {change_notice.code}"
    recipients = _collect_ecn_outcome_recipients(change_notice)

    for recipient in recipients:
        body = (
            f"Gentile {recipient.get_full_name() or recipient.username},\n\n"
            f"l'ECN {change_notice.code} «{change_notice.title}» è stato rifiutato dalla CCB.\n\n"
            f"Documento  : {change_notice.document.code} — {change_notice.document.title}\n"
            f"Proponente : {change_notice.proposed_by.get_full_name() or change_notice.proposed_by.username}\n"
            f"Motivo     : {change_notice.ccb_notes or '—'}\n\n"
            f"Accedi al sistema per i dettagli.\n\n"
            f"Questo messaggio è generato automaticamente, non rispondere a questa email."
        )
        _send_and_log(recipient, subject, body)


def notify_ecn_next_approver(change_notice, next_approver_user):
    """Notifica il prossimo membro CCB in una catena SEQUENTIAL."""
    _notify_ccb_member(change_notice, next_approver_user, is_first=False)


def notify_ecn_executed(change_notice):
    """
    Email quando ECN viene eseguita (nuova revisione creata).
    Destinatari: ccb_coordinator, proponente, Quality Manager. Deduplica.
    """
    from django.contrib.auth.models import User
    from notifications.services import _send_and_log

    recipients = set()
    if change_notice.ccb_coordinator:
        recipients.add(change_notice.ccb_coordinator)
    recipients.add(change_notice.proposed_by)
    for u in User.objects.filter(groups__name='Quality Manager', is_active=True):
        recipients.add(u)

    subject = f"[ECN] {change_notice.code} — Revisione eseguita"

    executed_version = change_notice.executed_version
    rev_label = executed_version.revision_label if executed_version else '—'

    for recipient in recipients:
        body = (
            f"Gentile {recipient.get_full_name() or recipient.username},\n\n"
            f"l'ECN {change_notice.code} «{change_notice.title}» è stato eseguito: "
            f"è stata creata la nuova revisione del documento.\n\n"
            f"Documento  : {change_notice.document.code} — {change_notice.document.title}\n"
            f"Revisione  : {rev_label}\n"
            f"Proponente : {change_notice.proposed_by.get_full_name() or change_notice.proposed_by.username}\n\n"
            f"La nuova revisione è ora in bozza e deve essere inviata in approvazione.\n\n"
            f"Accedi al sistema documentale per i dettagli.\n\n"
            f"Questo messaggio è generato automaticamente, non rispondere a questa email."
        )
        _send_and_log(recipient, subject, body)


def notify_ecn_closed(change_notice):
    """
    Invia email quando l'ECN è chiuso.
    Destinatari: proponente, owner documento, ccb_coordinator,
    tutti i membri CCB, Quality Manager. Deduplica.
    """
    from notifications.services import _send_and_log

    subject = f"[ECN] Chiuso: {change_notice.code}"
    recipients = _collect_ecn_outcome_recipients(change_notice)

    for recipient in recipients:
        body = (
            f"Gentile {recipient.get_full_name() or recipient.username},\n\n"
            f"l'ECN {change_notice.code} «{change_notice.title}» è stato chiuso.\n\n"
            f"Documento      : {change_notice.document.code} — {change_notice.document.title}\n"
            f"Proponente     : {change_notice.proposed_by.get_full_name() or change_notice.proposed_by.username}\n"
            f"Note chiusura  : {change_notice.close_notes or '—'}\n\n"
            f"Accedi al sistema documentale per i dettagli.\n\n"
            f"Questo messaggio è generato automaticamente, non rispondere a questa email."
        )
        _send_and_log(recipient, subject, body)


# ---------------------------------------------------------------------------
# Helpers interni
# ---------------------------------------------------------------------------

def _collect_ecn_outcome_recipients(change_notice):
    """
    Raccoglie l'insieme di tutti i destinatari per email di esito ECN
    (approvato / rifiutato / chiuso):
      - proponente
      - owner documento (se diverso dal proponente)
      - ccb_coordinator (se assegnato)
      - tutti i membri CCB
      - Quality Manager attivi
    Ritorna una lista di User deduplicati per pk.
    """
    from django.contrib.auth.models import User

    seen_ids = set()
    recipients = []

    def _add(user):
        if user and user.pk not in seen_ids:
            seen_ids.add(user.pk)
            recipients.append(user)

    _add(change_notice.proposed_by)
    _add(change_notice.document.owner)
    _add(change_notice.ccb_coordinator)

    for approver_rel in change_notice.approvers.select_related('user').all():
        _add(approver_rel.user)

    for u in User.objects.filter(groups__name='Quality Manager', is_active=True):
        _add(u)

    return recipients
