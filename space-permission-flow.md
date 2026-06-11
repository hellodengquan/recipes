# 多租户 Space 权限系统分析

## 一、核心数据模型

### 1.1 模型关系图

```
User (Django)
  │
  ├─ UserPreference (1:1) - 用户偏好设置（含 max_owned_spaces）
  │
  └─ UserSpace (1:N) - 用户与空间的关联表
       ├─ space (FK→Space)
       ├─ household (FK→Household, nullable)
       ├─ groups (M2M→Group) - 角色：guest/user/admin
       ├─ active (Boolean) - 当前激活的空间（唯一）
       └─ invite_link (FK→InviteLink)

Space
  ├─ created_by (FK→User, PROTECT) - 空间创建者/所有者
  ├─ Household (1:N) - 家庭组
  ├─ Recipe/Keyword/Food 等业务模型 (1:N)
  └─ created_at / max_recipes / max_users 等限制字段
```

### 1.2 核心模型定义

**Space 模型** [models.py:262-398](cookbook/models.py:262-L398)
- `created_by`: 空间所有者，使用 `PROTECT` 防止删除，**不可修改**（read_only）
- `max_recipes/max_users/max_file_storage_mb`: 空间配额限制
- `created_by` 在 `SpaceSerializer` 中标记为 `read_only=True`，意味着**没有空间转让功能**

**UserSpace 模型** [models.py:577-595](cookbook/models.py:577-L595)
- 多对多关联 `Group`（guest/user/admin），实现基于角色的权限控制
- `active` 字段：同一时间只能有一个激活的空间
- `household` 字段：支持家庭组共享

**Household 模型** [models.py:568-575](cookbook/models.py:568-L575)
- 同一空间内的用户分组，用于共享 MealPlan、ShoppingList 等

---

## 二、权限判定链路

### 2.1 整体调用链

```
HTTP Request
    ↓
ScopeMiddleware [scope_middleware.py:15-96]
    ├─ 确定当前用户的 active UserSpace
    ├─ 设置 request.space 和 request.user_space
    └─ 启用 django-scopes 上下文 with scope(space=request.space)
    ↓
DRF Permission Classes / Django Decorators
    ├─ has_group_permission() [permission_helper.py:37-66]
    │   ├─ 缓存检查（10秒）
    │   └─ 检查 UserSpace.groups
    ├─ is_object_owner() [permission_helper.py:69-83]
    ├─ is_space_owner() [permission_helper.py:86-98]
    ├─ is_object_shared() [permission_helper.py:101-113]
    └─ is_object_household() [permission_helper.py:116-127]
    ↓
业务逻辑（已自动过滤 space）
```

### 2.2 ScopeMiddleware 详解 [scope_middleware.py:15-96]

**核心职责**：请求进入时自动绑定空间上下文

```python
# 关键逻辑
def __call__(self, request):
    if request.user.is_authenticated:
        # 获取用户激活的 UserSpace
        user_space = request.user.userspace_set.filter(active=True).first()
        
        # 无激活空间时的兜底逻辑
        if not user_space and request.user.userspace_set.count() > 0:
            user_space = request.user.userspace_set.first()
            user_space.active = True
            user_space.save()
        
        # 完全没有空间则自动创建
        if not user_space:
            user_space = create_space_for_user(request.user)
        
        # 设置 request 上下文
        request.space = user_space.space
        request.user_space = user_space
        
        # 启用 django-scopes 隔离
        with scope(space=request.space):
            return self.get_response(request)
```

**特殊豁免路径**（跳过 scopes 隔离）：
- `/admin/` - Django 管理后台
- `/api/user-preference/` - 用户偏好（跨空间）
- 带 `share` 参数的 recipe GET 请求（共享链接访问）
- `/switch-space/` - 空间切换
- `/signup/`, `/invite/`, `/accounts/` - 认证相关

### 2.3 角色层级与权限继承 [permission_helper.py:22-34]

```
get_allowed_groups(['guest']) → ('guest', 'user', 'admin')
get_allowed_groups(['user'])  → ('user', 'admin')
get_allowed_groups(['admin']) → ('admin',)
```

**角色权限定义**：

| 角色   | 权限范围                                                                 |
|--------|--------------------------------------------------------------------------|
| guest  | 只读访问（GET/HEAD/OPTIONS），不能修改任何内容                           |
| user   | 可创建、修改自己的资源，可查看空间内公开资源                             |
| admin  | 空间管理员，可管理用户、修改空间设置、批量操作等（非所有者）             |
| owner  | 空间创建者（created_by），拥有最高权限，可删除空间、转让等（转让未实现） |

