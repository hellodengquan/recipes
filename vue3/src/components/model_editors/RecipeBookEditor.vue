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
                        v-if="isOwner && pendingChangeRequestsCount > 0"
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
                        <template #item.action="{item}">
                            <template v-if="item.status === 'PENDING'">
                                <template v-if="isOwner">
                                    <v-btn size="small" color="success" variant="flat" class="me-1" @click="approveRequest(item)">
                                        <v-icon start icon="$check"></v-icon>
                                        {{ $t('Approve') }}
                                    </v-btn>
                                    <v-btn size="small" color="error" variant="flat" @click="rejectRequest(item)">
                                        <v-icon start icon="$close"></v-icon>
                                        {{ $t('Reject') }}
                                    </v-btn>
                                </template>
                                <template v-else-if="isRequestCreator(item)">
                                    <v-btn size="small" color="warning" variant="flat" @click="withdrawRequest(item)">
                                        <v-icon start icon="$undo"></v-icon>
                                        {{ $t('Withdraw') }}
                                    </v-btn>
                                </template>
                            </template>
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

                        <template #item.action_type="{item}">
                            <v-chip
                                :color="item.action === 'ADD' ? 'success' : 'warning'"
                                size="small"
                                variant="outlined"
                            >
                                {{ item.action === 'ADD' ? $t('AddRecipe') : $t('RemoveRecipe') }}
                            </v-chip>
                        </template>
                    </v-data-table-server>
                </v-tabs-window-item>

            </v-tabs-window>
        </v-card-text>
    </model-editor-base>
</template>

<script setup lang="ts">

import {computed, onMounted, PropType, ref, watch} from "vue";
import {ApiApi, Recipe, RecipeBook, RecipeBookEntry, RecipeBookEntryChangeRequest, User} from "@/openapi";
import {VDataTableUpdateOptions} from "@/vuetify";

import {useModelEditorFunctions} from "@/composables/useModelEditorFunctions";
import ModelEditorBase from "@/components/model_editors/ModelEditorBase.vue";
import ModelSelect from "@/components/inputs/ModelSelect.vue";
import {ErrorMessageType, MessageType, PreparedMessage, useMessageStore} from "@/stores/MessageStore";
import {useUserPreferenceStore} from "@/stores/UserPreferenceStore";
import {useI18n} from "vue-i18n";

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
const changeRequests = ref([] as RecipeBookEntryChangeRequest[])

const selectedRecipe = ref({} as Recipe)
const addRequestNote = ref('')

const tablePage = ref(1)
const itemCount = ref(0)
const changeRequestCount = ref(0)

const currentUserId = computed(() => useUserPreferenceStore().userSettings.user?.id)
const isOwner = computed(() => {
    if (!editingObj.value || !editingObj.value.createdBy || !currentUserId.value) return false
    return editingObj.value.createdBy.id === currentUserId.value
})
const pendingChangeRequestsCount = computed(() => editingObj.value?.pendingChangeRequestsCount || 0)

function isRequestCreator(item: RecipeBookEntryChangeRequest): boolean {
    if (!item.createdBy || !currentUserId.value) return false
    return item.createdBy.id === currentUserId.value
}

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
    {key: 'action', width: '1%', noBreak: true, align: 'end'},
]

onMounted(() => {
    initializeEditor()
})

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

    let api = new ApiApi()

    const params = new URLSearchParams()
    params.set('book', String(editingObj.value.id!))
    params.set('recipe', String(selectedRecipe.value.id!))
    params.set('action', 'ADD')
    params.set('status', 'PENDING')

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

function approveRequest(item: RecipeBookEntryChangeRequest) {
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
    }).catch(err => {
        useMessageStore().addError(ErrorMessageType.UPDATE_ERROR, err)
    })
}

function rejectRequest(item: RecipeBookEntryChangeRequest) {
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
    }).catch(err => {
        useMessageStore().addError(ErrorMessageType.UPDATE_ERROR, err)
    })
}

function withdrawRequest(item: RecipeBookEntryChangeRequest) {
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
    }).catch(err => {
        useMessageStore().addError(ErrorMessageType.UPDATE_ERROR, err)
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
        changeRequests.value = r.results
        changeRequestCount.value = r.count
    }).catch((err: any) => {
        useMessageStore().addError(ErrorMessageType.FETCH_ERROR, err)
    }).finally(() => {
        loading.value = false
    })
}

</script>

<style scoped>

</style>
