import inspect
import threading

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.models import Group
from django.core.cache import cache, caches
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext as _
from django_scopes import scopes_disabled
from oauth2_provider.contrib.rest_framework import TokenHasReadWriteScope, TokenHasScope
from oauth2_provider.models import AccessToken
from oauth2_provider.settings import oauth2_settings
from rest_framework import permissions
from rest_framework.permissions import SAFE_METHODS
import random
from cookbook.models import Recipe, ShareLink, UserSpace, Space


PERMISSION_CACHE_VERSION_PREFIX = 'perm_cache_version_'
PERMISSION_CACHE_TTL = 10
PERMISSION_CACHE_LOCK_PREFIX = 'perm_cache_lock_'
PERMISSION_CACHE_LOCK_TIMEOUT = 5

_locmem_lock = threading.Lock()
_audit_actor_local = threading.local()


def _is_redis_backend():
    """Check if the default cache backend is Redis (cross-worker shared)."""
    try:
        backend = settings.CACHES.get('default', {}).get('BACKEND', '')
        return 'RedisCache' in backend or 'redis' in backend.lower()
    except Exception:
        return False


def _atomic_incr_version(version_key):
    """Atomically increment the permission cache version.

    - Redis backend: uses native cache.incr() which is atomic
    - LocMem backend: uses process-level threading.Lock to protect get+set
    - Returns the new version number after increment
    """
    if _is_redis_backend():
        try:
            try:
                new_version = cache.incr(version_key)
                return new_version
            except ValueError:
                cache.add(version_key, 2, timeout=None)
                return 2
        except Exception:
            pass

    with _locmem_lock:
        current = cache.get(version_key, 1)
        new_version = current + 1
        cache.set(version_key, new_version, timeout=None)
        return new_version


def set_audit_actor(actor_user):
    """Set the current request's actor for permission audit logging.

    Use this in API views before triggering a signal that creates an audit log.
    The actor is stored in thread-local storage, so it only affects the current thread.
    """
    _audit_actor_local.user = actor_user


def get_audit_actor():
    """Get the current actor from thread-local storage, or None."""
    return getattr(_audit_actor_local, 'user', None)


def clear_audit_actor():
    """Clear the thread-local actor."""
    _audit_actor_local.user = None


def _get_permission_cache_version(user_id, space_id):
    """Get the current permission cache version for a user in a specific space."""
    version_key = f'{PERMISSION_CACHE_VERSION_PREFIX}{space_id}_{user_id}'
    version = cache.get(version_key)
    if version is None:
        version = 1
        cache.set(version_key, version, timeout=None)
    return version


def invalidate_user_permission_cache(user_id, space_id=None):
    """Invalidate permission caches for a given user.

    If space_id is provided, only invalidate caches for that user+space.
    If space_id is None, invalidate caches across ALL spaces for that user.

    Uses atomic increment to ensure cross-worker consistency:
    - Redis: cache.incr() is atomic across all workers/processes
    - LocMem: threading.Lock protects get+set within same process
    """
    if not user_id:
        return
    try:
        if space_id is not None:
            version_key = f'{PERMISSION_CACHE_VERSION_PREFIX}{space_id}_{user_id}'
            _atomic_incr_version(version_key)
        else:
            from cookbook.models import UserSpace
            for us in UserSpace.objects.filter(user_id=user_id).only('space_id'):
                vk = f'{PERMISSION_CACHE_VERSION_PREFIX}{us.space_id}_{user_id}'
                _atomic_incr_version(vk)
    except Exception:
        pass


def get_allowed_groups(groups_required):
    """
    Builds a list of all groups equal or higher to the provided groups
    This means checking for guest will also allow admins to access
    :param groups_required: list or tuple of groups
    :return: tuple of groups
    """
    groups_allowed = tuple(groups_required)
    if 'guest' in groups_required:
        groups_allowed = groups_allowed + ('user', 'admin')
    if 'user' in groups_required:
        groups_allowed = groups_allowed + ('admin',)
    return groups_allowed


