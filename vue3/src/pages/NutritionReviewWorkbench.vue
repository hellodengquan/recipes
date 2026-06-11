<template>
    <v-container>
        <v-card :loading="loadingSummary || loadingList" class="mb-4">
            <v-card-title>
                <v-icon icon="fas fa-clipboard-check" class="me-2"></v-icon>
                {{ $t('NutritionReview_Workbench') }}
            </v-card-title>
        </v-card>

        <v-row v-if="summary" class="mb-4">
            <v-col cols="12" md="2" v-for="stat in summaryStats" :key="stat.label">
                <v-card :color="stat.color" variant="tonal" class="h-100">
                    <v-card-text class="text-center">
                        <div class="text-h5 font-weight-bold">{{ stat.value }}</div>
                        <div class="text-body-2">{{ $t(stat.label) }}</div>
                    </v-card-text>
                </v-card>
            </v-col>
            <v-col cols="12" md="4">
                <v-card color="info" variant="tonal" class="h-100">
                    <v-card-text>
                        <div class="d-flex align-center justify-space-between">
                            <div>
                                <div class="text-overline">{{ $t('NutritionReview_YourRole') }}</div>
                                <div class="text-h6 font-weight-bold">
                                    {{ getRoleText(summary.current_user_role) }}
                                </div>
                            </div>
                            <v-chip :color="getRoleColor(summary.current_user_role)" size="large">
                                <v-icon icon="fa-solid fa-user-shield" class="me-1"></v-icon>
                                {{ summary.current_user_role.toUpperCase() }}
                            </v-chip>
                        </div>
                    </v-card-text>
                </v-card>
            </v-col>
        </v-row>

        <v-card class="mb-4">
            <v-card-text>
                <v-row align="center">
                    <v-col cols="12" md="4">
                        <v-select
                            v-model="selectedGroupBy"
                            :label="$t('NutritionReview_GroupBy')"
                            :items="groupByOptions"
                            density="compact"
                            variant="outlined"
                            hide-details
                        ></v-select>
                    </v-col>
                    <v-col cols="12" md="4">
                        <v-select
                            v-model="selectedConfidenceFilter"
                            :label="$t('NutritionReview_FilterByConfidence')"
                            :items="confidenceFilterOptions"
                            density="compact"
                            variant="outlined"
                            hide-details
                        ></v-select>
                    </v-col>
                    <v-col cols="12" md="4">
                        <v-btn color="primary" block @click="refreshData">
                            <v-icon icon="fas fa-sync-alt" class="me-2"></v-icon>
                            {{ $t('Refresh') }}
                        </v-btn>
                    </v-col>
                </v-row>
                <v-divider class="my-3"></v-divider>
                <v-row align="center" v-if="totalItems > 0">
                    <v-col cols="12" sm="6">
                        <div class="d-flex align-center gap-3">
                            <v-btn
                                variant="outlined"
                                size="small"
                                @click="toggleSelectAll"
                            >
                                <v-icon :icon="isAllSelected ? 'fas fa-square-minus' : 'fas fa-square-check'" class="me-2"></v-icon>
                                {{ isAllSelected ? $t('NutritionReview_DeselectAll') : $t('NutritionReview_SelectAll') }}
                            </v-btn>
                            <v-chip color="primary" variant="tonal" v-if="selectedRecipeIds.size > 0">
                                {{ $t('NutritionReview_SelectedCount', { count: selectedRecipeIds.size }) }}
                            </v-chip>
                        </div>
                    </v-col>
                    <v-col cols="12" sm="6">
                        <div class="d-flex justify-end gap-2">
                            <v-btn
                                color="success"
                                size="small"
                                :disabled="selectedRecipeIds.size === 0 || !canApprove"
                                :loading="batchActionLoading"
                                @click="openBatchApproveDialog"
                            >
                                <v-icon icon="fas fa-check-double" class="me-2"></v-icon>
                                {{ $t('NutritionReview_BatchApprove') }}
                            </v-btn>
                            <v-btn
                                color="error"
                                size="small"
                                :disabled="selectedRecipeIds.size === 0 || !canApprove"
                                :loading="batchActionLoading"
                                @click="openBatchRejectDialog"
                            >
                                <v-icon icon="fas fa-times-circle" class="me-2"></v-icon>
                                {{ $t('NutritionReview_BatchReject') }}
                            </v-btn>
                            <v-btn
                                color="warning"
                                size="small"
                                :disabled="selectedRecipeIds.size === 0 || !canFlag"
                                :loading="batchActionLoading"
                                @click="openBatchFlagDialog"
                            >
                                <v-icon icon="fas fa-flag" class="me-2"></v-icon>
                                {{ $t('NutritionReview_BatchFlag') }}
                            </v-btn>
                        </div>
                    </v-col>
                </v-row>
            </v-card-text>
        </v-card>

        <v-card>
            <v-card-title>
                {{ $t('NutritionReview_Pending') }}
                <v-spacer></v-spacer>
                <v-chip color="primary" size="small">
                    {{ totalItems }} {{ $t('items') }}
                </v-chip>
            </v-card-title>

            <v-divider></v-divider>

            <v-card-text v-if="loadingList" class="text-center py-8">
                <v-progress-circular indeterminate color="primary"></v-progress-circular>
                <p class="mt-2">{{ $t('NutritionReview_Loading') }}</p>
            </v-card-text>

            <v-card-text v-else-if="groupedItems.size === 0" class="text-center py-8">
                <v-icon icon="fas fa-check-circle" size="64" color="success" class="mb-4"></v-icon>
                <p class="text-h6">{{ $t('NutritionReview_NoPendingItems') }}</p>
            </v-card-text>

            <v-expansion-panels v-else variant="accordion">
                <v-expansion-panel
                    v-for="[groupKey, items] in filteredGroupedItems"
                    :key="groupKey"
                >
                    <v-expansion-panel-title>
                        <template #default="{ open }">
                            <div class="d-flex align-center w-100">
                                <v-avatar size="36" :color="getGroupColor(items)" class="me-3">
                                    <v-icon>{{ getGroupIcon(items) }}</v-icon>
                                </v-avatar>
                                <div class="flex-grow-1">
                                    <div class="text-subtitle-1 font-weight-medium">
                                        {{ getGroupTitle(groupKey, items) }}
                                    </div>
                                    <div class="text-body-2 text-medium-emphasis">
                                        {{ items.length }} {{ $t('items') }}
                                    </div>
                                </div>
                                <v-chip
                                    :color="getGroupConfidenceColor(items)"
                                    size="small"
                                    class="me-3"
                                >
                                    {{ getAverageConfidence(items) }}%
                                </v-chip>
                                <v-chip color="warning" size="small" v-if="hasMissingData(items)">
                                    <v-icon icon="fas fa-triangle-exclamation" class="me-1"></v-icon>
                                    {{ countMissingData(items) }}
                                </v-chip>
                            </div>
                        </template>
                    </v-expansion-panel-title>

                    <v-expansion-panel-text>
                        <v-table density="comfortable">
                            <thead>
                                <tr>
                                    <th class="text-center" style="width: 48px;">
                                        <v-checkbox
                                            :model-value="isGroupSelected(items)"
                                            :indeterminate="isGroupIndeterminate(items)"
                                            density="compact"
                                            hide-details
                                            @click.stop="toggleGroupSelection(items)"
                                        ></v-checkbox>
                                    </th>
                                    <th>{{ $t('NutritionReview_Ingredient') }}</th>
                                    <th>{{ $t('NutritionReview_Amount') }}</th>
                                    <th>{{ $t('NutritionReview_Unit') }}</th>
                                    <th>{{ $t('NutritionReview_Recipe') }}</th>
                                    <th>{{ $t('NutritionReview_ConfidenceScore') }}</th>
                                    <th>{{ $t('NutritionReview_ReviewReasons') }}</th>
                                    <th class="text-right">{{ $t('Actions') }}</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr v-for="item in items" :key="`${item.ingredient_id}-${item.recipe_id}`" :class="{ 'bg-primary-lighten-5': isItemSelected(item) }">
                                    <td class="text-center">
                                        <v-checkbox
                                            :model-value="isItemSelected(item)"
                                            density="compact"
                                            hide-details
                                            @click.stop="toggleItemSelection(item)"
                                        ></v-checkbox>
                                    </td>
                                    <td>
                                        <div class="font-weight-medium">{{ item.food?.name || item.original_text }}</div>
                                        <div class="text-body-2 text-medium-emphasis">
                                            {{ item.original_text }}
                                        </div>
                                        <v-chip size="x-small" variant="tonal" color="info" class="mt-1" v-if="item.food?.fdc_id">
                                            FDC: {{ item.food.fdc_id }}
                                        </v-chip>
                                        <v-chip size="x-small" variant="tonal" color="error" class="mt-1" v-if="!item.food">
                                            {{ $t('NutritionReview_MissingFood') }}
                                        </v-chip>
                                    </td>
                                    <td>
                                        <v-chip variant="outlined" v-if="item.amount">
                                            {{ item.amount }}
                                        </v-chip>
                                        <span v-else class="text-medium-emphasis">-</span>
                                    </td>
                                    <td>
                                        <v-chip variant="outlined" color="secondary" v-if="item.unit">
                                            {{ item.unit.name }}
                                        </v-chip>
                                        <v-chip size="x-small" variant="tonal" color="warning" v-else>
                                            {{ $t('NutritionReview_MissingUnit') }}
                                        </v-chip>
                                    </td>
                                    <td>
                                        <template v-if="item.recipe_id">
                                            <router-link
                                                :to="{ name: 'RecipeViewPage', params: { id: item.recipe_id } }"
                                                class="text-primary text-decoration-none"
                                            >
                                                <v-icon icon="fas fa-external-link-alt" size="x-small" class="me-1"></v-icon>
                                                {{ item.recipe_name || `#${item.recipe_id}` }}
                                            </router-link>
                                        </template>
                                        <span v-else class="text-medium-emphasis">-</span>
                                    </td>
                                    <td>
                                        <v-chip
                                            :color="getConfidenceColor(item.confidence_score)"
                                            size="small"
                                        >
                                            {{ item.confidence_score }}%
                                        </v-chip>
                                    </td>
                                    <td>
                                        <div class="d-flex flex-column gap-1">
                                            <v-chip
                                                v-for="(reason, idx) in item.review_reasons_text"
                                                :key="idx"
                                                size="x-small"
                                                color="warning"
                                                variant="tonal"
                                                class="text-wrap"
                                            >
                                                {{ reason }}
                                            </v-chip>
                                        </div>
                                    </td>
                                    <td class="text-right">
                                        <div class="d-flex justify-end gap-1">
                                            <v-btn
                                                size="small"
                                                variant="plain"
                                                color="success"
                                                :disabled="!canApprove"
                                                @click="openApproveDialog(item)"
                                            >
                                                <v-icon icon="fas fa-check"></v-icon>
                                            </v-btn>
                                            <v-btn
                                                size="small"
                                                variant="plain"
                                                color="error"
                                                :disabled="!canApprove"
                                                @click="openRejectDialog(item)"
                                            >
                                                <v-icon icon="fas fa-times"></v-icon>
                                            </v-btn>
                                            <v-btn
                                                size="small"
                                                variant="plain"
                                                color="warning"
                                                :disabled="!canFlag"
                                                @click="openFlagDialog(item)"
                                            >
                                                <v-icon icon="fas fa-flag"></v-icon>
                                            </v-btn>
                                            <v-btn
                                                size="small"
                                                variant="plain"
                                                color="primary"
                                                :to="{ name: 'PropertyEditorPage', query: { recipe: item.recipe_id } }"
                                                v-if="item.recipe_id && canEdit"
                                            >
                                                <v-icon icon="fas fa-edit"></v-icon>
                                            </v-btn>
                                        </div>
                                    </td>
                                </tr>
                            </tbody>
                        </v-table>
                    </v-expansion-panel-text>
                </v-expansion-panel>
            </v-expansion-panels>
        </v-card>

        <v-dialog v-model="actionDialog.show" max-width="500">
            <v-card>
                <v-closable-card-title v-model="actionDialog.show" :title="getActionDialogTitle()"></v-closable-card-title>
                <v-card-text>
                    <v-alert
                        :type="actionDialog.type"
                        variant="tonal"
                        class="mb-4"
                    >
                        <template v-if="actionDialog.item">
                            <strong>{{ actionDialog.item.food?.name || actionDialog.item.original_text }}</strong>
                            <span class="text-medium-emphasis">
                                ({{ actionDialog.item.amount || '-' }} {{ actionDialog.item.unit?.name || '-' }})
                            </span>
                        </template>
                    </v-alert>
                    <v-textarea
                        v-model="actionDialog.comment"
                        :label="$t('NutritionReview_EnterComment')"
                        rows="3"
                        variant="outlined"
                        auto-grow
                    ></v-textarea>
                </v-card-text>
                <v-card-actions>
                    <v-btn @click="actionDialog.show = false" variant="outlined">
                        {{ $t('NutritionReview_Cancel') }}
                    </v-btn>
                    <v-spacer></v-spacer>
                    <v-btn
                        :color="actionDialog.btnColor"
                        :loading="actionDialog.loading"
                        @click="executeAction"
                    >
                        <v-icon :icon="actionDialog.btnIcon" class="me-2"></v-icon>
                        {{ actionDialog.btnText }}
                    </v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>

        <v-dialog v-model="batchActionDialog.show" max-width="550">
            <v-card>
                <v-closable-card-title v-model="batchActionDialog.show" :title="getBatchDialogTitle()"></v-closable-card-title>
                <v-card-text>
                    <v-alert
                        :type="batchActionDialog.type"
                        variant="tonal"
                        class="mb-4"
                    >
                        {{ getBatchConfirmMessage() }}
                    </v-alert>
                    <v-chip color="primary" variant="tonal" class="mb-4">
                        <v-icon icon="fas fa-list-check" class="me-2"></v-icon>
                        {{ $t('NutritionReview_SelectedCount', { count: selectedRecipeIds.size }) }}
                    </v-chip>
                    <v-textarea
                        v-model="batchActionDialog.comment"
                        :label="$t('NutritionReview_EnterComment')"
                        rows="3"
                        variant="outlined"
                        auto-grow
                    ></v-textarea>
                </v-card-text>
                <v-card-actions>
                    <v-btn @click="batchActionDialog.show = false" variant="outlined">
                        {{ $t('NutritionReview_Cancel') }}
                    </v-btn>
                    <v-spacer></v-spacer>
                    <v-btn
                        :color="batchActionDialog.btnColor"
                        :loading="batchActionLoading"
                        @click="executeBatchAction"
                    >
                        <v-icon :icon="batchActionDialog.btnIcon" class="me-2"></v-icon>
                        {{ batchActionDialog.btnText }}
                    </v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
    </v-container>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import NutritionReviewService from '@/services/NutritionReviewService'
