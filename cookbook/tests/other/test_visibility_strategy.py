import uuid

import pytest
from django.contrib import auth
from django_scopes import scopes_disabled

from cookbook.helper.visibility_strategy import RecipeVisibilityStrategy
from cookbook.models import Recipe, ShareLink


@pytest.fixture
def public_recipe(space_1, u1_s1):
    with scopes_disabled():
        r = Recipe.objects.create(
            name='Public Recipe',
            waiting_time=20,
            working_time=20,
            servings=4,
            created_by=auth.get_user(u1_s1),
            space=space_1,
            internal=True,
            private=False,
        )
    return r


@pytest.fixture
def private_recipe(space_1, u1_s1):
    with scopes_disabled():
        r = Recipe.objects.create(
            name='Private Recipe',
            waiting_time=20,
            working_time=20,
            servings=4,
            created_by=auth.get_user(u1_s1),
            space=space_1,
            internal=True,
            private=True,
        )
    return r


@pytest.fixture
def shared_private_recipe(private_recipe, u2_s1, space_1):
    with scopes_disabled():
        private_recipe.shared.add(auth.get_user(u2_s1))
        private_recipe.save()
    return private_recipe


@pytest.fixture
def share_link(private_recipe, u1_s1, space_1):
    with scopes_disabled():
        sl = ShareLink.objects.create(
            recipe=private_recipe,
            space=space_1,
            uuid=str(uuid.uuid4()),
        )
    return sl


class TestRecipeVisibilityStrategyQueryset:
    """Test QuerySet-level visibility filtering."""

    def test_public_recipe_visible_to_all(self, public_recipe, u2_s1, space_1):
        """Public recipes are visible to any user in the same space."""
        strategy = RecipeVisibilityStrategy(auth.get_user(u2_s1), space_1)
        qs = strategy.filter_queryset(Recipe.objects.filter(space=space_1))
        assert public_recipe.id in [r.id for r in qs]

    def test_private_recipe_visible_only_to_owner(self, private_recipe, u1_s1, u2_s1, space_1):
        """Private recipes are visible only to their creator."""
        strategy_owner = RecipeVisibilityStrategy(auth.get_user(u1_s1), space_1)
        qs_owner = strategy_owner.filter_queryset(Recipe.objects.filter(space=space_1))
        assert private_recipe.id in [r.id for r in qs_owner]

        strategy_other = RecipeVisibilityStrategy(auth.get_user(u2_s1), space_1)
        qs_other = strategy_other.filter_queryset(Recipe.objects.filter(space=space_1))
        assert private_recipe.id not in [r.id for r in qs_other]

    def test_private_recipe_visible_to_shared_users(self, shared_private_recipe, u2_s1, space_1):
        """Private recipes are visible to users in the shared list."""
        strategy = RecipeVisibilityStrategy(auth.get_user(u2_s1), space_1)
        qs = strategy.filter_queryset(Recipe.objects.filter(space=space_1))
        assert shared_private_recipe.id in [r.id for r in qs]


class TestRecipeVisibilityStrategyObjectView:
    """Test object-level view permission checks."""

    def test_public_recipe_viewable_by_guest(self, public_recipe, g1_s1, space_1):
        """Guest users can view public recipes with safe methods."""
        strategy = RecipeVisibilityStrategy(auth.get_user(g1_s1), space_1)
        strategy._read_method_flag = True
        assert strategy.can_view(public_recipe) is True

    def test_public_recipe_viewable_by_user(self, public_recipe, u2_s1, space_1):
        """Regular users can view public recipes."""
        strategy = RecipeVisibilityStrategy(auth.get_user(u2_s1), space_1)
        assert strategy.can_view(public_recipe) is True

    def test_private_recipe_not_viewable_by_other_users(self, private_recipe, u2_s1, space_1):
        """Private recipes are not viewable by non-owner, non-shared users."""
        strategy = RecipeVisibilityStrategy(auth.get_user(u2_s1), space_1)
        assert strategy.can_view(private_recipe) is False

    def test_private_recipe_viewable_by_owner(self, private_recipe, u1_s1, space_1):
        """Private recipes are viewable by their owner."""
        strategy = RecipeVisibilityStrategy(auth.get_user(u1_s1), space_1)
        assert strategy.can_view(private_recipe) is True

    def test_private_recipe_viewable_by_shared_users(self, shared_private_recipe, u2_s1, space_1):
        """Private recipes are viewable by shared users."""
        strategy = RecipeVisibilityStrategy(auth.get_user(u2_s1), space_1)
        assert strategy.can_view(shared_private_recipe) is True


