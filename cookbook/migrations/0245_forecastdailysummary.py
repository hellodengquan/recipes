from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cookbook', '0244_forecastlog'),
    ]

    operations = [
        migrations.CreateModel(
            name='ForecastDailySummary',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(db_index=True)),
                ('total_attempts', models.PositiveIntegerField(default=0)),
                ('success_count', models.PositiveIntegerField(default=0)),
                ('conflict_count', models.PositiveIntegerField(default=0)),
                ('hit_limit_count', models.PositiveIntegerField(default=0)),
                ('total_retry_count', models.PositiveIntegerField(default=0)),
                ('max_retry_count', models.PositiveSmallIntegerField(default=0)),
                ('avg_retry_limit', models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('food', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='cookbook.food')),
                ('space', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='cookbook.space')),
            ],
            options={
                'ordering': ('-date',),
                'unique_together': {('food', 'space', 'date')},
            },
        ),
        migrations.AddIndex(
            model_name='forecastdailysummary',
            index=models.Index(fields=['food', 'date'], name='forecastdaily_food_date_idx'),
        ),
        migrations.AddIndex(
            model_name='forecastdailysummary',
            index=models.Index(fields=['date'], name='forecastdaily_date_idx'),
        ),
    ]