def has_group_permission(user, groups, no_cache=False):
    """
    Tests if a given user is member of a certain group (or any higher group)
    Superusers always bypass permission checks.
    Unauthenticated users can't be member of any group thus always return false.
    :param no_cache: (optional) do not return cached results, always check agains DB
    :param user: django auth user object
    :param groups: list or tuple of groups the user should be checked for
    :return: True if user is in allowed groups, false otherwise
    """
    if not user.is_authenticated:
        return False
    groups_allowed = get_allowed_groups(groups)

    user_spaces = user.userspace_set.filter(active=True)
    if len(user_spaces) != 1:
        return False

    user_space = user_spaces.first()
    space_id = user_space.space_id

    cache_version = _get_permission_cache_version(user.pk, space_id)
    CACHE_KEY = f'perm_{cache_version}_{inspect.stack()[0][3]}_{space_id}_{user.pk}_{hash(groups_allowed)}'

    if not no_cache:
        cached_result = cache.get(CACHE_KEY, default=None)
        if cached_result is not None:
            return cached_result

    result = bool(user_space.groups.filter(name__in=groups_allowed))

    cache.set(CACHE_KEY, result, timeout=PERMISSION_CACHE_TTL)
    return result


def is_object_owner(user, obj):
    """
    Tests if a given user is the owner of a given object
    test performed by checking user against the objects user
    and create_by field (if exists)
    :param user django auth user object
    :param obj any object that should be tested
    :return: true if user is owner of object, false otherwise
    """
    if not user.is_authenticated:
        return False
    try:
        return obj.get_owner() == 'orphan' or obj.get_owner() == user
    except Exception:
        return False


def is_space_owner(user, obj):
    """
    Tests if a given user is the owner the space of a given object
    :param user django auth user object
    :param obj any object that should be tested
    :return: true if user is owner of the objects space, false otherwise
    """
    if not user.is_authenticated:
        return False
    try:
        return obj.get_space().get_owner() == user
    except Exception:
        return False


def is_object_shared(user, obj):
    """
    Tests if a given user is shared for a given object
    test performed by checking user against the objects shared table
    :param user django auth user object
    :param obj any object that should be tested
    :return: true if user is shared for object, false otherwise
    """
    # TODO this could be improved/cleaned up by adding
    #      share checks for relevant objects
    if not user.is_authenticated:
        return False
    return user in obj.get_shared()


def is_object_household(user, obj):
    """
    Tests if a given user is in the same household as the owener of the given object
    :param user django auth user object
    :param obj any object that should be tested
    :return: true if user is in the same household for object, false otherwise
    """
    # TODO this could be improved/cleaned up by adding
    #      share checks for relevant objects
    if not user.is_authenticated:
        return False
    return UserSpace.objects.filter(user=user, space=obj.space, household__in=obj.get_owner().userspace_set.values_list('household_id', flat=True)).exists()


def get_household_user_ids(user_space):
    """
    Return user IDs sharing the same household, or just the user's own ID if no household.
    Results are cached for 5 minutes per space/household (or space/user if no household).
    :param user_space: UserSpace instance (e.g. request.user_space)
    :return: list of user IDs
    """
    if user_space is None:
        return []

    if user_space.household_id:
        cache_key = f'household_user_ids_{user_space.space_id}_{user_space.household_id}'
    else:
        cache_key = f'household_user_ids_{user_space.space_id}_user_{user_space.user_id}'

    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    if user_space.household_id:
        result = set(UserSpace.objects.filter(space=user_space.space, household=user_space.household).values_list('user_id', flat=True))
    else:
        result = {user_space.user_id}

    result = list(result)
    cache.set(cache_key, result, timeout=5 * 60)
    return result


def invalidate_household_cache(user_space):
    """Delete the cached household_user_ids for a UserSpace's household."""
    if user_space.household_id:
        cache.delete(f'household_user_ids_{user_space.space_id}_{user_space.household_id}')


