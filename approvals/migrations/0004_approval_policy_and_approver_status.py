from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('approvals', '0003_approvalrequest_due_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='approvalrequest',
            name='approval_policy',
            field=models.CharField(
                choices=[
                    ('any', 'Basta un approvatore'),
                    ('all', 'Devono approvare tutti'),
                    ('sequential', 'Approvazione sequenziale'),
                ],
                default='all',
                max_length=20,
                verbose_name='Modalità approvazione',
            ),
        ),
        migrations.AddField(
            model_name='approvalrequestapprover',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'In attesa'),
                    ('approved', 'Approvato'),
                    ('rejected', 'Rifiutato'),
                ],
                default='pending',
                max_length=20,
                verbose_name='Stato',
            ),
        ),
        migrations.AddField(
            model_name='approvalrequestapprover',
            name='decided_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Deciso il'),
        ),
    ]
