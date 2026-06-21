from django.db.models import Q
from django_scopes import scopes_disabled
from rest_framework.permissions import SAFE_METHODS

from cookbook.helper.permission_helper import (
    has_group_permission,
    share_link_valid,
)


class RecipeVisibilityStrategy:
    """
    Centralized recipe visibility and permission clipping strategy.

    Provides a single source of truth for determining whether a recipe is
    visible to a given user/context.  Both the DRF permission classes and
    the view / queryset layer delegate to this module so that the rules
    stay consistent across API and template rendering paths.
    """

    def __init__(self, user, space, share_uuid=None):
        self.user = user
        self.space = space
        self.share_uuid = share_uuid

    # ------------------------------------------------------------------ #
    #  QuerySet-level visibility filter
    # ------------------------------------------------------------------ #

    def queryset_visibility_filter(self):
        """
        Return a ``Q`` object that, when applied to a Recipe queryset,
        restricts rows to those visible to *self.user*.

        Rule:
            A recipe is visible when **either**:
              - it is public (``private=False``), **or**
              - it is private **and** the user is the creator or is in the
                recipe's ``shared`` set.
        """
        return Q(private=False) | (
            Q(private=True) & (Q(created_by=self.user) | Q(shared=self.user))
        )

    def filter_queryset(self, queryset):
        """
        Apply the visibility filter to *queryset* and return the narrowed
        queryset.  Typically called after ``.filter(space=...)``.
        """
        return queryset.filter(self.queryset_visibility_filter())

    # ------------------------------------------------------------------ #
    #  Object-level permission checks
    # ------------------------------------------------------------------ #

    def can_view(self, recipe):
        """
        Return ``True`` when *recipe* can be viewed by *self.user*.

        Decision tree (evaluated in order):
          1. If a valid share link is provided → allow.
          2. If the recipe is private → only creator or shared users.
          3. If the recipe is public → any guest (read) or user-level member.
        """
        if self.share_uuid:
            with scopes_disabled():
                if share_link_valid(recipe, self.share_uuid):
                    return True

        if recipe.private:
            return (
                (recipe.created_by == self.user or self.user in recipe.shared.all())
                and recipe.space == self.space
            )

        return (
            (has_group_permission(self.user, ['guest']) and self._is_read_request())
            or has_group_permission(self.user, ['user'])
        ) and recipe.space == self.space

    def can_edit(self, recipe):
        """
        Return ``True`` when *recipe* can be modified by *self.user*.
        """
        if self.share_uuid:
            return False
        if recipe.private:
            return (
                recipe.created_by == self.user or self.user in recipe.shared.all()
            ) and recipe.space == self.space
        return has_group_permission(self.user, ['user']) and recipe.space == self.space

    def can_delete(self, recipe):
        """
        Return ``True`` when *recipe* can be deleted by *self.user*.
        """
        if self.share_uuid:
            return False
        if recipe.private:
            return recipe.created_by == self.user and recipe.space == self.space
        return has_group_permission(self.user, ['user']) and recipe.space == self.space

    # ------------------------------------------------------------------ #
    #  List-level (non-object) permission checks
    # ------------------------------------------------------------------ #

    def can_list(self):
        """
        Return ``True`` when the user is allowed to list recipes at all.

        A user can list when **either**:
          - they have at least ``guest`` group AND the request is a safe
            method, **or**
          - they have at least ``user`` group.
          - OR they provide a valid share link with a safe method for a
            specific recipe detail.
        """
        return (
            (has_group_permission(self.user, ['guest']) and self._is_read_request())
            or has_group_permission(self.user, ['user'])
            or (bool(self.share_uuid) and self._is_read_request())
        )

    # ------------------------------------------------------------------ #
    #  Template / Django-view helpers
    # ------------------------------------------------------------------ #

    def can_view_recipe(self, recipe):
        """
        Convenience wrapper used by Django template views (e.g. PDF viewer).

        Returns ``True`` when the recipe can be accessed via share link
        **or** the user has at least guest-level membership in the recipe's
        space.
        """
        if self.share_uuid:
            with scopes_disabled():
                if share_link_valid(recipe, self.share_uuid):
                    return True
        return (
            has_group_permission(self.user, ['guest'])
            and recipe.space == self.space
        )

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    _read_method_flag = True

    def _is_read_request(self):
        return self._read_method_flag

    @classmethod
    def from_request(cls, request):
        """
        Factory that builds a strategy from a standard Django/DRF request
        object, extracting *user*, *space*, and optional *share* query
        parameter.
        """
        share = request.query_params.get('share', None) if hasattr(request, 'query_params') else request.GET.get('share', None)
        strat = cls(user=request.user, space=getattr(request, 'space', None), share_uuid=share)
        strat._read_method_flag = request.method in SAFE_METHODS
        return strat
