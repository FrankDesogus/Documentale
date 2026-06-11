"""Data migration: valorizza il campo path per le cartelle esistenti (BFS root→foglie)."""
from django.db import migrations


def compute_paths_forward(apps, schema_editor):
    """BFS: root prima, poi figlie, poi nipoti, ecc."""
    ProjectFolder = apps.get_model('projects', 'ProjectFolder')

    # Root folders
    roots = list(ProjectFolder.objects.filter(parent__isnull=True))
    for folder in roots:
        folder.path = f"/{folder.pk}/"
        folder.save(update_fields=['path'])

    # Livello per livello (max 50 livelli di profondità)
    max_depth = 50
    for _ in range(max_depth):
        unset = list(
            ProjectFolder.objects.filter(path='').exclude(parent__isnull=True)
            .select_related('parent')
        )
        if not unset:
            break
        for folder in unset:
            parent = folder.parent
            if parent and parent.path:
                folder.path = f"{parent.path}{folder.pk}/"
                folder.save(update_fields=['path'])


def compute_paths_reverse(apps, schema_editor):
    """Revert: svuota tutti i path."""
    ProjectFolder = apps.get_model('projects', 'ProjectFolder')
    ProjectFolder.objects.all().update(path='')


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0007_projectfolder_path'),
    ]

    operations = [
        migrations.RunPython(compute_paths_forward, compute_paths_reverse),
    ]
