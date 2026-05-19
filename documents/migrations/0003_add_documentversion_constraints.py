from django.db import migrations, models
import django.db.models


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0002_initial'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='documentversion',
            constraint=models.UniqueConstraint(
                fields=['document', 'revision_number'],
                name='unique_revision_number_per_document',
            ),
        ),
        migrations.AddConstraint(
            model_name='documentversion',
            constraint=models.UniqueConstraint(
                condition=django.db.models.Q(is_current=True),
                fields=['document'],
                name='unique_current_version_per_document',
            ),
        ),
    ]
