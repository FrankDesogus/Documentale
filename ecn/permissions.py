"""
Permessi per l'app ECN / Varianti — MB1.

Regole generali:
  is_superuser       → bypass completo
  is_staff           → NON bypass applicativo (solo Django Admin)

Governance ECN (configura CCB, invia, chiude):
  solo Quality Manager oppure superuser.

Visibilità ECN:
  Quality Manager    → tutte le ECN (governance completa)
  Quality Operator   → tutte le ECN (sola consultazione)
  Direction          → tutte le ECN (sola consultazione)
  proposed_by / created_by → proprie ECN
  ChangeNoticeApprover assegnato → ECN specifiche assegnate
  ruolo cartella auditor/manager → ECN su documenti nella cartella
  Change Control Board (gruppo) → pool candidati, NESSUNA visibilità automatica
  Document Manager / Document Auditor → NESSUNA visibilità automatica sulle ECN
"""

GROUP_CCB = 'Change Control Board'


def _in_group(user, *group_names):
    return user.groups.filter(name__in=group_names).exists()


def _is_superuser_or_staff(user):
    return user.is_superuser or user.is_staff


def _is_quality_manager(user):
    """Solo Quality Manager e superuser possono fare governance ECN."""
    if user.is_superuser:
        return True
    from documents.permissions import GROUP_QUALITY_MANAGER
    return _in_group(user, GROUP_QUALITY_MANAGER)


def _can_consult_all_ecn(user):
    """
    Quality Manager, Quality Operator, Direction, superuser possono vedere
    tutte le ECN. Nessun bypass per is_staff o Document Manager/Auditor.
    """
    if user.is_superuser:
        return True
    from documents.permissions import (
        GROUP_QUALITY_MANAGER,
        GROUP_QUALITY_OPERATOR,
        GROUP_DIRECTION,
    )
    return _in_group(user, GROUP_QUALITY_MANAGER, GROUP_QUALITY_OPERATOR, GROUP_DIRECTION)


# ---------------------------------------------------------------------------
# Permessi pubblici
# ---------------------------------------------------------------------------

def can_view_ecn(user, change_notice):
    """
    Può vedere l'ECN:
      - superuser
      - Quality Manager / Quality Operator / Direction (visibilità globale)
      - proposed_by / created_by dell'ECN
      - ChangeNoticeApprover assegnato a questa specifica ECN
      - Utenti con ruolo auditor/manager sulla cartella del documento dell'ECN

    NON ricevono visibilità automatica:
      - is_staff (senza essere superuser)
      - gruppo Change Control Board
      - Document Manager
      - Document Auditor
    """
    if not user.is_authenticated:
        return False
    if _can_consult_all_ecn(user):
        return True
    # Proponente / creatore
    if change_notice.proposed_by_id == user.pk or change_notice.created_by_id == user.pk:
        return True
    # Approvatore assegnato a questa specifica ECN
    from ecn.models import ChangeNoticeApprover
    if ChangeNoticeApprover.objects.filter(change_notice=change_notice, user=user).exists():
        return True
    # Ruolo per-cartella (auditor o manager)
    if change_notice.document and change_notice.document.project_folder_id:
        from projects.permissions import get_folder_role, AUDIT_ROLES
        if get_folder_role(user, change_notice.document.project_folder) in AUDIT_ROLES:
            return True
    return False


def can_create_ecn(user, document):
    """
    Può proporre un ECN su un documento:
      - superuser
      - Quality Manager
      - ECN Proposers (gruppo globale)
      - Utenti con ruolo author/manager sulla cartella del documento
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from documents.permissions import GROUP_QUALITY_MANAGER, GROUP_ECN_PROPOSERS, GROUP_AUTHORS, GROUP_MANAGERS
    if _in_group(user, GROUP_QUALITY_MANAGER, GROUP_ECN_PROPOSERS):
        return True
    # Compatibilità legacy: Document Managers e Authors possono ancora creare ECN
    # (da rivedere in MB5 con il grant request_ecn per-cartella)
    if _in_group(user, GROUP_AUTHORS, GROUP_MANAGERS):
        return True
    # Ruolo per-cartella
    folder = document.project_folder
    if folder is not None:
        from projects.permissions import get_folder_role, WRITE_ROLES
        if get_folder_role(user, folder) in WRITE_ROLES:
            return True
    return False


def can_configure_ccb(user, change_notice):
    """
    Può configurare la CCB: solo Quality Manager e superuser.
    MB1: rimosso bypass Document Manager e is_staff.
    """
    if not user.is_authenticated:
        return False
    return _is_quality_manager(user)


def can_submit_ecn(user, change_notice):
    """
    Può inviare l'ECN alla CCB (draft → under_review):
    solo Quality Manager e superuser.
    """
    if not user.is_authenticated:
        return False
    return _is_quality_manager(user)


def can_review_ecn(user, change_notice):
    """
    Può approvare o rifiutare l'ECN:
      - superuser (bypass)
      - ChangeNoticeApprover assegnato a questa specifica ECN
      - Per policy SEQUENTIAL: solo il prossimo nella catena
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    from ecn.models import ChangeNoticeApprover, ChangeNoticeDecision

    approver_qs = ChangeNoticeApprover.objects.filter(
        change_notice=change_notice,
        user=user,
    )
    if not approver_qs.exists():
        return False

    # Policy SEQUENTIAL
    if change_notice.ccb_policy == 'sequential':
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
        if next_app is None or next_app.user_id != user.pk:
            return False

    return True


