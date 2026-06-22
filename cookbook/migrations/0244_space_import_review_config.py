# Generated manually for import review configurable thresholds and tiebreaker

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cookbook', '0243_import_recipe_import_issue'),
    ]

    operations = [
        migrations.AddField(
            model_name='space',
            name='import_review_fuzzy_threshold',
            field=models.DecimalField(
                decimal_places=2,
                default=0.7,
                help_text='Fuzzy match threshold (0.0-1.0) for import review unit recognition and food deduplication. Higher values are stricter. Default: 0.7 (edit distance). Trigram default is 0.6.',
                max_digits=3,
            ),
        ),
        migrations.AddField(
            model_name='space',
            name='import_review_trigram_threshold',
            field=models.DecimalField(
                decimal_places=2,
                default=0.6,
                help_text='Trigram similarity threshold (0.0-1.0) for import review. Default: 0.6.',
                max_digits=3,
            ),
        ),
        migrations.AddField(
            model_name='space',
            name='import_review_image_fetch_concurrency',
            field=models.PositiveIntegerField(
                default=3,
                help_text='Maximum concurrent image fetches during import review. Default: 3.',
            ),
        ),
        migrations.AddField(
            model_name='space',
            name='import_review_batch_result_limit',
            field=models.PositiveIntegerField(
                default=100,
                help_text='Maximum number of per-item detailed results returned for batch operations. Default: 100.',
            ),
        ),
        migrations.AddField(
            model_name='space',
            name='import_review_food_tiebreaker',
            field=models.CharField(
                choices=[
                    ('LEX', 'Lexicographic (alphabetical)'),
                    ('CREATED_AT', 'Most recently created'),
                    ('ID', 'Highest database ID (latest)'),
                ],
                default='LEX',
                help_text='Tiebreaker strategy for duplicate foods with same-length normalized names. Default: Lexicographic (alphabetical order picks the "smallest" name).',
                max_length=16,
            ),
        ),
    ]