import {
    CONFIDENCE_FILTERS,
    getConfidenceColor,
    type NutritionReviewItem,
    type NutritionReviewSummary,
} from '@/types/NutritionReview'
import { ErrorMessageType, MessageType, useMessageStore } from '@/stores/MessageStore'
import { PreparedMessage } from '@/stores/MessageStore'
import VClosableCardTitle from '@/components/dialogs/VClosableCardTitle.vue'

const { t } = useI18n()

const loadingSummary = ref(false)
const loadingList = ref(false)
const batchActionLoading = ref(false)

const summary = ref<NutritionReviewSummary | null>(null)
const reviewItems = ref<NutritionReviewItem[]>([])

const selectedGroupBy = ref<'food' | 'recipe' | 'unit'>('food')
const selectedConfidenceFilter = ref('all')

const selectedRecipeIds = ref<Set<number>>(new Set())

const groupByOptions = [
    { title: t('NutritionReview_GroupByFood'), value: 'food' },
    { title: t('NutritionReview_GroupByRecipe'), value: 'recipe' },
    { title: t('NutritionReview_GroupByUnit'), value: 'unit' },
]

const confidenceFilterOptions = computed(() => [
    { title: t('NutritionReview_All'), value: 'all' },
    ...CONFIDENCE_FILTERS.slice(1).map(f => ({
        title: t(f.label),
        value: f.value,
    })),
])

