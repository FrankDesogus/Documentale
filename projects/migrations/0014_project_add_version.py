from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0013_add_project_revision_scheme_remove_version'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='version_scheme',
            field=models.CharField(
                choices=[('numeric', 'Numerica'), ('alphabetic', 'Alfabetica')],
                default='numeric',
                help_text='Schema usato per la versione corrente.',
                max_length=20,
                verbose_name='Schema versione',
            ),
        ),
        migrations.AddField(
            model_name='project',
            name='version',
            field=models.CharField(
                default='00',
                help_text='Versione corrente del progetto. Modificabile manualmente.',
                max_length=32,
                verbose_name='Versione',
            ),
        ),
    ]