def cache_delete_pattern(pattern, cache_backend=None):
    """
    Delete cache keys matching a glob pattern with cross-backend compatibility.

    - Redis / django.core.cache.backends.redis.RedisCache:
      Uses native cache.delete_pattern(pattern) which is O(N) on Redis server
      but atomic and efficient across all workers.
    - LocMemCache and other backends without delete_pattern support:
      Iterates over cache keys via the private store and deletes individually.
      This may be slow for large caches but is the only reliable way to
      avoid silent failure on non-Redis backends.

    :param pattern: Glob-style pattern (e.g. 'perm_cache_version_*')
    :param cache_backend: Optional cache instance; defaults to django.core.cache.cache
    :return: Number of keys deleted
    """
    import fnmatch
    import re

    if cache_backend is None:
        from django.core.cache import cache as default_cache
        cache_backend = default_cache

    deleted = 0

    try:
        is_redis = False
        try:
            from django.core.cache.backends.redis import RedisCache
            is_redis = isinstance(cache_backend, RedisCache)
        except Exception:
            is_redis = False

        if not is_redis:
            try:
                backend_full_name = f"{type(cache_backend).__module__}.{type(cache_backend).__name__}"
                if 'RedisCache' in backend_full_name or 'redis' in backend_full_name.lower():
                    is_redis = True
            except Exception:
                pass

        if not is_redis:
            is_redis = _is_redis_backend()

        if is_redis:
            try:
                cache_backend.delete_pattern(pattern)
                try:
                    matched = cache_backend.keys(pattern)
                    deleted = len(matched) if matched else 0
                except Exception:
                    deleted = 0
                return deleted
            except (AttributeError, NotImplementedError):
                pass

        from django.core.cache.backends.locmem import LocMemCache
        is_locmem = isinstance(cache_backend, LocMemCache)

        if is_locmem:
            raw_keys = list(cache_backend._cache.keys())
            version_prefix = getattr(cache_backend, 'make_key', None)
            try:
                sample_key = cache_backend.make_key('__probe__')
                prefix = sample_key[:sample_key.rfind('__probe__')]
            except Exception:
                prefix = ':1:'

            regex = re.compile(fnmatch.translate(pattern))
            matched_unprefixed = []
            for raw in raw_keys:
                if prefix and raw.startswith(prefix):
                    unprefixed = raw[len(prefix):]
                else:
                    unprefixed = raw
                if regex.match(unprefixed) or fnmatch.fnmatch(unprefixed, pattern):
                    matched_unprefixed.append(unprefixed)

            if matched_unprefixed:
                try:
                    cache_backend.delete_many(matched_unprefixed)
                    deleted = len(matched_unprefixed)
                except Exception:
                    for unprefixed in matched_unprefixed:
                        try:
                            cache_backend.delete(unprefixed)
                            deleted += 1
                        except Exception:
                            continue
            return deleted

        try:
            matched_keys = cache_backend.keys(pattern)
        except Exception:
            matched_keys = None

        if matched_keys:
            try:
                cache_backend.delete_many(matched_keys)
                deleted = len(matched_keys)
            except Exception:
                for key in matched_keys:
                    try:
                        cache_backend.delete(key)
                        deleted += 1
                    except Exception:
                        continue

        return deleted
    except Exception as e:
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("cache_delete_pattern failed for pattern %s: %s", pattern, e)
        except Exception:
            pass
        return 0


def invalidate_all_permission_caches_for_space(space_id, cache_backend=None):
    """
    Bulk-invalidate all permission caches for a given space.

    This is useful when an entire space is being deleted, or when a bulk
    operation removes many members at once. Clears:
    - perm_cache_version_{space_id}_*  (version counters)
    - perm_*_{space_id}_*              (individual permission results)

    Uses cache_delete_pattern, which works correctly on both Redis and LocMem.
    """
    deleted_count = 0
    deleted_count += cache_delete_pattern(
        f'{PERMISSION_CACHE_VERSION_PREFIX}{space_id}_*',
        cache_backend=cache_backend
    )
    deleted_count += cache_delete_pattern(
        f'perm_*_{space_id}_*',
        cache_backend=cache_backend
    )
    return deleted_count


