"""Permessi per-cartella basati su ProjectFolderMembership."""

ROLE_READER = 'reader'
ROLE_AUTHOR = 'author'
ROLE_APPROVER = 'approver'
ROLE_AUDITOR = 'auditor'
ROLE_MANAGER = 'manager'

READ_ROLES = frozenset([ROLE_READER, ROLE_AUTHOR, ROLE_APPROVER, ROLE_AUDITOR, ROLE_MANAGER])
WRITE_ROLES = frozenset([ROLE_AUTHOR, ROLE_MANAGER])
APPROVE_ROLES = frozenset([ROLE_APPROVER, ROLE_MANAGER])
AUDIT_ROLES = frozenset([ROLE_AUDITOR, ROLE_MANAGER])


def get_folder_role(user, folder):
    """Restituisce il ruolo dell'utente nella cartella, o None se assente."""
    if not user.is_authenticated:
        return None
    from projects.models import ProjectFolderMembership
    try:
        return ProjectFolderMembership.objects.get(folder=folder, user=user).role
    except ProjectFolderMembership.DoesNotExist:
        return None


def _is_privileged(user):
    """True se superuser, staff o Document Manager/Auditor globale."""
    if user.is_superuser or user.is_staff:
        return True
    from documents.permissions import is_document_manager, is_document_auditor
    return is_document_manager(user) or is_document_auditor(user)


def _is_global_manager(user):
    if user.is_superuser or user.is_staff:
        return True
    from documents.permissions import is_document_manager
    return is_document_manager(user)


def has_folder_role(user, folder, roles):
    """True se l'utente ha uno dei ruoli indicati nella cartella (o è privilegiato)."""
    if not user.is_authenticated:
        return False
    if _is_global_manager(user):
        return True
    return get_folder_role(user, folder) in roles


def can_view_folder(user, folder):
    if not user.is_authenticated:
        return False
    if _is_privileged(user):
        return True
    return get_folder_role(user, folder) in READ_ROLES


def can_manage_folder(user, folder):
    if not user.is_authenticated:
        return False
    if _is_global_manager(user):
        return True
    return get_folder_role(user, folder) == ROLE_MANAGER


def can_create_document_in_folder(user, folder):
    """folder=None → nessuna cartella, usa solo i gruppi globali."""
    if not user.is_authenticated:
        return False
    if _is_global_manager(user):
        return True
    if folder is None:
        from documents.permissions import is_document_author
        return is_document_author(user)
    return get_folder_role(user, folder) in WRITE_ROLES


def user_has_any_folder_write_access(user):
    """True se l'utente ha ruolo author o manager in almeno una cartella."""
    if not user.is_authenticated:
        return False
    from projects.models import ProjectFolderMembership
    return ProjectFolderMembership.objects.filter(
        user=user, role__in=WRITE_ROLES
    ).exists()


def get_writable_folder_ids(user):
    """Restituisce i pk delle cartelle in cui l'utente può creare documenti."""
    from projects.models import ProjectFolderMembership
    return list(ProjectFolderMembership.objects.filter(
        user=user, role__in=WRITE_ROLES
    ).values_list('folder_id', flat=True))


def get_visible_folder_ids(user):
    """Restituisce i pk delle cartelle visibili all'utente."""
    from projects.models import ProjectFolderMembership
    return list(ProjectFolderMembership.objects.filter(
        user=user
    ).values_list('folder_id', flat=True))
