<template>
    <model-editor-base
        :loading="loading"
        :dialog="dialog"
        @save="saveObject"
        @delete="deleteObject"
        @close="emit('close'); editingObjChanged = false"
        :is-update="isUpdate()"
        :is-changed="editingObjChanged"
        :model-class="modelClass"
        :object-name="editingObjName()"
        :editing-object="editingObj">

        <v-card-text class="pa-0">
            <v-tabs v-model="tab" :disabled="loading" grow>
                <v-tab value="book">{{ $t('Book') }}</v-tab>
                <v-tab value="recipes" :disabled="!isUpdate()">{{ $t('Recipes') }}</v-tab>
                <v-tab value="change-requests" :disabled="!isUpdate()">
                    {{ $t('ChangeRequests') }}
                    <v-badge
                        v-if="pendingChangeRequestsCount > 0"
                        :content="pendingChangeRequestsCount"
                        color="warning"
                        class="ms-2"
                    ></v-badge>
                </v-tab>
            </v-tabs>
        </v-card-text>

        <v-card-text>
            <v-tabs-window v-model="tab">

                <v-tabs-window-item value="book">

                    <v-form :disabled="loading || !isOwner">
                        <v-text-field :label="$t('Name')" v-model="editingObj.name"></v-text-field>
                        <v-textarea :label="$t('Description')" v-model="editingObj.description" rows="3"></v-textarea>
                        <model-select model="User" v-model="editingObj.shared" mode="tags" :disabled="!isOwner"></model-select>
                        <model-select model="CustomFilter" v-model="editingObj.filter" :disabled="!isOwner"></model-select>
                        <v-number-input :label="$t('Order')" :hint="$t('OrderInformation')" v-model="editingObj.order" :disabled="!isOwner"></v-number-input>
                        <v-alert v-if="!isOwner && isUpdate()" type="info" class="mt-4">
                            {{ $t('CollaboratorInfo') }}
                        </v-alert>
                    </v-form>
                </v-tabs-window-item>

                <v-tabs-window-item value="recipes">
                    <template v-if="isOwner">
                        <model-select model="Recipe" v-model="selectedRecipe">
                            <template #append>
                                <v-btn icon color="create" @click="addRecipeToBook()">
                                    <v-icon icon="$create"></v-icon>
                                </v-btn>
                            </template>
                        </model-select>
                    </template>
                    <template v-else>
                        <v-row align="center">
                            <v-col cols="12" sm="8">
                                <model-select model="Recipe" v-model="selectedRecipe"></model-select>
                            </v-col>
                            <v-col cols="12" sm="4">
                                <v-btn color="success" @click="submitAddRequest()" :disabled="!selectedRecipe || !selectedRecipe.id">
                                    <v-icon start icon="$add"></v-icon>
                                    {{ $t('SubmitAddRequest') }}
                                </v-btn>
                            </v-col>
                        </v-row>
                        <v-text-field
                            v-model="addRequestNote"
                            :label="$t('RequestNote')"
                            :placeholder="$t('RequestNotePlaceholder')"
                            class="mt-2"
                        ></v-text-field>
                        <div v-if="draftSource" class="d-flex align-center mt-2">
                            <v-icon icon="$save" size="small" :color="draftSource === 'server' ? 'primary' : 'info'" class="me-2"></v-icon>
                            <span class="text-caption text-grey">
                                <template v-if="draftSource === 'server'">{{ $t('DraftSyncedAt', { time: formatDraftTime(serverDraftUpdatedAt) }) }}</template>
                                <template v-else>{{ $t('DraftSavedAt', { time: formatDraftTime(addDraftSavedAt) }) }}</template>
                            </span>
                            <v-spacer></v-spacer>
                            <v-btn size="x-small" variant="text" color="grey" @click="clearAddDraftAll">
                                {{ $t('ClearDraft') }}
                            </v-btn>
                        </div>
                    </template>

                    <v-data-table-server
                        @update:options="loadRecipeBookEntries"
                        :items="recipeBookEntries"
                        :headers="tableHeaders"
                        :items-length="itemCount"
                    >
                        <template #item.name="{item}">
                            <div class="d-flex align-center">
                                <span>{{ item.recipeContent.name }}</span>
                                <v-chip
                                    v-if="item.pendingRemoveRequest"
                                    size="x-small"
                                    color="warning"
                                    class="ms-2"
                                    variant="outlined"
                                >
                                    {{ $t('PendingRemoval') }}
                                </v-chip>
                            </div>
                        </template>

                        <template #item.action="{item}">
                            <template v-if="isOwner">
                                <v-btn icon="$delete" color="delete" @click="removeRecipeFromBook(item)"></v-btn>
                            </template>
                            <template v-else>
                                <v-btn
                                    v-if="!item.pendingRemoveRequest"
                                    icon
                                    color="warning"
                                    @click="submitRemoveRequest(item)"
                                >
                                    <v-icon icon="$delete"></v-icon>
                                </v-btn>
                                <v-tooltip v-else location="top">
                                    <template #activator="{ props }">
                                        <v-btn icon color="grey" variant="outlined" disabled v-bind="props">
                                            <v-icon icon="$clock"></v-icon>
                                        </v-btn>
                                    </template>
                                    <span>{{ $t('RemoveRequestPending') }}</span>
                                </v-tooltip>
                            </template>
                        </template>

                    </v-data-table-server>
                </v-tabs-window-item>

                <v-tabs-window-item value="change-requests">
                    <v-data-table-server
                        @update:options="loadChangeRequests"
                        :items="changeRequests"
                        :headers="changeRequestHeaders"
                        :items-length="changeRequestCount"
                    >
                        <template #item.action_type="{item}">
                            <v-chip
                                :color="item.action === 'ADD' ? 'success' : 'warning'"
                                size="small"
                                variant="outlined"
                            >
                                {{ item.action === 'ADD' ? $t('AddRecipe') : $t('RemoveRecipe') }}
                            </v-chip>
                        </template>

                        <template #item.status="{item}">
                            <v-chip
                                :color="getStatusColor(item.status)"
                                size="small"
                                variant="flat"
                            >
                                {{ $t('Status' + item.status) }}
                            </v-chip>
                        </template>

                        <template #item.operations="{item}">
                            <template v-if="item.status === 'PENDING'">
                                <template v-if="item.isBookOwner">
                                    <v-btn size="small" color="success" variant="flat" class="me-1" @click="approveRequest(item)">
                                        <v-icon start icon="$check"></v-icon>
                                        {{ $t('Approve') }}
                                    </v-btn>
                                    <v-btn size="small" color="error" variant="flat" @click="rejectRequest(item)">
                                        <v-icon start icon="$close"></v-icon>
                                        {{ $t('Reject') }}
                                    </v-btn>
                                </template>
                                <template v-else-if="item.isCreator">
                                    <v-btn size="small" color="warning" variant="flat" @click="withdrawRequest(item)">
                                        <v-icon start icon="$undo"></v-icon>
                                        {{ $t('Withdraw') }}
                                    </v-btn>
                                </template>
                            </template>
                            <template v-else-if="item.status === 'WITHDRAWN' && item.canResubmit && item.isCreator">
                                <v-btn size="small" color="primary" variant="flat" @click="openResubmitDialog(item)">
                                    <v-icon start icon="$refresh"></v-icon>
                                    {{ $t('Resubmit') }}
                                </v-btn>
                            </template>
                            <template v-else>
                                <span class="text-grey text-caption">{{ $t('Status' + item.status) }}</span>
                            </template>
                        </template>

                    </v-data-table-server>
                </v-tabs-window-item>

            </v-tabs-window>
        </v-card-text>
    </model-editor-base>

    <v-dialog v-model="resubmitDialogVisible" width="500">
        <v-card>
            <v-card-title>{{ $t('ResubmitChangeRequest') }}</v-card-title>
            <v-card-text>
                <v-row>
                    <v-col cols="12">
                        <v-chip
                            :color="resubmittingItem?.action === 'ADD' ? 'success' : 'warning'"
                            size="small"
                            variant="outlined"
                        >
                            {{ resubmittingItem?.action === 'ADD' ? $t('AddRecipe') : $t('RemoveRecipe') }}
                        </v-chip>
                        <span class="ms-2 fw-bold">{{ resubmittingItem?.recipeContent.name }}</span>
                    </v-col>
                    <v-col cols="12">
                        <v-textarea
                            v-model="resubmitNote"
                            :label="$t('RequestNote')"
                            :placeholder="$t('RequestNotePlaceholder')"
                            rows="4"
                        ></v-textarea>
                    </v-col>
                    <v-col cols="12" v-if="hasResubmitDraft">
                        <v-alert type="info" variant="tonal" density="compact">
                            <v-icon start icon="$save"></v-icon>
                            {{ $t('RestoredFromDraft') }}
                        </v-alert>
                    </v-col>
                </v-row>
            </v-card-text>
            <v-card-actions>
                <v-spacer></v-spacer>
                <v-btn variant="text" @click="resubmitDialogVisible = false">{{ $t('Cancel') }}</v-btn>
                <v-btn color="primary" @click="confirmResubmit" :disabled="resubmitLoading">
                    <v-icon start icon="$refresh"></v-icon>
                    {{ $t('Resubmit') }}
                </v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>

    <v-dialog v-model="conflictDialogVisible" width="520">
        <v-card>
            <v-card-title>{{ $t('DraftConflictTitle') }}</v-card-title>
            <v-card-text>
                <p class="mb-4">{{ $t('DraftConflictDesc') }}</p>
                <v-row>
                    <v-col cols="6">
                        <v-card variant="outlined" class="pa-3" :class="{ 'border-primary': conflictChoice === 'local' }" style="cursor: pointer;" @click="conflictChoice = 'local'">
                            <div class="text-subtitle-2 mb-1">{{ $t('DraftLocal') }}</div>
                            <div class="text-caption text-grey mb-2">{{ $t('DraftSavedAt', { time: formatDraftTime(conflictLocalDraft?.savedAt || '') }) }}</div>
                            <div v-if="conflictLocalDraft?.recipeName" class="text-body-2 mb-1">
                                <strong>{{ $t('Recipe') }}:</strong> {{ conflictLocalDraft.recipeName }}
                            </div>
                            <div v-if="conflictLocalDraft?.note" class="text-body-2">
                                <strong>{{ $t('RequestNote') }}:</strong> {{ conflictLocalDraft.note.substring(0, 80) }}{{ conflictLocalDraft.note.length > 80 ? '...' : '' }}
                            </div>
                        </v-card>
                    </v-col>
                    <v-col cols="6">
                        <v-card variant="outlined" class="pa-3" :class="{ 'border-primary': conflictChoice === 'server' }" style="cursor: pointer;" @click="conflictChoice = 'server'">
                            <div class="text-subtitle-2 mb-1">{{ $t('DraftServer') }}</div>
                            <div class="text-caption text-grey mb-2">{{ $t('DraftSyncedAt', { time: formatDraftTime(conflictServerDraft?.updatedAt || '') }) }}</div>
                            <div v-if="conflictServerDraft?.recipeName" class="text-body-2 mb-1">
                                <strong>{{ $t('Recipe') }}:</strong> {{ conflictServerDraft.recipeName }}
                            </div>
                            <div v-if="conflictServerDraft?.note" class="text-body-2">
                                <strong>{{ $t('RequestNote') }}:</strong> {{ conflictServerDraft.note.substring(0, 80) }}{{ conflictServerDraft.note.length > 80 ? '...' : '' }}
                            </div>
                        </v-card>
                    </v-col>
                </v-row>
            </v-card-text>
            <v-card-actions>
                <v-spacer></v-spacer>
                <v-btn variant="text" @click="conflictDialogVisible = false">{{ $t('Cancel') }}</v-btn>
                <v-btn color="primary" @click="resolveConflict" :disabled="!conflictChoice">
                    {{ $t('Apply') }}
                </v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>
