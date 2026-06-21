<template>
    <v-container>
        <v-row>
            <v-col>
                <v-card prepend-icon="fa-solid fa-clipboard-check" title="Import Review">
                    <template #subtitle>
                        <div class="text-wrap">
                            Review and fix imported recipes before saving to database. Fix unit errors, merge duplicate ingredients, and set missing images in bulk.
                        </div>
                    </template>
                    <v-card-actions>
                        <v-btn size="small" variant="tonal" color="primary" @click="loadImportRecipes" :loading="loading">
                            <v-icon start icon="$refresh"></v-icon>
                            Refresh
                        </v-btn>
                        <v-spacer></v-spacer>
                        <v-chip color="primary" variant="tonal">
                            Pending: {{ pendingCount }}
                        </v-chip>
                        <v-chip color="warning" variant="tonal">
                            Unit Errors: {{ unitErrorCount }}
                        </v-chip>
                        <v-chip color="error" variant="tonal">
                            Missing Images: {{ missingImageCount }}
                        </v-chip>
                        <v-chip color="info" variant="tonal">
                            Duplicate Foods: {{ duplicateFoodCount }}
                        </v-chip>
                    </v-card-actions>
                </v-card>
            </v-col>
        </v-row>

        <v-row class="mt-2">
            <v-col>
                <v-tabs v-model="activeTab" color="primary" align-tabs="start" bg-color="surface">
                    <v-tab value="all">All ({{ importRecipes.length }})</v-tab>
                    <v-tab value="UNIT_ERROR">Unit Errors ({{ getRecipesByIssueType('UNIT_ERROR').length }})</v-tab>
                    <v-tab value="MISSING_IMAGE">Missing Images ({{ getRecipesByIssueType('MISSING_IMAGE').length }})</v-tab>
                    <v-tab value="DUPLICATE_FOOD">Duplicate Ingredients ({{ getRecipesByIssueType('DUPLICATE_FOOD').length }})</v-tab>
                    <v-tab value="FIELD_MISMATCH">Field Mismatch ({{ getRecipesByIssueType('FIELD_MISMATCH').length }})</v-tab>
                    <v-tab value="OTHER">Other Issues ({{ getRecipesByIssueType('OTHER').length }})</v-tab>
                </v-tabs>
            </v-col>
        </v-row>

        <v-row class="mt-2">
            <v-col>
                <v-card>
                    <v-card-title>
                        <v-btn size="small" color="success" :disabled="selectedIds.length === 0" @click="dialogApprove = true">
                            <v-icon start icon="$save"></v-icon>
                            Approve & Import ({{ selectedIds.length }})
                        </v-btn>
                        <v-btn size="small" color="error" variant="tonal" :disabled="selectedIds.length === 0" @click="dialogReject = true">
                            <v-icon start icon="$delete"></v-icon>
                            Reject ({{ selectedIds.length }})
                        </v-btn>
                        <v-divider vertical class="mx-2"></v-divider>
                        <v-btn size="small" variant="tonal" color="warning" :disabled="selectedIds.length === 0" @click="dialogUnitFix = true">
                            <v-icon start icon="fa-solid fa-scale-balanced"></v-icon>
                            Fix Units
                        </v-btn>
                        <v-btn size="small" variant="tonal" color="info" :disabled="selectedIds.length === 0" @click="dialogMergeFood = true">
                            <v-icon start icon="fa-solid fa-arrows-to-dot"></v-icon>
                            Merge Ingredients
                        </v-btn>
                        <v-btn size="small" variant="tonal" color="secondary" :disabled="selectedIds.length === 0" @click="dialogImageFix = true">
                            <v-icon start icon="fa-solid fa-image"></v-icon>
                            Set Images
                        </v-btn>
                        <v-spacer></v-spacer>
                        <v-btn size="small" variant="tonal" @click="toggleSelectAll">
                            {{ isAllSelected ? 'Deselect All' : 'Select All' }}
                        </v-btn>
                    </v-card-title>

                    <v-data-table-server
                        :items="filteredRecipes"
                        :headers="tableHeaders"
                        :loading="loading"
                        item-value="id"
                        v-model:selected="selectedItems"
                        show-select
                        :items-per-page="25"
                    >
                        <template v-slot:item.name="{ item }">
                            <div class="font-weight-medium">{{ item.name }}</div>
                            <div class="text-caption text-medium-emphasis" v-if="item.sourceUrl">
                                {{ item.sourceUrl }}
                            </div>
                        </template>

                        <template v-slot:item.image="{ item }">
                            <div v-if="item.imageUrl">
                                <v-img :src="item.imageUrl" max-height="60" max-width="80" cover class="rounded"></v-img>
                            </div>
                            <div v-else class="text-error text-caption">
                                <v-icon size="small" icon="fa-solid fa-image"></v-icon>
                                Missing
                            </div>
                        </template>

                        <template v-slot:item.issues="{ item }">
                            <div v-if="item.unresolvedIssueCount > 0">
                                <v-chip
                                    v-for="issue in getUnresolvedIssues(item)"
                                    :key="issue.id"
                                    size="small"
                                    class="ma-1"
                                    :color="getIssueColor(issue.issueType)"
                                    variant="tonal">
                                    {{ getIssueLabel(issue.issueType) }}
                                </v-chip>
                            </div>
                            <v-chip v-else size="small" color="success" variant="tonal">
                                No Issues
                            </v-chip>
                        </template>

                        <template v-slot:item.ingredients="{ item }">
                            <div class="text-caption">
                                {{ countIngredients(item) }} ingredients
                            </div>
                            <div class="text-caption text-medium-emphasis">
                                in {{ countSteps(item) }} steps
                            </div>
                        </template>

                        <template v-slot:item.status="{ item }">
                            <v-chip size="small" :color="getStatusColor(item.status)" variant="flat">
                                {{ item.status }}
                            </v-chip>
                        </template>

                        <template v-slot:item.action="{ item }">
                            <v-btn variant="plain" icon size="small" @click="expandedItem = expandedItem === item.id ? null : item.id">
                                <v-icon :icon="expandedItem === item.id ? '$collapse' : '$expand'"></v-icon>
                            </v-btn>
                        </template>

                        <template v-slot:expanded-row="{ columns, item }">
                            <tr>
                                <td :colspan="columns.length">
                                    <v-container fluid class="py-4">
                                        <v-row>
                                            <v-col cols="12" md="4">
                                                <v-card variant="outlined" class="pa-2">
                                                    <v-card-title class="text-subtitle-2">Recipe Preview</v-card-title>
                                                    <v-divider></v-divider>
                                                    <v-card-text>
                                                        <v-img v-if="item.imageUrl" :src="item.imageUrl" max-height="150" class="mb-2 rounded"></v-img>
                                                        <div class="text-body-2 font-weight-medium mb-1">{{ item.name }}</div>
                                                        <div class="text-caption text-medium-emphasis" v-if="item.recipeData?.servings">
                                                            Servings: {{ item.recipeData.servings }}
                                                        </div>
                                                        <div class="text-caption text-medium-emphasis" v-if="item.recipeData?.description">
                                                            {{ item.recipeData.description }}
                                                        </div>
                                                    </v-card-text>
                                                </v-card>
                                            </v-col>
                                            <v-col cols="12" md="8">
                                                <v-card variant="outlined" class="pa-2">
                                                    <v-card-title class="text-subtitle-2">
                                                        Ingredients & Steps
                                                        <v-spacer></v-spacer>
                                                        <v-chip size="small" color="primary" variant="tonal">
                                                            {{ countIngredients(item) }} ingredients
                                                        </v-chip>
                                                    </v-card-title>
                                                    <v-divider></v-divider>
                                                    <v-card-text>
                                                        <v-row v-for="(step, idx) in item.recipeData?.steps || []" :key="idx" dense>
                                                            <v-col cols="12">
                                                                <v-chip size="small" color="primary" class="mr-2">#{{ idx + 1 }}</v-chip>
                                                                <span class="text-caption">{{ step.instruction?.substring(0, 100) }}{{ step.instruction?.length > 100 ? '...' : '' }}</span>
                                                            </v-col>
                                                            <v-col cols="12" v-if="step.ingredients?.length">
                                                                <v-chip
                                                                    v-for="(ing, ingIdx) in step.ingredients"
                                                                    :key="ingIdx"
                                                                    size="small"
                                                                    variant="outlined"
                                                                    class="ma-1">
                                                                    <span v-if="ing.amount">{{ ing.amount }}</span>
                                                                    <span v-if="ing.unit?.name" class="ml-1">{{ ing.unit.name }}</span>
                                                                    <span v-if="ing.food?.name" class="ml-1 font-weight-medium">{{ ing.food.name }}</span>
                                                                    <span v-if="ing.note" class="ml-1 text-medium-emphasis">({{ ing.note }})</span>
                                                                </v-chip>
                                                            </v-col>
                                                        </v-row>
                                                    </v-card-text>
                                                </v-card>
                                            </v-col>
                                        </v-row>
                                        <v-row class="mt-2">
                                            <v-col>
                                                <v-card variant="outlined" class="pa-2">
                                                    <v-card-title class="text-subtitle-2">Issues</v-card-title>
                                                    <v-divider></v-divider>
                                                    <v-list density="compact">
                                                        <v-list-item v-for="issue in item.issues || []" :key="issue.id" :disabled="issue.resolved">
                                                            <template #prepend>
                                                                <v-icon :color="getIssueColor(issue.issueType)">
                                                                    {{ issue.resolved ? '$check' : '$alert' }}
                                                                </v-icon>
                                                            </template>
                                                            <v-list-item-title>
                                                                <strong>{{ getIssueLabel(issue.issueType) }}</strong>
                                                                <span class="text-medium-emphasis"> - {{ issue.message }}</span>
                                                            </v-list-item-title>
                                                            <v-list-item-subtitle v-if="issue.fieldName || issue.originalValue">
                                                                <span v-if="issue.fieldName">Field: {{ issue.fieldName }}</span>
                                                                <span v-if="issue.originalValue" class="ml-2">Original: {{ issue.originalValue }}</span>
                                                                <span v-if="issue.suggestedValue" class="ml-2 text-success">Suggested: {{ issue.suggestedValue }}</span>
                                                            </v-list-item-subtitle>
                                                            <template #append>
                                                                <v-chip size="small" :color="issue.resolved ? 'success' : 'warning'" variant="tonal">
                                                                    {{ issue.resolved ? 'Resolved' : issue.severity }}
                                                                </v-chip>
                                                            </template>
                                                        </v-list-item>
                                                        <v-list-item v-if="!item.issues?.length">
                                                            <v-list-item-title class="text-medium-emphasis">No issues detected</v-list-item-title>
                                                        </v-list-item>
                                                    </v-list>
                                                </v-card>
                                            </v-col>
                                        </v-row>
                                    </v-container>
                                </td>
                            </tr>
                        </template>
                    </v-data-table-server>
                </v-card>
            </v-col>
        </v-row>

        <v-dialog v-model="dialogUnitFix" max-width="600px">
            <v-card>
                <v-closable-card-title v-model="dialogUnitFix" title="Bulk Fix Units"></v-closable-card-title>
                <v-card-text>
                    <v-alert variant="tonal" type="info" class="mb-4">
                        Replace a unit name across all selected recipes.
                    </v-alert>
                    <v-text-field
                        v-model="unitFix.original"
                        label="Original Unit Name"
                        placeholder="e.g. tbsp, cup, g"
                        :disabled="loadingAction">
                    </v-text-field>
                    <model-select
                        model="Unit"
                        v-model="unitFix.target"
                        label="Replace With"
                        allow-create
                        append-to-body
                        :disabled="loadingAction">
                    </model-select>
                </v-card-text>
                <v-card-actions>
                    <v-spacer></v-spacer>
                    <v-btn @click="dialogUnitFix = false" variant="tonal" :disabled="loadingAction">Cancel</v-btn>
                    <v-btn color="primary" @click="applyUnitFix" :loading="loadingAction" :disabled="!unitFix.original || !unitFix.target">
                        Apply to {{ selectedIds.length }} recipes
                    </v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>

        <v-dialog v-model="dialogMergeFood" max-width="600px">
            <v-card>
                <v-closable-card-title v-model="dialogMergeFood" title="Bulk Merge Ingredients"></v-closable-card-title>
                <v-card-text>
                    <v-alert variant="tonal" type="info" class="mb-4">
                        Merge multiple ingredient names into a single food item across all selected recipes.
                    </v-alert>
                    <v-combobox
                        v-model="foodMerge.originals"
                        label="Original Ingredient Names"
                        multiple
                        :items="getAllFoodNamesFromSelected()"
                        placeholder="Select or type ingredient names to merge"
                        :disabled="loadingAction">
                    </v-combobox>
                    <model-select
                        model="Food"
                        v-model="foodMerge.target"
                        label="Target Ingredient"
                        allow-create
                        append-to-body
                        :disabled="loadingAction"
                        class="mt-4">
                    </model-select>
                </v-card-text>
                <v-card-actions>
                    <v-spacer></v-spacer>
                    <v-btn @click="dialogMergeFood = false" variant="tonal" :disabled="loadingAction">Cancel</v-btn>
                    <v-btn color="primary" @click="applyFoodMerge" :loading="loadingAction" :disabled="foodMerge.originals.length === 0 || !foodMerge.target">
                        Apply to {{ selectedIds.length }} recipes
                    </v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>

        <v-dialog v-model="dialogImageFix" max-width="600px">
            <v-card>
                <v-closable-card-title v-model="dialogImageFix" title="Bulk Set Recipe Images"></v-closable-card-title>
                <v-card-text>
                    <v-alert variant="tonal" type="info" class="mb-4">
                        Set a default image URL for all selected recipes that are missing images.
                    </v-alert>
                    <v-text-field
                        v-model="imageFix.url"
                        label="Image URL"
                        placeholder="https://..."
                        :disabled="loadingAction">
                    </v-text-field>
                    <div v-if="imageFix.url" class="mt-4">
                        <div class="text-caption mb-2">Preview:</div>
                        <v-img :src="imageFix.url" max-height="150" cover class="rounded"></v-img>
                    </div>
                </v-card-text>
                <v-card-actions>
                    <v-spacer></v-spacer>
                    <v-btn @click="dialogImageFix = false" variant="tonal" :disabled="loadingAction">Cancel</v-btn>
                    <v-btn color="primary" @click="applyImageFix" :loading="loadingAction" :disabled="!imageFix.url">
                        Apply to {{ selectedIds.length }} recipes
                    </v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>

        <v-dialog v-model="dialogApprove" max-width="500px">
            <v-card>
                <v-closable-card-title v-model="dialogApprove" title="Approve and Import"></v-closable-card-title>
                <v-card-text>
                    Are you sure you want to approve and import <strong>{{ selectedIds.length }}</strong> selected recipes?
                    This will create them in the recipe database.
                </v-card-text>
                <v-card-actions>
                    <v-spacer></v-spacer>
                    <v-btn @click="dialogApprove = false" variant="tonal" :disabled="loadingAction">Cancel</v-btn>
                    <v-btn color="success" @click="approveSelected" :loading="loadingAction">
                        Import Recipes
                    </v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>

        <v-dialog v-model="dialogReject" max-width="500px">
            <v-card>
                <v-closable-card-title v-model="dialogReject" title="Reject Recipes"></v-closable-card-title>
                <v-card-text>
                    Are you sure you want to reject <strong>{{ selectedIds.length }}</strong> selected recipes?
                    They will be marked as rejected and not imported.
                </v-card-text>
                <v-card-actions>
                    <v-spacer></v-spacer>
                    <v-btn @click="dialogReject = false" variant="tonal" :disabled="loadingAction">Cancel</v-btn>
                    <v-btn color="error" @click="rejectSelected" :loading="loadingAction">
                        Reject
                    </v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>

    </v-container>