### 2.4 has_group_permission 核心判定 [permission_helper.py:37-66]

```python
def has_group_permission(user, groups, no_cache=False):
    # 1. 未认证直接拒绝
    if not user.is_authenticated:
        return False
    
    # 2. 获取继承后的允许角色列表
    groups_allowed = get_allowed_groups(groups)
    
    # 3. 缓存检查（见缓存机制章节）
    CACHE_KEY = hash(('has_group_permission', (user.pk, user.username, user.email), groups_allowed))
    
    # 4. 核心判定
    result = False
    if user.is_authenticated:
        # 获取激活的 UserSpace
        if user_space := user.userspace_set.filter(active=True):
            # ⚠️  关键限制：多于一个激活空间时直接返回 False
            # 目前不支持同时激活多个空间
            if len(user_space) != 1:
                result = False
            # 检查 UserSpace 的 groups 中是否包含允许的角色
            elif bool(user_space.first().groups.filter(name__in=groups_allowed)):
                result = True
    
    cache.set(CACHE_KEY, result, timeout=10)
    return result
```

### 2.5 所有权判定

**is_object_owner** [permission_helper.py:69-83]
- 检查 `obj.get_owner() == user` 或 `obj.get_owner() == 'orphan'`
- 通过 `PermissionModelMixin.get_owner()` 统一获取所有者

**is_space_owner** [permission_helper.py:86-98]
- 检查 `obj.get_space().get_owner() == user`
- 即检查用户是否是对象所属空间的创建者（created_by）

### 2.6 共享与家庭组判定

**is_object_shared** [permission_helper.py:101-113]
- 检查 `user in obj.get_shared()`
- 对象级别的共享（如 Recipe.shared 多对多字段）

**is_object_household** [permission_helper.py:116-127]
```python
def is_object_household(user, obj):
    return UserSpace.objects.filter(
        user=user, 
        space=obj.space, 
        household__in=obj.get_owner().userspace_set.values_list('household_id', flat=True)
    ).exists()
```
- 检查用户与对象所有者是否在同一家庭组

---

## 三、空间隔离机制

### 3.1 django-scopes 实现自动隔离

系统使用 `django-scopes` 库实现行级隔离：

**模型定义**：
```python
class Recipe(models.Model, PermissionModelMixin):
    space = models.ForeignKey(Space, on_delete=models.CASCADE)
    objects = ScopedManager(space='space')  # 自动添加 space 过滤
```

**Manager 行为**：
- 所有查询自动添加 `space=request.space` 条件
- 必须在 `scope(space=X)` 上下文中使用
- 无上下文时查询报错，防止数据泄露

### 3.2 API 层补充过滤

即使有 Manager 自动过滤，API 层仍有额外过滤：

**SpaceViewSet** [views/api.py:705-707]
```python
def get_queryset(self):
    return self.queryset.filter(
        id__in=UserSpace.objects.filter(user=self.request.user).values_list('space_id', flat=True)
    )
```
- 只返回用户有访问权限的空间

**UserSpaceViewSet** [views/api.py:747-751]
```python
def get_queryset(self):
    if has_group_permission(self.request.user, ['admin']):
        return self.queryset.filter(space=self.request.space)
    else:
        return self.queryset.filter(space=self.request.space, user=self.request.user)
```
- admin 看空间内所有用户，其他用户只能看到自己

### 3.3 业务对象查询过滤

**Recipe 查询** [views/api.py:1807-1811]
```python
self.queryset = self.queryset.filter(space=self.request.space).filter(
    Q(private=False) | (Q(private=True) & (Q(created_by=self.request.user) | Q(shared=self.request.user)))
)
```

**MealPlan 查询** [views/api.py:1497-1500]
```python
queryset = self.queryset.filter(
    Q(created_by=self.request.user) |
    Q(created_by_id__in=get_household_user_ids(self.request.user_space))
).filter(space=self.request.space).distinct().all()
```
- 家庭组成员可以互相查看 MealPlan

---

## 四、权限类使用模式

### 4.1 DRF 权限类组合

**SpaceViewSet** [views/api.py:701]
```python
permission_classes = [
    ((IsReadOnlyDRF | IsCreateDRF) & CustomIsGuest) |  # guest 可读+创建
    CustomIsOwner & CustomIsAdmin &                     # 所有者+管理员可修改
    CustomTokenHasReadWriteScope
]
```

