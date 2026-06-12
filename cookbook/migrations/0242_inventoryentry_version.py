from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cookbook', '0241_invitelink_household'),
    ]

    operations = [
        migrations.AddField(
            model_name='inventoryentry',
            name='version',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