def share_link_valid(recipe, share, user=None):
    """
    Verifies the validity of a share uuid.

    If a user is provided and the user is authenticated, also verifies that
    the user is still a member of the recipe's space. This prevents removed
    members from accessing recipes via cached/stale share links.

    :param recipe: recipe object
    :param share: share uuid
    :param user: optional user object to verify membership
    :return: true if a share link with the given recipe and uuid exists AND user has valid membership
    """
    try:
        CACHE_KEY = f'recipe_share_{recipe.pk}_{share}'
        if user and user.is_authenticated:
            CACHE_KEY = f'{CACHE_KEY}_{user.pk}'
        if c := cache.get(CACHE_KEY, False):
            return c

        with scopes_disabled():
            if link := ShareLink.objects.filter(recipe=recipe, uuid=share, abuse_blocked=False).first():
                if 0 < settings.SHARING_LIMIT < link.request_count and not link.space.no_sharing_limit:
                    return False

                if user and user.is_authenticated:
                    is_member = UserSpace.objects.filter(
                        user=user,
                        space_id=link.space_id,
                        active=True,
                    ).exists()
                    if not is_member:
                        cache.set(CACHE_KEY, False, timeout=3)
                        return False

                link.request_count += 1
                link.save()
                cache.set(CACHE_KEY, True, timeout=3)
                return True
        return False
    except ValidationError:
        return False


# Django Views

def group_required(*groups_required):
    """
    Decorator that tests the requesting user to be member
    of at least one of the provided groups or higher level groups
    :param groups_required: list of required groups
    :return: true if member of group, false otherwise
    """

    def in_groups(u):
        return has_group_permission(u, groups_required)

    return user_passes_test(in_groups, login_url='view_no_perm')


class GroupRequiredMixin(object):
    """
        groups_required - list of strings, required param
    """

    groups_required = None

    def dispatch(self, request, *args, **kwargs):
        if not has_group_permission(request.user, self.groups_required):
            if not request.user.is_authenticated:
                messages.add_message(request, messages.ERROR, _('You are not logged in and therefore cannot view this page!'))
                return HttpResponseRedirect(reverse_lazy('account_login') + '?next=' + request.path)
            else:
                messages.add_message(request, messages.ERROR, _('You do not have the required permissions to view this page!'))
                return HttpResponseRedirect(reverse_lazy('index'))
        try:
            obj = self.get_object()
            if obj.get_space() != request.space:
                messages.add_message(request, messages.ERROR, _('You do not have the required permissions to view this page!'))
                return HttpResponseRedirect(reverse_lazy('index'))
        except AttributeError:
            pass

        return super(GroupRequiredMixin, self).dispatch(request, *args, **kwargs)


class OwnerRequiredMixin(object):

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.add_message(request, messages.ERROR,
                                 _('You are not logged in and therefore cannot view this page!'))
            return HttpResponseRedirect(reverse_lazy('account_login') + '?next=' + request.path)
        else:
            if not is_object_owner(request.user, self.get_object()):
                messages.add_message(request, messages.ERROR,
                                     _('You cannot interact with this object as it is not owned by you!'))
                return HttpResponseRedirect(reverse('index'))

        try:
            obj = self.get_object()
            if not request.user.userspace.filter(space=obj.get_space()).exists():
                messages.add_message(request, messages.ERROR,
                                     _('You do not have the required permissions to view this page!'))
                return HttpResponseRedirect(reverse_lazy('index'))
        except AttributeError:
            pass

        return super(OwnerRequiredMixin, self).dispatch(request, *args, **kwargs)


# Django Rest Framework Permission classes

class CustomIsOwner(permissions.BasePermission):
    """
    Custom permission class for django rest framework views
    verifies user has ownership over object
    (either user or created_by or user is request user)
    """
    message = _('You cannot interact with this object as it is not owned by you!')

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        return is_object_owner(request.user, obj)


