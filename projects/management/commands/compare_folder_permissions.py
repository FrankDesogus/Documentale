"""
Management command: compare_folder_permissions

Confronta il comportamento del sistema legacy (ProjectFolderMembership)
con il resolver modulare shadow (FolderPermissionGrant) dopo il backfill.

Il comando è completamente read-only: non modifica il database.

Usa il resolver con include_legacy_fallback=False, quindi verifica i grant
modulari reali senza cadere sul fallback legacy.

Exit code:
  0  — nessuna divergenza
  1  — divergenze rilevate (a meno che non sia passato --allow-differences)
"""

import sys

from django.core.management.base import BaseCommand

from projects.management.commands.backfill_folder_permission_grants import (
    BACKFILL_ROLE_PERMISSIONS,
)
from projects.resolver import has_folder_permission


def _legacy_allows(user, folder, permission_code: str) -> bool:
    """
    Replica la decisione legacy per (user, folder, permission_code).

    Usa il mapping conservativo del backfill: se il ruolo dell'utente
    nella cartella include il permission_code, restituisce True.
    I privilegiati globali (superuser, Document Manager) ottengono allow.
    """
    from projects.permissions import _is_global_manager, get_folder_role

    if not getattr(user, 'is_authenticated', False):
        return False
    if _is_global_manager(user):
        return True

    role = get_folder_role(user, folder)
    if role is None:
        return False
    return permission_code in BACKFILL_ROLE_PERMISSIONS.get(role, frozenset())


class Command(BaseCommand):
    help = (
        'Confronta il comportamento legacy con il resolver modulare shadow. '
        'Read-only. Exit code 0 se nessuna divergenza, 1 se ci sono divergenze.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--user-id',
            type=int,
            default=None,
            help='Limita il confronto a un singolo utente.',
        )
        parser.add_argument(
            '--folder-id',
            type=int,
            default=None,
            help='Limita il confronto a una singola cartella.',
        )
        parser.add_argument(
            '--allow-differences',
            action='store_true',
            default=False,
            help='Termina con exit code 0 anche in presenza di divergenze.',
        )

    def handle(self, *args, **options):
        from projects.models import ProjectFolderMembership

        memberships = ProjectFolderMembership.objects.select_related(
            'folder', 'user'
        ).order_by('folder__code', 'user__username')

        if options['user_id']:
            memberships = memberships.filter(user_id=options['user_id'])
        if options['folder_id']:
            memberships = memberships.filter(folder_id=options['folder_id'])

        memberships = list(memberships)

        total = 0
        matches = 0
        divergences = []

        for membership in memberships:
            user = membership.user
            folder = membership.folder
            target_perms = BACKFILL_ROLE_PERMISSIONS.get(membership.role, frozenset())

            for perm_code in sorted(target_perms):
                total += 1
                legacy_result = _legacy_allows(user, folder, perm_code)
                resolver_result = has_folder_permission(
                    user, folder, perm_code,
                    include_legacy_fallback=False,
                )

                if legacy_result == resolver_result:
                    matches += 1
                else:
                    # Motivazione probabile
                    if legacy_result and not resolver_result:
                        reason = 'Grant modulare assente o non ancora backfillato'
                    else:
                        reason = 'Grant modulare più restrittivo del legacy (deny esplicito?)'

                    divergences.append({
                        'user': user,
                        'folder': folder,
                        'permission_code': perm_code,
                        'legacy_result': legacy_result,
                        'resolver_result': resolver_result,
                        'reason': reason,
                    })

        # ------------------------------------------------------------------
        # Report
        # ------------------------------------------------------------------
        self.stdout.write('')
        self.stdout.write('=' * 60)
        self.stdout.write('CONFRONTO LEGACY vs RESOLVER MODULARE')
        self.stdout.write('=' * 60)
        self.stdout.write(f'Combinazioni analizzate : {total}')
        self.stdout.write(f'Match                   : {matches}')
        self.stdout.write(f'Divergenze              : {len(divergences)}')

        if divergences:
            self.stdout.write('')
            self.stdout.write(self.style.ERROR('--- DIVERGENZE ---'))
            for d in divergences:
                self.stdout.write(
                    f'  user={d["user"].username} | '
                    f'folder={d["folder"].code} | '
                    f'perm={d["permission_code"]} | '
                    f'legacy={d["legacy_result"]} | '
                    f'resolver={d["resolver_result"]} | '
                    f'→ {d["reason"]}'
                )
            self.stdout.write('')

        if not divergences:
            self.stdout.write(self.style.SUCCESS('Nessuna divergenza rilevata.'))
        else:
            self.stdout.write(self.style.WARNING(
                f'{len(divergences)} divergenza/e rilevata/e.'
            ))

        if divergences and not options['allow_differences']:
            sys.exit(1)
