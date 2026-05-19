from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from documents.permissions import (
    GROUP_APPROVERS,
    GROUP_AUTHORS,
    GROUP_AUDITORS,
    GROUP_MANAGERS,
    GROUP_READERS,
)

ALL_GROUPS = [GROUP_READERS, GROUP_AUTHORS, GROUP_APPROVERS, GROUP_AUDITORS, GROUP_MANAGERS]


class Command(BaseCommand):
    help = 'Crea i gruppi base del documentale se non esistono già.'

    def handle(self, *args, **options):
        for name in ALL_GROUPS:
            _, created = Group.objects.get_or_create(name=name)
            label = 'creato' if created else 'già esistente'
            self.stdout.write(f'  Gruppo "{name}": {label}')
        self.stdout.write(self.style.SUCCESS('Gruppi documentali configurati.'))
