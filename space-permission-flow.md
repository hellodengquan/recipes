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

---

#### 2.2.1 关键薄弱路径：带 `share` 参数的 Recipe GET 请求

**豁免条件** [scope_middleware.py:27-35](cookbook/helper/scope_middleware.py:27)：
```python
if (request.GET.get('share')
        and re.match(rf'^{re.escape(prefix)}/api/recipe/\d+/?$', request.path)
        and request.method in ('GET', 'HEAD', 'OPTIONS')):
    with scopes_disabled():
        request.space = None
        return self.get_response(request)
```

**豁免行为**：
1. 跳过 `scope(space=X)` 上下文，直接进入 `scopes_disabled()`
2. `request.space = None` — 不绑定任何空间
3. 匿名用户也可以访问（因为走的是 scope_middleware.py:81-96 的未认证分支，也是 `scopes_disabled()`）

##### 完整请求链路分析

```
GET /api/recipe/42/?share=550e8400-e29b-41d4-a716-446655440000
    ↓
ScopeMiddleware
    ├─ 命中 share 豁免条件
    ├─ request.space = None
    └─ with scopes_disabled():  ← ⚠️  所有查询不受 space 隔离
        ↓
DRF 路由 → RecipeViewSet.retrieve()
    ↓
RecipeViewSet.get_queryset() [views/api.py:1771-1806]
    ├─ self.detail = True
    ├─ share = '550e8400-...' 存在
    └─ ❗  不做 space 过滤！直接返回 Recipe.objects.all()
        ↓
DRF get_object() → 拿到 recipe_id=42 的 Recipe 对象（可能来自任何空间）
    ↓
CustomRecipePermission.has_object_permission() [permission_helper.py:396-410]
    ├─ share 参数存在 → 调用 share_link_valid(obj, share)
    └─ 校验通过则 return True
    ↓
RecipeSerializer → 返回完整 Recipe 数据（含步骤、食材等）
```

##### 匿名用户实际能摸到的数据边界

| 场景 | 代码判定 | 结果 |
|------|---------|------|
| share link 有效（recipe+uuid 匹配，未封禁） | `share_link_valid` → True | ✅ 返回完整 recipe，**包括 private=True 的私有食谱** |
| share link 无效但用户在同一空间 | `obj.space == request.space` 比较，但 `request.space = None` → `obj.space != None` → raise Http404 | ❌ 404（防止枚举） |
| share link 无效且用户不在该空间 | `obj.space != request.space` → raise Http404 | ❌ 404（防止枚举） |
| recipe 不存在（id 乱猜） | DRF get_object 查不到 | ❌ 404 |

**关键点**：`share_link_valid(obj, share)` 同时校验 `recipe` 和 `uuid`，即使 `scopes_disabled()` 下可以查所有空间的 ShareLink，也需要恰好匹配 recipe_id + uuid 才能通过。

##### 分享参数有效性校验

**share_link_valid 实现** [permission_helper.py:165-186](cookbook/helper/permission_helper.py:165)：
```python
def share_link_valid(recipe, share):
    CACHE_KEY = f'recipe_share_{recipe.pk}_{share}'
    if c := cache.get(CACHE_KEY, False):
        return c  # 3 秒缓存
    
    # ⚠️  注意：这里也在 scopes_disabled() 上下文中执行
    # ShareLink 有 ScopedManager(space='space')，但 scopes_disabled 下查全表
    if link := ShareLink.objects.filter(
        recipe=recipe, 
        uuid=share, 
        abuse_blocked=False
    ).first():
        # 访问次数限制
        if 0 < settings.SHARING_LIMIT < link.request_count and not link.space.no_sharing_limit:
            return False
        link.request_count += 1
        link.save()
        cache.set(CACHE_KEY, True, timeout=3)
        return True
    return False
```

**ShareLink 模型** [models.py:1430-1445](cookbook/models.py:1430)：
```python
class ShareLink(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE)
    uuid = models.UUIDField(default=uuid.uuid4)  # UUID v4，122 位熵
    request_count = models.IntegerField(default=0)
    abuse_blocked = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    space = models.ForeignKey(Space, on_delete=models.CASCADE)
    objects = ScopedManager(space='space')  # 正常上下文受隔离
```

