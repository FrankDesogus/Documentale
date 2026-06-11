# Migration 0010 — Cartella radice esclusiva per progetto
#
# Operazioni:
#   1. Aggiunge FolderKind.PROJECT alle scelte
#   2. Aggiunge il campo root_folder (OneToOneField, nullable) al Project
#   3. Data migration: copia folder_id → root_folder_id per i record esistenti
#                      e marca le root folder come folder_kind=PROJECT
#   4. Rimuove il vecchio campo folder
#
# Sicurezza dati: AddField + copy + RemoveField invece di Remove+Add.

import django.db.models.deletion
from django.db import migrations, models


def copy_folder_to_root_folder(apps, schema_editor):
    """Copia folder_id → root_folder_id e marca le cartelle come PROJECT."""
    Project = apps.get_model('projects', 'Project')
    ProjectFolder = apps.get_model('projects', 'ProjectFolder')

    # Prima verifica che non ci siano cartelle condivise tra più progetti
    from collections import Counter
    folder_ids = list(
        Project.objects.exclude(folder_id__isnull=True).values_list('folder_id', flat=True)
    )
    duplicates = {fid: cnt for fid, cnt in Counter(folder_ids).items() if cnt > 1}
    if duplicates:
        raise Exception(
            f"Impossibile applicare la migration: le seguenti cartelle sono "
            f"associate a più progetti: {duplicates}. "
            "Correggi i dati manualmente prima di procedere."
        )

    # Copia folder_id → root_folder_id e marca il kind
    for project in Project.objects.exclude(folder_id__isnull=True):
        # Assegna root_folder_id
        project.root_folder_id = project.folder_id
        project.save(update_fields=['root_folder_id'])
        # Marca la cartella come PROJECT
        ProjectFolder.objects.filter(pk=project.folder_id).update(folder_kind='project')


def reverse_copy(apps, schema_editor):
    """Ripristino: copia root_folder_id → folder_id e rimuove il kind PROJECT."""
    Project = apps.get_model('projects', 'Project')
    ProjectFolder = apps.get_model('projects', 'ProjectFolder')

    for project in Project.objects.exclude(root_folder_id__isnull=True):
        project.folder_id = project.root_folder_id
        project.save(update_fields=['folder_id'])

    # Riporta le cartelle project a generic
    ProjectFolder.objects.filter(folder_kind='project').update(folder_kind='generic')


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0009_folder_permission_grant'),
    ]

    operations = [
        # 1. Aggiunge il nuovo choice PROJECT a ProjectFolder.folder_kind
        migrations.AlterField(
            model_name='projectfolder',
            name='folder_kind',
            field=models.CharField(
                choices=[
                    ('department', 'Reparto'),
                    ('generic', 'Generica'),
                    ('archive', 'Archivio'),
                    ('project', 'Progetto'),
                ],
                default='generic',
                max_length=20,
                verbose_name='Tipo cartella',
            ),
        ),

        # 2. Aggiunge root_folder come campo nullable (inizialmente come FK semplice)
        #    Sarà convertito in OneToOneField nella stessa operazione
        migrations.AddField(
            model_name='project',
            name='root_folder',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='root_project',
                to='projects.projectfolder',
                verbose_name='Cartella radice progetto',
                help_text='Cartella dedicata che contiene tutti i documenti e sottocartelle del progetto.',
            ),
        ),

        # 3. Data migration: copia folder_id → root_folder_id + marca kind=PROJECT
        migrations.RunPython(copy_folder_to_root_folder, reverse_copy),

        # 4. Rimuove il vecchio campo folder
        migrations.RemoveField(
            model_name='project',
            name='folder',
        ),
    ]
