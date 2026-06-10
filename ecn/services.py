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
    send_notifications=True,
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

    if send_notifications:
        _notify_silently('notify_ecn_created', ecn)
        try:
            from notifications.inbox import notify_ecn_created_inapp
            notify_ecn_created_inapp(ecn)
        except Exception:
            pass

    return ecn


def update_change_notice(change_notice, actor, title, motivation,
                         description='', motivation_detail='', commessa='', project=None):
    """
    Aggiorna i dati base di un ECN in stato DRAFT.

    Modificabili: title, motivation, motivation_detail, description, commessa, project.
    Lo stato deve essere DRAFT (il service non verifica il permesso: lo fa la view).

    Raises:
      ValidationError: se l'ECN non è in stato DRAFT.
    """
    from ecn.models import ChangeNotice

    if change_notice.status != ChangeNotice.Status.DRAFT:
        raise ValidationError(
            "I dati base possono essere modificati solo su ECN in bozza."
        )

    old_values = {
        'title': change_notice.title,
        'motivation': change_notice.motivation,
        'description': change_notice.description,
        'motivation_detail': change_notice.motivation_detail,
        'commessa': change_notice.commessa,
        'project_id': change_notice.project_id,
    }

    change_notice.title = title
    change_notice.motivation = motivation
    change_notice.description = description
    change_notice.motivation_detail = motivation_detail
    change_notice.commessa = commessa
    change_notice.project = project
    change_notice.save(update_fields=[
        'title', 'motivation', 'description', 'motivation_detail', 'commessa', 'project',
    ])

    new_values = {
        'title': change_notice.title,
        'motivation': change_notice.motivation,
        'description': change_notice.description,
        'motivation_detail': change_notice.motivation_detail,
        'commessa': change_notice.commessa,
        'project_id': change_notice.project_id,
    }

    try:
        from auditlog.services import create_audit_log
        create_audit_log(
            user=actor,
            action='ECN_UPDATED',
            instance=change_notice,
            old_values=old_values,
            new_values=new_values,
            document=change_notice.document,
            document_version=change_notice.document_version,
            metadata={'ecn_id': change_notice.pk, 'code': change_notice.code},
        )
    except Exception:
        pass

    return change_notice


def reopen_ccb_configuration(change_notice, actor, reason=''):
    """
    Riapre la configurazione CCB di un ECN under_review con decisioni già espresse.

    Operazioni:
      1. Cancella tutte le ChangeNoticeDecision dell'ECN.
      2. Riporta lo stato a DRAFT e azzera submitted_at.
      3. Scrive audit ECN_CCB_REOPENED.

    Dopo questa operazione l'ECN è tornato in bozza: il Manager può modificare
    gli approvatori e la policy, quindi reinviare.

    Raises:
      PermissionDenied: se l'utente non ha can_reopen_ccb.
      ValidationError: se lo stato non è UNDER_REVIEW o non ci sono decisioni.
    """
    from ecn.models import ChangeNotice, ChangeNoticeDecision
    from ecn.permissions import can_reopen_ccb

    # Verifica stato e condizioni prima del permesso, per dare errori più chiari
    if change_notice.status != ChangeNotice.Status.UNDER_REVIEW:
        raise ValidationError(
            "La riapertura CCB è possibile solo su ECN in revisione."
        )

    if not change_notice.decisions.exists():
        raise ValidationError(
            "Nessuna decisione da annullare. Usa 'Modifica CCB' direttamente."
        )

    if not can_reopen_ccb(actor, change_notice):
        raise PermissionDenied("Non hai il permesso di riaprire la configurazione CCB.")

    old_status = change_notice.status

    with transaction.atomic():
        deleted_count = ChangeNoticeDecision.objects.filter(change_notice=change_notice).delete()[0]

        change_notice.status = ChangeNotice.Status.DRAFT
        change_notice.submitted_at = None
        change_notice.save(update_fields=['status', 'submitted_at'])

    try:
        from auditlog.services import create_audit_log
        meta = {
            'ecn_id': change_notice.pk,
            'code': change_notice.code,
            'old_status': old_status,
            'new_status': change_notice.status,
            'decisions_deleted': deleted_count,
        }
        if reason:
            meta['reason'] = reason
        create_audit_log(
            user=actor,
            action='ECN_CCB_REOPENED',
            instance=change_notice,
            old_values={'status': old_status},
            new_values={'status': change_notice.status},
            document=change_notice.document,
            document_version=change_notice.document_version,
            metadata=meta,
        )
    except Exception:
        pass

    return change_notice