##### 分享参数的可猜测/可篡改分析

| 攻击方式 | 可行性 | 说明 |
|---------|--------|------|
| 暴力枚举 UUID | ❌ 不可行 | UUID v4 有 122 位随机熵，枚举概率可以忽略 |
| 修改 recipe_id 保持 uuid | ❌ 不可行 | `ShareLink.objects.filter(recipe=recipe, uuid=share)` 同时匹配两者 |
| 修改 uuid 保持 recipe_id | ❌ 不可行 | 同上，需要同时匹配 |
| 猜测已存在的 share link | ❌ 不可行 | 需要同时知道 recipe_id 和 uuid |
| 篡改已分享链接的访问权限 | ❌ 不可行 | 只读 GET 请求，不能修改 ShareLink |
| 绕过访问次数限制 | ⚠️  有 3 秒缓存窗口 | 3 秒内重复请求不计入 request_count（但已分享的内容也不会变化） |

**安全边界总结**：
- ✅ 私有食谱（private=True）通过有效分享链接可以正常访问（设计预期）
- ✅ 无效分享链接返回 404，不泄漏食谱是否存在
- ✅ UUID 不可猜测，recipe_id+uuid 双重绑定防篡改
- ⚠️  scopes_disabled 下 ShareLink 查询全表，但不构成实际风险（因为需要同时匹配 recipe+uuid）
- ⚠️  `request.space = None` 导致 `obj.space != request.space` 恒成立，所以分享链接无效时无法 fallback 到同空间正常权限（但返回 404 也是合理的）

---

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

---

#### 6.2.1 家庭组缓存失效一致性分析：写入 vs 删除

**疑问**：成员增减时按空间和家庭组拼键去删，删除时机和键的拼法跟写入侧是不是严丝合缝？会不会有过期缓存残留？

##### 写入侧键拼法 [permission_helper.py:130-156]

```python
def get_household_user_ids(user_space):
    if user_space.household_id:
        # Case A: 有家庭组 → 按家庭组聚合
        cache_key = f'household_user_ids_{user_space.space_id}_{user_space.household_id}'
        result = set(UserSpace.objects.filter(
            space=user_space.space, 
            household=user_space.household
        ).values_list('user_id', flat=True))
    else:
        # Case B: 无家庭组 → 用户独立缓存
        cache_key = f'household_user_ids_{user_space.space_id}_user_{user_space.user_id}'
        result = {user_space.user_id}
    
    cache.set(cache_key, result, timeout=5 * 60)
    return result
```

| 场景 | 键模式 | 缓存内容 |
|------|--------|---------|
| 用户 U 在家庭组 H | `household_user_ids_{S}_{H}` | [U1_id, U2_id, U3_id, ...] |
| 用户 U 不在任何家庭组 | `household_user_ids_{S}_user_{U}` | [U_id] |

##### 删除侧键拼法对比

让我们逐个信号场景对照：

| 触发场景 | 删除的键 | 对应写入场景 | 是否匹配 |
|---------|---------|------------|---------|
| **post_save**, household_id=H (非空) | `household_user_ids_{S}_{H}` | Case A | ✅ 匹配 |
| **post_save**, old_household_id=H_old (变化了) | `household_user_ids_{S}_{H_old}` | Case A（旧的） | ✅ 匹配 |
| **post_save**, household_id=None (空) | `household_user_ids_{S}_user_{U_id}` | Case B | ✅ 匹配 |
| **post_delete**, household_id=H (非空) | `household_user_ids_{S}_{H}` | Case A | ✅ 匹配 |
| **post_delete**, household_id=None (空) | **无！不删除任何键** | Case B | ❌ **遗漏** |

##### 五种成员增减场景的逐一场景分析

