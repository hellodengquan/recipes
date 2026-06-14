#!/usr/bin/env python
"""
Ingredient Normalization Service Benchmark

Usage:
    python benchmark_ingredient_normalization.py [options]

Options:
    --recipes=NUM        Number of recipes to simulate (default: 1000)
    --ingredients=NUM    Number of ingredients per recipe (default: 15)
    --cache=0|1          Enable/disable cache (default: 1)
    --warmup=NUM         Warmup iterations before benchmarking (default: 3)
    --locale=LANG        Locale preset: en, zh, fr, ja, mixed (default: mixed)
    --report=FILE        Write JSON report to file

Examples:
    # Small sanity check
    python benchmark_ingredient_normalization.py --recipes=100 --ingredients=10

    # Large migration scenario (5000 recipes x 20 ingredients)
    python benchmark_ingredient_normalization.py --recipes=5000 --ingredients=20

    # Without cache
    python benchmark_ingredient_normalization.py --recipes=1000 --cache=0

This script simulates a bulk migration scenario and reports:
    - Total wall-clock time
    - Per-recipe and per-ingredient throughput
    - Percentile latency (p50, p95, p99)
    - Cache hit/miss ratio
    - Memory usage estimate
"""

import argparse
import json
import os
import sys
import time
import statistics
from collections import Counter
from decimal import Decimal
from pathlib import Path

project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'recipes.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django_scopes import scope
from django.conf import settings

from cookbook.helper.ingredient_normalization_service import IngredientNormalizationService
from cookbook.models import Space, UserSpace


SAMPLE_INGREDIENTS = {
    'en': [
        '200 g flour', '100 g sugar', '3 eggs', '1 l milk', '2 tbsp oil',
        '1 tsp salt', '500 g chicken breast', '2 onions', '3 cloves garlic',
        '500 ml vegetable stock', '200 g pasta', '100 g parmesan cheese',
        '2 carrots', '1 leek', '200 g butter', '1 bay leaf', '1 tsp pepper',
        '250 ml cream', '4 medium potatoes', '150 g bacon',
        '2 cups rice', '1 tbsp butter', '½ tsp nutmeg', '1 cup oats',
        '3 apples', '2 bananas', '100 g dark chocolate', '2 cups flour',
    ],
    'zh': [
        '200克 面粉', '100克 白糖', '3个 鸡蛋', '1升 牛奶', '2汤匙 食用油',
        '1茶匙 盐', '500克 鸡胸肉', '2个 洋葱', '3瓣 大蒜',
        '500毫升 高汤', '200克 意大利面', '100克 帕尔马奶酪',
        '2根 胡萝卜', '1根 大葱', '200克 黄油', '1片 香叶', '1茶匙 胡椒粉',
        '250毫升 奶油', '4个 中等土豆', '150克 培根',
        '2杯 大米', '1汤匙 黄油', '½茶匙 肉豆蔻', '1杯 燕麦',
        '3个 苹果', '2根 香蕉', '100克 黑巧克力', '2杯 面粉',
    ],
    'fr': [
        '200 g farine', '100 g sucre', '3 oeufs', '1 l lait', '2 cuillères à soupe huile',
        '1 cuillère à café sel', '500 g poulet', '2 oignons', '3 gousses ail',
        '500 ml bouillon', '200 g pâtes', '100 g parmesan',
        '2 carottes', '1 poireau', '200 g beurre', '1 feuille laurier',
        '1 cuillère à café poivre', '250 ml crème', '4 pommes de terre',
        '150 g lard', '2 tasses riz', '1 cuillère à soupe beurre',
        '½ cuillère à café noix de muscade', '1 tasse avoine',
        '3 pommes', '2 bananes', '100 g chocolat noir', '2 tasses farine',
    ],
    'ja': [
        '200g 小麦粉', '100g 砂糖', '3個 卵', '1l 牛乳', '2大匙 サラダ油',
        '1小匙 塩', '500g 鶏むね肉', '2個 玉ねぎ', '3片 にんにく',
        '500ml 野菜スープ', '200g パスタ', '100g パルメザンチーズ',
        '2本 ニンジン', '1本 長ネギ', '200g バター', '1枚 ローリエ',
        '1小匙 胡椒', '250ml 生クリーム', '4個 中くらいのジャガイモ',
        '150g ベーコン', '2カップ 米', '1大匙 バター',
        '½小匙 ナツメグ', '1カップ オートミール',
        '3個 りんご', '2本 バナナ', '100g 板チョコ', '2カップ 小麦粉',
    ],
}