class CustomIsOwnerReadOnly(CustomIsOwner):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.method in SAFE_METHODS

    def has_object_permission(self, request, view, obj):
        return super().has_object_permission(request, view, obj) and request.method in SAFE_METHODS


class CustomIsOwnerDestroyOnly(CustomIsOwner):
    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.method == 'DELETE'

    def has_object_permission(self, request, view, obj):
        return super().has_object_permission(request, view, obj) and request.method == 'DELETE'


class CustomIsSpaceOwner(permissions.BasePermission):
    """
    Custom permission class for django rest framework views
    verifies if the user is the owner of the space the object belongs to
    """
    message = _('You cannot interact with this object as it is not owned by you!')

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.space.created_by == request.user

    def has_object_permission(self, request, view, obj):
        return is_space_owner(request.user, obj)


# TODO function duplicate/too similar name
class CustomIsShared(permissions.BasePermission):
    """
    Custom permission class for django rest framework views
    verifies user is shared for the object he is trying to access
    """
    message = _('You cannot interact with this object as it is not owned by you!')

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        return is_object_shared(request.user, obj)


class CustomIsHousehold(permissions.BasePermission):
    """
    Custom permission class for django rest framework views
    verifies user is in the same household as the object he is trying to access
    """
    message = _('You cannot interact with this object because you are not in the same household as the user who created it!')

    def has_permission(self, request, view):
        return request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        return is_object_household(request.user, obj)


class CustomIsGuest(permissions.BasePermission):
    """
    Custom permission class for django rest framework views
    verifies the user is member of at least the group: guest
    """
    message = _('You do not have the required permissions to view this page!')

    def has_permission(self, request, view):
        return has_group_permission(request.user, ['guest'])

    def has_object_permission(self, request, view, obj):
        return has_group_permission(request.user, ['guest'])


class CustomIsUser(permissions.BasePermission):
    """
    Custom permission class for django rest framework views
    verifies the user is member of at least the group: user
    """
    message = _('You do not have the required permissions to view this page!')

    def has_permission(self, request, view):
        return has_group_permission(request.user, ['user'])


class CustomIsAdmin(permissions.BasePermission):
    """
    Custom permission class for django rest framework views
    verifies the user is member of at least the group: admin
    """
    message = _('You do not have the required permissions to view this page!')

    def has_permission(self, request, view):
        return has_group_permission(request.user, ['admin'])


class CustomIsShare(permissions.BasePermission):
    """
    Custom permission class for django rest framework views
    verifies the requesting user provided a valid share link
    """
    message = _('You do not have the required permissions to view this page!')

    def has_permission(self, request, view):
        return request.method in SAFE_METHODS and 'pk' in view.kwargs

    def has_object_permission(self, request, view, obj):
        share = request.query_params.get('share', None)
        if share:
            return share_link_valid(obj, share, request.user)
        return False


class CustomRecipePermission(permissions.BasePermission):
    """
    Custom permission class for recipe api endpoint
    """
    message = _('You do not have the required permissions to view this page!')

    def has_permission(self, request, view):  # user is either at least a guest or a share link is given and the request is safe
        share = request.query_params.get('share', None)
        return ((has_group_permission(request.user, ['guest']) and request.method in SAFE_METHODS) or has_group_permission(
            request.user, ['user'])) or (share and request.method in SAFE_METHODS and 'pk' in view.kwargs)

    def has_object_permission(self, request, view, obj):
        share = request.query_params.get('share', None)
        if share:
            if share_link_valid(obj, share, request.user):
                return True
            # Invalid share link - check if user has normal access
            # If not, raise 404 to avoid leaking recipe existence
            if obj.space != request.space:
                raise Http404()
            # User is in same space, fall through to normal permission check
        if obj.private:
            return ((obj.created_by == request.user) or (request.user in obj.shared.all())) and obj.space == request.space
        else:
            return ((has_group_permission(request.user, ['guest']) and request.method in SAFE_METHODS)
                    or has_group_permission(request.user, ['user'])) and obj.space == request.space