**场景 1：新用户加入空间，指定家庭组 H**
```
UserSpace.objects.create(space=S, user=U_new, household=H, ...)
    ↓
pre_save: instance.pk 不存在 → _old_household_id = None
    ↓
post_save:
    instance.household_id = H → delete(`household_user_ids_{S}_{H}`) ✅
    _old_household_id = None → 跳过
    not instance.household_id = False → 跳过
```
**结果**：家庭组 H 的缓存被正确删除。新用户下一次 get_household_user_ids 会重新查询。

**场景 2：新用户加入空间，不指定家庭组（独立用户）**
```
UserSpace.objects.create(space=S, user=U_new, household=None, ...)
    ↓
pre_save: _old_household_id = None
    ↓
post_save:
    instance.household_id = None → 跳过
    _old_household_id = None → 跳过
    not instance.household_id = True → delete(`household_user_ids_{S}_user_{U_new}`) ✅
```
**结果**：该用户的独立缓存被正确删除。下一次查询会重新写入。

**场景 3：已有用户从「独立」→「加入家庭组 H」**
```
# 修改前：user_space.household_id = None
# 修改后：user_space.household_id = H
user_space.save()
    ↓
pre_save: _old_household_id = None (捕获成功)
    ↓
post_save:
    instance.household_id = H → delete(`household_user_ids_{S}_{H}`) ✅（删除新家庭组缓存）
    _old_household_id = None → 跳过（因为 None 与 H 不等，但条件是 old_household_id and ...）
    not instance.household_id = False → 跳过
```
**⚠️ 问题**：该用户之前的独立缓存 `household_user_ids_{S}_user_{U_id}` **没有被删除**！
- 如果接下来另一个查询传入的 UserSpace 还是 household=None（例如同一进程中缓存了旧的 UserSpace 对象），会命中残留缓存，返回 `[U_id]`（过期数据，不包含新的家庭成员）。
- 残留时间：最长 5 分钟 TTL。

**场景 4：已有用户从「家庭组 H_old」→「切换到家庭组 H_new」**
```
# 修改前：user_space.household_id = H_old
# 修改后：user_space.household_id = H_new
user_space.save()
    ↓
pre_save: _old_household_id = H_old (捕获成功)
    ↓
post_save:
    instance.household_id = H_new → delete(`household_user_ids_{S}_{H_new}`) ✅
    _old_household_id = H_old ≠ H_new → delete(`household_user_ids_{S}_{H_old}`) ✅
    not instance.household_id = False → 跳过
```
**结果**：新旧家庭组缓存都被正确删除。✅

**场景 5：已有用户从「家庭组 H」→「改为独立（无家庭组）」**
```
# 修改前：user_space.household_id = H
# 修改后：user_space.household_id = None
user_space.save()
    ↓
pre_save: _old_household_id = H (捕获成功)
    ↓
post_save:
    instance.household_id = None → 跳过
    _old_household_id = H ≠ None → delete(`household_user_ids_{S}_{H}`) ✅（删除旧家庭组缓存）
    not instance.household_id = True → delete(`household_user_ids_{S}_user_{U_id}`) ✅（删除用户独立缓存）
```
**结果**：新旧缓存都被正确删除。✅

**场景 6：删除一个有家庭组的 UserSpace**
```
# 被删除的 user_space.household_id = H
user_space.delete()
    ↓
post_delete:
    instance.household_id = H → delete(`household_user_ids_{S}_{H}`) ✅
```
**结果**：家庭组 H 的缓存被正确删除。✅

**场景 7：删除一个无家庭组的 UserSpace（独立用户）**
```
# 被删除的 user_space.household_id = None
user_space.delete()
    ↓
post_delete:
    instance.household_id = None → **不删除任何键** ❌
```
**⚠️ 问题**：该用户的独立缓存 `household_user_ids_{S}_user_{U_id}` **没有被删除**！
- 如果该用户后续被重新加入同一空间（household=None），且 5 分钟内有查询，可能命中残留缓存
- 但因为用户已被删除，通常不会有查询传入该 UserSpace，风险较低

##### 家庭组缓存使用位置及误用后果

