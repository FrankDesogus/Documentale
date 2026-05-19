from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from .models import Project, ProjectRevision, ProjectRevisionItem


def create_project_revision(
    project: Project,
    created_by: User,
    revision_label: str,
    revision_number: int,
    title: str = '',
    description: str = '',
) -> ProjectRevision:
    revision = ProjectRevision.objects.create(
        project=project,
        revision_label=revision_label,
        revision_number=revision_number,
        title=title or f'Baseline {revision_label}',
        description=description,
        status=ProjectRevision.Status.DRAFT,
        is_current=False,
        created_by=created_by,
    )
    _write_audit(
        actor=created_by,
        action='create_project_revision',
        project=project,
        revision=revision,
    )
    return revision


def populate_project_revision_from_current_documents(project_revision: ProjectRevision) -> int:
    from documents.models import Document

    if project_revision.status != ProjectRevision.Status.DRAFT:
        raise ValueError('Solo le baseline in bozza possono essere popolate automaticamente.')

    folder = project_revision.project.folder
    if folder is None:
        return 0

    documents = Document.objects.filter(
        project_folder=folder,
    ).select_related('current_version').order_by('code')

    added = 0
    existing_version_ids = set(
        project_revision.items.values_list('document_version_id', flat=True)
    )

    for doc in documents:
        version = doc.current_version
        if version is None:
            continue
        if version.pk in existing_version_ids:
            continue
        item_number = project_revision.items.count() + 1
        ProjectRevisionItem.objects.create(
            revision=project_revision,
            item_number=item_number,
            document_version=version,
            description=doc.title,
        )
        existing_version_ids.add(version.pk)
        added += 1

    _write_audit(
        actor=project_revision.created_by,
        action='populate_project_revision',
        project=project_revision.project,
        revision=project_revision,
        extra={'items_added': added},
    )
    return added


@transaction.atomic
def issue_project_revision(project_revision: ProjectRevision, issued_by: User) -> ProjectRevision:
    if project_revision.status != ProjectRevision.Status.DRAFT:
        raise ValueError('Solo le baseline in bozza possono essere emesse.')

    # Marca come superata la precedente baseline corrente
    ProjectRevision.objects.filter(
        project=project_revision.project,
        is_current=True,
    ).update(status=ProjectRevision.Status.SUPERSEDED, is_current=False)

    project_revision.status = ProjectRevision.Status.ISSUED
    project_revision.is_current = True
    project_revision.issued_at = timezone.now()
    project_revision.issued_by = issued_by
    project_revision.save(update_fields=['status', 'is_current', 'issued_at', 'issued_by'])

    _write_audit(
        actor=issued_by,
        action='issue_project_revision',
        project=project_revision.project,
        revision=project_revision,
    )
    return project_revision


def _write_audit(actor, action, project, revision, extra=None):
    try:
        from auditlog.models import AuditLog
        changes = {
            'project_id': project.pk,
            'project_code': project.code,
            'revision_id': revision.pk,
            'revision_label': revision.revision_label,
        }
        if extra:
            changes.update(extra)
        AuditLog.objects.create(
            actor=actor,
            action=action,
            changes=changes,
        )
    except Exception:
        pass
