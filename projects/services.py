from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Project, ProjectFolder, ProjectRevision, ProjectRevisionItem


# ---------------------------------------------------------------------------
# Materialized path helpers per ProjectFolder
# ---------------------------------------------------------------------------

def build_folder_path(folder) -> str:
    """
    Calcola il materialized path di una cartella senza salvarlo.

    Richiede che il parent abbia già un path valorizzato oppure sia None.
    Formato: /pk/ per root, /parent_pk/child_pk/ per le sottocartelle.
    """
    if folder.parent_id is None:
        return f"/{folder.pk}/"
    parent_path = folder.parent.path if folder.parent else ""
    if not parent_path:
        # Fallback nel caso il parent non abbia ancora un path
        parent_path = f"/{folder.parent_id}/"
    return f"{parent_path}{folder.pk}/"


def set_folder_path(folder, save: bool = True) -> str:
    """
    Calcola e assegna il path materializzato a una cartella.
    Se save=True, persiste solo il campo path.
    Ritorna il path calcolato.
    """
    folder.path = build_folder_path(folder)
    if save:
        folder.save(update_fields=['path'])
    return folder.path


def get_folder_ancestors(folder):
    """
    Restituisce QuerySet delle cartelle antenate (esclusa la cartella stessa),
    ordinate dal root verso il parent diretto.
    """
    if not folder.path:
        return ProjectFolder.objects.none()
    parts = [p for p in folder.path.split('/') if p]
    ancestor_pks = [int(p) for p in parts[:-1]]  # escludi l'ultimo (sé stessa)
    if not ancestor_pks:
        return ProjectFolder.objects.none()
    # Preserva l'ordine root → leaf
    from django.db.models import Case, When, IntegerField
    ordering = Case(
        *[When(pk=pk, then=pos) for pos, pk in enumerate(ancestor_pks)],
        output_field=IntegerField(),
    )
    return ProjectFolder.objects.filter(pk__in=ancestor_pks).order_by(ordering)


def get_folder_descendants(folder):
    """
    Restituisce QuerySet di tutte le cartelle discendenti (esclusa la cartella stessa).
    Usa il prefisso del path per la query, quindi O(1) SQL.
    """
    if not folder.path:
        return ProjectFolder.objects.none()
    return ProjectFolder.objects.filter(path__startswith=folder.path).exclude(pk=folder.pk)


@transaction.atomic
def move_folder(folder, new_parent) -> None:
    """
    Sposta una cartella sotto un nuovo parent (o alla root se new_parent=None).

    Operazioni:
      1. Verifica che new_parent non sia sé stessa.
      2. Verifica che lo spostamento non crei un ciclo.
      3. Aggiorna parent e path della cartella.
      4. Aggiorna il path di tutti i discendenti con bulk_update.

    Tutto in transazione atomica.
    """
    if new_parent is not None and new_parent.pk == folder.pk:
        raise ValidationError("Una cartella non può essere il proprio genitore.")

    if new_parent is not None:
        # Ciclo: new_parent è già un discendente di folder?
        if folder.path and new_parent.path.startswith(folder.path):
            raise ValidationError(
                f"Spostamento non consentito: '{new_parent.code}' è già "
                f"un discendente di '{folder.code}'."
            )

    old_path = folder.path

    # Calcola nuovo path
    if new_parent is None:
        new_path = f"/{folder.pk}/"
    else:
        if not new_parent.path:
            new_parent.path = f"/{new_parent.pk}/"
            new_parent.save(update_fields=['path'])
        new_path = f"{new_parent.path}{folder.pk}/"

    # Aggiorna la cartella
    folder.parent = new_parent
    folder.path = new_path
    folder.save(update_fields=['parent', 'path'])

    # Aggiorna i discendenti (se old_path è valorizzato)
    if old_path:
        descendants = list(
            ProjectFolder.objects.filter(path__startswith=old_path).exclude(pk=folder.pk)
        )
        for desc in descendants:
            desc.path = new_path + desc.path[len(old_path):]
        if descendants:
            ProjectFolder.objects.bulk_update(descendants, ['path'])