const totalItems = computed(() => reviewItems.value.length)

const canApprove = computed(() => summary.value?.user_permissions.approve ?? false)
const canFlag = computed(() => summary.value?.user_permissions.flag_for_review ?? false)
const canEdit = computed(() => summary.value?.user_permissions.edit_nutrition ?? false)

const summaryStats = computed(() => {
    if (!summary.value) return []
    return [
        { label: 'NutritionReview_TotalRecipes', value: summary.value.total_recipes, color: 'primary' },
        { label: 'NutritionReview_PendingCount', value: summary.value.pending_review, color: 'warning' },
        { label: 'NutritionReview_AverageConfidence', value: `${summary.value.average_confidence}%`, color: 'info' },
        { label: 'NutritionReview_LowConfidenceCount', value: summary.value.low_confidence_count, color: 'error' },
    ]
})

const groupedItems = computed(() => {
    const groups = new Map<string, NutritionReviewItem[]>()

    reviewItems.value.forEach(item => {
        let key = ''
        switch (selectedGroupBy.value) {
            case 'food':
                key = item.food ? `food_${item.food.id}` : 'food_unknown'
                break
            case 'recipe':
                key = `recipe_${item.recipe_id || 'unknown'}`
                break
            case 'unit':
                key = item.unit ? `unit_${item.unit.id}` : 'unit_unknown'
                break
        }

        if (!groups.has(key)) {
            groups.set(key, [])
        }
        groups.get(key)!.push(item)
    })

    return groups
})

