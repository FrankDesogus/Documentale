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
    """True se superuser, Document Manager o Document Auditor globale.
    MB1: is_staff NON è più privilegiato applicativo."""
    if user.is_superuser:
        return True
    from documents.permissions import is_document_manager, is_document_auditor
    return is_document_manager(user) or is_document_auditor(user)


def _is_global_manager(user):
    """MB1: is_staff NON è più privilegiato applicativo."""
    if user.is_superuser:
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
    if user.is_superuser:
        return True
    # I privilegiati globali (Document Manager / Auditor) vedono tutte le cartelle
    if _is_privileged(user):
        return True
    # Resolver modulare con fallback legacy per gli utenti normali
    from projects.resolver import has_folder_permission
    return has_folder_permission(
        user, folder, 'read_published',
        include_legacy_fallback=True,
    )


def can_manage_folder(user, folder):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    # I gestori globali (Document Manager) gestiscono tutte le cartelle
    if _is_global_manager(user):
        return True
    from projects.resolver import has_folder_permission
    return has_folder_permission(
        user, folder, 'manage_folder',
        include_legacy_fallback=True,
    )


def can_create_document_in_folder(user, folder):
    """folder=None → nessuna cartella, usa solo i gruppi globali."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if _is_global_manager(user):
        return True
    if folder is None:
        from documents.permissions import is_document_author
        return is_document_author(user)
    from projects.resolver import has_folder_permission
    return has_folder_permission(
        user, folder, 'create_draft',
        include_legacy_fallback=True,
    )


def get_visible_folder_ids(user) -> list:
    """
    Restituisce i pk delle cartelle dove l'utente ha read_published.
    Usa il resolver modulare con fallback legacy.
    """
    if not getattr(user, 'is_authenticated', False):
        return []
    from projects.models import ProjectFolder
    if user.is_superuser:
        return list(ProjectFolder.objects.filter(
            status=ProjectFolder.Status.ACTIVE
        ).values_list('pk', flat=True))
    if _is_privileged(user):
        return list(ProjectFolder.objects.filter(
            status=ProjectFolder.Status.ACTIVE
        ).values_list('pk', flat=True))
    from projects.resolver import PermissionResolver
    folders = list(
        ProjectFolder.objects.filter(status=ProjectFolder.Status.ACTIVE).only('pk', 'path')
    )
    resolver = PermissionResolver(user, include_legacy_fallback=True)
    results = resolver.resolve_bulk(folders, 'read_published')
    return [pk for pk, allowed in results.items() if allowed]


def get_navigation_folder_ids(user) -> set:
    """
    Restituisce i pk delle cartelle antenate necessarie per navigare verso
    cartelle leggibili (read_published=True), ma che non sono esse stesse leggibili.

    Questi folder "navigation-only" appaiono nell'albero come contenitori,
    ma non concedono accesso a documenti, progetti o azioni di scrittura.
    """
    readable_ids = set(get_visible_folder_ids(user))
    if not readable_ids:
        return set()
    from projects.models import ProjectFolder
    readable_folders = ProjectFolder.objects.filter(pk__in=readable_ids).only('pk', 'path')
    nav_ids: set = set()
    for f in readable_folders:
        if not f.path:
            continue
        parts = [p for p in f.path.split('/') if p]
        ancestor_pks = [int(p) for p in parts[:-1]]
        for pk in ancestor_pks:
            if pk not in readable_ids:
                nav_ids.add(pk)
    return nav_ids


def get_writable_folder_ids(user) -> list:
    """
    Restituisce i pk delle cartelle dove l'utente può creare documenti (create_draft).
    Usa il resolver modulare con fallback legacy.
    """
    if not getattr(user, 'is_authenticated', False):
        return []
    from projects.models import ProjectFolder
    if user.is_superuser:
        return list(ProjectFolder.objects.filter(
            status=ProjectFolder.Status.ACTIVE
        ).values_list('pk', flat=True))
    if _is_global_manager(user):
        return list(ProjectFolder.objects.filter(
            status=ProjectFolder.Status.ACTIVE
        ).values_list('pk', flat=True))
    from projects.resolver import PermissionResolver
    folders = list(
        ProjectFolder.objects.filter(status=ProjectFolder.Status.ACTIVE).only('pk', 'path')
    )
    resolver = PermissionResolver(user, include_legacy_fallback=True)
    results = resolver.resolve_bulk(folders, 'create_draft')
    return [pk for pk, allowed in results.items() if allowed]


def user_has_any_folder_write_access(user) -> bool:
    """True se l'utente ha create_draft in almeno una cartella."""
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(get_writable_folder_ids(user))


def get_project_visible_folder_ids(user) -> list:
    """
    Restituisce i pk delle cartelle i cui progetti sono visibili all'utente.
    Usa view_projects con fallback legacy.

    Debito tecnico: nel sistema legacy tutti i ruoli (reader → manager) hanno
    implicitamente view_projects, perché la visibilità del progetto era identica
    alla visibilità della cartella. Con il resolver modulare è possibile separare
    i due permessi tramite grant espliciti. Finché il fallback legacy è attivo
    il comportamento è invariato per gli utenti con sole membership.
    """
    if not getattr(user, 'is_authenticated', False):
        return []
    from projects.models import ProjectFolder
    if user.is_superuser:
        return list(ProjectFolder.objects.values_list('pk', flat=True))
    if _is_global_manager(user):
        return list(ProjectFolder.objects.values_list('pk', flat=True))
    from projects.resolver import PermissionResolver
    folders = list(ProjectFolder.objects.only('pk', 'path'))
    resolver = PermissionResolver(user, include_legacy_fallback=True)
    results = resolver.resolve_bulk(folders, 'view_projects')
    return [pk for pk, allowed in results.items() if allowed]