</template>

<script setup lang="ts">

import {computed, onMounted, PropType, ref, watch} from "vue";
import {ApiApi, Recipe, RecipeBook, RecipeBookEntry, User} from "@/openapi";
import {VDataTableUpdateOptions} from "@/vuetify";

import {useModelEditorFunctions} from "@/composables/useModelEditorFunctions";
import ModelEditorBase from "@/components/model_editors/ModelEditorBase.vue";
import ModelSelect from "@/components/inputs/ModelSelect.vue";
import {ErrorMessageType, MessageType, PreparedMessage, useMessageStore} from "@/stores/MessageStore";
import {useUserPreferenceStore} from "@/stores/UserPreferenceStore";
import {useI18n} from "vue-i18n";

const DRAFT_EXPIRE_DAYS = 7
const DRAFT_KEY_PREFIX = 'tandoor:cr_draft'

interface AddRequestDraft {
    recipeId: number | null
    recipeName: string
    note: string
    savedAt: string
}

interface ResubmitDraft {
    note: string
    savedAt: string
}

interface ServerDraft {
    id: number
    book: number
    changeRequest: number | null
    draftType: string
    recipeId: number | null
    recipeName: string
    note: string
    updatedAt: string
}

function getAddDraftKey(bookId: number | undefined): string {
    return `${DRAFT_KEY_PREFIX}:add:${bookId}`
}