</template>

<script lang="ts" setup>

import {computed, onMounted, ref} from "vue";
import {ApiApi, Food, ImportRecipe, Unit} from "@/openapi";
import {useMessageStore, MessageType, PreparedMessage} from "@/stores/MessageStore";
import {useI18n} from "vue-i18n";
import ModelSelect from "@/components/inputs/ModelSelect.vue";
import VClosableCardTitle from "@/components/dialogs/VClosableCardTitle.vue";

const {t} = useI18n()
const api = new ApiApi()

const loading = ref(false)
const loadingAction = ref(false)
const importRecipes = ref<ImportRecipe[]>([])
const activeTab = ref('all')
const selectedItems = ref<any[]>([])
const expandedItem = ref<number | null>(null)

const dialogUnitFix = ref(false)
const dialogMergeFood = ref(false)
const dialogImageFix = ref(false)
const dialogApprove = ref(false)
const dialogReject = ref(false)

const unitFix = ref({
    original: '',
    target: null as Unit | null
})

const foodMerge = ref({
    originals: [] as string[],
    target: null as Food | null
})

const imageFix = ref({
    url: ''
})

const tableHeaders = [
    {title: 'Name', key: 'name', sortable: false},
    {title: 'Image', key: 'image', sortable: false, width: '100px'},
    {title: 'Issues', key: 'issues', sortable: false},
    {title: 'Ingredients', key: 'ingredients', sortable: false, width: '120px'},
    {title: 'Status', key: 'status', sortable: false, width: '100px'},
    {title: 'Actions', key: 'action', sortable: false, width: '60px'}
]