`get_household_user_ids` 被用于：
1. **MealPlan 查询** [views/api.py:1497-1499]：
   ```python
   queryset.filter(
       Q(created_by=self.request.user) |
       Q(created_by_id__in=get_household_user_ids(self.request.user_space))
   )
   ```
   - 误用后果：**看不到新加入家庭成员的 MealPlan**，或者**还能看到已离开成员的 MealPlan**

2. **ShoppingList 查询** [views/api.py:2196-2198]：
   ```python
   filter(Q(entries__created_by=self.request.user) |
          Q(entries__created_by__in=get_household_user_ids(...)))
   ```
   - 误用后果：购物列表项显示不正确

3. **Food on_hand 批量操作** [views/api.py:1326-1327]：
   ```python
   household_user_ids = list(get_household_user_ids(request.user_space))
   ```
   - 误用后果：批量设置库存时，可能漏掉新成员或包含已离开成员

##### 结论与修复建议

**家庭组缓存失效一致性结论**：
- ✅ **大部分场景**（加入家庭组、切换家庭组、离开家庭组、删除有家庭组用户）失效正确
- ❌ **两个场景有遗漏**：
  1. 独立用户 → 加入家庭组时，原独立缓存未删除（场景 3）
  2. 删除独立用户时，独立缓存未删除（场景 7）
- ⚠️ **风险等级**：中等。最坏情况 5 分钟内成员关系不一致，不会导致越权，只会显示/操作不正确

**修复建议**：
```python
# 修复 post_save 场景 3：独立→家庭组时，额外删除原独立缓存
@receiver(post_save, sender=UserSpace)
def invalidate_household_cache_on_save(sender, instance=None, **kwargs):
    if not instance:
        return
    if instance.household_id:
        caches['default'].delete(f'household_user_ids_{instance.space_id}_{instance.household_id}')
    old_household_id = getattr(instance, '_old_household_id', None)
    if old_household_id and old_household_id != instance.household_id:
        caches['default'].delete(f'household_user_ids_{instance.space_id}_{old_household_id}')
    if not instance.household_id:
        caches['default'].delete(f'household_user_ids_{instance.space_id}_user_{instance.user_id}')
    # ✅ 新增：从独立→家庭组时，删除原独立缓存
    elif old_household_id is None and instance.household_id is not None:
        caches['default'].delete(f'household_user_ids_{instance.space_id}_user_{instance.user_id}')

# 修复 post_delete 场景 7：删除独立用户时也删除独立缓存
@receiver(post_delete, sender=UserSpace)
def invalidate_household_cache_on_delete(sender, instance=None, **kwargs):
    if instance and instance.household_id:
        caches['default'].delete(f'household_user_ids_{instance.space_id}_{instance.household_id}')
    # ✅ 新增：独立用户删除时也删除独立缓存
    elif instance and not instance.household_id:
        caches['default'].delete(f'household_user_ids_{instance.space_id}_user_{instance.user_id}')
```

---

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

### 6.6 关键疑问一：缓存键无空间信息 → 跨空间越权？

**疑问**：缓存键构成中不包含空间信息，当同一用户在不同空间拥有不同角色时，是否会导致跨空间越权？scope 隔离机制在这条路径上是否真正发挥作用？

**缓存键构成** [permission_helper.py:51](cookbook/helper/permission_helper.py:51)：
```python
CACHE_KEY = hash((
    inspect.stack()[0][3],               # 函数名 'has_group_permission'
    (user.pk, user.username, user.email), # 用户标识（⚠️ 无 space 信息）
    groups_allowed                        # 角色名元组
))
```

**实际查询逻辑** [permission_helper.py:59-63](cookbook/helper/permission_helper.py:59)：
```python
if user_space := user.userspace_set.filter(active=True):
    if len(user_space) != 1:
        result = False
    elif bool(user_space.first().groups.filter(name__in=groups_allowed)):
        result = True
```

#### 6.6.1 代码证据链分析

**证据 1：UserSpace 没有使用 ScopedManager**