function getResubmitDraftKey(requestId: number): string {
    return `${DRAFT_KEY_PREFIX}:resubmit:${requestId}`
}

function isDraftExpired(savedAt: string): boolean {
    const saved = new Date(savedAt).getTime()
    const now = Date.now()
    const expireMs = DRAFT_EXPIRE_DAYS * 24 * 60 * 60 * 1000
    return now - saved > expireMs
}

function loadAddDraft(bookId: number | undefined): AddRequestDraft | null {
    if (!bookId) return null
    try {
        const raw = localStorage.getItem(getAddDraftKey(bookId))
        if (!raw) return null
        const draft = JSON.parse(raw) as AddRequestDraft
        if (isDraftExpired(draft.savedAt)) {
            localStorage.removeItem(getAddDraftKey(bookId))
            return null
        }
        return draft
    } catch {
        return null
    }
}

function saveAddDraft(bookId: number | undefined, draft: Omit<AddRequestDraft, 'savedAt'>) {
    if (!bookId) return
    try {
        const full: AddRequestDraft = {...draft, savedAt: new Date().toISOString()}
        localStorage.setItem(getAddDraftKey(bookId), JSON.stringify(full))
    } catch {
    }
}

function clearAddDraft(bookId: number | undefined) {
    if (!bookId) return
    try {
        localStorage.removeItem(getAddDraftKey(bookId))
    } catch {
    }
}

function loadResubmitDraft(requestId: number): ResubmitDraft | null {
    try {
        const raw = localStorage.getItem(getResubmitDraftKey(requestId))
        if (!raw) return null
        const draft = JSON.parse(raw) as ResubmitDraft
        if (isDraftExpired(draft.savedAt)) {
            localStorage.removeItem(getResubmitDraftKey(requestId))
            return null
        }
        return draft
    } catch {
        return null
    }
}