**UserSpaceViewSet** [views/api.py:732]
```python
permission_classes = [
    (CustomIsSpaceOwner |                      # 空间所有者
     (IsReadOnlyDRF & CustomIsUser) |          # 普通用户只读
     CustomIsOwnerReadOnly |                   # 对象所有者只读
     CustomIsOwnerDestroyOnly) &               # 对象所有者可删除
    CustomTokenHasReadWriteScope
]
```

### 4.2 Django View 装饰器

```python
@group_required('admin')  # 装饰器方式
def my_view(request):
    ...

class MyView(GroupRequiredMixin, View):  # Mixin 方式
    groups_required = ['user']
    ...
```

---

## 五、空间转让流程分析

### 5.1 现状：**没有实现空间转让功能**

代码证据：

1. **`SpaceSerializer.created_by` 是只读的** [serializer.py:404,482]
   ```python
   class SpaceSerializer(WritableNestedModelSerializer):
       created_by = UserSerializer(read_only=True)
       ...
       read_only_fields = ('id', 'created_by', 'created_at', ...)
   ```

2. **`Space.created_by` 使用 `PROTECT` 约束** [models.py:309]
   ```python
   created_by = models.ForeignKey(User, on_delete=models.PROTECT, null=True)
   ```
   防止级联删除时意外删除空间，但同时也意味着不能直接修改

3. **UserSpace 删除时阻止所有者删除** [views/api.py:736-740]
   ```python
   def destroy(self, request, *args, **kwargs):
       userspace = UserSpace.objects.get(pk=kwargs['pk'])
       if userspace.space.created_by == userspace.user:
           raise APIException('Cannot delete Space owner permission.')
       return super().destroy(request, *args, **kwargs)
   ```

4. **没有 transfer 相关的 API 或代码**
   - 搜索 `transfer_space`、`transfer.*space`、`space.*transfer` 均无结果
   - `batch_update` 只能修改 `household` 和 `groups`，不能修改 `created_by`

### 5.2 空间所有权变更的替代方案

目前只能通过以下方式间接变更：
1. **超级用户在 Django Admin 中手动修改**
2. **删除原用户后重新分配**（但 PROTECT 约束会阻止）
3. **导出数据后在新空间导入**

### 5.3 实现空间转让需要补充的逻辑

如需实现转让功能，需要：
```python
# 1. 在 SpaceViewSet 添加 transfer action
@decorators.action(detail=True, methods=['POST'])
def transfer(self, request, pk=None):
    space = self.get_object()
    if space.created_by != request.user and not request.user.is_superuser:
        return Response({"msg": "No Permission"}, 403)
    
    new_owner_id = request.data.get('new_owner_id')
    new_owner = User.objects.get(pk=new_owner_id)
    
    # 检查新所有者是否在空间内
    if not UserSpace.objects.filter(space=space, user=new_owner).exists():
        return Response({"msg": "User not in space"}, 400)
    
    # 执行转让
    space.created_by = new_owner
    space.save()
    
    # 失效相关缓存
    cache.delete_pattern(f'has_group_permission*')
    
    return Response({"status": "success"})
```

---

## 六、缓存机制与失效策略

### 6.1 权限缓存 [permission_helper.py:37-66]

**缓存键**：
```python
CACHE_KEY = hash(('has_group_permission', (user.pk, user.username, user.email), groups_allowed))
```

**缓存时长**：10 秒

**缓存特性**：
- 基于用户标识 + 权限组组合生成唯一键
- 每次权限检查先查缓存，未命中才查数据库
- 提供 `no_cache` 参数绕过缓存

**存在的问题**：
- 使用 `hash()` 作为缓存键，Python 进程重启后 hash 种子变化，缓存失效
- 没有主动失效机制，权限变更后最多有 10 秒延迟

### 6.2 Household 缓存 [permission_helper.py:130-163]

**缓存键**：
```python
# 有家庭组
f'household_user_ids_{user_space.space_id}_{user_space.household_id}'
# 无家庭组（用户独立）
f'household_user_ids_{user_space.space_id}_user_{user_space.user_id}'
```

**缓存时长**：5 分钟

**主动失效机制** [signals.py:136-167]：

