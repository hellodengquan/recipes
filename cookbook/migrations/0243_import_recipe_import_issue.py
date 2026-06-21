# Generated manually for import review feature

import cookbook.models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('cookbook', '0242_space_household_setup_completed'),
    ]

    operations = [
        migrations.CreateModel(
            name='ImportRecipe',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=128)),
                ('source_url', models.CharField(blank=True, default=None, max_length=1024, null=True)),
                ('recipe_data', models.JSONField(default=dict)),
                ('image_url', models.CharField(blank=True, default=None, max_length=1024, null=True)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')], default='PENDING', max_length=32)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('import_log', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='import_recipes', to='cookbook.importlog')),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='cookbook.space')),
            ],
            options={
                'ordering': ('-created_at', 'pk'),
            },
            bases=(models.Model, cookbook.models.PermissionModelMixin),
        ),
        migrations.CreateModel(
            name='ImportIssue',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('issue_type', models.CharField(choices=[('UNIT_ERROR', 'Unit Recognition Error'), ('FIELD_MISMATCH', 'Field Mismatch'), ('MISSING_IMAGE', 'Missing Image'), ('DUPLICATE_FOOD', 'Duplicate Ingredient'), ('OTHER', 'Other Issue')], max_length=64)),
                ('severity', models.CharField(choices=[('LOW', 'Low'), ('MEDIUM', 'Medium'), ('HIGH', 'High')], default='MEDIUM', max_length=32)),
                ('message', models.TextField(default='')),
                ('field_name', models.CharField(blank=True, default=None, max_length=128, null=True)),
                ('original_value', models.TextField(blank=True, default=None, null=True)),
                ('suggested_value', models.TextField(blank=True, default=None, null=True)),
                ('resolved', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('import_recipe', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='issues', to='cookbook.importrecipe')),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='cookbook.space')),
            ],
            options={
                'ordering': ('-created_at', 'pk'),
            },
            bases=(models.Model, cookbook.models.PermissionModelMixin),
        ),
    ]