function saveResubmitDraft(requestId: number, draft: Omit<ResubmitDraft, 'savedAt'>) {
    try {
        const full: ResubmitDraft = {...draft, savedAt: new Date().toISOString()}
        localStorage.setItem(getResubmitDraftKey(requestId), JSON.stringify(full))
    } catch {
    }
}

function clearResubmitDraft(requestId: number) {
    try {
        localStorage.removeItem(getResubmitDraftKey(requestId))
    } catch {
    }
}

function mapServerDraft(raw: any): ServerDraft {
    return {
        id: raw.id,
        book: raw.book,
        changeRequest: raw.change_request ?? null,
        draftType: raw.draft_type,
        recipeId: raw.recipe_id ?? null,
        recipeName: raw.recipe_name || '',
        note: raw.note || '',
        updatedAt: raw.updated_at || '',
    }
}

interface ChangeRequestItem {
    id: number
    book: number
    recipe: number
    action: string
    status: string
    note: string
    isCreator: boolean
    isBookOwner: boolean
    canResubmit: boolean
    recipeContent: { name: string; id: number }
    createdBy: { id: number; displayName: string } | null
    reviewedBy: { id: number; displayName: string } | null
    reviewNote: string
    createdAt: string
    reviewedAt: string | null
}

const props = defineProps({
    item: {type: {} as PropType<RecipeBook>, required: false, default: null},
    itemId: {type: [Number, String], required: false, default: undefined},
    itemDefaults: {type: {} as PropType<RecipeBook>, required: false, default: {} as RecipeBook},
    dialog: {type: Boolean, default: false}
})

const emit = defineEmits(['create', 'save', 'delete', 'close', 'changedState'])
const {setupState, deleteObject, saveObject, isUpdate, editingObjName, loading, editingObj, editingObjChanged, modelClass} = useModelEditorFunctions<RecipeBook>('RecipeBook', emit)

watch([() => props.item, () => props.itemId], () => {
    initializeEditor()
})

const {t} = useI18n()
const tab = ref("book")
const recipeBookEntries = ref([] as RecipeBookEntry[])
const changeRequests = ref([] as ChangeRequestItem[])

const selectedRecipe = ref({} as Recipe)
const addRequestNote = ref('')

const serverAddDraft = ref<ServerDraft | null>(null)
const serverAddDraftId = ref<number | null>(null)
const draftSource = ref<'local' | 'server' | null>(null)

const resubmitDialogVisible = ref(false)
const resubmittingItem = ref<ChangeRequestItem | null>(null)
const resubmitNote = ref('')
const resubmitLoading = ref(false)
const hasResubmitDraft = ref(false)

const conflictDialogVisible = ref(false)
const conflictChoice = ref<'local' | 'server' | null>(null)
const conflictLocalDraft = ref<AddRequestDraft | null>(null)
const conflictServerDraft = ref<ServerDraft | null>(null)

const tablePage = ref(1)
const itemCount = ref(0)
const changeRequestCount = ref(0)

const currentUserId = computed(() => useUserPreferenceStore().userSettings.user?.id)
const isOwner = computed(() => {
    if (!editingObj.value || !editingObj.value.createdBy || !currentUserId.value) return false
    return editingObj.value.createdBy.id === currentUserId.value
})
const pendingChangeRequestsCount = computed(() => editingObj.value?.pendingChangeRequestsCount || 0)

const addDraftSavedAt = computed(() => {
    if (!editingObj.value?.id) return ''
    const draft = loadAddDraft(editingObj.value.id)
    return draft ? draft.savedAt : ''
})

const serverDraftUpdatedAt = computed(() => {
    return serverAddDraft.value?.updatedAt || ''
})

function getStatusColor(status: string): string {
    switch (status) {
        case 'APPROVED': return 'success'
        case 'REJECTED': return 'error'
        case 'WITHDRAWN': return 'grey'
        case 'PENDING': return 'warning'
        default: return 'grey'
    }
}

const tableHeaders = [
    {title: t('Name'), key: 'name',},
    {key: 'action', width: '1%', noBreak: true, align: 'end'},
]

const changeRequestHeaders = [
    {title: t('Recipe'), key: 'recipeContent.name'},
    {title: t('Action'), key: 'action_type'},
    {title: t('Status'), key: 'status'},
    {title: t('RequestedBy'), key: 'createdBy.displayName'},
    {title: t('CreatedAt'), key: 'createdAt'},
    {key: 'operations', width: '1%', noBreak: true, align: 'end'},
]

onMounted(() => {
    initializeEditor()
})

let draftSaveTimer: number | null = null

watch([selectedRecipe, addRequestNote], () => {
    if (!editingObj.value?.id) return
    if (draftSaveTimer) clearTimeout(draftSaveTimer)
    draftSaveTimer = window.setTimeout(() => {
        const draftData = {
            recipeId: selectedRecipe.value.id || null,
            recipeName: selectedRecipe.value.name || '',
            note: addRequestNote.value,
        }
        saveAddDraft(editingObj.value!.id, draftData)
        saveServerAddDraft(draftData)
        draftSource.value = 'local'
    }, 500)
}, { deep: true })