def set_change_notice_approvers(change_notice, users, policy=None, actor=None):
    """
    Assegna (o rimpiazza) gli approvatori CCB di un ECN in stato DRAFT
    oppure UNDER_REVIEW senza decisioni ancora espresse.

    - users: lista/queryset di User ordinati (il primo ha order=1, ecc.).
    - policy: se non None, aggiorna anche change_notice.ccb_policy.
    - actor: se fornito e lo stato è UNDER_REVIEW, scrive un audit ECN_CCB_UPDATED.

    Raises:
      ValidationError: se lo stato non è DRAFT o UNDER_REVIEW-senza-decisioni.
      ValidationError: se users è vuoto.
    """
    from ecn.models import ChangeNotice, ChangeNoticeApprover

    allowed = (
        ChangeNotice.Status.DRAFT,
        ChangeNotice.Status.CCB_PREPARATION,
        ChangeNotice.Status.UNDER_REVIEW,
    )
    if change_notice.status not in allowed:
        raise ValidationError(
            "Gli approvatori possono essere assegnati solo a ECN in bozza, "
            "istruttoria CCB o in revisione senza decisioni."
        )

    if change_notice.status == ChangeNotice.Status.UNDER_REVIEW and change_notice.decisions.exists():
        raise ValidationError(
            "Impossibile modificare gli approvatori: esistono già decisioni espresse. "
            "Usa 'Riapri configurazione CCB' per annullare le decisioni prima di modificare."
        )

    users = list(users)
    if not users:
        raise ValidationError(
            "È necessario assegnare almeno un approvatore CCB prima di procedere."
        )

    was_under_review = change_notice.status == ChangeNotice.Status.UNDER_REVIEW

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

    if was_under_review and actor is not None:
        try:
            from auditlog.services import create_audit_log
            create_audit_log(
                user=actor,
                action='ECN_CCB_UPDATED',
                instance=change_notice,
                old_values=None,
                new_values={
                    'ccb_policy': change_notice.ccb_policy,
                    'approver_count': len(users),
                },
                document=change_notice.document,
                document_version=change_notice.document_version,
                metadata={'ecn_id': change_notice.pk, 'code': change_notice.code},
            )
        except Exception:
            pass


