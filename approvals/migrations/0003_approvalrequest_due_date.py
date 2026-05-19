from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('approvals', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='approvalrequest',
            name='due_date',
            field=models.DateField(blank=True, null=True, verbose_name='Scadenza approvazione'),
        ),
    ]
