"""Servizi applicativi ECN / Varianti.

Workflow stati:
  DRAFT → UNDER_REVIEW → APPROVED → CLOSED
                       ↓
                    REJECTED (terminale)

Ogni transizione:
  - valida lo stato corrente;
  - verifica il permesso dell'utente;
  - aggiorna i campi necessari con save(update_fields=[...]);
  - scrive un AuditLog con document_id nel JSON (per integrazione con
    storico documento già esistente via changes__document_id=doc.pk);
  - invia notifiche email via ecn.notifications (errori silenziosi).

Politiche CCB (ccb_policy):
  ANY        – basta un approvatore che approvi → ECN APPROVATO
  ALL        – tutti gli approvatori devono approvare (un rifiuto = ECN RIFIUTATO)
  SEQUENTIAL – gli approvatori decidono in ordine; email al successivo dopo ogni approvazione
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone


# ---------------------------------------------------------------------------
# API pubblica
# ---------------------------------------------------------------------------

def create_change_notice(
    document,
    proposed_by,
    title,
    motivation,
    description='',
    motivation_detail='',
    commessa='',
    project=None,
    document_version=None,
    code=None,
    created_by=None,
):
    """
    Crea un ECN in stato DRAFT.

    Se document_version è None, usa document.current_version come snapshot.
    Se code è None, genera automaticamente un codice ECN-NNNN univoco.
    Se created_by è None, usa proposed_by.
    """
    from ecn.models import ChangeNotice

    if created_by is None:
        created_by = proposed_by

    if document_version is None:
        if document.current_version is None:
            raise ValidationError(
                "Il documento non ha una versione corrente approvata. "
                "L'ECN può essere proposto solo su documenti con almeno una revisione corrente."
            )
        document_version = document.current_version

    if code is None:
        code = _generate_ecn_code()

    ecn = ChangeNotice.objects.create(
        code=code,
        title=title,
        description=description,
        motivation=motivation,
        motivation_detail=motivation_detail,
        commessa=commessa,
        document=document,
        document_version=document_version,
        project=project,
        proposed_by=proposed_by,
        created_by=created_by,
    )

    _write_audit(
        actor=proposed_by,
        action='ECN_CREATED',
        ecn=ecn,
        old_status=None,
        new_status=ecn.status,
    )

    return ecn


def set_change_notice_approvers(change_notice, users, policy=None):
    """
    Assegna (o rimpiazza) gli approvatori CCB di un ECN in stato DRAFT.

    - users: lista/queryset di User ordinati (il primo ha order=1, ecc.).
    - policy: se non None, aggiorna anche change_notice.ccb_policy.

    Raises:
      ValidationError: se l'ECN non è in stato DRAFT.
      ValidationError: se users è vuoto.
    """
    from ecn.models import ChangeNotice, ChangeNoticeApprover

    if change_notice.status != ChangeNotice.Status.DRAFT:
        raise ValidationError(
            "Gli approvatori possono essere assegnati solo a ECN in bozza."
        )

    users = list(users)
    if not users:
        raise ValidationError(
            "È necessario assegnare almeno un approvatore CCB prima di procedere."
        )

    with transaction.atomic():
        ChangeNoticeApprover.objects.filter(change_notice=change_notice).delete()
        for i, user in enumerate(users, start=1):
            ChangeNoticeApprover.objects.create(
                change_notice=change_notice,
                user=user,
                order=i,
            )
        if policy is not None:
            change_notice.ccb_policy = policy
            change_notice.save(update_fields=['ccb_policy'])


def submit_change_notice(change_notice, user):
    """
    Invia l'ECN alla CCB: DRAFT → UNDER_REVIEW.

    Richiede almeno un approvatore assegnato.
    Invia email a tutti gli approvatori.

    Raises:
      PermissionDenied: se l'utente non ha il permesso.
      ValidationError: se lo stato non è DRAFT o non ci sono approvatori.
    """
    from ecn.models import ChangeNotice
    from ecn.permissions import can_submit_ecn

    if not can_submit_ecn(user, change_notice):
        raise PermissionDenied("Non hai il permesso di inviare questo ECN alla CCB.")

    if change_notice.status != ChangeNotice.Status.DRAFT:
        raise ValidationError(
            f"Solo gli ECN in bozza possono essere inviati alla CCB. "
            f"Stato attuale: {change_notice.get_status_display()}."
        )

    if not change_notice.approvers.exists():
        raise ValidationError(
            "È necessario assegnare almeno un approvatore CCB prima di inviare l'ECN."
        )

    old_status = change_notice.status
    change_notice.status = ChangeNotice.Status.UNDER_REVIEW
    change_notice.submitted_at = timezone.now()
    change_notice.save(update_fields=['status', 'submitted_at'])

    _write_audit(
        actor=user,
        action='ECN_SUBMITTED',
        ecn=change_notice,
        old_status=old_status,
        new_status=change_notice.status,
    )

    _notify_silently('notify_ecn_submitted', change_notice)

    return change_notice


def approve_change_notice(
    change_notice,
    user,
    ccb_class=None,
    ccb_requirements='',
    ccb_technical_impact='',
    ccb_cost_impact='',
    ccb_time_impact='',
    ccb_quality_impact='',
    ccb_other_impact='',
    ccb_notes='',
    comment='',
):
    """
    Un approvatore CCB approva l'ECN.

    Crea un ChangeNoticeDecision(APPROVE).
    Salva i campi CCB sul ChangeNotice (chi approva per ultimo vince).
    Verifica la policy per decidere se l'ECN è finalizzato (→ APPROVED).

    ccb_class è obbligatorio solo quando l'approvazione finalizza l'ECN.

    Raises:
      PermissionDenied: se l'utente non è un approvatore assegnato.
      ValidationError: se lo stato non è UNDER_REVIEW, o ccb_class manca al finalizzare.
    """
    from ecn.models import ChangeNotice, ChangeNoticeApprover, ChangeNoticeDecision
    from ecn.permissions import can_review_ecn

    if not can_review_ecn(user, change_notice):
        raise PermissionDenied("Non hai il permesso di approvare questo ECN.")

    if change_notice.status != ChangeNotice.Status.UNDER_REVIEW:
        raise ValidationError(
            f"Solo gli ECN in revisione CCB possono essere approvati. "
            f"Stato attuale: {change_notice.get_status_display()}."
        )

    # Recupero l'approvatore assegnato (superuser/staff può non averne uno)
    approver_obj = ChangeNoticeApprover.objects.filter(
        change_notice=change_notice, user=user,
    ).first()

    # Se è superuser/staff senza approvatore assegnato, usiamo il primo
    # approvatore non ancora deciso come placeholder (o creiamo uno temporaneo)
    if approver_obj is None:
        # Superuser/staff bypass: creo un record approvatore temporaneo
        # con ordine = max+1 per non interferire con l'ordine esistente
        max_order = (
            ChangeNoticeApprover.objects.filter(change_notice=change_notice)
            .aggregate(m=Max('order'))['m'] or 0
        )
        approver_obj = ChangeNoticeApprover.objects.create(
            change_notice=change_notice,
            user=user,
            order=max_order + 1,
        )

    # Controlla che questo approvatore non abbia già deciso
    if ChangeNoticeDecision.objects.filter(
        change_notice=change_notice, approver=approver_obj,
    ).exists():
        raise ValidationError(
            "Hai già espresso una decisione su questo ECN."
        )

    now = timezone.now()

    with transaction.atomic():
        # Salva i campi CCB analisi sul ChangeNotice (l'ultimo che approva vince)
        update_fields_ccb = [
            'ccb_requirements', 'ccb_technical_impact', 'ccb_cost_impact',
            'ccb_time_impact', 'ccb_quality_impact', 'ccb_other_impact',
            'ccb_notes',
        ]
        change_notice.ccb_requirements    = ccb_requirements
        change_notice.ccb_technical_impact = ccb_technical_impact
        change_notice.ccb_cost_impact     = ccb_cost_impact
        change_notice.ccb_time_impact     = ccb_time_impact
        change_notice.ccb_quality_impact  = ccb_quality_impact
        change_notice.ccb_other_impact    = ccb_other_impact
        change_notice.ccb_notes           = ccb_notes
        if ccb_class:
            change_notice.ccb_class = ccb_class
            update_fields_ccb.append('ccb_class')
        change_notice.save(update_fields=update_fields_ccb)

        # Crea la decisione individuale
        ChangeNoticeDecision.objects.create(
            change_notice=change_notice,
            approver=approver_obj,
            user=user,
            decision=ChangeNoticeDecision.Decision.APPROVE,
            comment=comment,
        )

        # Verifica se l'ECN è finalizzato
        finalized = _check_policy_after_approve(change_notice)

        if finalized:
            if not ccb_class and not change_notice.ccb_class:
                raise ValidationError(
                    "La classe variante (Classe 1 / Classe 2) è obbligatoria per approvare l'ECN."
                )
            old_status = change_notice.status
            change_notice.status         = ChangeNotice.Status.APPROVED
            change_notice.ccb_reviewed_by = user
            change_notice.ccb_reviewed_at = now
            change_notice.save(update_fields=['status', 'ccb_reviewed_by', 'ccb_reviewed_at'])

            _write_audit(
                actor=user,
                action='ECN_APPROVED',
                ecn=change_notice,
                old_status=old_status,
                new_status=change_notice.status,
            )
            _notify_silently('notify_ecn_approved', change_notice)

        else:
            # Per SEQUENTIAL: notifica il prossimo approvatore
            if change_notice.ccb_policy == ChangeNotice.CCBPolicy.SEQUENTIAL:
                _notify_next_sequential(change_notice)

    return change_notice


def reject_change_notice(change_notice, user, reason, comment=None):
    """
    Un approvatore CCB rifiuta l'ECN: UNDER_REVIEW → REJECTED.

    reason è obbligatorio e viene salvato in ccb_notes.
    Crea un ChangeNoticeDecision(REJECT).
    Invia email al proponente.

    Raises:
      PermissionDenied: se l'utente non è un approvatore assegnato.
      ValidationError: se lo stato non è UNDER_REVIEW o reason è vuoto.
    """
    from ecn.models import ChangeNotice, ChangeNoticeApprover, ChangeNoticeDecision
    from ecn.permissions import can_review_ecn

    if not can_review_ecn(user, change_notice):
        raise PermissionDenied("Non hai il permesso di rifiutare questo ECN.")

    if change_notice.status != ChangeNotice.Status.UNDER_REVIEW:
        raise ValidationError(
            f"Solo gli ECN in revisione CCB possono essere rifiutati. "
            f"Stato attuale: {change_notice.get_status_display()}."
        )

    if not reason or not reason.strip():
        raise ValidationError("Il motivo del rifiuto è obbligatorio.")

    approver_obj = ChangeNoticeApprover.objects.filter(
        change_notice=change_notice, user=user,
    ).first()

    if approver_obj is None:
        # Superuser/staff bypass
        max_order = (
            ChangeNoticeApprover.objects.filter(change_notice=change_notice)
            .aggregate(m=Max('order'))['m'] or 0
        )
        approver_obj = ChangeNoticeApprover.objects.create(
            change_notice=change_notice,
            user=user,
            order=max_order + 1,
        )

    if ChangeNoticeDecision.objects.filter(
        change_notice=change_notice, approver=approver_obj,
    ).exists():
        raise ValidationError(
            "Hai già espresso una decisione su questo ECN."
        )

    old_status = change_notice.status
    now = timezone.now()

    decision_comment = comment if comment is not None else reason.strip()

    with transaction.atomic():
        ChangeNoticeDecision.objects.create(
            change_notice=change_notice,
            approver=approver_obj,
            user=user,
            decision=ChangeNoticeDecision.Decision.REJECT,
            comment=decision_comment,
        )

        change_notice.status           = ChangeNotice.Status.REJECTED
        change_notice.ccb_notes        = reason.strip()
        change_notice.ccb_reviewed_by  = user
        change_notice.ccb_reviewed_at  = now
        change_notice.save(update_fields=['status', 'ccb_notes', 'ccb_reviewed_by', 'ccb_reviewed_at'])

    _write_audit(
        actor=user,
        action='ECN_REJECTED',
        ecn=change_notice,
        old_status=old_status,
        new_status=change_notice.status,
    )
    _notify_silently('notify_ecn_rejected', change_notice)

    return change_notice


def close_change_notice(change_notice, user, close_notes=''):
    """
    Il Responsabile Qualità chiude l'ECN: APPROVED → CLOSED.

    Richiede che executed_version sia già stato impostato (la nuova revisione
    eseguita deve essere collegata prima di chiudere l'ECN).
    Invia email al proponente.

    Raises:
      PermissionDenied: se l'utente non è Document Manager/staff.
      ValidationError: se lo stato non è APPROVED o executed_version è None.
    """
    from ecn.models import ChangeNotice
    from ecn.permissions import can_close_ecn

    if not can_close_ecn(user, change_notice):
        raise PermissionDenied("Non hai il permesso di chiudere questo ECN.")

    if change_notice.status != ChangeNotice.Status.APPROVED:
        raise ValidationError(
            f"Solo gli ECN approvati possono essere chiusi. "
            f"Stato attuale: {change_notice.get_status_display()}."
        )

    if change_notice.executed_version_id is None:
        raise ValidationError(
            "Impossibile chiudere l'ECN: nessuna revisione di esecuzione collegata. "
            "Crea prima la nuova revisione del documento a partire da questo ECN."
        )

    old_status = change_notice.status
    now = timezone.now()

    change_notice.status     = ChangeNotice.Status.CLOSED
    change_notice.close_notes = close_notes
    change_notice.closed_by  = user
    change_notice.closed_at  = now
    change_notice.save(update_fields=['status', 'close_notes', 'closed_by', 'closed_at'])

    _write_audit(
        actor=user,
        action='ECN_CLOSED',
        ecn=change_notice,
        old_status=old_status,
        new_status=change_notice.status,
    )
    _notify_silently('notify_ecn_closed', change_notice)

    return change_notice


# ---------------------------------------------------------------------------
# Helpers interni
# ---------------------------------------------------------------------------

def _check_policy_after_approve(change_notice):
    """
    Verifica se la policy CCB è soddisfatta dopo un'approvazione.

    Ritorna True se l'ECN deve essere finalizzato come APPROVATO.
    Deve essere chiamato dentro una transaction.atomic() per coerenza.
    """
    from ecn.models import ChangeNoticeApprover, ChangeNoticeDecision

    policy = change_notice.ccb_policy
    approvers = list(
        ChangeNoticeApprover.objects.filter(change_notice=change_notice).order_by('order', 'id')
    )
    if not approvers:
        return True  # nessun approvatore → autoapprovato

    decisions = {
        d.approver_id: d.decision
        for d in ChangeNoticeDecision.objects.filter(change_notice=change_notice)
    }

    if policy == change_notice.CCBPolicy.ANY:
        # Basta una singola approvazione
        return True

    elif policy == change_notice.CCBPolicy.ALL:
        # Tutti devono approvare
        for app in approvers:
            dec = decisions.get(app.pk)
            if dec is None:
                return False  # non ha ancora deciso
            if dec == 'reject':
                return False  # uno ha rifiutato → non approvato (reject già gestito)
        return True  # tutti hanno approvato

    elif policy == change_notice.CCBPolicy.SEQUENTIAL:
        # Tutti in ordine devono approvare
        for app in approvers:
            dec = decisions.get(app.pk)
            if dec is None:
                return False  # questo non ha ancora deciso
            if dec == 'reject':
                return False  # uno ha rifiutato
        return True  # tutti (in ordine) hanno approvato

    return False


def _notify_next_sequential(change_notice):
    """
    Per policy SEQUENTIAL: trova il prossimo approvatore che non ha ancora deciso
    e gli invia una notifica email.
    """
    from ecn.models import ChangeNoticeApprover, ChangeNoticeDecision
    from ecn.notifications import notify_ecn_next_approver

    decided_ids = set(
        ChangeNoticeDecision.objects.filter(change_notice=change_notice)
        .values_list('approver_id', flat=True)
    )
    next_app = (
        ChangeNoticeApprover.objects.filter(change_notice=change_notice)
        .exclude(pk__in=decided_ids)
        .order_by('order', 'id')
        .first()
    )
    if next_app:
        try:
            notify_ecn_next_approver(change_notice, next_app.user)
        except Exception:
            pass


def _generate_ecn_code():
    """
    Genera il prossimo codice ECN-NNNN non ancora utilizzato.
    Usa Max(pk) come base: semplice e robusto per un sistema a bassa concorrenza.
    """
    from ecn.models import ChangeNotice
    result = ChangeNotice.objects.aggregate(max_pk=Max('pk'))
    next_n = (result['max_pk'] or 0) + 1
    code = f'ECN-{next_n:04d}'
    # Ciclo di sicurezza contro race condition o codici già esistenti
    while ChangeNotice.objects.filter(code=code).exists():
        next_n += 1
        code = f'ECN-{next_n:04d}'
    return code


def _notify_silently(func_name, change_notice):
    """Chiama una funzione di notifica silenziando ogni eccezione."""
    try:
        import ecn.notifications as notif
        getattr(notif, func_name)(change_notice)
    except Exception:
        pass


def _write_audit(actor, action, ecn, old_status, new_status):
    """
    Scrive un AuditLog per una transizione ECN.

    Passa document= in modo che changes['document_id'] sia presente:
    gli eventi ECN appaiono automaticamente nello storico documento
    (la query AuditLog.objects.filter(changes__document_id=doc.pk)
    già esistente nelle views li cattura senza modifiche).
    """
    try:
        from auditlog.services import create_audit_log
        metadata = {
            'ecn_id': ecn.pk,
            'code': ecn.code,
            'new_status': new_status,
        }
        if old_status is not None:
            metadata['old_status'] = old_status
        if ecn.project_id:
            metadata['project_id'] = ecn.project_id

        create_audit_log(
            user=actor,
            action=action,
            instance=ecn,
            old_values={'status': old_status} if old_status is not None else None,
            new_values={'status': new_status},
            document=ecn.document,
            document_version=ecn.document_version,
            metadata=metadata,
        )
    except Exception:
        pass
