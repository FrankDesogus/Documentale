"""
Management command: backfill_folder_permission_grants

Converte le membership legacy (ProjectFolderMembership) in grant modulari
equivalenti (FolderPermissionGrant).

Il comando è:
  - idempotente   (può essere eseguito più volte senza creare duplicati)
  - conservativo  (non assegna capacità che il ruolo legacy non possiede)
  - non distruttivo (non cancella né sovrascrive nulla di esistente)
  - transazionale (in modalità --apply usa transaction.atomic)

Modalità predefinita: --dry-run (non scrive nulla nel database).

Mapping conservativo (vedi tabella in resolver.py):
  reader   → read_published
  author   → read_published, create_draft, submit_for_approval
  approver → read_published, eligible_document_approver
  auditor  → read_published, view_history
  manager  → read_published, create_draft, submit_for_approval,
             eligible_document_approver, view_history, manage_folder

Esclusi per mancanza di prova esplicita nel codice legacy o per essere
in lista esclusioni:
  view_projects, view_folder_ecns, manage_project_documents, request_ecn,
  view_obsolete_documents, manage_rejected_drafts
"""

from django.core.management.base import BaseCommand
from django.db import transaction


# Mapping conservativo: ruolo → frozenset di permission_code
BACKFILL_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    'reader': frozenset([
        'read_published',
    ]),
    'author': frozenset([
        'read_published',
        'create_draft',
        'submit_for_approval',
    ]),
    'approver': frozenset([
        'read_published',
        'eligible_document_approver',
    ]),
    'auditor': frozenset([
        'read_published',
        'view_history',
    ]),
    'manager': frozenset([
        'read_published',
        'create_draft',
        'submit_for_approval',
        'eligible_document_approver',
        'view_history',
        'manage_folder',
    ]),
}


def _compute_backfill_plan(memberships):
    """
    Calcola il piano di backfill senza toccare il database.

    Restituisce:
      to_create   — lista di dict con i parametri del grant da creare
      to_skip     — lista di dict con i grant già esistenti
      conflicts   — lista di dict con i conflitti rilevati
    """
    from projects.models import FolderPermissionGrant

    to_create = []
    to_skip = []
    conflicts = []

    for membership in memberships:
        target_perms = BACKFILL_ROLE_PERMISSIONS.get(membership.role, frozenset())

        for perm_code in sorted(target_perms):
            existing = (
                FolderPermissionGrant.objects
                .filter(
                    folder=membership.folder,
                    user=membership.user,
                    permission_code=perm_code,
                )
                .first()
            )

            entry = {
                'membership_id': membership.pk,
                'folder': membership.folder,
                'user': membership.user,
                'permission_code': perm_code,
                'role': membership.role,
            }

            if existing is None:
                to_create.append(entry)
            elif existing.effect == FolderPermissionGrant.Effect.ALLOW:
                to_skip.append({**entry, 'existing_grant_id': existing.pk})
            else:
                # Grant esistente con effect=deny → conflitto
                conflicts.append({
                    **entry,
                    'existing_grant_id': existing.pk,
                    'existing_effect': existing.effect,
                    'proposed_effect': 'allow',
                })

    return to_create, to_skip, conflicts


class Command(BaseCommand):
    help = (
        'Converte le ProjectFolderMembership legacy in FolderPermissionGrant modulari. '
        'Modalità predefinita: --dry-run (non scrive nulla).'
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            '--dry-run',
            action='store_true',
            default=True,
            help='Mostra il piano senza scrivere nulla (predefinito).',
        )
        mode.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Applica il backfill al database in modo transazionale.',
        )
        parser.add_argument(
            '--folder-id',
            type=int,
            default=None,
            help='Limita il backfill a una singola cartella.',
        )
        parser.add_argument(
            '--user-id',
            type=int,
            default=None,
            help='Limita il backfill a un singolo utente.',
        )

    def handle(self, *args, **options):
        from projects.models import FolderPermissionGrant, ProjectFolderMembership

        apply_mode = options['apply']

        memberships = ProjectFolderMembership.objects.select_related(
            'folder', 'user'
        ).order_by('folder__code', 'user__username')

        if options['folder_id']:
            memberships = memberships.filter(folder_id=options['folder_id'])
        if options['user_id']:
            memberships = memberships.filter(user_id=options['user_id'])

        memberships = list(memberships)

        to_create, to_skip, conflicts = _compute_backfill_plan(memberships)

        # ------------------------------------------------------------------
        # Report
        # ------------------------------------------------------------------
        self.stdout.write('')
        self.stdout.write('=' * 60)
        self.stdout.write(
            self.style.SUCCESS('BACKFILL FOLDER PERMISSION GRANTS')
            if apply_mode else
            self.style.WARNING('BACKFILL FOLDER PERMISSION GRANTS — DRY RUN')
        )
        self.stdout.write('=' * 60)
        self.stdout.write(f'Membership analizzate : {len(memberships)}')
        self.stdout.write(f'Grant da creare       : {len(to_create)}')
        self.stdout.write(f'Grant già esistenti   : {len(to_skip)}')
        self.stdout.write(f'Conflitti rilevati    : {len(conflicts)}')

        if conflicts:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR('--- CONFLITTI ---'))
            for c in conflicts:
                self.stdout.write(
                    f'  membership #{c["membership_id"]} | '
                    f'folder={c["folder"].code} | '
                    f'user={c["user"].username} | '
                    f'perm={c["permission_code"]} | '
                    f'esistente=grant#{c["existing_grant_id"]}({c["existing_effect"]}) | '
                    f'proposto={c["proposed_effect"]}'
                )

        # ------------------------------------------------------------------
        # Apply
        # ------------------------------------------------------------------
        if not apply_mode:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'Dry-run completato. Usa --apply per applicare le modifiche.'
            ))
            return

        created_count = 0
        try:
            with transaction.atomic():
                for entry in to_create:
                    FolderPermissionGrant.objects.create(
                        folder=entry['folder'],
                        user=entry['user'],
                        permission_code=entry['permission_code'],
                        effect=FolderPermissionGrant.Effect.ALLOW,
                        inherit_to_children=False,
                        notes=(
                            f'Legacy backfill from '
                            f'ProjectFolderMembership #{entry["membership_id"]}'
                        ),
                    )
                    created_count += 1
        except Exception as exc:
            self.stderr.write(self.style.ERROR(
                f'Errore durante apply: {exc}. Nessuna modifica applicata.'
            ))
            raise

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Apply completato. Grant creati: {created_count}.'
        ))
