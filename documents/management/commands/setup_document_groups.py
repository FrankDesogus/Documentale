"""Crea (idempotente) tutti i gruppi del documentale."""
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from documents.permissions import (
    GROUP_APPROVERS,
    GROUP_AUTHORS,
    GROUP_AUDITORS,
    GROUP_DIRECTION,
    GROUP_ECN_PROPOSERS,
    GROUP_MANAGERS,
    GROUP_QUALITY_MANAGER,
    GROUP_QUALITY_OPERATOR,
    GROUP_READERS,
)
from ecn.permissions import GROUP_CCB

ALL_GROUPS = [
    # ── Gruppi documentali core ──────────────────────────────────────────────
    (GROUP_READERS,         'Può leggere documenti approvati correnti.'),
    (GROUP_AUTHORS,         'Può creare documenti e bozze, inviarle in approvazione.'),
    (GROUP_APPROVERS,       'Può essere selezionato come approvatore di documenti.'),
    (GROUP_AUDITORS,        'Può vedere storico versioni e audit trail.'),
    (GROUP_MANAGERS,        'Gestione cartelle e baseline. NON concede governance ECN.'),
    # ── Qualità ──────────────────────────────────────────────────────────────
    (GROUP_QUALITY_MANAGER, 'Governance ECN/CCB completa: configura, invia, chiude.'),
    (GROUP_QUALITY_OPERATOR,'Consultazione ECN/CCB. Nessuna governance.'),
    # ── Direzione ────────────────────────────────────────────────────────────
    (GROUP_DIRECTION,       'Consultazione ECN in sola lettura.'),
    # ── ECN ──────────────────────────────────────────────────────────────────
    (GROUP_CCB,             'Pool candidati CCB. Visibilità/voto SOLO sulle ECN assegnate.'),
    (GROUP_ECN_PROPOSERS,   'Può richiedere una variante ECN su documenti autorizzati.'),
]


class Command(BaseCommand):
    help = 'Crea i gruppi base del documentale (idempotente — eseguibile più volte).'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('\n=== SETUP GRUPPI DOCUMENTALE ===\n'))
        for name, description in ALL_GROUPS:
            _, created = Group.objects.get_or_create(name=name)
            label = self.style.SUCCESS('creato') if created else 'già esistente'
            self.stdout.write(f'  [{label}] {name}')
            if created:
                self.stdout.write(f'          → {description}')
        self.stdout.write(self.style.SUCCESS('\nGruppi documentali configurati.'))
