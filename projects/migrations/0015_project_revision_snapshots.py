from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0014_project_add_version'),
    ]

    operations = [
        # --- snapshot_type su ProjectRevision ---
        migrations.AddField(
            model_name='projectrevision',
            name='snapshot_type',
            field=models.CharField(
                choices=[('version', 'Versione'), ('revision', 'Revisione')],
                default='revision',
                max_length=20,
                verbose_name='Tipo snapshot',
            ),
        ),

        # --- Metadati progetto congelati ---
        migrations.AddField(
            model_name='projectrevision',
            name='snapshot_project_name',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Nome progetto (snapshot)'),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='snapshot_project_description',
            field=models.TextField(blank=True, default='', verbose_name='Descrizione progetto (snapshot)'),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='snapshot_project_type',
            field=models.CharField(blank=True, default='', max_length=20, verbose_name='Tipo progetto (snapshot)'),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='snapshot_project_manager_display',
            field=models.CharField(blank=True, default='', max_length=200, verbose_name='Responsabile (snapshot)'),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='snapshot_project_version',
            field=models.CharField(blank=True, default='', max_length=32, verbose_name='Versione progetto (snapshot)'),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='snapshot_project_version_scheme',
            field=models.CharField(blank=True, default='', max_length=20, verbose_name='Schema versione (snapshot)'),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='snapshot_project_revision',
            field=models.CharField(blank=True, default='', max_length=32, verbose_name='Revisione progetto (snapshot)'),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='snapshot_project_revision_scheme',
            field=models.CharField(blank=True, default='', max_length=20, verbose_name='Schema revisione (snapshot)'),
        ),

        # --- Campi denormalizzati su ProjectRevisionItem ---
        migrations.AddField(
            model_name='projectrevisionitem',
            name='snapshot_document_code',
            field=models.CharField(blank=True, default='', max_length=50, verbose_name='Codice documento (snapshot)'),
        ),
        migrations.AddField(
            model_name='projectrevisionitem',
            name='snapshot_document_title',
            field=models.CharField(blank=True, default='', max_length=255, verbose_name='Titolo documento (snapshot)'),
        ),
        migrations.AddField(
            model_name='projectrevisionitem',
            name='snapshot_document_revision_label',
            field=models.CharField(blank=True, default='', max_length=20, verbose_name='Revisione documento (snapshot)'),
        ),
        migrations.AddField(
            model_name='projectrevisionitem',
            name='snapshot_folder_path',
            field=models.CharField(blank=True, default='', max_length=1000, verbose_name='Path cartella (snapshot)'),
        ),

        # --- Aggiorna unique_together per includere snapshot_type ---
        migrations.AlterUniqueTogether(
            name='projectrevision',
            unique_together={
                ('project', 'snapshot_type', 'revision_label'),
                ('project', 'snapshot_type', 'revision_number'),
            },
        ),

        # --- Vincolo is_current per (project, snapshot_type) ---
        migrations.AddConstraint(
            model_name='projectrevision',
            constraint=models.UniqueConstraint(
                condition=models.Q(is_current=True),
                fields=['project', 'snapshot_type'],
                name='unique_current_snapshot_per_type',
            ),
        ),

        # --- Aggiorna verbose_name ---
        migrations.AlterModelOptions(
            name='projectrevision',
            options={
                'ordering': ['project', 'snapshot_type', '-revision_number'],
                'verbose_name': 'Snapshot progetto',
                'verbose_name_plural': 'Snapshot progetto',
            },
        ),
        migrations.AlterModelOptions(
            name='projectrevisionitem',
            options={
                'ordering': ['revision', 'item_number'],
                'verbose_name': 'Voce snapshot progetto',
                'verbose_name_plural': 'Voci snapshot progetto',
            },
        ),
    ]