const selectedIds = computed(() => selectedItems.value.map((i: any) => i.id))

const isAllSelected = computed(() => selectedIds.value.length === filteredRecipes.value.length && filteredRecipes.value.length > 0)

const pendingCount = computed(() => importRecipes.value.filter(r => r.status === 'PENDING').length)
const unitErrorCount = computed(() => getRecipesByIssueType('UNIT_ERROR').length)
const missingImageCount = computed(() => getRecipesByIssueType('MISSING_IMAGE').length)
const duplicateFoodCount = computed(() => getRecipesByIssueType('DUPLICATE_FOOD').length)

const filteredRecipes = computed(() => {
    if (activeTab.value === 'all') {
        return importRecipes.value
    }
    return getRecipesByIssueType(activeTab.value)
})

function getRecipesByIssueType(issueType: string): ImportRecipe[] {
    return importRecipes.value.filter(r =>
        r.issues?.some((i: any) => i.issueType === issueType && !i.resolved)
    )
}

function getUnresolvedIssues(item: ImportRecipe) {
    return item.issues?.filter((i: any) => !i.resolved) || []
}

function getIssueColor(issueType: string): string {
    const colorMap: Record<string, string> = {
        'UNIT_ERROR': 'warning',
        'FIELD_MISMATCH': 'error',
        'MISSING_IMAGE': 'error',
        'DUPLICATE_FOOD': 'info',
        'OTHER': 'secondary'
    }
    return colorMap[issueType] || 'secondary'
}