def can_close_ecn(user, change_notice):
    """
    Può chiudere l'ECN: solo Quality Manager e superuser.
    """
    if not user.is_authenticated:
        return False
    return _is_quality_manager(user)


def can_compile_dossier(user, change_notice):
    """
    Può compilare/aggiornare il dossier istruttorio CCB:
      - superuser
      - Quality Manager
      - ccb_coordinator (responsabile istruttoria designato)

    Consentito solo in stato DRAFT o CCB_PREPARATION.
    Dopo l'invio alla CCB (UNDER_REVIEW+) il dossier è congelato.
    """
    if not user.is_authenticated:
        return False
    from ecn.models import ChangeNotice
    if change_notice.status not in (
        ChangeNotice.Status.DRAFT,
        ChangeNotice.Status.CCB_PREPARATION,
    ):
        return False
    if user.is_superuser:
        return True
    if _is_quality_manager(user):
        return True
    if change_notice.ccb_coordinator_id and change_notice.ccb_coordinator_id == user.pk:
        return True
    return False


def can_edit_ecn(user, change_notice):
    """
    Può modificare i dati base (solo DRAFT):
      - superuser
      - Quality Manager
      - proposed_by / created_by dell'ECN
    MB1: rimosso bypass Document Manager e is_staff.
    """
    if not user.is_authenticated:
        return False
    from ecn.models import ChangeNotice
    if change_notice.status != ChangeNotice.Status.DRAFT:
        return False
    if user.is_superuser:
        return True
    if _is_quality_manager(user):
        return True
    if change_notice.proposed_by_id == user.pk or change_notice.created_by_id == user.pk:
        return True
    return False


def can_reconfigure_ccb(user, change_notice):
    """
    Può modificare approvatori CCB e policy:
      - DRAFT, CCB_PREPARATION o UNDER_REVIEW senza decisioni
      - Solo Quality Manager e superuser
    """
    if not user.is_authenticated:
        return False
    if not _is_quality_manager(user):
        return False
    from ecn.models import ChangeNotice
    if change_notice.status in (
        ChangeNotice.Status.DRAFT,
        ChangeNotice.Status.CCB_PREPARATION,
    ):
        return True
    if change_notice.status == ChangeNotice.Status.UNDER_REVIEW:
        return not change_notice.decisions.exists()
    return False


def can_reopen_ccb(user, change_notice):
    """
    Può riaprire la configurazione CCB (under_review con decisioni → DRAFT):
      - Solo Quality Manager e superuser
      - Solo se UNDER_REVIEW con almeno una decisione
    """
    if not user.is_authenticated:
        return False
    if not _is_quality_manager(user):
        return False
    from ecn.models import ChangeNotice
    if change_notice.status != ChangeNotice.Status.UNDER_REVIEW:
        return False
    return change_notice.decisions.exists()


def can_add_ecn_attachment(user, change_notice):
    """
    Può aggiungere allegati a un ECN:
      - superuser
      - Quality Manager
      - proposed_by / created_by
    NON: Quality Operator, Direction (sola lettura), CCB generico.
    """
    if not user.is_authenticated:
        return False
    from ecn.models import ChangeNotice
    if change_notice.status in (ChangeNotice.Status.REJECTED, ChangeNotice.Status.CLOSED):
        return False
    if user.is_superuser:
        return True
    if _is_quality_manager(user):
        return True
    if change_notice.proposed_by_id == user.pk or change_notice.created_by_id == user.pk:
        return True
    return False


def can_download_ecn_attachment(user, attachment):
    """
    Può scaricare un allegato ECN:
      - superuser
      - Quality Manager
      - proposed_by / created_by dell'ECN
      - ChangeNoticeApprover assegnato a quella specifica ECN

    Nega automaticamente:
      - is_staff senza superuser
      - Document Manager / Document Auditor
      - Quality Operator / Direction
      - membro gruppo CCB globale non assegnato
      - utenti senza relazione con l'ECN
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if _is_quality_manager(user):
        return True
    ecn = attachment.change_notice
    if ecn.proposed_by_id == user.pk or ecn.created_by_id == user.pk:
        return True
    from ecn.models import ChangeNoticeApprover
    return ChangeNoticeApprover.objects.filter(change_notice=ecn, user=user).exists()