def build_folder_path_for_existing() -> int:
    """
    Valorizza il campo path per tutte le cartelle esistenti che lo hanno vuoto.
    Usato dalla data migration. Lavora BFS dal root verso le foglie.
    Ritorna il numero di cartelle aggiornate.
    """
    updated = 0
    # Prima passata: root (parent=None)
    roots = ProjectFolder.objects.filter(parent__isnull=True, path='')
    for folder in roots:
        folder.path = f"/{folder.pk}/"
        folder.save(update_fields=['path'])
        updated += 1

    # Passate successive: livello per livello fino a max depth
    max_depth = 50
    for _ in range(max_depth):
        unset = ProjectFolder.objects.filter(path='').exclude(parent__isnull=True)
        if not unset.exists():
            break
        for folder in unset.select_related('parent'):
            if folder.parent and folder.parent.path:
                folder.path = f"{folder.parent.path}{folder.pk}/"
                folder.save(update_fields=['path'])
                updated += 1

    return updated


def get_project_document_folders(project):
    """Restituisce la root folder del progetto e tutte le sottocartelle (BFS, qualsiasi stato)."""
    root = project.root_folder
    if root is None:
        return []
    result = [root]
    queue = list(root.subfolders.all())
    while queue:
        folder = queue.pop(0)
        result.append(folder)
        queue.extend(folder.subfolders.all())
    return result


@transaction.atomic
def update_project_metadata(
    *,
    project,
    name: str,
    description: str = '',
    manager=None,
    updated_by=None,
) -> 'Project':
    """
    Aggiorna atomicamente i metadati modificabili di un progetto.

    Sincronizza anche il nome della root folder col nome del progetto.
    Codice progetto e root folder non vengono mai modificati.
    """
    from .models import Project, ProjectFolder

    name = name.strip()
    if not name:
        raise ValidationError("Il nome del progetto è obbligatorio.")

    project.name = name
    project.description = description
    project.manager = manager
    project.save(update_fields=['name', 'description', 'manager', 'updated_at'])

    if project.root_folder_id:
        ProjectFolder.objects.filter(pk=project.root_folder_id).update(name=name)

    try:
        from auditlog.models import AuditLog
        AuditLog.objects.create(
            user=updated_by,
            action='update_project_metadata',
            app_label='projects',
            model_name='project',
            object_id=str(project.pk),
            object_repr=str(project)[:255],
            changes={
                'project_id': project.pk,
                'project_code': project.code,
                'name': name,
            },
        )
    except Exception:
        pass

    return project


@transaction.atomic
def create_project_with_root_folder(
    *,
    parent_folder,
    code: str,
    name: str,
    description: str = '',
    project_type: str = 'other',
    manager=None,
    created_by=None,
) -> 'Project':
    """
    Crea atomicamente un progetto con la propria root folder dedicata.

    La root folder viene creata come figlio diretto di parent_folder,
    con folder_kind=PROJECT, stesso codice e nome del progetto.

    Il path materializzato viene valorizzato tramite set_folder_path.

    Raises:
        ValidationError: codice già in uso nel Project o ProjectFolder,
                         o parent_folder non valida.
    """
    from django.core.exceptions import ValidationError
    from .models import Project, ProjectFolder

    if not parent_folder:
        raise ValidationError("La cartella padre è obbligatoria per creare un progetto.")

    if not code or not code.strip():
        raise ValidationError("Il codice progetto è obbligatorio.")

    code = code.strip()
    name = name.strip() if name else code

    # Verifica codice univoco
    if Project.objects.filter(code=code).exists():
        raise ValidationError(f"Esiste già un progetto con codice '{code}'.")
    if ProjectFolder.objects.filter(code=code).exists():
        raise ValidationError(f"Esiste già una cartella con codice '{code}'.")

    # Crea la root folder
    root_folder = ProjectFolder(
        code=code,
        name=name,
        folder_kind=ProjectFolder.FolderKind.PROJECT,
        parent=parent_folder,
        status=ProjectFolder.Status.ACTIVE,
        owner=created_by or manager or parent_folder.owner,
        created_by=created_by,
    )
    root_folder.save()
    set_folder_path(root_folder)

    # Crea il progetto con la root folder
    project = Project(
        code=code,
        name=name,
        description=description,
        project_type=project_type,
        manager=manager,
        created_by=created_by,
        root_folder=root_folder,
    )
    project.full_clean()  # Esegue clean() per validare coerenza root_folder
    project.save()

    return project


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
            user=actor,
            action=action,
            app_label='projects',
            model_name='projectrevision',
            object_id=str(revision.pk),
            object_repr=str(revision)[:255],
            changes=changes,
        )
    except Exception:
        pass