查看 UserSpace 模型定义 [models.py:577-594](cookbook/models.py:577)：
```python
class UserSpace(models.Model, PermissionModelMixin):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    space = models.ForeignKey(Space, on_delete=models.CASCADE)
    household = models.ForeignKey(Household, ...)
    groups = models.ManyToManyField(Group)
    active = models.BooleanField(default=False)
    # ❌ 注意：这里没有定义 objects = ScopedManager(space='space')
```

对比业务模型 Recipe [models.py:1119](cookbook/models.py:1119)：
```python
objects = ScopedManager(space='space')  # ✅ 有 ScopedManager，自动受 scope 隔离
```

**结论**：`user.userspace_set.filter(active=True)` 查询 **不受 django-scopes 影响**，会从全局查找激活的 UserSpace。

**证据 2：has_group_permission 执行位置与 scope 的关系**

ScopeMiddleware 的执行顺序 [scope_middleware.py:55-80](cookbook/helper/scope_middleware.py:55)：
```python
# Step 1: 先在 scope 外获取激活的 UserSpace（因为 UserSpace 无 ScopedManager，这里本来就需要全局查）
user_space = request.user.userspace_set.filter(active=True).first()
...
request.space = user_space.space
request.user_space = user_space

# Step 2: 进入 scope 上下文，后续所有业务代码在这里面执行
with scope(space=request.space):
    return self.get_response(request)
```

`has_group_permission()` 在 **Step 2 内部**被 DRF 权限类调用，但它查询的是 `user.userspace_set`（无 ScopedManager），因此 **scope 隔离对 userspace_set 查询不生效**。

**Scope 隔离失效总结**：

| 隔离层级 | 是否生效 | 原因 |
|---------|---------|------|
| Middleware `with scope(space=X)` | ❌ 不生效 | UserSpace 模型没有 ScopedManager |
| Model Manager 层 | ❌ 不生效 | UserSpace 未定义 `ScopedManager(space='space')` |

**scope 隔离机制在这条缓存路径上完全不发挥作用**。

#### 6.6.2 攻击场景复现

**前置条件**：
- 用户 U 同时属于两个空间：
  - SpaceA (id=1)：角色 admin
  - SpaceB (id=2)：角色 guest
- 攻击窗口：缓存 TTL 10 秒内

| 时刻 | 操作 | 代码路径与执行结果 | 判定结果 |
|------|------|-------------------|---------|
| T0 | 在 SpaceA 请求需要 `admin` 权限的接口 | 1. `has_group_permission(U, ['admin'])` 缓存未命中<br>2. `user.userspace_set.filter(active=True)` → 返回 SpaceA_UserSpace<br>3. groups 包含 admin → result=True<br>4. 缓存写入 `KEY = hash(('has_group_permission', U标识, ('admin',))) → True` | ✅ 正确，有权限 |
| T0.2s | 调用切换空间 API `/api/switch-active-space/2/` | `switch_user_active_space(U, SpaceB)` 执行：<br>1. `UserSpace.objects.filter(user=U).update(active=False)`<br>2. SpaceB_UserSpace.active = True, save()<br>3. ⚠️ **不清理 has_group_permission 缓存** | 切换成功，前端 reload |
| T0.5s | 在 SpaceB 请求需要 `admin` 权限的接口 | 1. ScopeMiddleware 正确设置 `request.space = SpaceB`<br>2. 进入 `with scope(space=SpaceB)` 上下文<br>3. 调用 `has_group_permission(U, ['admin'])`<br>4. **缓存 KEY 完全相同**（用户+角色没变，无 space 因子）<br>5. **命中 T0 缓存，直接返回 True**<br>6. **实际查询根本没有执行**，SpaceB_UserSpace.groups=guest 没人看 | ⚠️ **越权成功** |

#### 6.6.3 为什么「单一激活空间」也救不了？

一个容易混淆的点：既然 `switch_user_active_space` 保证同一时刻只有一个激活空间，为什么还会出错？

**答案：因为缓存优先级高于实际查询。**

真正的执行顺序是：
```
has_group_permission(U, ['admin'])
    ↓
检查缓存 KEY = hash(用户, ('admin',))
    ↓ 命中（KEY 相同）
直接返回缓存值（SpaceA 时的 True）
    ↓ ❌
userspace_set 实际查询永远不会被触发
```