function getIssueLabel(issueType: string): string {
    const labelMap: Record<string, string> = {
        'UNIT_ERROR': 'Unit Error',
        'FIELD_MISMATCH': 'Field Mismatch',
        'MISSING_IMAGE': 'Missing Image',
        'DUPLICATE_FOOD': 'Duplicate Food',
        'OTHER': 'Other'
    }
    return labelMap[issueType] || issueType
}

function getStatusColor(status: string): string {
    const colorMap: Record<string, string> = {
        'PENDING': 'warning',
        'APPROVED': 'success',
        'REJECTED': 'error'
    }
    return colorMap[status] || 'secondary'
}

function countIngredients(item: ImportRecipe): number {
    let count = 0
    if (item.recipeData?.steps) {
        for (const step of item.recipeData.steps) {
            count += step.ingredients?.length || 0
        }
    }
    return count
}

function countSteps(item: ImportRecipe): number {
    return item.recipeData?.steps?.length || 0
}

function getAllFoodNamesFromSelected(): string[] {
    const names = new Set<string>()
    for (const recipe of importRecipes.value.filter(r => selectedIds.value.includes(r.id!))) {
        if (recipe.recipeData?.steps) {
            for (const step of recipe.recipeData.steps) {
                if (step.ingredients) {
                    for (const ing of step.ingredients) {
                        if (ing.food?.name) {
                            names.add(ing.food.name)
                        }
                    }
                }
            }
        }
    }
    return Array.from(names).sort()
}

