from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('cookbook', '0242_space_household_setup_completed'),
    ]

    operations = [
        migrations.AddField(
            model_name='mealplan',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name='mealplan',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
        migrations.AddField(
            model_name='shoppinglistrecipe',
            name='created_at',
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        migrations.AddField(
            model_name='shoppinglistrecipe',
            name='updated_at',
            field=models.DateTimeField(auto_now=True, null=True),
        ),
    ]
