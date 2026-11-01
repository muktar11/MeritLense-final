from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contracts', '0005_alter_cookieconsent_choice'),
    ]

    operations = [
        migrations.AddField(
            model_name='agreement',
            name='signatory_title',
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