class CustomAiProviderPermission(permissions.BasePermission):
    """
    Custom permission class for the AiProvider api endpoint
    users: can read all
    admins: can read and write
    superusers: can read and write + write providers without a space
    """
    message = _('You do not have the required permissions to view this page!')

    def has_permission(self, request, view):  # user is either at least a user and the request is safe
        return (has_group_permission(request.user, ['user']) and request.method in SAFE_METHODS) or (has_group_permission(request.user, ['admin']) or request.user.is_superuser)

    # editing of global providers allowed for superusers, space providers by admins and users can read only access
    def has_object_permission(self, request, view, obj):
        return ((obj.space is None and request.user.is_superuser)
                or (obj.space == request.space and has_group_permission(request.user, ['admin']))
                or (obj.space == request.space and has_group_permission(request.user, ['user']) and request.method in SAFE_METHODS))


class CustomUserPermission(permissions.BasePermission):
    """
    Custom permission class for user api endpoint
    """
    message = _('You do not have the required permissions to view this page!')

    def has_permission(self, request, view):  # a space filtered user list is visible for everyone
        return has_group_permission(request.user, ['guest'])

    def has_object_permission(self, request, view, obj):  # object write permissions are only available for user
        if request.method in SAFE_METHODS and 'pk' in view.kwargs and has_group_permission(request.user, ['guest']) and request.space in obj.userspace_set.all():
            return True
        elif request.user == obj:
            return True
        else:
            return False


class CustomTokenHasScope(TokenHasScope):
    """
    Custom implementation of Django OAuth Toolkit TokenHasScope class
    Only difference: if any other authentication method except OAuth2Authentication is used the scope check is ignored
    IMPORTANT: do not use this class without any other permission class as it will not check anything besides token scopes
    """

    def has_permission(self, request, view):
        if isinstance(request.auth, AccessToken):
            return super().has_permission(request, view)
        else:
            return request.user.is_authenticated


class CustomTokenHasReadWriteScope(TokenHasReadWriteScope):
    """
    Custom implementation of Django OAuth Toolkit TokenHasReadWriteScope class
    Only difference: if any other authentication method except OAuth2Authentication is used the scope check is ignored
    IMPORTANT: do not use this class without any other permission class as it will not check anything besides token scopes
    """

    def get_scopes(self, request, view):
        if request.method.upper() in SAFE_METHODS:
            read_write_scope = oauth2_settings.READ_SCOPE
        else:
            read_write_scope = oauth2_settings.WRITE_SCOPE

        return [read_write_scope]

    def has_permission(self, request, view):
        if isinstance(request.auth, AccessToken):
            return super().has_permission(request, view)
        else:
            return True


def above_space_limit(space):  # TODO add file storage limit
    """
    Test if the space has reached any limit (e.g. max recipes, users, ..)
    :param space: Space to test for limits
    :return: Tuple (True if above or equal any limit else false, message)
    """
    r_limit, r_msg = above_space_recipe_limit(space)
    u_limit, u_msg = above_space_user_limit(space)
    return r_limit or u_limit, (r_msg + ' ' + u_msg).strip()


def above_space_recipe_limit(space):
    """
    Test if a space has reached its recipe limit
    :param space: Space to test for limits
    :return: Tuple (True if above or equal limit else false, message)
    """
    limit = space.max_recipes != 0 and Recipe.objects.filter(space=space).count() >= space.max_recipes
    if limit:
        return True, _('You have reached the maximum number of recipes for your space.')
    return False, ''


def above_space_user_limit(space):
    """
    Test if a space has reached its user limit
    :param space: Space to test for limits
    :return: Tuple (True if above or equal limit else false, message)
    """
    limit = space.max_users != 0 and UserSpace.objects.filter(space=space).count() > space.max_users
    if limit:
        return True, _('You have more users than allowed in your space.')
    return False, ''


