from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cookbook', '0242_inventoryentry_version'),
    ]

    operations = [
        migrations.AddField(
            model_name='food',
            name='reserve_retry_limit',
            field=models.PositiveSmallIntegerField(default=1),
        ),
    ]