只要缓存键相同，缓存就会「短路」整个判定流程，不管当前激活的是哪个空间。

#### 6.6.4 最终结论与修复建议

**疑问一的结论：该漏洞成立。**

当满足以下条件时，会发生跨空间越权：
1. 同一用户在多个空间拥有不同角色
2. 在 10 秒缓存窗口内执行了空间切换
3. 切换后查询与切换前相同的权限组（例如切换前后都检查 admin）

**根本原因**：
1. **缓存键缺失 space_id**：用户、角色相同则键相同，空间变化无法反映
2. **切换空间时不失效缓存**：`switch_user_active_space` 没有清理权限缓存
3. **缓存查询优先级最高**：即使有 scope 隔离或单一激活空间的保障，缓存命中后实际查询不会执行

**修复建议**：
```python
# 修复方案一：缓存键加入 active_space_id（推荐，最彻底）
# 在 has_group_permission 中先获取激活空间，再参与 KEY 计算
def has_group_permission(user, groups, no_cache=False):
    if not user.is_authenticated:
        return False
    groups_allowed = get_allowed_groups(groups)
    
    # 先查激活空间（全局查询，本来就不受 scope 影响）
    active_user_space = user.userspace_set.filter(active=True).first()
    active_space_id = active_user_space.space_id if active_user_space else None
    
    # KEY 中包含 space_id，切换空间后 KEY 自然变化
    CACHE_KEY = 'perm_' + hashlib.md5(
        f"{user.pk}_{active_space_id}_{groups_allowed}".encode()
    ).hexdigest()
    ...

# 修复方案二：切换空间时失效该用户的所有权限缓存
# （需要方案一的字符串键前缀才能精确匹配删除）
def switch_user_active_space(user, space):
    ...  # 原有逻辑
    cache.delete_pattern(f"perm_{user.pk}_*")
```

---

### 6.7 关键疑问二：裸 hash() 作为缓存键的碰撞风险

**疑问**：使用 Python 内置 `hash()` 函数（裸哈希值）作为缓存键，是否存在碰撞风险？不同用户/角色组合碰撞后是否返回错误的权限结果？

#### 6.7.1 Python hash() 函数特性

| 特性 | 说明 | 对本场景的影响 |
|------|------|--------------|
| **随机性** | Python 3.3+ 默认启用 hash 随机化，每次启动生成随机种子 | 重启进程后所有缓存键失效，多 worker 不共享 |
| **算法** | 字符串用 SipHash-2-4；整数 hash(n)=n；元组做异或+移位组合 | 64 位输出空间，自然碰撞概率极低 |
| **非加密** | 不抗碰撞，非单向 | 理论上可被攻击者构造碰撞（需获取种子） |
| **输出类型** | 返回 int（可为负整数） | 与项目其他字符串键风格完全不同 |

#### 6.7.2 三类碰撞场景分析

**场景一：两个用户/角色组合的自然碰撞**

计算公式（生日悖论）：
- 返回值空间：约 2^64（1.8×10^19 种可能）
- P(碰撞) ≈ n² / (2 × 2^64)
- 当 n = 1,000,000 次缓存写入时：
  - P ≈ 10^12 / (3.6×10^19) ≈ **2.7×10^-8**（约 3700 万次才会发生一次）

**结论**：正常使用下几乎不会发生自然碰撞。

---

**场景二：与项目其他缓存项碰撞**

全项目审计 `hash()` 使用情况：
```
# grep 结果
cookbook/helper/permission_helper.py:51: CACHE_KEY = hash(...)
```

项目中**只有 `has_group_permission` 使用整数 hash 作为缓存键**。其他缓存全部使用可读字符串前缀：
- `household_user_ids_{space_id}_{household_id}` → 字符串
- `SPACE_{id}_BASE_UNITS` → 字符串
- `recipe_share_{pk}_{uuid}` → 字符串