const filteredGroupedItems = computed(() => {
    const filter = CONFIDENCE_FILTERS.find(f => f.value === selectedConfidenceFilter.value)
    if (!filter || filter.value === 'all') {
        return groupedItems.value
    }

    const filtered = new Map<string, NutritionReviewItem[]>()
    groupedItems.value.forEach((items, key) => {
        const filteredItems = items.filter(item => {
            const score = parseFloat(item.confidence_score)
            return score >= filter.min && score < filter.max
        })
        if (filteredItems.length > 0) {
            filtered.set(key, filteredItems)
        }
    })

    return filtered
})

const actionDialog = ref({
    show: false,
    type: 'success' as 'success' | 'error' | 'warning',
    action: '' as 'approve' | 'reject' | 'flag',
    item: null as NutritionReviewItem | null,
    comment: '',
    loading: false,
    btnText: '',
    btnIcon: '',
    btnColor: '',
})

const batchActionDialog = ref({
    show: false,
    type: 'success' as 'success' | 'error' | 'warning',
    action: '' as 'approve' | 'reject' | 'flag',
    comment: '',
    btnText: '',
    btnIcon: '',
    btnColor: '',
})

const allVisibleRecipeIds = computed(() => {
    const ids = new Set<number>()
    reviewItems.value.forEach(item => {
        if (item.recipe_id) {
            ids.add(item.recipe_id)
        }
    })
    return ids
})

