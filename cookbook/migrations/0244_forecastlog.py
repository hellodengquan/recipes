from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cookbook', '0243_food_reserve_retry_limit'),
    ]

    operations = [
        migrations.CreateModel(
            name='ForecastLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('success', 'Success'), ('conflict', 'Conflict')], default='success', max_length=10)),
                ('retry_count', models.PositiveSmallIntegerField(default=0)),
                ('hit_limit', models.BooleanField(default=False)),
                ('reserve_retry_limit', models.PositiveSmallIntegerField(default=1)),
                ('note', models.CharField(blank=True, max_length=256, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='cookbook.user')),
                ('food', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='cookbook.food')),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='cookbook.space')),
            ],
            options={
                'ordering': ('-created_at',),
            },
        ),
        migrations.AddIndex(
            model_name='forecastlog',
            index=models.Index(fields=['food', 'created_at'], name='forecastlog_food_created_idx'),
        ),
        migrations.AddIndex(
            model_name='forecastlog',
            index=models.Index(fields=['hit_limit', 'created_at'], name='forecastlog_hitlimit_created_idx'),
        ),
    ]