```python
# pre_save: 捕获旧的 household_id
@receiver(pre_save, sender=UserSpace)
def capture_old_household(sender, instance=None, **kwargs):
    if instance and instance.pk:
        instance._old_household_id = UserSpace.objects.filter(
            pk=instance.pk
        ).values_list('household_id', flat=True).first()

# post_save: 失效新旧家庭组缓存
@receiver(post_save, sender=UserSpace)
def invalidate_household_cache_on_save(sender, instance=None, **kwargs):
    # 失效新家庭组
    if instance.household_id:
        caches['default'].delete(f'household_user_ids_{instance.space_id}_{instance.household_id}')
    # 失效旧家庭组（如果变更了）
    old_household_id = getattr(instance, '_old_household_id', None)
    if old_household_id and old_household_id != instance.household_id:
        caches['default'].delete(f'household_user_ids_{instance.space_id}_{old_household_id}')
    # 失效用户独立缓存
    if not instance.household_id:
        caches['default'].delete(f'household_user_ids_{instance.space_id}_user_{instance.user_id}')

# post_delete: 失效被删除用户的家庭组缓存
@receiver(post_delete, sender=UserSpace)
def invalidate_household_cache_on_delete(sender, instance=None, **kwargs):
    if instance and instance.household_id:
        caches['default'].delete(f'household_user_ids_{instance.space_id}_{instance.household_id}')
```

### 6.3 ShareLink 缓存 [permission_helper.py:165-186]

**缓存键**：`recipe_share_{recipe.pk}_{share_uuid}`

**缓存时长**：3 秒

**作用**：防止短时间内大量重复请求导致的计数暴涨

### 6.4 其他缓存

**Unit 缓存** [signals.py:122-127]
- 键：`SPACE_{space.id}_BASE_UNITS`
- 失效：Unit post_save 信号

**PropertyType 缓存** [signals.py:130-133]
- 键：`SPACE_{space.id}_PROPERTY_TYPES`
- 失效：PropertyType post_save 信号

### 6.5 缓存失效时机汇总

| 缓存类型       | 自动失效时机                     | 手动失效方式               |
|----------------|----------------------------------|----------------------------|
| has_group_permission | 10 秒 TTL 自动失效 | 修改用户角色后需等待或重启 |
| household_user_ids | UserSpace save/delete 信号 | `invalidate_household_cache()` |
| recipe_share   | 3 秒 TTL 自动失效               | 无                         |
| BASE_UNITS     | Unit post_save                  | 无                         |
| PROPERTY_TYPES | PropertyType post_save          | 无                         |

**权限缓存的缺陷**：
- 当管理员修改用户角色（UserSpace.groups）时，权限缓存不会主动失效
- 用户需要等待最长 10 秒才能获得新权限
- 建议：在 `UserSpaceSerializer.update()` 中添加缓存失效逻辑

---

## 七、前端权限判断逻辑

### 7.1 前端权限数据来源 [UserPreferenceStore.ts]

**Store 数据结构**：
```typescript
// 用户所有空间权限
let userSpaces = useStorage(USER_SPACES_KEY, [] as UserSpace[])
// 当前激活空间
let activeSpace = useStorage(ACTIVE_SPACE_KEY, {} as Space)
// 当前激活的 UserSpace（计算属性）
let activeUserSpace: ComputedRef<null | UserSpace> = computed(() => {
    return userSpaces.value.find(us => us.space == activeSpace.value.id)
})
```

**加载时机**：
```typescript
function init() {
    const promises = [] as Promise<any>[]
    promises.push(loadUserSettings())      // /api/user-preference/
    promises.push(loadServerSettings())    // /api/server-settings/current/
    promises.push(loadActiveSpace())       // /api/space/current/
    promises.push(loadUserSpaces())        // /api/user-space/all-personal/
    promises.push(loadSpaces())            // /api/space/
    ...
}
```

### 7.2 前端权限判断模式

前端没有完整的权限判定逻辑，主要通过以下方式：

**模式 1：检查 activeUserSpace.groups**
```vue
<!-- 显示家庭组信息 -->
<v-chip v-if="useUserPreferenceStore().activeUserSpace?.household != null">
    {{ useUserPreferenceStore().activeUserSpace.household.name }}
</v-chip>
```

**模式 2：检查 created_by**
```vue
<!-- 判断是否是空间所有者 -->
<v-btn v-if="useUserPreferenceStore().activeSpace.created_by?.id === 
            useUserPreferenceStore().userSettings.user?.id">
    空间设置
</v-btn>
```