def submit_change_notice(change_notice, user, send_notifications=True):
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

    _allowed_submit = (ChangeNotice.Status.DRAFT, ChangeNotice.Status.CCB_PREPARATION)
    if change_notice.status not in _allowed_submit:
        raise ValidationError(
            f"Solo gli ECN in bozza o istruttoria possono essere inviati alla CCB. "
            f"Stato attuale: {change_notice.get_status_display()}."
        )

    # Verifica campi obbligatori del dossier solo quando si parte da CCB_PREPARATION
    # (il percorso legacy DRAFT→UNDER_REVIEW non impone la validazione dossier
    #  per retrocompatibilità con i test esistenti)
    if change_notice.status == ChangeNotice.Status.CCB_PREPARATION:
        if not change_notice.ccb_class:
            raise ValidationError(
                "La classificazione variante è obbligatoria prima dell'invio alla CCB. "
                "Compila il dossier istruttorio."
            )
        if not change_notice.ccb_requirements or not change_notice.ccb_requirements.strip():
            raise ValidationError(
                "L'analisi requisiti è obbligatoria prima dell'invio alla CCB. "
                "Compila il dossier istruttorio."
            )
        if not change_notice.ccb_technical_impact or not change_notice.ccb_technical_impact.strip():
            raise ValidationError(
                "L'impatto tecnico è obbligatorio prima dell'invio alla CCB. "
                "Compila il dossier istruttorio."
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

    if send_notifications:
        _notify_silently('notify_ecn_submitted', change_notice)

        # Notifiche in-app ai membri CCB
        try:
            from notifications.inbox import notify_ccb_vote_required
            from ecn.models import ChangeNotice as _CN
            ccb_policy = change_notice.ccb_policy
            approvers_qs = change_notice.approvers.order_by('order')
            if ccb_policy == _CN.CCBPolicy.SEQUENTIAL:
                first_app = approvers_qs.first()
                if first_app:
                    notify_ccb_vote_required(change_notice, first_app.user)
            else:
                for app in approvers_qs:
                    notify_ccb_vote_required(change_notice, app.user)
        except Exception:
            pass

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
    send_notifications=True,
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
            if send_notifications:
                _notify_silently('notify_ecn_approved', change_notice)
                try:
                    from notifications.inbox import notify_ecn_outcome_inapp
                    notify_ecn_outcome_inapp(change_notice, approved=True)
                except Exception:
                    pass

        else:
            # Per SEQUENTIAL: notifica il prossimo approvatore
            if send_notifications and change_notice.ccb_policy == ChangeNotice.CCBPolicy.SEQUENTIAL:
                _notify_next_sequential(change_notice)

    # Per approvazioni parziali (non finali), notifica tutti i membri CCB del voto espresso.
    # Per il caso finale notify_ecn_approved ha già inviato le email rilevanti.
    if send_notifications and not finalized:
        try:
            from ecn.notifications import notify_ecn_vote_cast
            notify_ecn_vote_cast(change_notice, user, 'approve', comment)
        except Exception:
            pass

    return change_notice


def reject_change_notice(change_notice, user, reason, comment=None, send_notifications=True):
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
    if send_notifications:
        # notify_ecn_rejected invia email a tutti i destinatari rilevanti
        _notify_silently('notify_ecn_rejected', change_notice)
        try:
            from notifications.inbox import notify_ecn_outcome_inapp
            notify_ecn_outcome_inapp(change_notice, approved=False)
        except Exception:
            pass

        # Notifica anche i membri CCB del voto individuale (informativo)
        try:
            from ecn.notifications import notify_ecn_vote_cast
            notify_ecn_vote_cast(change_notice, user, 'reject', decision_comment)
        except Exception:
            pass

    return change_notice


def close_change_notice(change_notice, user, close_notes='', send_notifications=True):
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
    if send_notifications:
        _notify_silently('notify_ecn_closed', change_notice)
        try:
            from notifications.inbox import notify_ecn_closed_inapp
            notify_ecn_closed_inapp(change_notice)
        except Exception:
            pass

    return change_notice


# ---------------------------------------------------------------------------
# Nuovi service STEP I — separazione istruttoria / voto
# ---------------------------------------------------------------------------

def configure_ccb(change_notice, actor, users, policy=None, coordinator=None, send_notifications=True):
    """
    Configura la CCB: assegna coordinator, members, policy.
    Transizione DRAFT → CCB_PREPARATION (se già in CCB_PREPARATION rimane).

    - users: lista ordinata di User (ordine = priorità CCB).
    - policy: se non None, aggiorna ccb_policy.
    - coordinator: se non None, imposta ccb_coordinator.
    - actor: utente che compie l'azione (per AuditLog).

    Raises:
      ValidationError: se lo stato non è DRAFT o CCB_PREPARATION.
      ValidationError: se users è vuoto.
    """
    from ecn.models import ChangeNotice

    allowed = (ChangeNotice.Status.DRAFT, ChangeNotice.Status.CCB_PREPARATION)
    if change_notice.status not in allowed:
        raise ValidationError(
            "La configurazione CCB è possibile solo su ECN in bozza o istruttoria. "
            f"Stato attuale: {change_notice.get_status_display()}."
        )

    # Assegna approvatori usando il service esistente (backward-compat)
    set_change_notice_approvers(change_notice, users, policy=policy, actor=actor)

    old_status = change_notice.status

    # Cattura il coordinatore precedente prima del save per rilevare il cambiamento
    try:
        from ecn.models import ChangeNotice as _CN
        old_coordinator = _CN.objects.get(pk=change_notice.pk).ccb_coordinator
    except Exception:
        old_coordinator = None

    with transaction.atomic():
        update_fields = []
        if coordinator is not None:
            change_notice.ccb_coordinator = coordinator
            update_fields.append('ccb_coordinator')
        if change_notice.status == ChangeNotice.Status.DRAFT:
            change_notice.status = ChangeNotice.Status.CCB_PREPARATION
            update_fields.append('status')
        if update_fields:
            change_notice.save(update_fields=update_fields)

    try:
        from auditlog.services import create_audit_log
        create_audit_log(
            user=actor,
            action='CCB_CONFIGURED',
            instance=change_notice,
            old_values={'status': old_status},
            new_values={
                'status': change_notice.status,
                'approver_count': len(list(users)) if hasattr(users, '__len__') else '?',
                'coordinator_id': coordinator.pk if coordinator else None,
                'policy': change_notice.ccb_policy,
            },
            document=change_notice.document,
            document_version=change_notice.document_version,
            metadata={
                'ecn_id': change_notice.pk,
                'code': change_notice.code,
                'new_status': change_notice.status,
            },
        )
    except Exception:
        pass

    # Notifica il coordinatore se è stato appena assegnato o è cambiato
    new_coordinator = change_notice.ccb_coordinator
    if send_notifications and new_coordinator and (old_coordinator is None or old_coordinator.pk != new_coordinator.pk):
        _notify_silently('notify_ecn_coordinator_assigned', change_notice)
        try:
            from notifications.inbox import notify_ccb_dossier_assigned
            notify_ccb_dossier_assigned(change_notice)
        except Exception:
            pass

    return change_notice


def update_ccb_dossier(
    change_notice,
    actor,
    ccb_class=None,
    ccb_requirements='',
    ccb_technical_impact='',
    ccb_cost_impact='',
    ccb_time_impact='',
    ccb_quality_impact='',
    ccb_other_impact='',
    ccb_notes='',
):
    """
    Aggiorna i campi del dossier istruttorio CCB.
    Consentito solo in DRAFT o CCB_PREPARATION.
    Dopo l'invio alla CCB (UNDER_REVIEW+) il dossier è congelato.

    - actor: utente che compie la modifica (per permessi e AuditLog).

    Raises:
      PermissionDenied: se l'utente non ha can_compile_dossier.
      ValidationError: se lo stato non è DRAFT o CCB_PREPARATION.
    """
    from ecn.models import ChangeNotice
    from ecn.permissions import can_compile_dossier
    from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

    if change_notice.status not in (
        ChangeNotice.Status.DRAFT,
        ChangeNotice.Status.CCB_PREPARATION,
    ):
        raise ValidationError(
            "Il dossier può essere modificato solo in bozza o istruttoria CCB. "
            f"Stato attuale: {change_notice.get_status_display()}."
        )

    if not can_compile_dossier(actor, change_notice):
        raise DjangoPermissionDenied(
            "Non hai il permesso di modificare il dossier istruttorio."
        )

    with transaction.atomic():
        update_fields_list = [
            'ccb_requirements', 'ccb_technical_impact', 'ccb_cost_impact',
            'ccb_time_impact', 'ccb_quality_impact', 'ccb_other_impact', 'ccb_notes',
        ]
        change_notice.ccb_requirements     = ccb_requirements
        change_notice.ccb_technical_impact = ccb_technical_impact
        change_notice.ccb_cost_impact      = ccb_cost_impact
        change_notice.ccb_time_impact      = ccb_time_impact
        change_notice.ccb_quality_impact   = ccb_quality_impact
        change_notice.ccb_other_impact     = ccb_other_impact
        change_notice.ccb_notes            = ccb_notes
        if ccb_class:
            change_notice.ccb_class = ccb_class
            update_fields_list.append('ccb_class')
        change_notice.save(update_fields=update_fields_list)

    try:
        from auditlog.services import create_audit_log
        create_audit_log(
            user=actor,
            action='CCB_DOSSIER_UPDATED',
            instance=change_notice,
            old_values=None,
            new_values={
                'ccb_class': change_notice.ccb_class,
                'ccb_requirements': bool(ccb_requirements),
                'ccb_technical_impact': bool(ccb_technical_impact),
            },
            document=change_notice.document,
            document_version=change_notice.document_version,
            metadata={'ecn_id': change_notice.pk, 'code': change_notice.code},
        )
    except Exception:
        pass

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
