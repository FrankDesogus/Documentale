from documents.models import Document, DocumentVersion

GROUP_READERS = 'Document Readers'
GROUP_AUTHORS = 'Document Authors'
GROUP_APPROVERS = 'Document Approvers'
GROUP_AUDITORS = 'Document Auditors'
GROUP_MANAGERS = 'Document Managers'


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
    """Qualsiasi utente autenticato può vedere documenti attivi con versione approvata.
    Se il documento è in una cartella, deve avere membership in quella cartella."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    if _in_group(user, GROUP_AUDITORS, GROUP_MANAGERS):
        return True
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
    """Determina se l'utente può vedere una specifica revisione."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    if _in_group(user, GROUP_AUDITORS, GROUP_MANAGERS):
        return True
    if (
        version.status == DocumentVersion.Status.APPROVED
        and version.is_current
        and version.document.status == Document.Status.ACTIVE
    ):
        if version.document.project_folder_id:
            return _has_folder_membership(user, version.document.project_folder_id)
        return True
    if version.created_by_id == user.pk and version.status in (
        DocumentVersion.Status.DRAFT,
        DocumentVersion.Status.REJECTED,
    ):
        return True
    if version.status == DocumentVersion.Status.IN_APPROVAL:
        from approvals.models import ApprovalRequest
        if ApprovalRequest.objects.filter(
            document_version=version,
            status=ApprovalRequest.Status.PENDING,
            approvers__approver=user,
        ).exists():
            return True
    return False


def can_create_document(user):
    """Può creare documenti se ha ruolo globale author/manager O membership author/manager
    in almeno una cartella."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    if _in_group(user, GROUP_AUTHORS, GROUP_MANAGERS):
        return True
    from projects.permissions import user_has_any_folder_write_access
    return user_has_any_folder_write_access(user)


def can_create_revision(user, document):
    if document.status != Document.Status.ACTIVE:
        return False
    if user.is_superuser or user.is_staff:
        return True
    if _in_group(user, GROUP_MANAGERS):
        return True
    if document.project_folder_id:
        return _has_folder_write(user, document.project_folder_id)
    # Nessuna cartella: usa i gruppi globali
    return _in_group(user, GROUP_AUTHORS)


def can_submit_for_approval(user, version):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    if _in_group(user, GROUP_MANAGERS):
        return True
    doc = version.document
    if doc.project_folder_id:
        if _has_folder_write(user, doc.project_folder_id):
            return True
        return False
    # Nessuna cartella: autori possono inviare le proprie revisioni
    if _in_group(user, GROUP_AUTHORS) and version.created_by_id == user.pk:
        return True
    return False


def can_edit_version(user, version):
    if not user.is_authenticated:
        return False
    if version.status not in (DocumentVersion.Status.DRAFT, DocumentVersion.Status.REJECTED):
        return False
    if user.is_superuser:
        return True
    if _in_group(user, GROUP_MANAGERS):
        return True
    if version.created_by_id == user.pk:
        return True
    return False


def can_download_version_file(user, version):
    if not user.is_authenticated:
        return False
    if not version.file_id:
        return False
    if user.is_superuser or user.is_staff:
        return True
    if _in_group(user, GROUP_AUDITORS, GROUP_MANAGERS):
        return True
    # Versione corrente approvata di un documento attivo
    if (
        version.status == DocumentVersion.Status.APPROVED
        and version.is_current
        and version.document.status == Document.Status.ACTIVE
    ):
        if version.document.project_folder_id:
            return _has_folder_membership(user, version.document.project_folder_id)
        return True
    # Autore: propria bozza o rifiutata (nessuna restrizione cartella)
    if version.created_by_id == user.pk and version.status in (
        DocumentVersion.Status.DRAFT,
        DocumentVersion.Status.REJECTED,
    ):
        return True
    # Approvatore assegnato: versione in_approval con richiesta pendente
    if version.status == DocumentVersion.Status.IN_APPROVAL:
        from approvals.models import ApprovalRequest
        if ApprovalRequest.objects.filter(
            document_version=version,
            status=ApprovalRequest.Status.PENDING,
            approvers__approver=user,
        ).exists():
            return True
    return False
