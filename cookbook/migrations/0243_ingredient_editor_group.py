from django.db import migrations
from django_scopes import scopes_disabled


def create_ingredient_editor_group(apps, schema_editor):
    with scopes_disabled():
        Group = apps.get_model('auth', 'Group')
        Group.objects.get_or_create(name='ingredient-editor')


class Migration(migrations.Migration):

    dependencies = [
        ('cookbook', '0242_space_household_setup_completed'),
    ]

    operations = [
        migrations.RunPython(create_ingredient_editor_group, migrations.RunPython.noop),
    ]
