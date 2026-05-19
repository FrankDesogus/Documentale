import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='projectfolder',
            name='folder_kind',
            field=models.CharField(
                choices=[('department', 'Reparto'), ('generic', 'Generica'), ('archive', 'Archivio')],
                default='generic',
                max_length=20,
                verbose_name='Tipo cartella',
            ),
        ),
        migrations.AddField(
            model_name='projectfolder',
            name='status',
            field=models.CharField(
                choices=[('active', 'Attiva'), ('archived', 'Archiviata'), ('obsolete', 'Obsoleta')],
                default='active',
                max_length=20,
                verbose_name='Stato',
            ),
        ),
        migrations.AddField(
            model_name='projectfolder',
            name='parent',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='subfolders',
                to='projects.projectfolder',
                verbose_name='Cartella padre',
            ),
        ),
        migrations.AddField(
            model_name='projectfolder',
            name='created_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='created_folders',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Creato da',
            ),
        ),
        migrations.AlterModelOptions(
            name='projectfolder',
            options={
                'ordering': ['code'],
                'verbose_name': 'Cartella',
                'verbose_name_plural': 'Cartelle',
            },
        ),
    ]