watch(resubmitNote, () => {
    if (!resubmittingItem.value) return
    saveResubmitDraft(resubmittingItem.value.id, {
        note: resubmitNote.value,
    })
    saveServerResubmitDraft(resubmittingItem.value.id, resubmitNote.value)
    hasResubmitDraft.value = true
})

watch(() => editingObj.value?.id, (newId) => {
    if (newId && isUpdate()) {
        restoreDrafts()
    }
})

function saveServerAddDraft(draftData: { recipeId: number | null; recipeName: string; note: string }) {
    if (!editingObj.value?.id) return
    const payload: any = {
        book: editingObj.value.id,
        draft_type: 'ADD',
        recipe_id: draftData.recipeId,
        recipe_name: draftData.recipeName,
        note: draftData.note,
        change_request: null,
    }
    if (serverAddDraftId.value) {
        fetch(`/api/change-request-draft/${serverAddDraftId.value}/`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') || '' },
            credentials: 'same-origin',
            body: JSON.stringify(payload),
        }).then(r => r.ok ? r.json() : Promise.reject(r)).then(data => {
            serverAddDraft.value = mapServerDraft(data)
            draftSource.value = 'server'
        }).catch(() => {
            serverAddDraftId.value = null
            createServerAddDraft(payload)
        })
    } else {
        createServerAddDraft(payload)
    }
}

function createServerAddDraft(payload: any) {
    fetch(`/api/change-request-draft/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') || '' },
        credentials: 'same-origin',
        body: JSON.stringify(payload),
    }).then(r => r.ok ? r.json() : Promise.reject(r)).then(data => {
        serverAddDraft.value = mapServerDraft(data)
        serverAddDraftId.value = data.id
        draftSource.value = 'server'
    }).catch(() => {})
}

function saveServerResubmitDraft(changeRequestId: number, note: string) {
    const payload: any = {
        book: editingObj.value!.id,
        draft_type: 'RESUBMIT',
        change_request: changeRequestId,
        recipe_id: null,
        recipe_name: '',
        note: note,
    }
    fetch(`/api/change-request-draft/?book=${editingObj.value!.id}&change_request=${changeRequestId}`, {
        credentials: 'same-origin',
    }).then(r => r.ok ? r.json() : Promise.reject(r)).then(data => {
        const existing = (data.results || []).find((d: any) => d.change_request === changeRequestId)
        if (existing) {
            fetch(`/api/change-request-draft/${existing.id}/`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') || '' },
                credentials: 'same-origin',
                body: JSON.stringify({ note }),
            }).catch(() => {})
        } else {
            fetch(`/api/change-request-draft/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') || '' },
                credentials: 'same-origin',
                body: JSON.stringify(payload),
            }).catch(() => {})
        }
    }).catch(() => {})
}

function deleteServerAddDraft() {
    if (!serverAddDraftId.value) return
    fetch(`/api/change-request-draft/${serverAddDraftId.value}/`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCookie('csrftoken') || '' },
        credentials: 'same-origin',
    }).catch(() => {})
    serverAddDraft.value = null
    serverAddDraftId.value = null
}

function deleteServerResubmitDraft(changeRequestId: number) {
    fetch(`/api/change-request-draft/?book=${editingObj.value!.id}&change_request=${changeRequestId}`, {
        credentials: 'same-origin',
    }).then(r => r.ok ? r.json() : Promise.reject(r)).then(data => {
        const existing = (data.results || []).find((d: any) => d.change_request === changeRequestId)
        if (existing) {
            fetch(`/api/change-request-draft/${existing.id}/`, {
                method: 'DELETE',
                headers: { 'X-CSRFToken': getCookie('csrftoken') || '' },
                credentials: 'same-origin',
            }).catch(() => {})
        }
    }).catch(() => {})
}

function restoreDrafts() {
    if (!editingObj.value?.id) return
    const bookId = editingObj.value.id

    const localDraft = loadAddDraft(bookId)

    fetch(`/api/change-request-draft/?book=${bookId}&draft_type=ADD`, {
        credentials: 'same-origin',
    }).then(r => r.ok ? r.json() : Promise.reject(r)).then(data => {
        const results = (data.results || []).map(mapServerDraft)
        const serverDraft: ServerDraft | null = results.length > 0 ? results[0] : null

        if (serverDraft) {
            serverAddDraft.value = serverDraft
            serverAddDraftId.value = serverDraft.id
        }

        if (localDraft && serverDraft) {
            const localTime = new Date(localDraft.savedAt).getTime()
            const serverTime = new Date(serverDraft.updatedAt).getTime()
            const sameContent = localDraft.recipeId === serverDraft.recipeId
                && localDraft.note === serverDraft.note
                && localDraft.recipeName === serverDraft.recipeName

            if (sameContent) {
                applyDraft(localDraft)
                draftSource.value = 'server'
            } else if (Math.abs(localTime - serverTime) < 2000) {
                applyDraft(localDraft)
                draftSource.value = 'local'
            } else {
                conflictLocalDraft.value = localDraft
                conflictServerDraft.value = serverDraft
                conflictChoice.value = serverTime > localTime ? 'server' : 'local'
                conflictDialogVisible.value = true
            }
        } else if (localDraft) {
            applyDraft(localDraft)
            draftSource.value = 'local'
            saveServerAddDraft({
                recipeId: localDraft.recipeId,
                recipeName: localDraft.recipeName,
                note: localDraft.note,
            })
        } else if (serverDraft) {
            applyServerDraft(serverDraft)
            draftSource.value = 'server'
        } else {
            draftSource.value = null
        }
    }).catch(() => {
        if (localDraft) {
            applyDraft(localDraft)
            draftSource.value = 'local'
        }
    })
}

