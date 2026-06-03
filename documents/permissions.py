"""
Permessi documentali.

Regole generali MB1:
  is_superuser  → bypass completo applicativo
  is_staff      → solo accesso Django Admin, nessun bypass applicativo

Bozze private:
  draft / rejected → visibile SOLO all'autore e al superuser.
  Nessun altro — inclusi Manager, Auditor, Quality Manager, staff — può vederle.
"""
from documents.models import Document, DocumentVersion

# ---------------------------------------------------------------------------
# Costanti gruppo globali
# ---------------------------------------------------------------------------

GROUP_READERS = 'Document Readers'
GROUP_AUTHORS = 'Document Authors'
GROUP_APPROVERS = 'Document Approvers'
GROUP_AUDITORS = 'Document Auditors'
GROUP_MANAGERS = 'Document Managers'

# Nuovi gruppi MB0
GROUP_QUALITY_MANAGER = 'Quality Manager'
GROUP_QUALITY_OPERATOR = 'Quality Operator'
GROUP_DIRECTION = 'Direction'
GROUP_ECN_PROPOSERS = 'ECN Proposers'


# ---------------------------------------------------------------------------
# Helper interni
# ---------------------------------------------------------------------------

def _in_group(user, *group_names):
    return user.groups.filter(name__in=group_names).exists()


def is_document_reader(user):
    return _in_group(user, GROUP_READERS)


def is_document_author(user):
    return _in_group(user, GROUP_AUTHORS)


def is_document_approver(user):
    return _in_group(user, GROUP_APPROVERS)


def is_document_auditor(user):
    return _in_group(user, GROUP_AUDITORS)


def is_document_manager(user):
    return _in_group(user, GROUP_MANAGERS)


def is_quality_manager(user):
    return _in_group(user, GROUP_QUALITY_MANAGER)


def is_quality_operator(user):
    return _in_group(user, GROUP_QUALITY_OPERATOR)


def is_direction(user):
    return _in_group(user, GROUP_DIRECTION)


def is_ecn_proposer(user):
    return _in_group(user, GROUP_ECN_PROPOSERS)


# ---------------------------------------------------------------------------
# Folder-aware helpers
# ---------------------------------------------------------------------------

def _has_folder_membership(user, folder_id):
    """True se l'utente ha qualsiasi membership nella cartella indicata."""
    from projects.models import ProjectFolderMembership
    return ProjectFolderMembership.objects.filter(
        user=user, folder_id=folder_id
    ).exists()


def _has_folder_write(user, folder_id):
    """True se l'utente ha ruolo author o manager nella cartella."""
    from projects.models import ProjectFolderMembership
    from projects.permissions import WRITE_ROLES
    return ProjectFolderMembership.objects.filter(
        user=user, folder_id=folder_id, role__in=WRITE_ROLES
    ).exists()


# ---------------------------------------------------------------------------
# Funzioni pubbliche
# ---------------------------------------------------------------------------

def can_view_document(user, document):
    """
    Determina se l'utente può accedere alla pagina di dettaglio di un documento.

    - superuser: sempre
    - Auditor / Manager / Quality Manager: documenti attivi con versione corrente approvata
      (nessuna restrizione di cartella, ma NON vedono documenti con solo bozze)
    - Altri utenti: documento attivo, versione corrente approvata, membership nella cartella
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    # Auditor, Manager, Quality Manager: vedono tutti i documenti ATTIVI
    # (la pagina di dettaglio è accessibile; le bozze altrui sono comunque
    # invisibili tramite can_view_version che filtra le versioni nel dettaglio)
    if _in_group(user, GROUP_AUDITORS, GROUP_MANAGERS, GROUP_QUALITY_MANAGER):
        return document.status == Document.Status.ACTIVE
    # Utenti normali: documento attivo, versione corrente approvata, accesso alla cartella
    if not (
        document.status == Document.Status.ACTIVE
        and document.current_version is not None
        and document.current_version.status == DocumentVersion.Status.APPROVED
    ):
        return False
    if document.project_folder_id:
        return _has_folder_membership(user, document.project_folder_id)
    return True


def can_view_version(user, version):
    """
    Determina se l'utente può vedere una specifica revisione.

    Regola bozze private (MB1):
      DRAFT / REJECTED → solo autore e superuser. Nessun altro.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    # ── DRAFT / REJECTED: solo l'autore ──────────────────────────────────────
    if version.status in (DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED):
        return version.created_by_id == user.pk

    # ── IN_APPROVAL: autore + approvatori assegnati ───────────────────────────
    if version.status == DocumentVersion.Status.IN_APPROVAL:
        if version.created_by_id == user.pk:
            return True
        from approvals.models import ApprovalRequest
        return ApprovalRequest.objects.filter(
            document_version=version,
            status=ApprovalRequest.Status.PENDING,
            approvers__approver=user,
        ).exists()

    # ── APPROVED corrente: utenti con accesso alla cartella o ruolo globale ──
    if version.status == DocumentVersion.Status.APPROVED and version.is_current:
        if _in_group(user, GROUP_AUDITORS, GROUP_MANAGERS, GROUP_QUALITY_MANAGER):
            return True
        if version.document.status != Document.Status.ACTIVE:
            return False
        if version.document.project_folder_id:
            return _has_folder_membership(user, version.document.project_folder_id)
        return True

    # ── Versioni storiche (SUPERSEDED): solo utenti con diritto di audit ─────
    if version.status == DocumentVersion.Status.SUPERSEDED:
        if _in_group(user, GROUP_AUDITORS, GROUP_MANAGERS, GROUP_QUALITY_MANAGER):
            return True
        if version.document.project_folder_id:
            from projects.permissions import get_folder_role, AUDIT_ROLES
            return get_folder_role(user, version.document.project_folder_id) in AUDIT_ROLES
        return False

    return False


