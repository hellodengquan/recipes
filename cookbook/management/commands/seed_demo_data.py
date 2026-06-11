from datetime import datetime, timedelta, timezone

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction
from django_scopes import scopes_disabled

from cookbook.models import (
    Food,
    Ingredient,
    Keyword,
    MealPlan,
    MealType,
    Recipe,
    RecipeBook,
    RecipeBookEntry,
    Space,
    Step,
    Unit,
    UserSpace,
)


DEMO_SPACES = [
    {
        'name': '家庭厨房',
        'users': [
            {'username': 'admin_home', 'password': 'test123', 'group': 'admin', 'first_name': '张', 'last_name': '大厨'},
            {'username': 'user_home', 'password': 'test123', 'group': 'user', 'first_name': '李', 'last_name': '小明'},
            {'username': 'guest_home', 'password': 'test123', 'group': 'guest', 'first_name': '王', 'last_name': '访客'},
        ],
        'keywords': ['家常菜', '快手菜', '下饭菜', '素菜', '荤菜', '汤品', '主食', '甜点'],
        'units': ['克', '千克', '毫升', '升', '个', '勺', '茶匙', '杯', '片', '适量'],
        'foods': [
            '西红柿', '鸡蛋', '猪肉', '牛肉', '鸡肉', '土豆', '胡萝卜', '洋葱',
            '大蒜', '生姜', '葱', '酱油', '盐', '糖', '醋', '料酒', '食用油',
            '大米', '面粉', '水',
        ],
        'meal_types': [
            {'name': '早餐', 'order': 1, 'color': '#FF9800'},
            {'name': '午餐', 'order': 2, 'color': '#4CAF50'},
            {'name': '晚餐', 'order': 3, 'color': '#2196F3'},
            {'name': '加餐', 'order': 4, 'color': '#9C27B0'},
        ],
        'recipes': [
            {
                'name': '西红柿炒鸡蛋',
                'description': '经典家常菜，酸甜可口，简单易做',
                'servings': 2,
                'working_time': 10,
                'waiting_time': 0,
                'keywords': ['家常菜', '快手菜', '素菜'],
                'steps': [
                    {
                        'name': '准备食材',
                        'instruction': '西红柿洗净切块，鸡蛋打散备用。',
                        'ingredients': [
                            {'food': '西红柿', 'amount': 200, 'unit': '克', 'note': '约2个'},
                            {'food': '鸡蛋', 'amount': 3, 'unit': '个', 'note': ''},
                            {'food': '食用油', 'amount': 2, 'unit': '勺', 'note': ''},
                            {'food': '盐', 'amount': 1, 'unit': '茶匙', 'note': ''},
                            {'food': '糖', 'amount': 1, 'unit': '茶匙', 'note': ''},
                        ],
                    },
                    {
                        'name': '炒鸡蛋',
                        'instruction': '热锅倒油，倒入蛋液，炒至凝固盛出备用。',
                        'ingredients': [],
                    },
                    {
                        'name': '炒西红柿',
                        'instruction': '锅中再放少许油，下西红柿翻炒出汁，加入盐和糖调味。',
                        'ingredients': [],
                    },
                    {
                        'name': '混合出锅',
                        'instruction': '倒入炒好的鸡蛋，翻炒均匀即可出锅。',
                        'ingredients': [],
                    },
                ],
            },
            {
                'name': '红烧肉',
                'description': '肥而不腻，入口即化的经典红烧肉',
                'servings': 4,
                'working_time': 30,
                'waiting_time': 60,
                'keywords': ['家常菜', '荤菜', '下饭菜'],
                'steps': [
                    {
                        'name': '准备食材',
                        'instruction': '五花肉切块，冷水下锅焯水去腥。',
                        'ingredients': [
                            {'food': '猪肉', 'amount': 500, 'unit': '克', 'note': '五花肉'},
                            {'food': '酱油', 'amount': 2, 'unit': '勺', 'note': ''},
                            {'food': '料酒', 'amount': 1, 'unit': '勺', 'note': ''},
                            {'food': '糖', 'amount': 1, 'unit': '勺', 'note': ''},
                            {'food': '生姜', 'amount': 3, 'unit': '片', 'note': ''},
                            {'food': '葱', 'amount': 2, 'unit': '根', 'note': ''},
                        ],
                    },
                    {
                        'name': '炒糖色',
                        'instruction': '锅中放油和糖，小火炒出焦糖色。',
                        'ingredients': [
                            {'food': '食用油', 'amount': 1, 'unit': '勺', 'note': ''},
                        ],
                    },
                    {
                        'name': '炖煮',
                        'instruction': '放入肉块翻炒上色，加酱油、料酒、葱姜和适量水，大火烧开后转小火炖60分钟。',
                        'ingredients': [
                            {'food': '水', 'amount': 500, 'unit': '毫升', 'note': ''},
                        ],
                    },
                    {
                        'name': '收汁',
                        'instruction': '大火收汁，汤汁浓稠即可出锅。',
                        'ingredients': [],
                    },
                ],
            },
            {
                'name': '土豆胡萝卜汤',
                'description': '营养丰富的家常蔬菜汤',
                'servings': 3,
                'working_time': 15,
                'waiting_time': 20,
                'keywords': ['汤品', '素菜', '家常菜'],
                'steps': [
                    {
                        'name': '准备食材',
                        'instruction': '土豆和胡萝卜去皮切滚刀块，洋葱切碎。',
                        'ingredients': [
                            {'food': '土豆', 'amount': 200, 'unit': '克', 'note': '约1个'},
                            {'food': '胡萝卜', 'amount': 150, 'unit': '克', 'note': '约1根'},
                            {'food': '洋葱', 'amount': 50, 'unit': '克', 'note': '半个'},
                            {'food': '盐', 'amount': 1, 'unit': '茶匙', 'note': ''},
                        ],
                    },
                    {
                        'name': '炒香',
                        'instruction': '锅中放油，下洋葱炒香。',
                        'ingredients': [
                            {'food': '食用油', 'amount': 1, 'unit': '勺', 'note': ''},
                        ],
                    },
                    {
                        'name': '煮汤',
                        'instruction': '加入土豆、胡萝卜翻炒，加水煮开后转小火煮20分钟。',
                        'ingredients': [
                            {'food': '水', 'amount': 800, 'unit': '毫升', 'note': ''},
                        ],
                    },
                    {
                        'name': '调味',
                        'instruction': '加盐调味，即可享用。',
                        'ingredients': [],
                    },
                ],
            },
        ],
        'recipe_books': [
            {
                'name': '新手入门菜谱',
                'description': '适合厨房新手的简单菜谱合集',
                'recipes': ['西红柿炒鸡蛋', '土豆胡萝卜汤'],
            },
            {
                'name': '硬菜大集合',
                'description': '宴客必备的硬核菜品',
                'recipes': ['红烧肉'],
            },
        ],
    },
    {
        'name': '专业烘焙',
        'users': [
            {'username': 'admin_bake', 'password': 'test123', 'group': 'admin', 'first_name': '陈', 'last_name': '烘焙师'},
            {'username': 'user_bake', 'password': 'test123', 'group': 'user', 'first_name': '刘', 'last_name': '学徒'},
        ],
        'keywords': ['烘焙', '甜点', '蛋糕', '面包', '饼干', '巧克力', '奶油', '入门级'],
        'units': ['克', '千克', '毫升', '个', '勺', '茶匙', '杯'],
        'foods': [
            '面粉', '黄油', '鸡蛋', '糖', '牛奶', '淡奶油', '巧克力', '酵母',
            '盐', '水', '食用油', '香草精',
        ],
        'meal_types': [
            {'name': '早餐', 'order': 1, 'color': '#FF9800'},
            {'name': '下午茶', 'order': 2, 'color': '#E91E63'},
            {'name': '甜点', 'order': 3, 'color': '#9C27B0'},
        ],
        'recipes': [
            {
                'name': '基础戚风蛋糕',
                'description': '松软绵密的基础款戚风蛋糕',
                'servings': 8,
                'working_time': 40,
                'waiting_time': 45,
                'keywords': ['烘焙', '蛋糕', '入门级'],
                'steps': [
                    {
                        'name': '准备食材',
                        'instruction': '蛋黄蛋白分离，面粉过筛备用。',
                        'ingredients': [
                            {'food': '鸡蛋', 'amount': 5, 'unit': '个', 'note': ''},
                            {'food': '面粉', 'amount': 85, 'unit': '克', 'note': '低筋面粉'},
                            {'food': '糖', 'amount': 60, 'unit': '克', 'note': '加入蛋白'},
                            {'food': '糖', 'amount': 30, 'unit': '克', 'note': '加入蛋黄'},
                            {'food': '牛奶', 'amount': 50, 'unit': '毫升', 'note': ''},
                            {'food': '食用油', 'amount': 40, 'unit': '毫升', 'note': ''},
                            {'food': '盐', 'amount': 1, 'unit': '克', 'note': ''},
                        ],
                    },
                    {
                        'name': '制作蛋黄糊',
                        'instruction': '蛋黄加糖打散，加入牛奶和油搅拌均匀，筛入面粉拌匀。',
                        'ingredients': [],
                    },
                    {
                        'name': '打发蛋白',
                        'instruction': '蛋白加盐打发至鱼眼泡，分三次加入糖，打至湿性发泡。',
                        'ingredients': [],
                    },
                    {
                        'name': '混合面糊',
                        'instruction': '取1/3蛋白霜加入蛋黄糊，翻拌均匀后倒回蛋白霜中，翻拌均匀。',
                        'ingredients': [],
                    },
                    {
                        'name': '烘烤',
                        'instruction': '倒入模具，震出气泡，150度烤45分钟。',
                        'ingredients': [],
                    },
                ],
            },
            {
                'name': '黄油曲奇',
                'description': '酥香可口的经典黄油曲奇',
                'servings': 20,
                'working_time': 25,
                'waiting_time': 15,
                'keywords': ['烘焙', '饼干', '甜点'],
                'steps': [
                    {
                        'name': '准备食材',
                        'instruction': '黄油软化，面粉过筛。',
                        'ingredients': [
                            {'food': '黄油', 'amount': 120, 'unit': '克', 'note': '室温软化'},
                            {'food': '糖', 'amount': 50, 'unit': '克', 'note': '糖粉'},
                            {'food': '鸡蛋', 'amount': 1, 'unit': '个', 'note': ''},
                            {'food': '面粉', 'amount': 200, 'unit': '克', 'note': '低筋面粉'},
                            {'food': '盐', 'amount': 1, 'unit': '克', 'note': ''},
                            {'food': '香草精', 'amount': 1, 'unit': '茶匙', 'note': ''},
                        ],
                    },
                    {
                        'name': '打发黄油',
                        'instruction': '软化黄油加糖粉和盐，打发至颜色变浅体积膨大。',
                        'ingredients': [],
                    },
                    {
                        'name': '加入蛋液',
                        'instruction': '分次加入蛋液和香草精，每次都打匀。',
                        'ingredients': [],
                    },
                    {
                        'name': '混合面团',
                        'instruction': '筛入面粉，用刮刀翻拌至无干粉。',
                        'ingredients': [],
                    },
                    {
                        'name': '烘烤',
                        'instruction': '装入裱花袋挤出花型，180度烤15分钟左右。',
                        'ingredients': [],
                    },
                ],
            },
        ],
        'recipe_books': [
            {
                'name': '烘焙入门',
                'description': '烘焙新手的第一本书',
                'recipes': ['基础戚风蛋糕', '黄油曲奇'],
            },
        ],
    },
]


