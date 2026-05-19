import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0002_projectfolder_folder_fields'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProjectFolderMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(
                    choices=[
                        ('reader', 'Lettore'),
                        ('author', 'Autore'),
                        ('approver', 'Approvatore'),
                        ('auditor', 'Revisore'),
                        ('manager', 'Gestore'),
                    ],
                    max_length=20,
                    verbose_name='Ruolo',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_memberships',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Creato da',
                )),
                ('folder', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='memberships',
                    to='projects.projectfolder',
                    verbose_name='Cartella',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='folder_memberships',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Utente',
                )),
            ],
            options={
                'verbose_name': 'Permesso cartella',
                'verbose_name_plural': 'Permessi cartella',
                'ordering': ['folder', 'user'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='projectfoldermembership',
            unique_together={('folder', 'user')},
        ),
    ]