def can_create_document(user):
    """
    Può creare documenti se ha ruolo globale author/manager O membership
    author/manager in almeno una cartella.
    is_staff NON concede questo permesso.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if _in_group(user, GROUP_AUTHORS, GROUP_MANAGERS):
        return True
    from projects.permissions import user_has_any_folder_write_access
    return user_has_any_folder_write_access(user)


def can_create_revision(user, document):
    """
    is_staff NON concede questo permesso.
    """
    if document.status != Document.Status.ACTIVE:
        return False
    if user.is_superuser:
        return True
    if _in_group(user, GROUP_MANAGERS):
        return True
    if document.project_folder_id:
        return _has_folder_write(user, document.project_folder_id)
    return _in_group(user, GROUP_AUTHORS)


def can_submit_for_approval(user, version):
    """
    is_staff NON concede questo permesso.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if _in_group(user, GROUP_MANAGERS):
        return True
    doc = version.document
    if doc.project_folder_id:
        if _has_folder_write(user, doc.project_folder_id):
            return True
        return False
    if _in_group(user, GROUP_AUTHORS) and version.created_by_id == user.pk:
        return True
    return False


def can_edit_version(user, version):
    """
    Solo l'autore e il superuser possono modificare una propria bozza.
    MB1: rimosso bypass Manager e is_staff.
    """
    if not user.is_authenticated:
        return False
    if version.status not in (DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED):
        return False
    if user.is_superuser:
        return True
    # Solo l'autore della bozza
    return version.created_by_id == user.pk


def can_view_audit(user, folder=None):
    """
    True se l'utente può vedere lo storico audit.
    is_staff NON concede questo permesso.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if _in_group(user, GROUP_MANAGERS, GROUP_AUDITORS, GROUP_QUALITY_MANAGER):
        return True
    if folder is not None:
        from projects.permissions import get_folder_role, AUDIT_ROLES
        return get_folder_role(user, folder) in AUDIT_ROLES
    return False


def can_download_version_file(user, version):
    """
    is_staff NON concede questo permesso.
    Bozze/rifiutate: solo l'autore e il superuser.
    Versione corrente approvata: utenti con accesso alla cartella + Auditor/Manager.
    """
    if not user.is_authenticated:
        return False
    if not version.file_id:
        return False
    if user.is_superuser:
        return True

    # DRAFT / REJECTED: solo l'autore
    if version.status in (DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED):
        return version.created_by_id == user.pk

    # IN_APPROVAL: autore + approvatori assegnati
    if version.status == DocumentVersion.Status.IN_APPROVAL:
        if version.created_by_id == user.pk:
            return True
        from approvals.models import ApprovalRequest
        return ApprovalRequest.objects.filter(
            document_version=version,
            status=ApprovalRequest.Status.PENDING,
            approvers__approver=user,
        ).exists()

    # Versione corrente approvata
    if (
        version.status == DocumentVersion.Status.APPROVED
        and version.is_current
        and version.document.status == Document.Status.ACTIVE
    ):
        if _in_group(user, GROUP_AUDITORS, GROUP_MANAGERS, GROUP_QUALITY_MANAGER):
            return True
        if version.document.project_folder_id:
            return _has_folder_membership(user, version.document.project_folder_id)
        return True

    # Versioni storiche (SUPERSEDED): solo chi può fare audit
    if version.status == DocumentVersion.Status.SUPERSEDED:
        if _in_group(user, GROUP_AUDITORS, GROUP_MANAGERS, GROUP_QUALITY_MANAGER):
            return True
        if version.document.project_folder_id:
            from projects.permissions import get_folder_role, AUDIT_ROLES
            return get_folder_role(user, version.document.project_folder_id) in AUDIT_ROLES
        return False

    return False
