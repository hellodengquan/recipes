import json
import sys
import time
import traceback
from collections import OrderedDict
from urllib.parse import urlparse

import requests
from django.core.management.base import BaseCommand, CommandError
from recipe_scrapers import scrape_html
from recipe_scrapers._exceptions import NoSchemaFoundInWildMode
from recipe_scrapers._utils import get_host_name

from cookbook.helper.HelperFunctions import safe_request
from cookbook.helper.recipe_url_import import get_from_scraper


FIELDS_TO_CHECK = [
    'name',
    'description',
    'image_url',
    'source_url',
    'servings',
    'working_time',
    'waiting_time',
    'keywords',
    'steps',
]


BUILTIN_TEST_URLS = OrderedDict([
    ('allrecipes.com', 'https://www.allrecipes.com/recipe/24010/easy-chicken-marsala/'),
    ('chefkoch.de', 'https://www.chefkoch.de/rezepte/1913681311847861/Couscous-und-Garnelen-im-Pergament.html'),
    ('cookpad.com', 'https://cookpad.com/us/recipes/14815875-chicken-and-moringa-drumsticks-soup'),
    ('delish.com', 'https://www.delish.com/cooking/recipe-ideas/recipes/a52405/cheesy-baked-asparagus-recipe/'),
    ('foodnetwork.com', 'https://www.foodnetwork.com/recipes/bobby-flay/cast-iron-home-fries-recipe-1945083'),
    ('giallozafferano.it', 'https://ricette.giallozafferano.it/Strangolapreti-alla-trentina.html'),
    ('marmiton.org', 'https://www.marmiton.org/recettes/recette_fricassee-d-agneau-a-l-oseille_22719.aspx'),
    ('tasteofhome.com', 'https://www.tasteofhome.com/recipes/rhubarb-tart/'),
    ('thespruceeats.com', 'https://www.thespruceeats.com/creamy-potato-soup-with-ham-3059797'),
    ('tudogostoso.com.br', 'https://www.tudogostoso.com.br/receita/146568-arroz-com-bacalhau-tomate-e-ervas.html'),
    ('bbcgoodfood.com', 'https://www.bbcgoodfood.com/recipes/ultimate-tiramisu'),
    ('jamieoliver.com', 'https://www.jamieoliver.com/recipes/pasta-recipes/gennaro-s-classic-spaghetti-carbonara/'),
    ('seriouseats.com', 'https://www.seriouseats.com/pasta-allamatriciana-recipe'),
    ('epicurious.com', 'https://www.epicurious.com/recipes/food/views/spaghetti-aglio-e-olio-56389844'),
    ('bonappetit.com', 'https://www.bonappetit.com/recipe/simple-tomato-sauce'),
])


class MockRequest:
    def __init__(self):
        self.space = None
        self.user = MockUser()


class MockUser:
    def __init__(self):
        self.userpreference = MockUserPreference()


class MockUserPreference:
    def __init__(self):
        self.show_step_ingredients = True


def check_field_presence(recipe_json, field):
    if field not in recipe_json:
        return False
    value = recipe_json[field]
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == '':
        return False
    if isinstance(value, list) and len(value) == 0:
        return False
    if isinstance(value, dict) and len(value) == 0:
        return False
    return True


def get_domain(url):
    try:
        return get_host_name(url)
    except Exception:
        parsed = urlparse(url)
        return parsed.netloc