function loadImportRecipes() {
    loading.value = true
    api.apiImportRecipeList({status: 'PENDING', pageSize: 100}).then((response: any) => {
        importRecipes.value = response.results || []
    }).catch(err => {
        useMessageStore().addError(err)
    }).finally(() => {
        loading.value = false
    })
}

function toggleSelectAll() {
    if (isAllSelected.value) {
        selectedItems.value = []
    } else {
        selectedItems.value = [...filteredRecipes.value]
    }
}

async function applyUnitFix() {
    if (!unitFix.value.original || !unitFix.value.target?.id) return
    loadingAction.value = true
    try {
        const response = await (api as any).apiImportRecipeBatchUpdateUnitCreate({
            ids: selectedIds.value,
            original_unit: unitFix.value.original,
            target_unit_id: unitFix.value.target.id
        })
        useMessageStore().addMessage(MessageType.SUCCESS, `Updated ${response.updatedCount} unit references`, 3000)
        dialogUnitFix.value = false
        unitFix.value = {original: '', target: null}
        loadImportRecipes()
    } catch (err) {
        useMessageStore().addError(err as any)
    } finally {
        loadingAction.value = false
    }
}

async function applyFoodMerge() {
    if (foodMerge.value.originals.length === 0 || !foodMerge.value.target?.id) return
    loadingAction.value = true
    try {
        const response = await (api as any).apiImportRecipeBatchMergeFoodCreate({
            ids: selectedIds.value,
            original_food_names: foodMerge.value.originals,
            target_food_id: foodMerge.value.target.id
        })
        useMessageStore().addMessage(MessageType.SUCCESS, `Merged ${response.updatedCount} ingredient references`, 3000)
        dialogMergeFood.value = false
        foodMerge.value = {originals: [], target: null}
        loadImportRecipes()
    } catch (err) {
        useMessageStore().addError(err as any)
    } finally {
        loadingAction.value = false
    }
}

