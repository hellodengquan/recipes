import io
import threading
from typing import Any, Optional, Tuple

from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _

from cookbook.forms import ExportForm, ImportExportBase
from cookbook.helper.permission_helper import group_required
from cookbook.helper.recipe_search import RecipeSearch
from cookbook.integration.cheftap import ChefTap
from cookbook.integration.chowdown import Chowdown
from cookbook.integration.cookbookapp import CookBookApp
from cookbook.integration.cooklang import Cooklang
from cookbook.integration.cookmate import Cookmate
from cookbook.integration.copymethat import CopyMeThat
from cookbook.integration.default import Default
from cookbook.integration.domestica import Domestica
from cookbook.integration.gourmet import Gourmet
from cookbook.integration.mealie import Mealie
from cookbook.integration.mealie1 import Mealie1
from cookbook.integration.mealmaster import MealMaster
from cookbook.integration.melarecipes import MelaRecipes
from cookbook.integration.nextcloud_cookbook import NextcloudCookbook
from cookbook.integration.openeats import OpenEats
from cookbook.integration.paprika import Paprika
# from cookbook.integration.pdfexport import PDFexport  # pyppeteer dependency removed
from cookbook.integration.pepperplate import Pepperplate
from cookbook.integration.plantoeat import Plantoeat
from cookbook.integration.recettetek import RecetteTek
from cookbook.integration.recipekeeper import RecipeKeeper
from cookbook.integration.recipesage import RecipeSage
from cookbook.integration.rezeptsuitede import Rezeptsuitede
from cookbook.integration.rezkonv import RezKonv
from cookbook.integration.saffron import Saffron
from cookbook.models import ExportLog, ImportLog, Recipe
from recipes import settings


MSG_PROVIDER_NOT_IMPLEMENTED = _('Importing is not implemented for this provider')
MSG_PDF_UNAVAILABLE = 'PDF export is no longer available. Use your browser\'s print function (Ctrl+P) to save recipes as PDF.'
MSG_ABOVE_SPACE_LIMIT = _('File is above space limit')


INTEGRATION_REGISTRY = {
    ImportExportBase.DEFAULT: Default,
    ImportExportBase.PAPRIKA: Paprika,
    ImportExportBase.NEXTCLOUD: NextcloudCookbook,
    ImportExportBase.MEALIE: Mealie,
    ImportExportBase.MEALIE1: Mealie1,
    ImportExportBase.CHOWDOWN: Chowdown,
    ImportExportBase.SAFFRON: Saffron,
    ImportExportBase.CHEFTAP: ChefTap,
    ImportExportBase.PEPPERPLATE: Pepperplate,
    ImportExportBase.DOMESTICA: Domestica,
    ImportExportBase.RECIPEKEEPER: RecipeKeeper,
    ImportExportBase.RECETTETEK: RecetteTek,
    ImportExportBase.RECIPESAGE: RecipeSage,
    ImportExportBase.REZKONV: RezKonv,
    ImportExportBase.MEALMASTER: MealMaster,
    ImportExportBase.OPENEATS: OpenEats,
    ImportExportBase.PLANTOEAT: Plantoeat,
    ImportExportBase.COOKBOOKAPP: CookBookApp,
    ImportExportBase.COOKLANG: Cooklang,
    ImportExportBase.COPYMETHAT: CopyMeThat,
    ImportExportBase.MELARECIPES: MelaRecipes,
    ImportExportBase.COOKMATE: Cookmate,
    ImportExportBase.REZEPTSUITEDE: Rezeptsuitede,
    ImportExportBase.GOURMET: Gourmet,
}


def get_integration(request, export_type):
    if export_type == ImportExportBase.PDF:
        raise NotImplementedError(MSG_PDF_UNAVAILABLE)
    cls = INTEGRATION_REGISTRY.get(export_type)
    if cls is None:
        raise NotImplementedError(MSG_PROVIDER_NOT_IMPLEMENTED)
    return cls(request, export_type)


def prepare_import_files(file_list) -> list:
    return [{'file': io.BytesIO(f.read()), 'name': f.name} for f in file_list]


def start_import_thread(integration, files, il, import_duplicates: bool,
                        meal_plans: bool = True, shopping_lists: bool = True,
                        nutrition_per_serving: bool = False) -> threading.Thread:
    kwargs = {}
    if meal_plans or shopping_lists or nutrition_per_serving:
        kwargs['meal_plans'] = meal_plans
        kwargs['shopping_lists'] = shopping_lists
        kwargs['nutrition_per_serving'] = nutrition_per_serving

    t = threading.Thread(
        target=integration.do_import,
        args=[files, il, import_duplicates],
        kwargs=kwargs,
    )
    t.setDaemon(True)
    t.start()
    return t


def launch_import(request, form) -> Tuple[bool, dict]:
    integration = get_integration(request, form.cleaned_data['type'])

    il = ImportLog.objects.create(
        type=form.cleaned_data['type'],
        created_by=request.user,
        space=request.space,
    )
    files = prepare_import_files(request.FILES.getlist('files'))
    start_import_thread(
        integration,
        files,
        il,
        form.cleaned_data['duplicates'],
        meal_plans=form.cleaned_data.get('meal_plans', True),
        shopping_lists=form.cleaned_data.get('shopping_lists', True),
        nutrition_per_serving=form.cleaned_data.get('nutrition_per_serving', False),
    )
    return True, {'import_id': il.pk}


@group_required('user')
def export_file(request, pk):
    el = get_object_or_404(ExportLog, pk=pk, space=request.space)

    cacheData = cache.get(f'export_file_{el.pk}')

    if cacheData is None:
        el.possibly_not_expired = False
        el.save()
        return JsonResponse({'msg': 'Export Expired or not found'}, status=404)

    response = HttpResponse(cacheData['file'], content_type='application/force-download')
    response['Content-Disposition'] = 'attachment; filename="' + cacheData['filename'] + '"'
    return response