**模式 3：检查 groups 中是否包含 admin**
```vue
<!-- 在 ModelListPage 中显示 groups 列 -->
<template v-slot:item.groups="{ item }" v-if="genericModel.model.name == 'UserSpace'">
    {{ item.groups.flatMap((x: Group) => x.name).join(', ') }}
</template>
```

### 7.3 前端权限判断的问题

1. **没有统一的权限判断函数**
   - 散落在各组件中，代码重复
   - 建议封装 `usePermission()` composable

2. **权限判断逻辑不完整**
   - 前端只做简单显示控制，真正的权限校验在后端
   - 前端判断错误不会导致数据泄露，只会影响 UX

3. **缓存问题**
   - 权限数据存在 localStorage 中
   - 权限变更后需要调用 `loadUserSpaces()` 刷新
   - 目前在 `UserSpaceEditor` 的 `onAfterSave` 中调用了刷新

---

## 八、关键代码路径速查

### 8.1 用户加入空间流程
```
InviteLink 接受邀请
    ↓
views/views.py:view_invite()
    ↓
创建 UserSpace（绑定 invite_link 和 groups）
    ↓
signals.py:invalidate_household_cache_on_save()
    ↓
ScopeMiddleware 下次请求时激活新空间
```

### 8.2 切换空间流程
```
前端 switchSpace(space)
    ↓
/api/switch-active-space/{space_id}/ [views/api.py:3069-3079]
    ↓
switch_user_active_space() [permission_helper.py:521-538]
    ├─ 所有 UserSpace.active = False
    └─ 目标 UserSpace.active = True
    ↓
前端 reload 页面
    ↓
ScopeMiddleware 读取新的 active UserSpace
```

### 8.3 创建空间流程
```
前端提交 Space 表单
    ↓
SpaceSerializer.create() [serializer.py:441-450]
    ↓
create_space_for_user() [permission_helper.py:555-581]
    ├─ 创建 Space，created_by = request.user
    ├─ 创建 UserSpace，groups = ['admin']
    └─ 第一个空间自动激活
```

### 8.4 批量修改用户角色流程
```
UserSpaceViewSet.batch_update() [views/api.py:765-784]
    ↓
权限检查：request.space.created_by == request.user（只有所有者能批量改）
    ↓
更新 household 或 groups
    ↓
⚠️  注意：这里没有调用权限缓存失效！
    ↓
用户需要等待 10 秒缓存过期后权限才生效
```

---

## 九、已知问题与改进建议

### 9.1 已发现的问题

1. **权限缓存无主动失效**
   - 问题：修改 UserSpace.groups 后，`has_group_permission` 缓存不失效
   - 影响：用户最长 10 秒后才获得新权限
   - 位置：[views/api.py:765-784](cookbook/views/api.py:765-L784)
   - 建议：添加 `cache.delete_pattern(f'*{user_id}*')`

2. **空间转让功能缺失**
   - 问题：没有官方的空间所有权转让方式
   - 建议：添加 `SpaceViewSet.transfer` action

3. **前端权限判断分散**
   - 问题：没有统一的权限检查 composable
   - 建议：封装 `usePermission().hasRole('admin')`、`isSpaceOwner()` 等

4. **多空间同时激活支持不完善**
   - 问题：`has_group_permission` 中如果 `len(user_space) != 1` 直接返回 False
   - 注释也提到："needs to be changed when simultaneous multi-space-tenancy is added"

### 9.2 安全边界

| 层级 | 保障措施 | 失效后果 |
|------|----------|----------|
| Model 层 | `ScopedManager(space='space')` 自动过滤 | 最严重，跨空间数据泄露 |
| Middleware 层 | `with scope(space=request.space)` 上下文 | 严重，所有查询不过滤 |
| API 层 | `get_queryset()` 补充过滤 + 权限类 | 中等，部分接口可能泄露 |
| 前端 | UI 显示控制 | 轻微，仅影响 UX，后端仍校验 |

### 9.3 关键防护点

1. **ShareLink 跨空间访问** [scope_middleware.py:27-35]
   - 带 `share` 参数的 recipe 请求豁免 scopes 隔离
   - 但 `CustomRecipePermission` 会校验 share link 有效性
   - 无效 link 时检查 `obj.space != request.space` 则抛 404（防止枚举）

2. **空间创建者不能被删除** [views/api.py:736-740]
   - 防止误操作导致空间无主

3. **跨空间对象访问检查** [permission_helper.py:220-228]
   - `GroupRequiredMixin.dispatch()` 中检查 `obj.get_space() != request.space`
   - 防止通过修改 URL 访问其他空间对象