const isAllSelected = computed(() => {
    const visibleIds = allVisibleRecipeIds.value
    if (visibleIds.size === 0) return false
    for (const id of visibleIds) {
        if (!selectedRecipeIds.value.has(id)) return false
    }
    return true
})

function getUniqueRecipeIds(items: NutritionReviewItem[]): Set<number> {
    const ids = new Set<number>()
    items.forEach(item => {
        if (item.recipe_id) {
            ids.add(item.recipe_id)
        }
    })
    return ids
}

function isGroupSelected(items: NutritionReviewItem[]): boolean {
    const recipeIds = getUniqueRecipeIds(items)
    if (recipeIds.size === 0) return false
    for (const id of recipeIds) {
        if (!selectedRecipeIds.value.has(id)) return false
    }
    return true
}

function isGroupIndeterminate(items: NutritionReviewItem[]): boolean {
    const recipeIds = getUniqueRecipeIds(items)
    if (recipeIds.size === 0) return false
    let selectedCount = 0
    for (const id of recipeIds) {
        if (selectedRecipeIds.value.has(id)) selectedCount++
    }
    return selectedCount > 0 && selectedCount < recipeIds.size
}

function isItemSelected(item: NutritionReviewItem): boolean {
    return item.recipe_id ? selectedRecipeIds.value.has(item.recipe_id) : false
}

function toggleSelectAll() {
    if (isAllSelected.value) {
        selectedRecipeIds.value.clear()
    } else {
        selectedRecipeIds.value = new Set(allVisibleRecipeIds.value)
    }
}

function toggleGroupSelection(items: NutritionReviewItem[]) {
    const recipeIds = getUniqueRecipeIds(items)
    const allSelected = isGroupSelected(items)

    if (allSelected) {
        recipeIds.forEach(id => selectedRecipeIds.value.delete(id))
    } else {
        recipeIds.forEach(id => selectedRecipeIds.value.add(id))
    }

    selectedRecipeIds.value = new Set(selectedRecipeIds.value)
}

