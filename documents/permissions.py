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


def can_view_document(user, document):
    """Qualsiasi utente autenticato può vedere documenti attivi con versione approvata.
    Staff, auditor e manager possono vedere tutto."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    if _in_group(user, GROUP_AUDITORS, GROUP_MANAGERS):
        return True
    return (
        document.status == Document.Status.ACTIVE
        and document.current_version is not None
        and document.current_version.status == DocumentVersion.Status.APPROVED
    )


def can_view_version(user, version):
    """Determina se l'utente può vedere una specifica revisione."""
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    if _in_group(user, GROUP_AUDITORS, GROUP_MANAGERS):
        return True
    # Versione corrente approvata: qualsiasi utente autenticato
    if (
        version.status == DocumentVersion.Status.APPROVED
        and version.is_current
        and version.document.status == Document.Status.ACTIVE
    ):
        return True
    # Propria bozza o rifiutata
    if version.created_by_id == user.pk and version.status in (
        DocumentVersion.Status.DRAFT,
        DocumentVersion.Status.REJECTED,
    ):
        return True
    # Approvatore assegnato con richiesta pendente
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
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    return _in_group(user, GROUP_AUTHORS, GROUP_MANAGERS)


def can_create_revision(user, document):
    if not can_create_document(user):
        return False
    return document.status == Document.Status.ACTIVE


def can_submit_for_approval(user, version):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    if _in_group(user, GROUP_MANAGERS):
        return True
    # Gli autori possono inviare solo le proprie revisioni
    if _in_group(user, GROUP_AUTHORS) and version.created_by_id == user.pk:
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
        return True
    # Autore: propria bozza o rifiutata
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
