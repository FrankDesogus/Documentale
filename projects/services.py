from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Project, ProjectRevision, ProjectRevisionItem


def get_project_document_folders(project):
    """Restituisce la cartella del progetto e tutte le sottocartelle (BFS, qualsiasi stato)."""
    if project.folder is None:
        return []
    result = [project.folder]
    queue = list(project.folder.subfolders.all())
    while queue:
        folder = queue.pop(0)
        result.append(folder)
        queue.extend(folder.subfolders.all())
    return result


def create_project_revision(
    project: Project,
    created_by: User,
    revision_label: str,
    revision_number: int,
    title: str = '',
    description: str = '',
) -> ProjectRevision:
    if ProjectRevision.objects.filter(project=project, revision_label=revision_label).exists():
        raise ValidationError(
            f'Esiste già una baseline con etichetta "{revision_label}" '
            f'per il progetto {project.code}.'
        )
    if ProjectRevision.objects.filter(project=project, revision_number=revision_number).exists():
        raise ValidationError(
            f'Esiste già una baseline con numero progressivo {revision_number} '
            f'per il progetto {project.code}.'
        )
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
        raise ValueError('Solo le revisioni in bozza possono essere popolate automaticamente.')

    folders = get_project_document_folders(project_revision.project)
    if not folders:
        return 0

    documents = (
        Document.objects
        .filter(project_folder__in=folders, current_version__isnull=False)
        .select_related('current_version')
        .order_by('code')
    )

    existing_version_ids = set(
        project_revision.items.values_list('document_version_id', flat=True)
    )

    added = 0
    item_number = project_revision.items.count()

    for doc in documents:
        version = doc.current_version
        if version is None:
            continue
        if version.pk in existing_version_ids:
            continue
        item_number += 1
        ProjectRevisionItem.objects.create(
            revision=project_revision,
            item_number=item_number,
            document_version=version,
            description=doc.title,
        )
        existing_version_ids.add(version.pk)
        added += 1

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


def build_project_baseline_comparison(project):
    """
    Confronta i documenti approvati correnti del progetto con la baseline corrente.

    Restituisce (current_baseline, rows) dove rows è una lista di dict:
      {
        'document':        Document (o None per i "missing"),
        'current_version': DocumentVersion | None,
        'baseline_version': DocumentVersion | None,
        'status':          'aligned' | 'changed' | 'new' | 'missing',
        'status_label':    str,
      }

    Se non esiste una baseline corrente restituisce (None, []).
    """
    from documents.models import Document

    current_baseline = project.revisions.filter(is_current=True).first()
    if current_baseline is None:
        return None, []

    # Mappa document_id → DocumentVersion dalla baseline corrente
    baseline_map = {
        item.document_version.document_id: item.document_version
        for item in current_baseline.items.select_related(
            'document_version', 'document_version__document'
        )
    }

    folders = get_project_document_folders(project)
    if folders:
        current_docs = (
            Document.objects
            .filter(project_folder__in=folders, current_version__isnull=False)
            .select_related('current_version')
            .order_by('code')
        )
    else:
        current_docs = []

    rows = []
    seen_doc_ids = set()

    for doc in current_docs:
        current_version = doc.current_version
        seen_doc_ids.add(doc.pk)
        baseline_version = baseline_map.get(doc.pk)

        if baseline_version is None:
            status, label = 'new', 'Nuovo non in baseline'
        elif baseline_version.pk == current_version.pk:
            status, label = 'aligned', 'Allineato'
        else:
            status, label = 'changed', 'Modificato dopo baseline'

        rows.append({
            'document': doc,
            'current_version': current_version,
            'baseline_version': baseline_version,
            'status': status,
            'status_label': label,
        })

    # Documenti presenti in baseline ma non più tra i correnti del progetto
    for doc_id, baseline_version in baseline_map.items():
        if doc_id not in seen_doc_ids:
            rows.append({
                'document': baseline_version.document,
                'current_version': None,
                'baseline_version': baseline_version,
                'status': 'missing',
                'status_label': 'Non più presente / da verificare',
            })

    return current_baseline, rows


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