def test_url(url, timeout=30):
    result = {
        'url': url,
        'domain': get_domain(url),
        'success': False,
        'http_status': None,
        'fetch_time_ms': 0,
        'parse_time_ms': 0,
        'total_time_ms': 0,
        'fields': {},
        'field_completeness': 0,
        'error': None,
        'error_type': None,
        'scraper_class': None,
    }

    t_total_start = time.time()

    try:
        t_fetch_start = time.time()
        resp = safe_request('get', url, headers={
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'User-Agent': 'Mozilla/5.0 (compatible; TandoorRecipesHealthCheck/1.0)',
        }, timeout=timeout)
        t_fetch_end = time.time()
        result['fetch_time_ms'] = round((t_fetch_end - t_fetch_start) * 1000, 2)
        result['http_status'] = resp.status_code

        if not resp.ok:
            result['error'] = f'HTTP {resp.status_code}'
            result['error_type'] = 'HTTPError'
            result['total_time_ms'] = round((time.time() - t_total_start) * 1000, 2)
            return result

        html = resp.content

        t_parse_start = time.time()
        scrape = scrape_html(org_url=url, html=html, supported_only=False)
        result['scraper_class'] = scrape.__class__.__name__

        mock_request = MockRequest()
        recipe_json = get_from_scraper(scrape, mock_request)
        t_parse_end = time.time()
        result['parse_time_ms'] = round((t_parse_end - t_parse_start) * 1000, 2)

        present_fields = 0
        for field in FIELDS_TO_CHECK:
            present = check_field_presence(recipe_json, field)
            result['fields'][field] = present
            if present:
                present_fields += 1

        if 'steps' in result['fields'] and result['fields']['steps']:
            steps = recipe_json.get('steps', [])
            has_ingredients = any(
                len(s.get('ingredients', [])) > 0
                for s in steps
            )
            has_instructions = any(
                s.get('instruction', '').strip() != ''
                for s in steps
            )
            result['fields']['ingredients'] = has_ingredients
            result['fields']['instructions'] = has_instructions
            if has_ingredients:
                present_fields += 1
            if has_instructions:
                present_fields += 1

        total_checkable = len(FIELDS_TO_CHECK) + 2
        result['field_completeness'] = round((present_fields / total_checkable) * 100, 1)
        result['success'] = True

    except NoSchemaFoundInWildMode:
        result['error'] = 'No recipe schema found on page'
        result['error_type'] = 'NoSchemaFound'
    except requests.exceptions.ConnectionError as e:
        result['error'] = f'Connection failed: {str(e)[:100]}'
        result['error_type'] = 'ConnectionError'
    except requests.exceptions.Timeout as e:
        result['error'] = f'Request timed out after {timeout}s'
        result['error_type'] = 'Timeout'
    except requests.exceptions.RequestException as e:
        result['error'] = f'Request error: {str(e)[:100]}'
        result['error_type'] = 'RequestError'
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {str(e)[:150]}'
        result['error_type'] = type(e).__name__
        result['traceback'] = traceback.format_exc()

    result['total_time_ms'] = round((time.time() - t_total_start) * 1000, 2)
    return result


def print_text_report(results, stream=sys.stdout):
    total = len(results)
    successful = sum(1 for r in results if r['success'])
    failed = total - successful

    stream.write('\n')
    stream.write('=' * 100 + '\n')
    stream.write('External Recipe Scraper Health Check Report\n')
    stream.write('=' * 100 + '\n')
    stream.write('\n')
    stream.write(f'Summary: {successful}/{total} succeeded, {failed} failed\n')
    if total > 0:
        avg_fetch = round(sum(r['fetch_time_ms'] for r in results) / total, 2)
        avg_parse = round(sum(r['parse_time_ms'] for r in results if r['parse_time_ms'] > 0) / max(1, successful), 2)
        avg_total = round(sum(r['total_time_ms'] for r in results) / total, 2)
        avg_completeness = round(sum(r['field_completeness'] for r in results if r['success']) / max(1, successful), 1)
        stream.write(f'  Average fetch time: {avg_fetch:.2f} ms\n')
        stream.write(f'  Average parse time: {avg_parse:.2f} ms\n')
        stream.write(f'  Average total time: {avg_total:.2f} ms\n')
        stream.write(f'  Average field completeness: {avg_completeness:.1f}%\n')
    stream.write('\n')

    header = f"{'Domain':<30} {'Status':<10} {'Complete%':<10} {'Fetch(ms)':<12} {'Parse(ms)':<12} {'Total(ms)':<12} {'Error'}"
    stream.write(header + '\n')
    stream.write('-' * 100 + '\n')

    for r in results:
        status = 'OK' if r['success'] else 'FAIL'
        completeness = f"{r['field_completeness']}%" if r['success'] else 'N/A'
        error_display = r.get('error', '') or ''
        if len(error_display) > 35:
            error_display = error_display[:32] + '...'
        line = f"{r['domain'][:28]:<30} {status:<10} {completeness:<10} {r['fetch_time_ms']:<12} {r['parse_time_ms']:<12} {r['total_time_ms']:<12} {error_display}"
        stream.write(line + '\n')

    stream.write('\n')

    if any(not r['success'] for r in results):
        stream.write('=== Failure Details ===\n')
        for r in results:
            if not r['success']:
                stream.write(f"\n  [{r['domain']}] {r['url']}\n")
                stream.write(f"    Error Type: {r.get('error_type', 'Unknown')}\n")
                stream.write(f"    Message: {r.get('error', 'N/A')}\n")
                if r.get('http_status'):
                    stream.write(f"    HTTP Status: {r['http_status']}\n")

    stream.write('\n')

    if any(r['success'] for r in results):
        stream.write('=== Field Completeness Details ===\n')
        all_fields = FIELDS_TO_CHECK + ['ingredients', 'instructions']
        for r in results:
            if r['success']:
                field_status = []
                for f in all_fields:
                    present = r['fields'].get(f, False)
                    field_status.append(f"{'✓' if present else '✗'}{f}")
                stream.write(f"\n  [{r['domain']}] Scraper: {r.get('scraper_class', 'N/A')}\n")
                stream.write(f"    Fields: {' '.join(field_status)}\n")

    stream.write('\n')


