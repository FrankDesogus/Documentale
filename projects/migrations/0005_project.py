import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0004_alter_projectrevision_options_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Project',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=50, unique=True, verbose_name='Codice')),
                ('name', models.CharField(max_length=255, verbose_name='Nome')),
                ('description', models.TextField(blank=True, verbose_name='Descrizione')),
                ('status', models.CharField(
                    choices=[
                        ('draft', 'Bozza'),
                        ('active', 'In corso'),
                        ('suspended', 'Sospeso'),
                        ('closed', 'Chiuso'),
                        ('archived', 'Archiviato'),
                    ],
                    default='draft',
                    max_length=20,
                    verbose_name='Stato',
                )),
                ('project_type', models.CharField(
                    choices=[
                        ('customer', 'Cliente'),
                        ('internal', 'Interno'),
                        ('engineering', 'Ingegneria'),
                        ('quality', 'Qualità'),
                        ('other', 'Altro'),
                    ],
                    default='other',
                    max_length=20,
                    verbose_name='Tipo',
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_projects',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Creato da',
                )),
                ('folder', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='projects',
                    to='projects.projectfolder',
                    verbose_name='Cartella documentale',
                )),
                ('manager', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='managed_projects',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Responsabile',
                )),
            ],
            options={
                'verbose_name': 'Progetto',
                'verbose_name_plural': 'Progetti',
                'ordering': ['code'],
            },
        ),
    ]