class Command(BaseCommand):
    help = 'Seeds demo data with multiple spaces, users, cookbooks and meal plans (idempotent)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete existing demo data before seeding',
        )
        parser.add_argument(
            '--spaces',
            nargs='+',
            help='Only seed specific space names',
        )
        parser.add_argument(
            '--mealplan-days',
            type=int,
            default=7,
            help='Number of days to generate meal plans for (default: 7)',
        )

    def handle(self, *args, **options):
        with scopes_disabled(), transaction.atomic():
            self.stdout.write('Starting demo data seeding...')

            if options['reset']:
                self._reset_demo_data()
                self.stdout.write('  Demo data reset completed')

            guest_group = Group.objects.get_or_create(name='guest')[0]
            user_group = Group.objects.get_or_create(name='user')[0]
            admin_group = Group.objects.get_or_create(name='admin')[0]
            group_map = {'guest': guest_group, 'user': user_group, 'admin': admin_group}

            for space_config in DEMO_SPACES:
                if options['spaces'] and space_config['name'] not in options['spaces']:
                    continue

                self.stdout.write(f'\n=== Processing space: {space_config["name"]} ===')

                creator_username = space_config['users'][0]['username']
                creator_user = self._create_user(space_config['users'][0])

                space, space_created = Space.objects.get_or_create(
                    name=space_config['name'],
                    defaults={'created_by': creator_user},
                )
                if space_created:
                    self.stdout.write(f'  Created space: {space_config["name"]}')

                users = {}
                for user_config in space_config['users']:
                    user = self._create_user(user_config)
                    users[user_config['username']] = user
                    self._setup_user_space(user, space, group_map[user_config['group']])

                keywords = {}
                for kw_name in space_config['keywords']:
                    keyword = Keyword.objects.filter(name__iexact=kw_name, space=space).first()
                    if keyword is None:
                        keyword = Keyword.add_root(name=kw_name.strip(), space=space)
                        created = True
                    else:
                        created = False
                    keywords[kw_name] = keyword
                    if created:
                        self.stdout.write(f'  Created keyword: {kw_name}')

                units = {}
                for unit_name in space_config['units']:
                    unit, created = Unit.objects.get_or_create(
                        name=unit_name,
                        space=space,
                        defaults={'plural_name': unit_name},
                    )
                    units[unit_name] = unit

                foods = {}
                for food_name in space_config['foods']:
                    food = Food.objects.filter(name__iexact=food_name, space=space).first()
                    if food is None:
                        food = Food.add_root(name=food_name.strip(), space=space)
                    foods[food_name] = food

                meal_types = {}
                for mt_config in space_config['meal_types']:
                    meal_type, created = MealType.objects.get_or_create(
                        name=mt_config['name'],
                        space=space,
                        defaults={
                            'order': mt_config['order'],
                            'color': mt_config['color'],
                            'created_by': creator_user,
                        },
                    )
                    meal_types[mt_config['name']] = meal_type

                recipes = {}
                for recipe_config in space_config['recipes']:
                    recipe = self._create_recipe(
                        recipe_config,
                        space,
                        users[creator_username],
                        keywords,
                        foods,
                        units,
                    )
                    recipes[recipe_config['name']] = recipe

                for book_config in space_config['recipe_books']:
                    self._create_recipe_book(
                        book_config,
                        space,
                        users[creator_username],
                        recipes,
                    )

                self._create_meal_plans(
                    space,
                    users[creator_username],
                    recipes,
                    meal_types,
                    options['mealplan_days'],
                )

            self.stdout.write('\n✓ Demo data seeding completed successfully!')
            self.stdout.write('  Default passwords for all users: test123')

    def _reset_demo_data(self):
        demo_space_names = [s['name'] for s in DEMO_SPACES]
        demo_usernames = []
        for s in DEMO_SPACES:
            demo_usernames.extend([u['username'] for u in s['users']])

        for space_name in demo_space_names:
            try:
                space = Space.objects.get(name=space_name)
                space.safe_delete()
                self.stdout.write(f'  Deleted space: {space_name}')
            except Space.DoesNotExist:
                pass

        for username in demo_usernames:
            try:
                user = User.objects.get(username=username)
                user.delete()
                self.stdout.write(f'  Deleted user: {username}')
            except User.DoesNotExist:
                pass

    def _create_user(self, user_config):
        user, created = User.objects.get_or_create(
            username=user_config['username'],
            defaults={
                'first_name': user_config.get('first_name', ''),
                'last_name': user_config.get('last_name', ''),
                'email': f"{user_config['username']}@example.com",
            },
        )
        user.set_password(user_config['password'])
        user.save()
        if created:
            self.stdout.write(f'  Created user: {user_config["username"]}')
        return user

    def _setup_user_space(self, user, space, group):
        user_space, created = UserSpace.objects.get_or_create(
            user=user,
            space=space,
            defaults={'active': True},
        )
        if not user_space.groups.filter(pk=group.pk).exists():
            user_space.groups.add(group)
        if created:
            self.stdout.write(f'  Linked user {user.username} to space {space.name} as {group.name}')
        return user_space

    def _create_recipe(self, recipe_config, space, created_by, keywords, foods, units):
        recipe, created = Recipe.objects.get_or_create(
            name=recipe_config['name'],
            space=space,
            defaults={
                'description': recipe_config['description'],
                'servings': recipe_config['servings'],
                'working_time': recipe_config['working_time'],
                'waiting_time': recipe_config['waiting_time'],
                'created_by': created_by,
                'internal': False,
            },
        )

        if created:
            self.stdout.write(f'  Created recipe: {recipe_config["name"]}')
            self._populate_recipe_steps(recipe, recipe_config, space, keywords, foods, units)

            for kw_name in recipe_config['keywords']:
                if kw_name in keywords:
                    recipe.keywords.add(keywords[kw_name])

        return recipe

    def _populate_recipe_steps(self, recipe, recipe_config, space, keywords, foods, units):
        for i, step_config in enumerate(recipe_config['steps']):
            step = Step.objects.create(
                name=step_config['name'],
                instruction=step_config['instruction'],
                order=i,
                space=space,
            )
            recipe.steps.add(step)

            for ing_config in step_config['ingredients']:
                food = foods.get(ing_config['food'])
                unit = units.get(ing_config['unit'])
                if food:
                    ingredient = Ingredient.objects.create(
                        food=food,
                        unit=unit,
                        amount=ing_config['amount'],
                        note=ing_config.get('note', ''),
                        space=space,
                    )
                    step.ingredients.add(ingredient)

    def _create_recipe_book(self, book_config, space, created_by, recipes):
        book, created = RecipeBook.objects.get_or_create(
            name=book_config['name'],
            space=space,
            defaults={
                'description': book_config['description'],
                'created_by': created_by,
            },
        )

        if created:
            self.stdout.write(f'  Created recipe book: {book_config["name"]}')

        for recipe_name in book_config['recipes']:
            if recipe_name in recipes:
                RecipeBookEntry.objects.get_or_create(
                    book=book,
                    recipe=recipes[recipe_name],
                )

        return book

    def _create_meal_plans(self, space, created_by, recipes, meal_types, days=7):
        recipe_list = list(recipes.values())
        meal_type_list = list(meal_types.values())

        if not recipe_list or not meal_type_list:
            return

        today = datetime.now(timezone.utc).date()
        created_count = 0

        for day_offset in range(days):
            day = today + timedelta(days=day_offset)
            meal_count = min(len(meal_type_list), 3)

            for mt_idx in range(meal_count):
                meal_type = meal_type_list[mt_idx % len(meal_type_list)]
                recipe = recipe_list[(day_offset + mt_idx) % len(recipe_list)]

                from_dt = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
                to_dt = from_dt + timedelta(hours=2)

                _, created = MealPlan.objects.get_or_create(
                    recipe=recipe,
                    meal_type=meal_type,
                    from_date=from_dt,
                    to_date=to_dt,
                    space=space,
                    defaults={
                        'title': '',
                        'servings': recipe.servings,
                        'created_by': created_by,
                        'note': '',
                    },
                )
                if created:
                    created_count += 1

        if created_count > 0:
            self.stdout.write(f'  Created {created_count} meal plan entries for {days} days')