function toggleItemSelection(item: NutritionReviewItem) {
    if (!item.recipe_id) return

    if (selectedRecipeIds.value.has(item.recipe_id)) {
        selectedRecipeIds.value.delete(item.recipe_id)
    } else {
        selectedRecipeIds.value.add(item.recipe_id)
    }

    selectedRecipeIds.value = new Set(selectedRecipeIds.value)
}

function getRoleText(role: string): string {
    const roleMap: Record<string, string> = {
        'admin': t('NutritionReview_RoleAdmin'),
        'user': t('NutritionReview_RoleUser'),
        'guest': t('NutritionReview_RoleGuest'),
    }
    return roleMap[role] || role
}

function getRoleColor(role: string): string {
    const colorMap: Record<string, string> = {
        'admin': 'error',
        'user': 'primary',
        'guest': 'grey',
    }
    return colorMap[role] || 'grey'
}

function getGroupTitle(key: string, items: NutritionReviewItem[]): string {
    if (items.length === 0) return key
    const first = items[0]
    switch (selectedGroupBy.value) {
        case 'food':
            return first.food?.name || first.original_text || t('NutritionReview_UnknownFood')
        case 'recipe':
            return first.recipe_name || `${t('NutritionReview_Recipe')} #${first.recipe_id}`
        case 'unit':
            return first.unit?.name || t('NutritionReview_UnknownUnit')
        default:
            return key
    }
}

function getGroupColor(items: NutritionReviewItem[]): string {
    return getGroupConfidenceColor(items)
}

function getGroupIcon(items: NutritionReviewItem[]): string {
    switch (selectedGroupBy.value) {
        case 'food':
            return 'fas fa-carrot'
        case 'recipe':
            return 'fas fa-utensils'
        case 'unit':
            return 'fas fa-scale-balanced'
        default:
            return 'fas fa-box'
    }
}

function getGroupConfidenceColor(items: NutritionReviewItem[]): string {
    const avg = parseFloat(getAverageConfidence(items))
    return getConfidenceColor(avg)
}

function getAverageConfidence(items: NutritionReviewItem[]): string {
    if (items.length === 0) return '0'
    const total = items.reduce((sum, item) => sum + parseFloat(item.confidence_score), 0)
    return (total / items.length).toFixed(1)
}

function hasMissingData(items: NutritionReviewItem[]): boolean {
    return items.some(item => item.review_reasons.length > 0)
}

function countMissingData(items: NutritionReviewItem[]): number {
    return items.reduce((count, item) => count + item.review_reasons.length, 0)
}

async function loadSummary() {
    loadingSummary.value = true
    try {
        summary.value = await NutritionReviewService.getReviewSummary()
    } catch (err) {
        useMessageStore().addError(ErrorMessageType.FETCH_ERROR, err)
    } finally {
        loadingSummary.value = false
    }
}

async function loadPendingReviews() {
    loadingList.value = true
    try {
        const filter = CONFIDENCE_FILTERS.find(f => f.value === selectedConfidenceFilter.value)
        const params = filter && filter.value !== 'all'
            ? { confidence_min: filter.min, confidence_max: filter.max }
            : undefined

        reviewItems.value = await NutritionReviewService.getAllPendingReviewItems(params)
    } catch (err) {
        useMessageStore().addError(ErrorMessageType.FETCH_ERROR, err)
    } finally {
        loadingList.value = false
    }
}

async function refreshData() {
    await Promise.all([loadSummary(), loadPendingReviews()])
}

function openApproveDialog(item: NutritionReviewItem) {
    actionDialog.value = {
        show: true,
        type: 'success',
        action: 'approve',
        item,
        comment: '',
        loading: false,
        btnText: t('NutritionReview_Approve'),
        btnIcon: 'fas fa-check',
        btnColor: 'success',
    }
}

function openRejectDialog(item: NutritionReviewItem) {
    actionDialog.value = {
        show: true,
        type: 'error',
        action: 'reject',
        item,
        comment: '',
        loading: false,
        btnText: t('NutritionReview_Reject'),
        btnIcon: 'fas fa-times',
        btnColor: 'error',
    }
}

function openFlagDialog(item: NutritionReviewItem) {
    actionDialog.value = {
        show: true,
        type: 'warning',
        action: 'flag',
        item,
        comment: '',
        loading: false,
        btnText: t('NutritionReview_FlagForReview'),
        btnIcon: 'fas fa-flag',
        btnColor: 'warning',
    }
}

