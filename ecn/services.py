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
    storico documento già esistente via changes__document_id=doc.pk).
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


def submit_change_notice(change_notice, user):
    """
    Invia l'ECN alla CCB: DRAFT → UNDER_REVIEW.

    Raises:
      PermissionDenied: se l'utente non ha il permesso.
      ValidationError: se lo stato non è DRAFT.
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
):
    """
    La CCB approva l'ECN: UNDER_REVIEW → APPROVED.

    ccb_class è obbligatorio (Classe 1 / Classe 2).
    Tutti gli altri campi CCB sono opzionali ma vengono salvati.

    Raises:
      PermissionDenied: se l'utente non è CCB/manager/staff.
      ValidationError: se lo stato non è UNDER_REVIEW o ccb_class manca.
    """
    from ecn.models import ChangeNotice
    from ecn.permissions import can_review_ecn

    if not can_review_ecn(user, change_notice):
        raise PermissionDenied("Non hai il permesso di approvare questo ECN.")

    if change_notice.status != ChangeNotice.Status.UNDER_REVIEW:
        raise ValidationError(
            f"Solo gli ECN in revisione CCB possono essere approvati. "
            f"Stato attuale: {change_notice.get_status_display()}."
        )

    if not ccb_class:
        raise ValidationError(
            "La classe variante (Classe 1 / Classe 2) è obbligatoria per approvare l'ECN."
        )

    old_status = change_notice.status
    now = timezone.now()

    change_notice.status = ChangeNotice.Status.APPROVED
    change_notice.ccb_class = ccb_class
    change_notice.ccb_requirements = ccb_requirements
    change_notice.ccb_technical_impact = ccb_technical_impact
    change_notice.ccb_cost_impact = ccb_cost_impact
    change_notice.ccb_time_impact = ccb_time_impact
    change_notice.ccb_quality_impact = ccb_quality_impact
    change_notice.ccb_other_impact = ccb_other_impact
    change_notice.ccb_notes = ccb_notes
    change_notice.ccb_reviewed_by = user
    change_notice.ccb_reviewed_at = now
    change_notice.save(update_fields=[
        'status', 'ccb_class',
        'ccb_requirements', 'ccb_technical_impact', 'ccb_cost_impact',
        'ccb_time_impact', 'ccb_quality_impact', 'ccb_other_impact',
        'ccb_notes', 'ccb_reviewed_by', 'ccb_reviewed_at',
    ])

    _write_audit(
        actor=user,
        action='ECN_APPROVED',
        ecn=change_notice,
        old_status=old_status,
        new_status=change_notice.status,
    )

    return change_notice


def reject_change_notice(change_notice, user, reason):
    """
    La CCB rifiuta l'ECN: UNDER_REVIEW → REJECTED.

    reason è obbligatorio e viene salvato in ccb_notes.

    Raises:
      PermissionDenied: se l'utente non è CCB/manager/staff.
      ValidationError: se lo stato non è UNDER_REVIEW o reason è vuoto.
    """
    from ecn.models import ChangeNotice
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

    old_status = change_notice.status
    now = timezone.now()

    change_notice.status = ChangeNotice.Status.REJECTED
    change_notice.ccb_notes = reason.strip()
    change_notice.ccb_reviewed_by = user
    change_notice.ccb_reviewed_at = now
    change_notice.save(update_fields=['status', 'ccb_notes', 'ccb_reviewed_by', 'ccb_reviewed_at'])

    _write_audit(
        actor=user,
        action='ECN_REJECTED',
        ecn=change_notice,
        old_status=old_status,
        new_status=change_notice.status,
    )

    return change_notice


def close_change_notice(change_notice, user, close_notes=''):
    """
    Il Responsabile Qualità chiude l'ECN: APPROVED → CLOSED.

    Non richiede ancora executed_version: il collegamento alla revisione
    eseguita sarà gestito nello step ECN-C (gate revisione).

    Raises:
      PermissionDenied: se l'utente non è Document Manager/staff.
      ValidationError: se lo stato non è APPROVED.
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

    old_status = change_notice.status
    now = timezone.now()

    change_notice.status = ChangeNotice.Status.CLOSED
    change_notice.close_notes = close_notes
    change_notice.closed_by = user
    change_notice.closed_at = now
    change_notice.save(update_fields=['status', 'close_notes', 'closed_by', 'closed_at'])

    _write_audit(
        actor=user,
        action='ECN_CLOSED',
        ecn=change_notice,
        old_status=old_status,
        new_status=change_notice.status,
    )

    return change_notice


# ---------------------------------------------------------------------------
# Helpers interni
# ---------------------------------------------------------------------------

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