class TestRecipeVisibilityStrategyShareLink:
    """Test share_link special access path - the most critical validation."""

    def test_valid_share_link_grants_view_access(self, private_recipe, u1_s2, space_1, share_link):
        """A valid share link grants view access regardless of space or ownership."""
        strategy = RecipeVisibilityStrategy(
            user=auth.get_user(u1_s2),
            space=space_1,
            share_uuid=share_link.uuid,
        )
        assert strategy.can_view(private_recipe) is True

    def test_valid_share_link_works_with_guest_user(self, private_recipe, a_u, space_1, share_link):
        """Anonymous users with a valid share link can view the recipe."""
        strategy = RecipeVisibilityStrategy(
            user=auth.get_user(a_u),
            space=space_1,
            share_uuid=share_link.uuid,
        )
        assert strategy.can_view(private_recipe) is True

    def test_invalid_share_link_does_not_grant_access(self, private_recipe, u1_s2, space_1):
        """An invalid share link does NOT grant access."""
        strategy = RecipeVisibilityStrategy(
            user=auth.get_user(u1_s2),
            space=space_1,
            share_uuid=str(uuid.uuid4()),
        )
        assert strategy.can_view(private_recipe) is False

    def test_share_link_does_not_grant_edit_access(self, private_recipe, u1_s1, space_1, share_link):
        """Share link grants view-only, not edit."""
        strategy = RecipeVisibilityStrategy(
            user=auth.get_user(u1_s1),
            space=space_1,
            share_uuid=share_link.uuid,
        )
        assert strategy.can_view(private_recipe) is True
        assert strategy.can_edit(private_recipe) is False

    def test_share_link_does_not_grant_delete_access(self, private_recipe, u1_s1, space_1, share_link):
        """Share link grants view-only, not delete."""
        strategy = RecipeVisibilityStrategy(
            user=auth.get_user(u1_s1),
            space=space_1,
            share_uuid=share_link.uuid,
        )
        assert strategy.can_delete(private_recipe) is False

    def test_share_link_works_for_can_view_recipe_template_helper(self, private_recipe, a_u, space_1, share_link):
        """can_view_recipe helper (used by Django template views) also respects share links."""
        strategy = RecipeVisibilityStrategy(
            user=auth.get_user(a_u),
            space=space_1,
            share_uuid=share_link.uuid,
        )
        assert strategy.can_view_recipe(private_recipe) is True


class TestRecipeVisibilityStrategyGuestUser:
    """Test guest user special access path."""

    def test_guest_can_view_public_recipe_read_only(self, public_recipe, g1_s1, space_1):
        """Guest users with safe methods can view public recipes."""
        strategy = RecipeVisibilityStrategy(auth.get_user(g1_s1), space_1)
        strategy._read_method_flag = True
        assert strategy.can_view(public_recipe) is True
        assert strategy.can_list() is True

    def test_guest_cannot_edit_public_recipe(self, public_recipe, g1_s1, space_1):
        """Guest users cannot edit even public recipes."""
        strategy = RecipeVisibilityStrategy(auth.get_user(g1_s1), space_1)
        assert strategy.can_edit(public_recipe) is False

    def test_guest_cannot_delete_public_recipe(self, public_recipe, g1_s1, space_1):
        """Guest users cannot delete even public recipes."""
        strategy = RecipeVisibilityStrategy(auth.get_user(g1_s1), space_1)
        assert strategy.can_delete(public_recipe) is False

    def test_guest_cannot_view_private_recipe(self, private_recipe, g1_s1, space_1):
        """Guest users cannot view private recipes."""
        strategy = RecipeVisibilityStrategy(auth.get_user(g1_s1), space_1)
        strategy._read_method_flag = True
        assert strategy.can_view(private_recipe) is False

    def test_guest_with_share_link_can_view_private_recipe(self, private_recipe, g1_s1, space_1, share_link):
        """Guest users with a valid share link CAN view private recipes."""
        strategy = RecipeVisibilityStrategy(
            user=auth.get_user(g1_s1),
            space=space_1,
            share_uuid=share_link.uuid,
        )
        strategy._read_method_flag = True
        assert strategy.can_view(private_recipe) is True


class TestRecipeVisibilityStrategyEditDelete:
    """Test edit and delete permissions."""

    def test_public_recipe_editable_by_user(self, public_recipe, u2_s1, space_1):
        """Regular users can edit public recipes."""
        strategy = RecipeVisibilityStrategy(auth.get_user(u2_s1), space_1)
        assert strategy.can_edit(public_recipe) is True

    def test_private_recipe_editable_only_by_owner(self, private_recipe, u1_s1, u2_s1, space_1):
        """Private recipes can only be edited by their owner."""
        strategy_owner = RecipeVisibilityStrategy(auth.get_user(u1_s1), space_1)
        assert strategy_owner.can_edit(private_recipe) is True

        strategy_other = RecipeVisibilityStrategy(auth.get_user(u2_s1), space_1)
        assert strategy_other.can_edit(private_recipe) is False

    def test_shared_private_recipe_editable_by_shared_users(self, shared_private_recipe, u2_s1, space_1):
        """Private recipes can be edited by shared users."""
        strategy = RecipeVisibilityStrategy(auth.get_user(u2_s1), space_1)
        assert strategy.can_edit(shared_private_recipe) is True

    def test_private_recipe_deletable_only_by_owner(self, private_recipe, u1_s1, u2_s1, space_1):
        """Private recipes can only be deleted by their owner, not by shared users."""
        strategy_owner = RecipeVisibilityStrategy(auth.get_user(u1_s1), space_1)
        assert strategy_owner.can_delete(private_recipe) is True

        strategy_shared = RecipeVisibilityStrategy(auth.get_user(u2_s1), space_1)
        assert strategy_shared.can_delete(private_recipe) is False


class TestRecipeVisibilityStrategySpaceCheck:
    """Test cross-space access prevention."""

    def test_recipe_not_accessible_from_different_space(self, public_recipe, space_2, u1_s2):
        """Recipes are not accessible from a different space even for valid users."""
        strategy = RecipeVisibilityStrategy(auth.get_user(u1_s2), space_2)
        assert strategy.can_view(public_recipe) is False

    def test_cross_space_share_link_allowed(self, private_recipe, space_2, u1_s2, share_link):
        """Valid share links bypass space restrictions."""
        strategy = RecipeVisibilityStrategy(
            user=auth.get_user(u1_s2),
            space=space_2,
            share_uuid=share_link.uuid,
        )
        assert strategy.can_view(private_recipe) is True