**Django cache 的键前缀机制**（以 Redis 为例）：
```
最终存储键 = ":1:<CACHE_KEY>"
  ↑     ↑
版本号  用户的 hash() 整数
```
整数和字符串在 Redis 中作为键时不会因类型隐式转换发生碰撞（Redis 键本身就是二进制安全的）。

**结论**：与其他缓存项碰撞的概率几乎为零。

---

**场景三：攻击者刻意构造碰撞（安全风险）**

攻击者可控字段：
- `username` — 注册时可自选
- `email` — 注册时可自选
- `groups_allowed` — 由权限类传入，但攻击者可以选择访问需要特定权限的接口

攻击路径：
```
攻击者注册账号 username="..."（精心构造）
    ↓
正常访问需要 ['user'] 权限的接口
    ↓
hash(('has_group_permission', (attacker_pk, attacker_username, attacker_email), ('guest','user','admin')))
    ↓  恰好等于
hash(('has_group_permission', (admin_pk, admin_username, admin_email), ('guest','user','admin')))
    ↓
攻击者查询自己的权限 → 命中管理员的缓存 → 返回 True → 权限提升
```

**攻击难度评估**：
| 前提条件 | 难度 |
|---------|------|
| 获取服务器的 hash 种子（每进程随机） | 高 |
| 在同一进程中找到碰撞对 | 中（离线+在线结合） |
| 碰撞同时双方都在同一 10 秒 TTL 窗口内产生缓存 | 中低 |
| admin 恰好 10 秒内查询过相同权限 | 取决于使用频率 |

**结论**：攻击可行但门槛较高，需要多条件同时满足。

#### 6.7.3 比碰撞更实际的功能性问题

碰撞是概率性的，但以下问题是**确定性的**：

1. **多 worker 部署缓存完全不共享**：
   ```
   Worker 1（种子=α）：KEY = hash(用户, admin) = 12345  — 写入
   Worker 2（种子=β）：KEY = hash(用户, admin) = 98765  — 读不到，重新查DB
   ```
   gunicorn/uwsgi 多 worker 下缓存命中率趋近于 0。

2. **进程重启后全缓存失效**：
   - 每次部署/重启都有大量孤儿缓存占用内存直到 TTL 过期
   - 重启后瞬时所有权限请求穿透到 DB

3. **负整数键的兼容性问题**：
   - `hash()` 可能返回负数，如 `-28493728947239847`
   - 部分缓存后端可能对负整数键的字符串表示有特殊处理

#### 6.7.4 碰撞后果总结

| 碰撞类型 | 发生概率 | 业务影响 |
|---------|---------|---------|
| 自然碰撞（两用户） | 极低（~10^-8） | 用户 A 权限 → 用户 B 权限，权限提/降 |
| 与其他缓存项碰撞 | ~0 | 无实际风险 |
| 攻击者构造碰撞 | 中低（需种子+TTL 窗口） | 权限提升至目标用户级别 |
| **多 worker 不共享** | **100% 确定发生** | **缓存几乎无效，DB 压力大** |
| **重启后缓存失效** | **每次重启** | **瞬时 DB 压力尖峰** |

#### 6.7.5 疑问二结论与修复建议

**疑问二的结论**：
- **安全层面**：自然碰撞可忽略，攻击碰撞理论存在但门槛较高
- **工程层面**：多 worker 不共享、重启失效等问题是**确定性存在的严重功能性缺陷**

**修复建议**：
```python
# 推荐方案：使用 hashlib.md5 + 可读前缀（进程/重启稳定、多 worker 共享）
import hashlib

def has_group_permission(user, groups, no_cache=False):
    if not user.is_authenticated:
        return False
    groups_allowed = get_allowed_groups(groups)
    
    # 稳定、跨进程、加入 space_id 顺便修复疑问一
    active_us = user.userspace_set.filter(active=True).only('space_id').first()
    space_factor = active_us.space_id if active_us else 0
    
    key_material = f"perm:{user.pk}:{space_factor}:{groups_allowed}"
    CACHE_KEY = "perm_" + hashlib.md5(key_material.encode()).hexdigest()
    
    if not no_cache:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached
    # ... 后续逻辑 ...
```

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
