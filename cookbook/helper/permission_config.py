from cookbook.helper.permission_helper import (
    CustomIsAdmin, CustomIsUser, CustomIsGuest, has_group_permission,
)


def can_review_nutrition(user, space=None):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return has_group_permission(user, ['admin', 'user'])


def can_approve_nutrition(user, space=None):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return has_group_permission(user, ['admin'])


def can_flag_nutrition(user, space=None):
    if not user.is_authenticated:
        return False
    return has_group_permission(user, ['guest', 'user', 'admin'])


class PermissionConfig:
    BOOKS = {
        'owner': True,
        'groups': ['user'],
        'drf': [CustomIsUser],
    }

    NUTRITION_REVIEW = {
        'view': {
            'groups': ['guest'],
            'drf': [CustomIsGuest],
            'description': 'Can view nutrition review status and confidence scores',
        },
        'flag': {
            'groups': ['user'],
            'drf': [CustomIsUser],
            'description': 'Can flag nutrition data for review',
            'permission_fn': can_flag_nutrition,
        },
        'review': {
            'groups': ['user'],
            'drf': [CustomIsUser],
            'description': 'Can review nutrition data and leave comments',
            'permission_fn': can_review_nutrition,
        },
        'approve': {
            'groups': ['admin'],
            'drf': [CustomIsAdmin],
            'description': 'Can approve or reject nutrition data',
            'permission_fn': can_approve_nutrition,
        },
    }

    NUTRITION_ROLE_MATRIX = {
        'admin': {
            'view_review_status': True,
            'flag_for_review': True,
            'submit_review': True,
            'approve': True,
            'reject': True,
            'edit_nutrition': True,
            'override_auto_approval': True,
            'view_all_pending': True,
            'assign_reviewer': True,
        },
        'user': {
            'view_review_status': True,
            'flag_for_review': True,
            'submit_review': True,
            'approve': False,
            'reject': False,
            'edit_nutrition': True,
            'override_auto_approval': False,
            'view_all_pending': False,
            'assign_reviewer': False,
        },
        'guest': {
            'view_review_status': True,
            'flag_for_review': False,
            'submit_review': False,
            'approve': False,
            'reject': False,
            'edit_nutrition': False,
            'override_auto_approval': False,
            'view_all_pending': False,
            'assign_reviewer': False,
        },
    }