def switch_user_active_space(user, space):
    """
    Switch the currently active space of a user by setting all spaces to inactive and activating the one passed
    :param user: user to change active space for
    :param space: space to activate user for
    :return user space object or none if not found/no permission
    """
    try:
        us = UserSpace.objects.get(space=space, user=user)
        if not us.active:
            UserSpace.objects.filter(user=user).update(active=False)
            us.active = True
            us.save()
            return us
        else:
            return us
    except ObjectDoesNotExist:
        return None


class IsReadOnlyDRF(permissions.BasePermission):
    message = 'You cannot interact with this object as it is not owned by you!'

    def has_permission(self, request, view):
        return request.method in SAFE_METHODS


class IsCreateDRF(permissions.BasePermission):
    message = 'You cannot interact with this object, you can only create'

    def has_permission(self, request, view):
        return request.method == 'POST'


def create_space_for_user(user, name=None):
    with scopes_disabled():
        if not name:
            name = f"{user.username}'s Space"

        if Space.objects.filter(name=name).exists():
            name = f'{name} #{random.randrange(1, 10 ** 5)}'

        created_space = Space(name=name,
                              created_by=user,
                              max_file_storage_mb=settings.SPACE_DEFAULT_MAX_FILES,
                              max_recipes=settings.SPACE_DEFAULT_MAX_RECIPES,
                              max_users=settings.SPACE_DEFAULT_MAX_USERS,
                              allow_sharing=settings.SPACE_DEFAULT_ALLOW_SHARING,
                              ai_enabled=settings.SPACE_AI_ENABLED,
                              ai_credits_monthly=settings.SPACE_AI_CREDITS_MONTHLY,
                              space_setup_completed=False, )
        created_space.save()

        new_space_active = False
        if UserSpace.objects.filter(user=user).count() == 0:
            new_space_active = True

        user_space = UserSpace.objects.create(space=created_space, user=user, active=new_space_active)
        user_space.groups.add(Group.objects.filter(name='admin').get())

        return user_space


# ---------------------------------------------------------------------------
# Permission Audit Log
# ---------------------------------------------------------------------------

def log_permission_change(
    action,
    space_id,
    target_user,
    actor_user=None,
    old_groups=None,
    new_groups=None,
    old_household_id=None,
    new_household_id=None,
    message='',
):
    """
    Create a PermissionAuditLog entry.
    Always runs outside of django-scopes context to ensure writability.

    If actor_user is not provided, attempts to retrieve it from thread-local
    storage (set via set_audit_actor()).

    :param action: PermissionAuditLog.ACTION_* constant
    :param space_id: ID of the space the permission change applies to
    :param target_user: User object or user ID whose permissions changed
    :param actor_user: Optional User object or user ID who performed the action
    :param old_groups: Optional list of group names before change
    :param new_groups: Optional list of group names after change
    :param old_household_id: Optional household ID before change
    :param new_household_id: Optional household ID after change
    :param message: Optional additional information
    """
    from cookbook.models import PermissionAuditLog

    with scopes_disabled():
        try:
            target_user_id = target_user.pk if hasattr(target_user, 'pk') else target_user
            target_username = target_user.username if hasattr(target_user, 'username') else ''

            if actor_user is None:
                actor_user = get_audit_actor()

            actor_user_id = None
            actor_username = ''
            if actor_user is not None:
                actor_user_id = actor_user.pk if hasattr(actor_user, 'pk') else actor_user
                actor_username = actor_user.username if hasattr(actor_user, 'username') else ''

            PermissionAuditLog.objects.create(
                action=action,
                space_id=space_id,
                target_user_id=target_user_id,
                target_username=target_username,
                actor_user_id=actor_user_id,
                actor_username=actor_username,
                old_groups=old_groups or [],
                new_groups=new_groups or [],
                old_household_id=old_household_id,
                new_household_id=new_household_id,
                message=message,
            )
        except Exception:
            pass