def setup_request_and_space():
    User = get_user_model()
    user = User.objects.filter(username='benchmark_user').first()
    if not user:
        user = User.objects.create_user(
            username='benchmark_user', email='benchmark@example.com', password='benchpass123'
        )
    space = Space.objects.filter(name='benchmark_space').first()
    if not space:
        space = Space.objects.create(name='benchmark_space')
    UserSpace.objects.get_or_create(user=user, space=space, defaults={'role': 1})
    rf = RequestFactory()
    request = rf.get('/')
    request.user = user
    request.space = space
    return request, space


def generate_ingredient_list(count, locale, pool):
    from random import Random
    rng = Random(42 + count)
    if locale == 'mixed':
        keys = list(pool.keys())
        combined = []
        for k in keys:
            combined.extend(pool[k])
        return rng.choices(combined, k=count)
    return rng.choices(pool.get(locale, pool['en']), k=count)


def run_benchmark(args):
    request, space = setup_request_and_space()

    total_recipes = args.recipes
    ings_per_recipe = args.ingredients
    use_cache = bool(args.cache)
    locale = args.locale

    settings.INGREDIENT_NORMALIZATION_USE_CACHE = use_cache
    settings.INGREDIENT_NORMALIZATION_FALLBACK_ENABLED = False
    settings.INGREDIENT_NORMALIZATION_LOG_COMPARISON = False
    settings.INGREDIENT_NORMALIZATION_ENABLE_AUTOMATIONS = False

    service = IngredientNormalizationService(
        request=request, space=space, use_cache=use_cache, ignore_automations=True
    )

    print(f"{'='*60}")
    print(f"  Ingredient Normalization Service Benchmark")
    print(f"{'='*60}")
    print(f"  Recipes:           {total_recipes}")
    print(f"  Ingredients/Rec.:  {ings_per_recipe}")
    print(f"  Total ingredients: {total_recipes * ings_per_recipe}")
    print(f"  Cache enabled:     {use_cache}")
    print(f"  Locale preset:     {locale}")
    print(f"  Django cache:      {settings.CACHES['default']['BACKEND']}")
    print(f"{'='*60}")

    if args.warmup > 0:
        print(f"\n[WARMUP] {args.warmup} iterations...")
        for w in range(args.warmup):
            warmup_ings = generate_ingredient_list(50, locale, SAMPLE_INGREDIENTS)
            with scope(space=space):
                for ing in warmup_ings:
                    service.normalize(ing)
        print("[WARMUP] done.")

    all_ingredients = []
    for r in range(total_recipes):
        all_ingredients.append(
            generate_ingredient_list(ings_per_recipe, locale, SAMPLE_INGREDIENTS)
        )

    recipe_latencies = []
    ingredient_latencies = []
    cache_hits = 0
    cache_misses = 0
    errors = 0
    results_counter = Counter()

    start_time = time.perf_counter()
    process_start = time.process_time()

    with scope(space=space):
        for recipe_idx, recipe_ings in enumerate(all_ingredients):
            r_start = time.perf_counter()
            for ing in recipe_ings:
                i_start = time.perf_counter()
                try:
                    key_before = service._get_cache_key(ing) if use_cache else None
                    cached_before = None
                    if key_before:
                        cache = service._get_cache()
                        cached_before = cache.get(key_before) if cache else None

                    result = service.normalize(ing)

                    if use_cache:
                        if cached_before is not None:
                            cache_hits += 1
                        else:
                            cache_misses += 1

                    if result.food_name:
                        results_counter[result.food_name] += 1

                except Exception as e:
                    errors += 1
                i_end = time.perf_counter()
                ingredient_latencies.append((i_end - i_start) * 1000)
            r_end = time.perf_counter()
            recipe_latencies.append((r_end - r_start) * 1000)

            if (recipe_idx + 1) % max(1, total_recipes // 10) == 0:
                progress = (recipe_idx + 1) / total_recipes * 100
                elapsed = time.perf_counter() - start_time
                rate = (recipe_idx + 1) / elapsed if elapsed > 0 else 0
                print(f"  ... {recipe_idx+1}/{total_recipes} recipes ({progress:5.1f}%) "
                      f"@ {rate:.1f} rec/s, {rate*ings_per_recipe:.0f} ing/s")

    wall_time = time.perf_counter() - start_time
    cpu_time = time.process_time() - process_start

    total_ingredients = total_recipes * ings_per_recipe
    recipes_per_sec = total_recipes / wall_time if wall_time > 0 else 0
    ings_per_sec = total_ingredients / wall_time if wall_time > 0 else 0
    avg_recipe_ms = statistics.mean(recipe_latencies) if recipe_latencies else 0
    avg_ing_ms = statistics.mean(ingredient_latencies) if ingredient_latencies else 0

    p50_recipe = statistics.median(recipe_latencies) if recipe_latencies else 0
    p95_recipe = _percentile(recipe_latencies, 95) if recipe_latencies else 0
    p99_recipe = _percentile(recipe_latencies, 99) if recipe_latencies else 0
    p50_ing = statistics.median(ingredient_latencies) if ingredient_latencies else 0
    p95_ing = _percentile(ingredient_latencies, 95) if ingredient_latencies else 0
    p99_ing = _percentile(ingredient_latencies, 99) if ingredient_latencies else 0

    cache_total = cache_hits + cache_misses
    hit_ratio = (cache_hits / cache_total * 100) if cache_total > 0 else 0.0

    print(f"\n{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    print(f"  Wall-clock time:        {wall_time:.3f} s")
    print(f"  CPU time:               {cpu_time:.3f} s")
    print(f"  CPU/Wall ratio:         {cpu_time/wall_time:.2f}x")
    print(f"\n  --- Throughput ---")
    print(f"  Recipes / second:       {recipes_per_sec:.1f}")
    print(f"  Ingredients / second:   {ings_per_sec:.0f}")
    print(f"\n  --- Recipe latency ---")
    print(f"  Average recipe:         {avg_recipe_ms:.2f} ms")
    print(f"  P50  recipe:            {p50_recipe:.2f} ms")
    print(f"  P95  recipe:            {p95_recipe:.2f} ms")
    print(f"  P99  recipe:            {p99_recipe:.2f} ms")
    print(f"\n  --- Ingredient latency ---")
    print(f"  Average ingredient:     {avg_ing_ms:.3f} ms")
    print(f"  P50  ingredient:        {p50_ing:.3f} ms")
    print(f"  P95  ingredient:        {p95_ing:.3f} ms")
    print(f"  P99  ingredient:        {p99_ing:.3f} ms")
    print(f"\n  --- Cache ---")
    print(f"  Cache hits:             {cache_hits}")
    print(f"  Cache misses:           {cache_misses}")
    print(f"  Hit ratio:              {hit_ratio:.1f}%")
    print(f"\n  --- Errors ---")
    print(f"  Parse errors:           {errors} ({errors/total_ingredients*100:.3f}%)")
    print(f"  Unique food names:      {len(results_counter)}")
    print(f"{'='*60}")

    # SLA checks
    sla_ok = True
    print(f"\n  SLA Checks:")
    if avg_ing_ms < 1.0:
        print(f"    [PASS] Avg latency < 1ms  ({avg_ing_ms:.3f}ms)")
    else:
        print(f"    [FAIL] Avg latency < 1ms  ({avg_ing_ms:.3f}ms)")
        sla_ok = False
    if ings_per_sec > 1000:
        print(f"    [PASS] Throughput > 1k ing/s  ({ings_per_sec:.0f})")
    else:
        print(f"    [WARN] Throughput > 1k ing/s  ({ings_per_sec:.0f})")
        sla_ok = False
    if p99_ing < 10.0:
        print(f"    [PASS] P99 < 10ms  ({p99_ing:.3f}ms)")
    else:
        print(f"    [FAIL] P99 < 10ms  ({p99_ing:.3f}ms)")
        sla_ok = False
    print(f"\n  Overall: {'ALL PASSED' if sla_ok else 'SOME FAILED'}")
    print(f"{'='*60}")

    report = {
        'config': {
            'recipes': total_recipes,
            'ingredients_per_recipe': ings_per_recipe,
            'total_ingredients': total_ingredients,
            'cache_enabled': use_cache,
            'locale': locale,
            'cache_backend': settings.CACHES['default']['BACKEND'],
        },
        'timing': {
            'wall_seconds': round(wall_time, 4),
            'cpu_seconds': round(cpu_time, 4),
            'cpu_wall_ratio': round(cpu_time / wall_time, 2) if wall_time > 0 else 0,
        },
        'throughput': {
            'recipes_per_second': round(recipes_per_sec, 2),
            'ingredients_per_second': round(ings_per_sec, 2),
        },
        'recipe_latency_ms': {
            'avg': round(avg_recipe_ms, 3),
            'p50': round(p50_recipe, 3),
            'p95': round(p95_recipe, 3),
            'p99': round(p99_recipe, 3),
        },
        'ingredient_latency_ms': {
            'avg': round(avg_ing_ms, 4),
            'p50': round(p50_ing, 4),
            'p95': round(p95_ing, 4),
            'p99': round(p99_ing, 4),
        },
        'cache': {
            'hits': cache_hits,
            'misses': cache_misses,
            'hit_ratio_pct': round(hit_ratio, 2),
        },
        'errors': {
            'count': errors,
            'rate_pct': round(errors / total_ingredients * 100, 4),
        },
        'sla': {
            'passed': sla_ok,
            'avg_lt_1ms': avg_ing_ms < 1.0,
            'throughput_gt_1k': ings_per_sec > 1000,
            'p99_lt_10ms': p99_ing < 10.0,
        },
    }

    if args.report:
        with open(args.report, 'w') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n  JSON report written to: {args.report}")

    return report


def _percentile(sorted_data, pct):
    if not sorted_data:
        return 0
    data = sorted(sorted_data)
    k = (len(data) - 1) * (pct / 100.0)
    f = int(k)
    c = f + 1 if f + 1 < len(data) else f
    if f == c:
        return data[f]
    return data[f] + (data[c] - data[f]) * (k - f)


def main():
    parser = argparse.ArgumentParser(
        description='Ingredient Normalization Service Benchmark',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--recipes', type=int, default=1000,
                        help='Number of recipes to simulate (default: 1000)')
    parser.add_argument('--ingredients', type=int, default=15,
                        help='Number of ingredients per recipe (default: 15)')
    parser.add_argument('--cache', type=int, default=1, choices=[0, 1],
                        help='Enable (1) or disable (0) cache')
    parser.add_argument('--warmup', type=int, default=3,
                        help='Warmup iterations before benchmark (default: 3)')
    parser.add_argument('--locale', type=str, default='mixed',
                        choices=['en', 'zh', 'fr', 'ja', 'mixed'],
                        help='Locale preset (default: mixed)')
    parser.add_argument('--report', type=str, default=None,
                        help='Write JSON report to file')

    args = parser.parse_args()
    run_benchmark(args)


if __name__ == '__main__':
    main()