async function applyImageFix() {
    if (!imageFix.value.url) return
    loadingAction.value = true
    try {
        const response = await (api as any).apiImportRecipeBatchUpdateImageCreate({
            ids: selectedIds.value,
            image_url: imageFix.value.url
        })
        useMessageStore().addMessage(MessageType.SUCCESS, `Updated ${response.updatedCount} recipe images`, 3000)
        dialogImageFix.value = false
        imageFix.value = {url: ''}
        loadImportRecipes()
    } catch (err) {
        useMessageStore().addError(err as any)
    } finally {
        loadingAction.value = false
    }
}

async function approveSelected() {
    if (selectedIds.value.length === 0) return
    loadingAction.value = true
    try {
        const response = await (api as any).apiImportRecipeBatchApproveCreate({
            ids: selectedIds.value
        })
        useMessageStore().addMessage(MessageType.SUCCESS, `Successfully imported ${response.count} recipes`, 4000)
        dialogApprove.value = false
        selectedItems.value = []
        loadImportRecipes()
    } catch (err) {
        useMessageStore().addError(err as any)
    } finally {
        loadingAction.value = false
    }
}

async function rejectSelected() {
    if (selectedIds.value.length === 0) return
    loadingAction.value = true
    try {
        const response = await (api as any).apiImportRecipeBatchRejectCreate({
            ids: selectedIds.value
        })
        useMessageStore().addMessage(MessageType.WARNING, `Rejected ${response.count} recipes`, 3000)
        dialogReject.value = false
        selectedItems.value = []
        loadImportRecipes()
    } catch (err) {
        useMessageStore().addError(err as any)
    } finally {
        loadingAction.value = false
    }
}

onMounted(() => {
    loadImportRecipes()
})

</script>

<style scoped>
</style>
