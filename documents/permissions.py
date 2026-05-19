from documents.models import Document, DocumentVersion


def can_download_version_file(user, version):
    if not user.is_authenticated:
        return False
    if not version.file_id:
        return False
    if user.is_staff or user.is_superuser:
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