class Command(BaseCommand):
    help = 'Check health of external recipe site scrapers by testing sample URLs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--url',
            action='append',
            dest='urls',
            default=None,
            help='Specific URL(s) to test. Can be specified multiple times.',
        )
        parser.add_argument(
            '--source',
            action='append',
            dest='sources',
            default=None,
            help='Specific source domain(s) to test from built-in list (e.g. "chefkoch.de"). Can be specified multiple times.',
        )
        parser.add_argument(
            '--all-sources',
            action='store_true',
            dest='all_sources',
            default=False,
            help='Test all built-in source URLs.',
        )
        parser.add_argument(
            '--list-sources',
            action='store_true',
            dest='list_sources',
            default=False,
            help='List all built-in source URLs and exit.',
        )
        parser.add_argument(
            '--timeout',
            type=int,
            dest='timeout',
            default=30,
            help='Timeout in seconds per URL request (default: 30).',
        )
        parser.add_argument(
            '--format',
            dest='output_format',
            choices=['text', 'json'],
            default='text',
            help='Output format: "text" (default) or "json".',
        )
        parser.add_argument(
            '--output',
            dest='output_file',
            default=None,
            help='Write output to file instead of stdout.',
        )
        parser.add_argument(
            '--fail-on-error',
            action='store_true',
            dest='fail_on_error',
            default=False,
            help='Exit with non-zero code if any scraper fails.',
        )

    def handle(self, *args, **options):
        if options['list_sources']:
            self.stdout.write('Available built-in source URLs:\n')
            for domain, url in BUILTIN_TEST_URLS.items():
                self.stdout.write(f'  {domain:<30} {url}')
            return

        urls_to_test = OrderedDict()

        if options['urls']:
            for u in options['urls']:
                urls_to_test[get_domain(u)] = u

        if options['sources']:
            for s in options['sources']:
                if s in BUILTIN_TEST_URLS:
                    urls_to_test[s] = BUILTIN_TEST_URLS[s]
                else:
                    raise CommandError(f'Unknown source "{s}". Use --list-sources to see available sources.')

        if options['all_sources'] or not urls_to_test:
            if not urls_to_test:
                self.stdout.write(self.style.WARNING('No URLs specified, testing all built-in sources...'))
            for domain, url in BUILTIN_TEST_URLS.items():
                if domain not in urls_to_test:
                    urls_to_test[domain] = url

        if not urls_to_test:
            raise CommandError('No URLs to test. Provide --url, --source, or --all-sources.')

        results = []
        total = len(urls_to_test)
        for idx, (domain, url) in enumerate(urls_to_test.items(), 1):
            self.stderr.write(f'[{idx}/{total}] Testing {domain}...')
            result = test_url(url, timeout=options['timeout'])
            results.append(result)
            status = self.style.SUCCESS('OK') if result['success'] else self.style.ERROR('FAIL')
            extra = ''
            if result['success']:
                extra = f" {result['field_completeness']:.1f}% complete, {result['total_time_ms']:.0f}ms"
            else:
                extra = f" {result.get('error', '')}"
            self.stderr.write(f'  {status}{extra}')

        if options['output_format'] == 'json':
            output = json.dumps({
                'summary': {
                    'total': len(results),
                    'successful': sum(1 for r in results if r['success']),
                    'failed': sum(1 for r in results if not r['success']),
                    'average_fetch_time_ms': round(sum(r['fetch_time_ms'] for r in results) / len(results), 2) if results else 0,
                    'average_parse_time_ms': round(sum(r['parse_time_ms'] for r in results if r['parse_time_ms'] > 0) / max(1, sum(1 for r in results if r['parse_time_ms'] > 0)), 2),
                    'average_total_time_ms': round(sum(r['total_time_ms'] for r in results) / len(results), 2) if results else 0,
                    'average_field_completeness': round(sum(r['field_completeness'] for r in results if r['success']) / max(1, sum(1 for r in results if r['success'])), 1),
                },
                'results': results,
            }, indent=2, ensure_ascii=False)
        else:
            from io import StringIO
            buf = StringIO()
            print_text_report(results, stream=buf)
            output = buf.getvalue()

        if options['output_file']:
            with open(options['output_file'], 'w', encoding='utf-8') as f:
                f.write(output)
            self.stdout.write(self.style.SUCCESS(f"Report written to {options['output_file']}"))
        else:
            self.stdout.write(output)

        if options['fail_on_error'] and any(not r['success'] for r in results):
            raise CommandError(f"{sum(1 for r in results if not r['success'])} scraper(s) failed.")
