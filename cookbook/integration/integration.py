import traceback
import uuid
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Callable, Iterable, List, Optional, Tuple
from zipfile import BadZipFile, ZipFile

from bs4 import Tag
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.files import File
from django.db import IntegrityError
from django.http import HttpResponse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django_scopes import scope
from lxml import etree

from cookbook.helper.image_processing import handle_image
from cookbook.models import Keyword, Recipe
from recipes.settings import DEBUG, EXPORT_FILE_CACHE_DURATION, MAX_ZIP_FILE_COUNT, MAX_ZIP_FILE_SIZE, MAX_ZIP_NESTING_DEPTH, MAX_ZIP_TOTAL_SIZE


ERROR_SEPARATOR = '--------------------'
ERROR_PREFIX = 'ERROR'
MSG_RECIPE_PROCESSED_TPL = '{pk} - {name} \n'
MSG_DUPLICATES_IGNORED = _('The following recipes were ignored because they already existed:')
MSG_IMPORTED_COUNT = _('Imported %s recipes.')
MSG_EXPECTED_ZIP = _('Importer expected a .zip file. Did you choose the correct importer type for your data ?')
MSG_UNEXPECTED_ERROR = _('An unexpected error occurred during the import. Please make sure you have uploaded a valid file.')
MSG_IMPORTING_FILE = 'IMPORTING {filename}'
MSG_NOT_IMPLEMENTED = 'Method not implemented in integration'


@dataclass
class ImportError:
    message: str
    exception: Optional[Exception] = None
    filename: Optional[str] = None

    def format(self) -> str:
        parts = [ERROR_SEPARATOR]
        if self.filename:
            parts.append(f'{ERROR_PREFIX} {MSG_IMPORTING_FILE.format(filename=self.filename)}')
        else:
            parts.append(ERROR_PREFIX)
        parts.append(str(self.message))
        parts.append(ERROR_SEPARATOR)
        return '\n'.join(parts) + '\n'


@dataclass
class ImportContext:
    files: List[dict] = field(default_factory=list)
    import_log: Any = None
    import_duplicates: bool = False
    import_meal_plans: bool = True
    import_shopping_lists: bool = True
    nutrition_per_serving: bool = False
    errors: List[ImportError] = field(default_factory=list)
    ignored_recipes: List[str] = field(default_factory=list)
    imported_count: int = 0

    def add_error(self, message: str, exception: Optional[Exception] = None, filename: Optional[str] = None) -> None:
        self.errors.append(ImportError(message=message, exception=exception, filename=filename))

    def get_error_messages(self) -> str:
        return ''.join(e.format() for e in self.errors)