function applyDraft(draft: AddRequestDraft) {
    if (draft.recipeId) {
        let api = new ApiApi()
        api.apiRecipeRead({ id: draft.recipeId }).then(r => {
            if (r.id) selectedRecipe.value = r
        }).catch(() => {
            if (draft.recipeName) {
                selectedRecipe.value = { id: draft.recipeId, name: draft.recipeName } as Recipe
            }
        })
    }
    if (draft.note) {
        addRequestNote.value = draft.note
    }
}

function applyServerDraft(draft: ServerDraft) {
    if (draft.recipeId) {
        let api = new ApiApi()
        api.apiRecipeRead({ id: draft.recipeId }).then(r => {
            if (r.id) selectedRecipe.value = r
        }).catch(() => {
            if (draft.recipeName) {
                selectedRecipe.value = { id: draft.recipeId, name: draft.recipeName } as Recipe
            }
        })
    }
    if (draft.note) {
        addRequestNote.value = draft.note
    }
}

function resolveConflict() {
    if (!conflictChoice.value) return
    if (conflictChoice.value === 'local' && conflictLocalDraft.value) {
        applyDraft(conflictLocalDraft.value)
        draftSource.value = 'local'
        saveServerAddDraft({
            recipeId: conflictLocalDraft.value.recipeId,
            recipeName: conflictLocalDraft.value.recipeName,
            note: conflictLocalDraft.value.note,
        })
    } else if (conflictChoice.value === 'server' && conflictServerDraft.value) {
        applyServerDraft(conflictServerDraft.value)
        draftSource.value = 'server'
        if (editingObj.value?.id) {
            saveAddDraft(editingObj.value.id, {
                recipeId: conflictServerDraft.value.recipeId,
                recipeName: conflictServerDraft.value.recipeName,
                note: conflictServerDraft.value.note,
            })
        }
    }
    conflictDialogVisible.value = false
    conflictLocalDraft.value = null
    conflictServerDraft.value = null
    conflictChoice.value = null
}

function clearAddDraftAll() {
    clearAddDraft(editingObj.value?.id)
    deleteServerAddDraft()
    if (draftSaveTimer) {
        clearTimeout(draftSaveTimer)
        draftSaveTimer = null
    }
    selectedRecipe.value = {} as Recipe
    addRequestNote.value = ''
    draftSource.value = null
}

