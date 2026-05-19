import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('approvals', '0004_approval_policy_and_approver_status'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ApprovalRequestAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to='approval_attachments/%Y/%m/', verbose_name='File')),
                ('attachment_type', models.CharField(
                    choices=[('signature_template', 'Modello da firmare')],
                    default='signature_template',
                    max_length=30,
                    verbose_name='Tipo allegato',
                )),
                ('original_filename', models.CharField(max_length=255, verbose_name='Nome file originale')),
                ('mime_type', models.CharField(blank=True, max_length=100, verbose_name='Tipo MIME')),
                ('extension', models.CharField(blank=True, max_length=20, verbose_name='Estensione')),
                ('size', models.PositiveIntegerField(blank=True, null=True, verbose_name='Dimensione (byte)')),
                ('sha256_hash', models.CharField(blank=True, max_length=64, verbose_name='Hash SHA-256')),
                ('uploaded_at', models.DateTimeField(auto_now_add=True, verbose_name='Caricato il')),
                ('approval_request', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='attachments',
                    to='approvals.approvalrequest',
                    verbose_name='Richiesta di approvazione',
                )),
                ('uploaded_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='uploaded_approval_attachments',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Caricato da',
                )),
            ],
            options={
                'verbose_name': 'Allegato richiesta approvazione',
                'verbose_name_plural': 'Allegati richiesta approvazione',
                'ordering': ['uploaded_at'],
            },
        ),
    ]