class Integration:
    request = None
    keyword = None
    files = None
    export_type = None
    ignored_recipes = []
    import_log = None
    import_duplicates = False

    import_meal_plans = True
    import_shopping_lists = True
    nutrition_per_serving = False

    _ctx: Optional[ImportContext] = None

    ZIP_EXTENSIONS = ('.zip', '.paprikarecipes', '.mcb', '.rtk')
    SINGLE_FILE_EXTENSIONS = ('.json', '.xml', '.txt', '.mmf', '.rk', '.melarecipe')

    def __init__(self, request, export_type):
        """
        Integration for importing and exporting recipes
        :param request: request context of import session (used to link user to created objects)
        """
        self.request = request
        self.export_type = export_type
        self.ignored_recipes = []

        description = f'Imported by {request.user.get_user_display_name()} at {date_format(timezone.now(), "DATETIME_FORMAT")}. Type: {export_type}'

        try:
            last_kw = Keyword.objects.filter(name__regex=r'^(Import [0-9]+)', space=request.space).latest('created_at')
            name = f'Import {int(last_kw.name.replace("Import ", "")) + 1}'
        except (ObjectDoesNotExist, ValueError):
            name = 'Import 1'

        parent, created = Keyword.objects.get_or_create(name='Import', space=request.space)
        try:
            self.keyword = parent.add_child(name=name, description=description, space=request.space)
        except (IntegrityError, ValueError):  # in case, for whatever reason, the name does exist append UUID to it. Not nice but works for now.
            self.keyword = parent.add_child(name=f'{name} {str(uuid.uuid4())[0:8]}', description=description, space=request.space)

    def get_zip_file(self, file):
        """
        Safely open a zip file and check the total decompressed size and file count
        :param file: in memory file
        :return: ZipFile object
        """
        zip_file = ZipFile(file)
        if len(zip_file.infolist()) > MAX_ZIP_FILE_COUNT:
            raise Exception(_('Too many files in zip') + ' ' + str(len(zip_file.infolist())) + '/' + str(MAX_ZIP_FILE_COUNT))

        total_size = sum([z.file_size for z in zip_file.infolist()])
        if total_size > MAX_ZIP_TOTAL_SIZE:
            raise Exception(_('Zip file too large') + ' ' + str(total_size) + '/' + str(MAX_ZIP_TOTAL_SIZE))
        return zip_file

    def safe_read(self, zip_file, filename, depth=0):
        """
        Safely read a file from a zip file and check the decompressed size.
        If the file is a zip file, it also checks for nesting depth.
        :param zip_file: ZipFile object
        :param filename: filename or ZipInfo object
        :param depth: current nesting depth
        :return: bytes
        """
        if depth > MAX_ZIP_NESTING_DEPTH:
            raise Exception(_('Nested zip files too deep') + ' ' + str(depth) + '/' + str(MAX_ZIP_NESTING_DEPTH))

        if isinstance(filename, str):
            info = zip_file.getinfo(filename)
        else:
            info = filename

        if info.file_size > MAX_ZIP_FILE_SIZE:
            raise Exception(_('File in zip too large') + ' ' + str(info.file_size) + '/' + str(MAX_ZIP_FILE_SIZE) )

        return zip_file.read(info)

    def do_export(self, recipes, el):

        with scope(space=self.request.space):
            el.total_recipes = len(recipes)
            el.cache_duration = EXPORT_FILE_CACHE_DURATION
            el.save()

            files = self.get_files_from_recipes(recipes, el, self.request.COOKIES)

            if len(files) == 1:
                filename, file = files[0]
                export_filename = filename
                export_file = file

            else:
                # zip the files if there is more then one file
                export_filename = self.get_export_file_name()
                export_stream = BytesIO()
                export_obj = ZipFile(export_stream, 'w')

                for filename, file in files:
                    export_obj.writestr(filename, file)

                export_obj.close()
                export_file = export_stream.getvalue()

            cache.set('export_file_' + str(el.pk), {'filename': export_filename, 'file': export_file}, EXPORT_FILE_CACHE_DURATION)
            el.running = False
            el.save()

        response = HttpResponse(export_file, content_type='application/force-download')
        response['Content-Disposition'] = 'attachment; filename="' + export_filename + '"'
        return response

    def import_file_name_filter(self, zip_info_object):
        """
        Since zipfile.namelist() returns all files in all subdirectories this function allows filtering of files
        If false is returned the file will be ignored
        By default all files are included
        :param zip_info_object: ZipInfo object
        :return: Boolean if object should be included
        """
        return True

    # -------------------------------------------------------------------------
    # Unified Import Strategy Interface
    # -------------------------------------------------------------------------

    def _init_import_context(self, files, il, import_duplicates, meal_plans, shopping_lists, nutrition_per_serving) -> ImportContext:
        self._ctx = ImportContext(
            files=files,
            import_log=il,
            import_duplicates=import_duplicates,
            import_meal_plans=meal_plans,
            import_shopping_lists=shopping_lists,
            nutrition_per_serving=nutrition_per_serving,
        )
        self.import_log = il
        self.import_duplicates = import_duplicates
        self.import_meal_plans = meal_plans
        self.import_shopping_lists = shopping_lists
        self.nutrition_per_serving = nutrition_per_serving
        self.files = files
        return self._ctx

    def _detect_file_category(self, filename: str) -> str:
        if 'RecipeKeeper' in filename:
            return 'recipekeeper_zip'
        if filename.endswith('.rtk'):
            return 'rtk_zip'
        for ext in self.ZIP_EXTENSIONS:
            if ext in filename:
                return 'generic_zip'
        for ext in self.SINGLE_FILE_EXTENSIONS:
            if ext in filename:
                return 'single_data'
        return 'raw_file'

    def _process_recipe(self, data: Any, filename: Optional[str] = None) -> None:
        try:
            recipe = self.get_recipe_from_file(data)
            recipe.keywords.add(self.keyword)
            self._append_log(self._format_recipe_processed(recipe))
            self.handle_duplicates(recipe, self._ctx.import_duplicates)
            self._ctx.imported_recipes += 1
            self._flush_log()
        except Exception as e:
            self._handle_import_error(e, filename=filename)

    def _handle_import_error(self, exception: Exception, filename: Optional[str] = None) -> None:
        traceback.print_exc()
        self._ctx.add_error(message=str(exception), exception=exception, filename=filename)
        self._append_log(ImportError(message=str(exception), filename=filename).format())
        self.handle_exception(exception, log=self._ctx.import_log, message=ImportError(message=str(exception), filename=filename).format())

    def _format_recipe_processed(self, recipe) -> str:
        return MSG_RECIPE_PROCESSED_TPL.format(pk=recipe.pk, name=recipe.name)

    def _append_log(self, msg: str) -> None:
        self._ctx.import_log.msg += msg

    def _flush_log(self) -> None:
        self._ctx.import_log.imported_recipes = self._ctx.imported_recipes
        self._ctx.import_log.save()

    def _increment_total(self, count: int) -> None:
        self._ctx.import_log.total_recipes += count

    def _set_total(self, count: int) -> None:
        self._ctx.import_log.total_recipes = count

    def _log_info(self, message: str, persist: bool = True) -> None:
        self._append_log(message + '\n')
        if persist:
            self._ctx.import_log.save()

    def _log_warning(self, message: str, context: Optional[str] = None, persist: bool = False) -> None:
        prefix = 'WARN'
        if context:
            msg = f'{prefix} [{context}] {message}\n'
        else:
            msg = f'{prefix} {message}\n'
        self._append_log(msg)
        if persist:
            self._ctx.import_log.save()
        if DEBUG:
            print(msg.strip())

    def _log_error(self, message: str, exception: Optional[Exception] = None,
                   recipe_name: Optional[str] = None, persist: bool = False) -> None:
        if recipe_name:
            ctx_msg = f'{ERROR_PREFIX} [{recipe_name}] {message}'
        else:
            ctx_msg = f'{ERROR_PREFIX} {message}'
        if exception:
            ctx_msg += f'\n  Exception: {str(exception)}'
        self._append_log(ctx_msg + '\n')
        if self._ctx and exception:
            self._ctx.add_error(message=message, exception=exception, filename=recipe_name)
        if persist:
            self._ctx.import_log.save()
        if DEBUG:
            if exception:
                traceback.print_exc()

    def _import_recipe_safe(self, recipe_name: str, recipe: Recipe, image_file: Optional[Any] = None,
                            filetype: str = '.jpeg') -> bool:
        try:
            if image_file:
                self.import_recipe_image(recipe, image_file, filetype=filetype)
            return True
        except Exception as e:
            self._log_warning(f'failed to import image: {str(e)}', context=recipe_name)
            return False

    # --- Concrete file processors (strategy methods) --------------------------

    def _process_recipekeeper_zip(self, f: dict) -> None:
        import_zip = self.get_zip_file(f['file'])
        try:
            file_list = [z for z in import_zip.filelist if self.import_file_name_filter(z)]
            self._increment_total(len(file_list))
            for z in file_list:
                data_list = self.split_recipe_file(self.safe_read(import_zip, z.filename).decode('utf-8'))
                for d in data_list:
                    self._process_recipe(d, filename=z.filename)
        finally:
            import_zip.close()

    def _process_rtk_zip(self, f: dict) -> None:
        import_zip = self.get_zip_file(f['file'])
        try:
            for z in import_zip.filelist:
                if not self.import_file_name_filter(z):
                    continue
                data_list = self.split_recipe_file(self.safe_read(import_zip, z.filename).decode('utf-8'))
                self._increment_total(len(data_list))
                for d in data_list:
                    self._process_recipe(d, filename=z.filename)
        finally:
            import_zip.close()

    def _preprocess_generic_zip(self, import_zip: ZipFile, file_list: list) -> Tuple[list, bool]:
        import cookbook

        skip_iteration = False

        if isinstance(self, cookbook.integration.copymethat.CopyMeThat):
            file_list = self.split_recipe_file(BytesIO(self.safe_read(import_zip, 'recipes.html')))
            self._increment_total(len(file_list))

        if isinstance(self, cookbook.integration.cookmate.Cookmate):
            new_file_list = []
            for file in file_list:
                new_file_list += etree.parse(BytesIO(self.safe_read(import_zip, file.filename))).getroot().getchildren()
            self._set_total(len(new_file_list))
            file_list = new_file_list

        if isinstance(self, cookbook.integration.gourmet.Gourmet):
            self.import_zip = import_zip
            new_file_list = []
            for file in file_list:
                if file.file_size == 0:
                    continue
                if file.filename.startswith("index.htm"):
                    continue
                if file.filename.endswith(".htm"):
                    new_file_list += self.split_recipe_file(BytesIO(self.safe_read(import_zip, file.filename)))
            self._set_total(len(new_file_list))
            file_list = new_file_list

        if isinstance(self, cookbook.integration.mealie1.Mealie1):
            self.get_recipe_from_file(import_zip)
            skip_iteration = True

        return file_list, skip_iteration

    def _process_generic_zip(self, f: dict) -> None:
        import_zip = self.get_zip_file(f['file'])
        try:
            file_list = [z for z in import_zip.filelist if self.import_file_name_filter(z)]
            self._increment_total(len(file_list))

            file_list, skip_iteration = self._preprocess_generic_zip(import_zip, file_list)

            if not skip_iteration:
                for z in file_list:
                    try:
                        if not hasattr(z, 'filename') or isinstance(z, Tag):
                            recipe_data = z
                            fname = None
                        else:
                            recipe_data = BytesIO(self.safe_read(import_zip, z.filename))
                            fname = z.filename
                    except Exception as e:
                        self._handle_import_error(e, filename=getattr(z, 'filename', None))
                        continue
                    self._process_recipe(recipe_data, filename=fname)
        finally:
            import_zip.close()

    def _process_single_data(self, f: dict) -> None:
        data_list = self.split_recipe_file(f['file'])
        self._increment_total(len(data_list))
        for d in data_list:
            self._process_recipe(d, filename=f['name'])

    def _process_raw_file(self, f: dict) -> None:
        buffer = f['file']
        buffer.name = f['name']
        self._process_recipe(buffer, filename=f['name'])

    # --- Main orchestration ---------------------------------------------------

    def do_import(self, files, il, import_duplicates, meal_plans=True, shopping_lists=True, nutrition_per_serving=False):
        """
        Imports given files using the unified import strategy interface.

        Strategy pipeline:
          1. Initialize ImportContext
          2. For each file, detect category and dispatch to concrete processor
          3. Each processor iterates recipes, calling _process_recipe
          4. Collect errors, report duplicates, finalize log

        :param import_duplicates: if true duplicates are imported as well
        :param files: List of in memory files
        :param il: Import Log object to refresh while running
        :return: HttpResponseRedirect to the recipe search showing all imported recipes
        """
        PROCESSORS: dict = {
            'recipekeeper_zip': self._process_recipekeeper_zip,
            'generic_zip': self._process_generic_zip,
            'rtk_zip': self._process_rtk_zip,
            'single_data': self._process_single_data,
            'raw_file': self._process_raw_file,
        }

        with scope(space=self.request.space):
            ctx = self._init_import_context(files, il, import_duplicates, meal_plans, shopping_lists, nutrition_per_serving)

            try:
                for f in files:
                    category = self._detect_file_category(f['name'])
                    processor: Callable = PROCESSORS.get(category, self._process_raw_file)
                    processor(f)
            except BadZipFile:
                err_msg = f'{ERROR_PREFIX} {MSG_EXPECTED_ZIP}\n'
                ctx.add_error(message=MSG_EXPECTED_ZIP)
                self._append_log(err_msg)
            except Exception as e:
                msg = f'{ERROR_PREFIX} {MSG_UNEXPECTED_ERROR}\n{str(e)}\n'
                ctx.add_error(message=str(e), exception=e)
                self.handle_exception(e, log=ctx.import_log, message=msg)

            if len(self.ignored_recipes) > 0:
                self._append_log(f'\n{MSG_DUPLICATES_IGNORED} {", ".join(self.ignored_recipes)}\n\n')

            ctx.import_log.keyword = self.keyword
            self._append_log(f'{MSG_IMPORTED_COUNT % Recipe.objects.filter(keywords=self.keyword).count()}\n')
            ctx.import_log.running = False
            ctx.import_log.save()

    def handle_duplicates(self, recipe, import_duplicates):
        """
        Checks if a recipe is already present, if so deletes it
        :param recipe: Recipe object
        :param import_duplicates: if duplicates should be imported
        """
        if Recipe.objects.filter(space=self.request.space, name=recipe.name).count() > 1 and not import_duplicates:
            self.ignored_recipes.append(recipe.name)
            if self._ctx is not None:
                self._ctx.ignored_recipes.append(recipe.name)
            recipe.delete()

    def import_recipe_image(self, recipe, image_file, filetype='.jpeg'):
        """
        Adds an image to a recipe naming it correctly.
        Delegates image processing to handle_image helper.

        :param recipe: Recipe object
        :param image_file: ByteIO stream containing the image
        :param filetype: type of file to write bytes to, default to .jpeg if unknown
        """
        processed = handle_image(self.request, File(image_file, name='image'), filetype=filetype)
        if processed is not None:
            recipe.image = File(processed, name=f'{uuid.uuid4()}_{recipe.pk}{filetype}')
            recipe.save()

    # -------------------------------------------------------------------------
    # Abstract hooks for concrete integrations
    # -------------------------------------------------------------------------

    def get_recipe_from_file(self, file):
        """
        Takes any file like object and converts it into a recipe
        :param file: ByteIO or any file like object, depends on provider
        :return: Recipe object
        """
        raise NotImplementedError(MSG_NOT_IMPLEMENTED)

    def split_recipe_file(self, file):
        """
        Takes a file that contains multiple recipes and splits it into a list of strings of various formats (e.g. json, text, ..)
        :param file: ByteIO or any file like object, depends on provider
        :return: list of strings
        """
        raise NotImplementedError(MSG_NOT_IMPLEMENTED)

    def get_file_from_recipe(self, recipe):
        """
        Takes a recipe object and converts it to a string (depending on the format)
        returns both the filename of the exported file and the file contents
        :param recipe: Recipe object that should be converted
        :returns:
            - name - file name in export
            - data - string content for file to get created in export zip
        """
        raise NotImplementedError(MSG_NOT_IMPLEMENTED)

    def get_files_from_recipes(self, recipes, el, cookie):
        """
        Takes a list of recipe object and converts it to a array containing each file.
        Each file is represented as an array [filename, data] where data is a string of the content of the file.
        :param recipe: Recipe object that should be converted
        :returns:
            [[filename, data], ...]
        """
        raise NotImplementedError(MSG_NOT_IMPLEMENTED)

    @staticmethod
    def handle_exception(exception, log=None, message=''):
        if log:
            if message:
                log.msg += message
            else:
                log.msg += f'{ERROR_PREFIX} {getattr(exception, "msg", str(exception))}\n'
        if DEBUG:
            traceback.print_exc()

    def get_export_file_name(self, format='zip'):
        return "export_{}.{}".format(timezone.now().strftime("%Y-%m-%d"), format)

    def get_recipe_processed_msg(self, recipe):
        return self._format_recipe_processed(recipe)
