"""Permessi per l'app ECN / Varianti.

Struttura:
  - Superuser/staff: possono tutto.
  - Change Control Board (gruppo): possono vedere e revisionare ECN.
  - Document Managers (gruppo globale): possono vedere, revisionare e chiudere.
  - Document Auditors (gruppo globale): possono vedere.
  - Document Authors (gruppo globale): possono creare ECN.
  - Proponente / created_by: può vedere e inviare il proprio ECN.
  - Per-cartella: ruoli author/manager possono creare; auditor/manager possono vedere.
"""

GROUP_CCB = 'Change Control Board'


def _in_group(user, *group_names):
    return user.groups.filter(name__in=group_names).exists()


def _is_superuser_or_staff(user):
    return user.is_superuser or user.is_staff


def _is_global_manager(user):
    if _is_superuser_or_staff(user):
        return True
    from documents.permissions import GROUP_MANAGERS
    return _in_group(user, GROUP_MANAGERS)


# ---------------------------------------------------------------------------
# Permessi pubblici
# ---------------------------------------------------------------------------

def can_view_ecn(user, change_notice):
    """
    Può vedere l'ECN:
      - superuser/staff
      - Document Managers globali
      - Document Auditors globali
      - Membri CCB
      - Proponente o created_by dell'ECN
      - Utenti con ruolo auditor/manager sulla cartella del documento
    """
    if not user.is_authenticated:
        return False
    if _is_superuser_or_staff(user):
        return True
    from documents.permissions import GROUP_MANAGERS, GROUP_AUDITORS
    if _in_group(user, GROUP_MANAGERS, GROUP_AUDITORS, GROUP_CCB):
        return True
    # Proponente / creatore
    if change_notice.proposed_by_id == user.pk or change_notice.created_by_id == user.pk:
        return True
    # Ruolo per-cartella
    folder = change_notice.document.project_folder
    if folder is not None:
        from projects.permissions import get_folder_role, AUDIT_ROLES
        if get_folder_role(user, folder) in AUDIT_ROLES:
            return True
    return False


def can_create_ecn(user, document):
    """
    Può proporre un ECN su un documento:
      - superuser/staff
      - Document Managers globali
      - Document Authors globali
      - Utenti con ruolo author/manager sulla cartella del documento
    Non verifica lo stato del documento: la validazione business
    è nel service (il documento deve avere una versione corrente approvata).
    """
    if not user.is_authenticated:
        return False
    if _is_superuser_or_staff(user):
        return True
    from documents.permissions import GROUP_MANAGERS, GROUP_AUTHORS
    if _in_group(user, GROUP_MANAGERS):
        return True
    if _in_group(user, GROUP_AUTHORS):
        return True
    # Ruolo per-cartella
    folder = document.project_folder
    if folder is not None:
        from projects.permissions import get_folder_role, WRITE_ROLES
        if get_folder_role(user, folder) in WRITE_ROLES:
            return True
    return False


def can_submit_ecn(user, change_notice):
    """
    Può inviare l'ECN alla CCB (draft → under_review):
      - superuser/staff
      - Document Managers globali
      - Proponente o created_by dell'ECN
    """
    if not user.is_authenticated:
        return False
    if _is_superuser_or_staff(user):
        return True
    if _is_global_manager(user):
        return True
    if change_notice.proposed_by_id == user.pk or change_notice.created_by_id == user.pk:
        return True
    return False


def can_review_ecn(user, change_notice):
    """
    Può approvare o rifiutare l'ECN (under_review → approved/rejected):
      - superuser/staff
      - Membri CCB
      - Document Managers globali
    """
    if not user.is_authenticated:
        return False
    if _is_superuser_or_staff(user):
        return True
    from documents.permissions import GROUP_MANAGERS
    if _in_group(user, GROUP_MANAGERS, GROUP_CCB):
        return True
    return False


def can_close_ecn(user, change_notice):
    """
    Può chiudere l'ECN (approved → closed) — Responsabile Qualità:
      - superuser/staff
      - Document Managers globali
    """
    if not user.is_authenticated:
        return False
    if _is_superuser_or_staff(user):
        return True
    from documents.permissions import GROUP_MANAGERS
    return _in_group(user, GROUP_MANAGERS)


def can_add_ecn_attachment(user, change_notice):
    """
    Può aggiungere allegati a un ECN:
      - chi può vedere l'ECN (can_view_ecn)
      - a patto che l'ECN non sia REJECTED né CLOSED (stati terminali/archiviati)
    """
    from ecn.models import ChangeNotice
    if not can_view_ecn(user, change_notice):
        return False
    return change_notice.status not in (ChangeNotice.Status.REJECTED, ChangeNotice.Status.CLOSED)