function formatDraftTime(isoString: string): string {
    if (!isoString) return ''
    const d = new Date(isoString)
    const now = new Date()
    const diffMs = now.getTime() - d.getTime()
    const diffMins = Math.floor(diffMs / 60000)
    const diffHours = Math.floor(diffMs / 3600000)
    if (diffMins < 1) return t('JustNow')
    if (diffMins < 60) return t('MinutesAgo', { count: diffMins })
    if (diffHours < 24) return t('HoursAgo', { count: diffHours })
    return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function initializeEditor() {
    setupState(props.item, props.itemId, {
        newItemFunction: () => {
            editingObj.value.shared = [] as User[]
            recipeBookEntries.value = []
            changeRequests.value = []
        },
        existingItemFunction: () => {
            recipeBookEntries.value = []
            changeRequests.value = []
        },
        itemDefaults: props.itemDefaults
    })
}

function mapChangeRequest(raw: any): ChangeRequestItem {
    return {
        id: raw.id,
        book: raw.book,
        recipe: raw.recipe,
        action: raw.action,
        status: raw.status,
        note: raw.note || '',
        isCreator: raw.is_creator === true,
        isBookOwner: raw.is_book_owner === true,
        canResubmit: raw.can_resubmit === true,
        recipeContent: raw.recipe_content
            ? {name: raw.recipe_content.name || '', id: raw.recipe_content.id}
            : {name: '', id: 0},
        createdBy: raw.created_by
            ? {id: raw.created_by.id, displayName: raw.created_by.display_name || ''}
            : null,
        reviewedBy: raw.reviewed_by
            ? {id: raw.reviewed_by.id, displayName: raw.reviewed_by.display_name || ''}
            : null,
        reviewNote: raw.review_note || '',
        createdAt: raw.created_at || '',
        reviewedAt: raw.reviewed_at || null,
    }
}

function addRecipeToBook() {
    let api = new ApiApi()

    if (Object.keys(selectedRecipe.value).length > 0) {
        let duplicateFound = false

        recipeBookEntries.value.forEach(rBE => {
            if (rBE.recipe == selectedRecipe.value.id) {
                duplicateFound = true
            }
        })

        if (!duplicateFound) {
            api.apiRecipeBookEntryCreate({recipeBookEntry: {book: editingObj.value.id!, recipe: selectedRecipe.value.id!}}).then(r => {
                recipeBookEntries.value.push(r)
                selectedRecipe.value = {} as Recipe
            }).catch(err => {
                useMessageStore().addError(ErrorMessageType.CREATE_ERROR, err)
            })
        } else {
            selectedRecipe.value = {} as Recipe
            useMessageStore().addMessage(MessageType.WARNING, t('WarningRecipeBookEntryDuplicate'), 5000)
        }
    }
}

function submitAddRequest() {
    if (!selectedRecipe.value || !selectedRecipe.value.id || !editingObj.value.id) return

    fetch(`/api/recipe-book-entry-change-request/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken') || '',
        },
        credentials: 'same-origin',
        body: JSON.stringify({
            book: editingObj.value.id!,
            recipe: selectedRecipe.value.id!,
            action: 'ADD',
            note: addRequestNote.value,
        })
    }).then(response => {
        if (!response.ok) throw response
        return response.json()
    }).then(() => {
        selectedRecipe.value = {} as Recipe
        addRequestNote.value = ''
        clearAddDraftAll()
        useMessageStore().addPreparedMessage(PreparedMessage.CREATE_SUCCESS)
        loadChangeRequests({page: 1, itemsPerPage: 10, sortBy: [], groupBy: [], search: ''})
    }).catch(err => {
        useMessageStore().addError(ErrorMessageType.CREATE_ERROR, err)
    })
}

function submitRemoveRequest(recipeBookEntry: RecipeBookEntry) {
    if (!editingObj.value.id) return

    fetch(`/api/recipe-book-entry-change-request/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken') || '',
        },
        credentials: 'same-origin',
        body: JSON.stringify({
            book: editingObj.value.id!,
            recipe: recipeBookEntry.recipe!,
            action: 'REMOVE',
            note: '',
        })
    }).then(response => {
        if (!response.ok) throw response
        return response.json()
    }).then(() => {
        useMessageStore().addPreparedMessage(PreparedMessage.CREATE_SUCCESS)
        loadRecipeBookEntries({page: tablePage.value, itemsPerPage: 10, sortBy: [], groupBy: [], search: ''})
        loadChangeRequests({page: 1, itemsPerPage: 10, sortBy: [], groupBy: [], search: ''})
    }).catch(err => {
        useMessageStore().addError(ErrorMessageType.CREATE_ERROR, err)
    })
}

function removeRecipeFromBook(recipeBookEntry: RecipeBookEntry) {
    let api = new ApiApi()

    api.apiRecipeBookEntryDestroy({id: recipeBookEntry.id!}).then((r) => {
        recipeBookEntries.value.splice(recipeBookEntries.value.findIndex(rBE => rBE.id! == recipeBookEntry.id!), 1)
        useMessageStore().addPreparedMessage(PreparedMessage.DELETE_SUCCESS)
    }).catch(err => {
        useMessageStore().addError(ErrorMessageType.DELETE_ERROR, err)
    })
}

function approveRequest(item: ChangeRequestItem) {
    fetch(`/api/recipe-book-entry-change-request/${item.id}/approve/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken') || '',
        },
        credentials: 'same-origin',
        body: JSON.stringify({})
    }).then(response => {
        if (!response.ok) throw response
        return response.json()
    }).then(() => {
        useMessageStore().addMessage(MessageType.SUCCESS, t('ChangeRequestApproved'), 3000)
        loadRecipeBookEntries({page: tablePage.value, itemsPerPage: 10, sortBy: [], groupBy: [], search: ''})
        loadChangeRequests({page: 1, itemsPerPage: 10, sortBy: [], groupBy: [], search: ''})
        refreshEditingObj()
    }).catch(err => {
        useMessageStore().addError(ErrorMessageType.UPDATE_ERROR, err)
    })
}

function rejectRequest(item: ChangeRequestItem) {
    fetch(`/api/recipe-book-entry-change-request/${item.id}/reject/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken') || '',
        },
        credentials: 'same-origin',
        body: JSON.stringify({})
    }).then(response => {
        if (!response.ok) throw response
        return response.json()
    }).then(() => {
        useMessageStore().addMessage(MessageType.INFO, t('ChangeRequestRejected'), 3000)
        loadChangeRequests({page: 1, itemsPerPage: 10, sortBy: [], groupBy: [], search: ''})
        refreshEditingObj()
    }).catch(err => {
        useMessageStore().addError(ErrorMessageType.UPDATE_ERROR, err)
    })
}

