from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cookbook', '0242_space_household_setup_completed'),
    ]

    operations = [
        migrations.CreateModel(
            name='RecipeBookEntryChangeRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('ADD', 'Add Recipe'), ('REMOVE', 'Remove Recipe')], default='ADD', max_length=16)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected'), ('WITHDRAWN', 'Withdrawn')], default='PENDING', max_length=16)),
                ('note', models.TextField(blank=True, default='')),
                ('review_note', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('book', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='change_requests', to='cookbook.recipebook')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='change_requests_created', to=settings.AUTH_USER_MODEL)),
                ('recipe', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='cookbook.recipe')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='change_requests_reviewed', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='recipebookentrychangerequest',
            constraint=models.UniqueConstraint(condition=models.Q(status='PENDING'), fields=('book', 'recipe', 'action'), name='unique_pending_change_request'),
        ),
    ]
