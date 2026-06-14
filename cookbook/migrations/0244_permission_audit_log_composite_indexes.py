from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cookbook', '0243_add_permission_audit_log'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='permissionauditlog',
            index=models.Index(fields=['space_id', 'created_at'], name='cookbook_permis_space_i_a0b8f3_idx'),
        ),
        migrations.AddIndex(
            model_name='permissionauditlog',
            index=models.Index(fields=['target_user_id', 'created_at'], name='cookbook_permis_target__01ff96_idx'),
        ),
        migrations.AddIndex(
            model_name='permissionauditlog',
            index=models.Index(fields=['actor_user_id', 'created_at'], name='cookbook_permis_actor_u_2b2f9c_idx'),
        ),
        migrations.AddIndex(
            model_name='permissionauditlog',
            index=models.Index(fields=['action', 'created_at'], name='cookbook_permis_action_79b8de_idx'),
        ),
    ]