function withdrawRequest(item: ChangeRequestItem) {
    fetch(`/api/recipe-book-entry-change-request/${item.id}/withdraw/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken') || '',
        },
        credentials: 'same-origin',
        body: JSON.stringify({})
    }).then(response => {
        if (!response.ok) throw response
        return response.json()
    }).then(() => {
        useMessageStore().addMessage(MessageType.INFO, t('ChangeRequestWithdrawn'), 3000)
        loadRecipeBookEntries({page: tablePage.value, itemsPerPage: 10, sortBy: [], groupBy: [], search: ''})
        loadChangeRequests({page: 1, itemsPerPage: 10, sortBy: [], groupBy: [], search: ''})
        refreshEditingObj()
    }).catch(err => {
        useMessageStore().addError(ErrorMessageType.UPDATE_ERROR, err)
    })
}

function refreshEditingObj() {
    if (!editingObj.value.id) return
    let api = new ApiApi()
    api.apiRecipeBookRead({id: editingObj.value.id}).then(r => {
        editingObj.value = r
    })
}

function openResubmitDialog(item: ChangeRequestItem) {
    resubmittingItem.value = item
    resubmitNote.value = item.note || ''

    const localDraft = loadResubmitDraft(item.id)

    fetch(`/api/change-request-draft/?book=${editingObj.value!.id}&change_request=${item.id}`, {
        credentials: 'same-origin',
    }).then(r => r.ok ? r.json() : Promise.reject(r)).then(data => {
        const results = (data.results || []).map(mapServerDraft)
        const serverDraft: ServerDraft | null = results.length > 0 ? results[0] : null

        if (serverDraft && serverDraft.note !== item.note) {
            if (localDraft && localDraft.note !== serverDraft.note && localDraft.note !== item.note) {
                const localTime = new Date(localDraft.savedAt).getTime()
                const serverTime = new Date(serverDraft.updatedAt).getTime()
                resubmitNote.value = serverTime > localTime ? serverDraft.note : localDraft.note
            } else {
                resubmitNote.value = serverDraft.note
            }
            hasResubmitDraft.value = true
        } else if (localDraft && localDraft.note !== item.note) {
            resubmitNote.value = localDraft.note
            hasResubmitDraft.value = true
        } else {
            hasResubmitDraft.value = false
        }
    }).catch(() => {
        if (localDraft && localDraft.note !== item.note) {
            resubmitNote.value = localDraft.note
            hasResubmitDraft.value = true
        } else {
            hasResubmitDraft.value = false
        }
    })

    resubmitDialogVisible.value = true
}

function confirmResubmit() {
    if (!resubmittingItem.value || !editingObj.value.id) return

    resubmitLoading.value = true

    fetch(`/api/recipe-book-entry-change-request/${resubmittingItem.value.id}/resubmit/`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken') || '',
        },
        credentials: 'same-origin',
        body: JSON.stringify({
            note: resubmitNote.value,
        })
    }).then(response => {
        if (!response.ok) throw response
        return response.json()
    }).then(() => {
        useMessageStore().addPreparedMessage(PreparedMessage.CREATE_SUCCESS)
        resubmitDialogVisible.value = false
        if (resubmittingItem.value) {
            clearResubmitDraft(resubmittingItem.value.id)
            deleteServerResubmitDraft(resubmittingItem.value.id)
        }
        resubmittingItem.value = null
        resubmitNote.value = ''
        hasResubmitDraft.value = false
        loadRecipeBookEntries({page: tablePage.value, itemsPerPage: 10, sortBy: [], groupBy: [], search: ''})
        loadChangeRequests({page: 1, itemsPerPage: 10, sortBy: [], groupBy: [], search: ''})
        refreshEditingObj()
    }).catch(err => {
        useMessageStore().addError(ErrorMessageType.CREATE_ERROR, err)
    }).finally(() => {
        resubmitLoading.value = false
    })
}

function getCookie(name: string): string | null {
    let cookieValue = null
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';')
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim()
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1))
                break
            }
        }
    }
    return cookieValue
}

function loadRecipeBookEntries(options: VDataTableUpdateOptions) {
    let api = new ApiApi()

    loading.value = true
    window.scrollTo({top: 0, behavior: 'smooth'})

    if (tablePage.value != options.page) {
        tablePage.value = options.page
    }

    useUserPreferenceStore().deviceSettings.general_tableItemsPerPage = options.itemsPerPage

    api.apiRecipeBookEntryList({page: options.page, pageSize: options.itemsPerPage, book: editingObj.value.id}).then((r: any) => {
        recipeBookEntries.value = r.results
        itemCount.value = r.count
    }).catch((err: any) => {
        useMessageStore().addError(ErrorMessageType.FETCH_ERROR, err)
    }).finally(() => {
        loading.value = false
    })
}

function loadChangeRequests(options: VDataTableUpdateOptions) {
    if (!editingObj.value.id) return

    loading.value = true

    fetch(`/api/recipe-book-entry-change-request/?book=${editingObj.value.id}&page=${options.page}&page_size=${options.itemsPerPage}`, {
        credentials: 'same-origin',
    }).then(response => {
        if (!response.ok) throw response
        return response.json()
    }).then(r => {
        changeRequests.value = (r.results || []).map(mapChangeRequest)
        changeRequestCount.value = r.count
    }).catch((err: any) => {
        useMessageStore().addError(ErrorMessageType.FETCH_ERROR, err)
    }).finally(() => {
        loading.value = false
    })
}

</script>

<style scoped>
.border-primary {
    border: 2px solid rgb(var(--v-theme-primary)) !important;
}
</style>
