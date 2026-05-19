import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0005_project'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── ProjectRevision: rimuovi unique_together vecchio (referenzia project_folder + revision_code) ──
        migrations.AlterUniqueTogether(
            name='projectrevision',
            unique_together=set(),
        ),

        # ── ProjectRevision: aggiungi nuovi campi ──
        migrations.AddField(
            model_name='projectrevision',
            name='project',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='revisions',
                to='projects.project',
                verbose_name='Progetto',
            ),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='revision_number',
            field=models.PositiveIntegerField(default=0, verbose_name='Numero revisione'),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='description',
            field=models.TextField(blank=True, default='', verbose_name='Descrizione'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='is_current',
            field=models.BooleanField(default=False, verbose_name='Corrente'),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='issued_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Emessa il'),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='issued_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='issued_project_revisions',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Emessa da',
            ),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='created_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='created_project_revisions',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Creato da',
            ),
        ),
        migrations.AddField(
            model_name='projectrevision',
            name='replaces_revision',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='replaced_by',
                to='projects.projectrevision',
                verbose_name='Sostituisce',
            ),
        ),

        # ── ProjectRevision: rinomina revision_code → revision_label ──
        migrations.RenameField(
            model_name='projectrevision',
            old_name='revision_code',
            new_name='revision_label',
        ),

        # ── ProjectRevision: rimuovi campi vecchi ──
        migrations.RemoveField(
            model_name='projectrevision',
            name='project_folder',
        ),
        migrations.RemoveField(
            model_name='projectrevision',
            name='author',
        ),

        # ── ProjectRevision: aggiorna status choices e verbose_name campo ──
        migrations.AlterField(
            model_name='projectrevision',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Bozza'),
                    ('issued', 'Emessa'),
                    ('superseded', 'Superata'),
                    ('archived', 'Archiviata'),
                ],
                default='draft',
                max_length=20,
                verbose_name='Stato',
            ),
        ),
        migrations.AlterField(
            model_name='projectrevision',
            name='revision_label',
            field=models.CharField(max_length=20, verbose_name='Etichetta revisione'),
        ),

        # ── ProjectRevision: aggiorna Meta ──
        migrations.AlterModelOptions(
            name='projectrevision',
            options={
                'ordering': ['project', '-revision_number'],
                'verbose_name': 'Baseline progetto',
                'verbose_name_plural': 'Baseline progetto',
            },
        ),

        # ── ProjectRevision: aggiungi nuovo unique_together ──
        migrations.AlterUniqueTogether(
            name='projectrevision',
            unique_together={('project', 'revision_label'), ('project', 'revision_number')},
        ),

        # ── ProjectRevisionItem: rimuovi unique_together vecchio ──
        migrations.AlterUniqueTogether(
            name='projectrevisionitem',
            unique_together=set(),
        ),

        # ── ProjectRevisionItem: trasforma document_version in campo obbligatorio ──
        migrations.AlterField(
            model_name='projectrevisionitem',
            name='document_version',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='project_revision_items',
                to='documents.documentversion',
                verbose_name='Revisione documento',
            ),
        ),

        # ── ProjectRevisionItem: rendi description opzionale ──
        migrations.AlterField(
            model_name='projectrevisionitem',
            name='description',
            field=models.CharField(blank=True, max_length=255, verbose_name='Descrizione'),
        ),

        # ── ProjectRevisionItem: aggiorna FK verbose_name ──
        migrations.AlterField(
            model_name='projectrevisionitem',
            name='revision',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='items',
                to='projects.projectrevision',
                verbose_name='Baseline',
            ),
        ),

        # ── ProjectRevisionItem: aggiorna Meta ──
        migrations.AlterModelOptions(
            name='projectrevisionitem',
            options={
                'ordering': ['revision', 'item_number'],
                'verbose_name': 'Voce baseline progetto',
                'verbose_name_plural': 'Voci baseline progetto',
            },
        ),

        # ── ProjectRevisionItem: aggiungi nuovo unique_together ──
        migrations.AlterUniqueTogether(
            name='projectrevisionitem',
            unique_together={('revision', 'item_number'), ('revision', 'document_version')},
        ),
    ]