function getActionDialogTitle(): string {
    const titles: Record<string, string> = {
        'approve': t('NutritionReview_Approve'),
        'reject': t('NutritionReview_Reject'),
        'flag': t('NutritionReview_FlagForReview'),
    }
    return titles[actionDialog.value.action] || ''
}

async function executeAction() {
    if (!actionDialog.value.item?.recipe_id) return

    actionDialog.value.loading = true
    try {
        const recipeId = actionDialog.value.item.recipe_id
        const comment = actionDialog.value.comment || undefined

        switch (actionDialog.value.action) {
            case 'approve':
                await NutritionReviewService.approveRecipeNutrition(recipeId, { comment })
                break
            case 'reject':
                await NutritionReviewService.rejectRecipeNutrition(recipeId, { comment })
                break
            case 'flag':
                await NutritionReviewService.flagRecipeForReview(recipeId, { comment })
                break
        }

        useMessageStore().addPreparedMessage(PreparedMessage.UPDATE_SUCCESS)
        actionDialog.value.show = false
        await refreshData()
    } catch (err) {
        useMessageStore().addError(ErrorMessageType.UPDATE_ERROR, err)
    } finally {
        actionDialog.value.loading = false
    }
}

function openBatchApproveDialog() {
    batchActionDialog.value = {
        show: true,
        type: 'success',
        action: 'approve',
        comment: '',
        btnText: t('NutritionReview_BatchApprove'),
        btnIcon: 'fas fa-check-double',
        btnColor: 'success',
    }
}

function openBatchRejectDialog() {
    batchActionDialog.value = {
        show: true,
        type: 'error',
        action: 'reject',
        comment: '',
        btnText: t('NutritionReview_BatchReject'),
        btnIcon: 'fas fa-times-circle',
        btnColor: 'error',
    }
}

function openBatchFlagDialog() {
    batchActionDialog.value = {
        show: true,
        type: 'warning',
        action: 'flag',
        comment: '',
        btnText: t('NutritionReview_BatchFlag'),
        btnIcon: 'fas fa-flag',
        btnColor: 'warning',
    }
}

function getBatchDialogTitle(): string {
    const titles: Record<string, string> = {
        'approve': t('NutritionReview_BatchApprove'),
        'reject': t('NutritionReview_BatchReject'),
        'flag': t('NutritionReview_BatchFlag'),
    }
    return titles[batchActionDialog.value.action] || ''
}

function getBatchConfirmMessage(): string {
    const count = selectedRecipeIds.size
    const messages: Record<string, string> = {
        'approve': t('NutritionReview_BatchApproveConfirm', { count }),
        'reject': t('NutritionReview_BatchRejectConfirm', { count }),
        'flag': t('NutritionReview_BatchFlagConfirm', { count }),
    }
    return messages[batchActionDialog.value.action] || ''
}

async function executeBatchAction() {
    if (selectedRecipeIds.size === 0) return

    batchActionLoading.value = true
    try {
        const recipeIds = Array.from(selectedRecipeIds.value)
        const comment = batchActionDialog.value.comment || undefined

        let result
        switch (batchActionDialog.value.action) {
            case 'approve':
                result = await NutritionReviewService.batchApproveRecipes(recipeIds, comment)
                break
            case 'reject':
                result = await NutritionReviewService.batchRejectRecipes(recipeIds, comment)
                break
            case 'flag':
                result = await NutritionReviewService.batchFlagRecipes(recipeIds, { comment })
                break
            default:
                return
        }

        const successCount = result.approved_count ?? result.rejected_count ?? result.flagged_count ?? 0
        useMessageStore().addMessage(
            MessageType.SUCCESS,
            t('NutritionReview_BatchSuccess', {
                success: successCount,
                failed: result.failed_count,
            }),
            5000
        )

        selectedRecipeIds.value.clear()
        batchActionDialog.value.show = false
        await refreshData()
    } catch (err) {
        useMessageStore().addError(ErrorMessageType.UPDATE_ERROR, err)
    } finally {
        batchActionLoading.value = false
    }
}

onMounted(() => {
    refreshData()
})
</script>

<style scoped>
.text-wrap {
    white-space: normal !important;
    max-width: 250px;
}
</style>
