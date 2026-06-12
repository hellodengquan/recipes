
<template>
    <v-card class="w-100" :loading="store.forecastLoading">
        <v-card-title class="pb-1 pt-2 d-flex align-center">
            <v-icon icon="fa-solid fa-kitchen-set" class="me-2"></v-icon>
            {{ $t('MealPlan_IngredientForecast') }}
            <v-spacer></v-spacer>
            <v-btn size="small" variant="text" icon="fa-solid fa-rotate" @click="refreshForecast"></v-btn>
        </v-card-title>

        <v-card-text class="pt-1">
            <closable-help-alert class="mb-2" :text="$t('MealPlan_ForecastHelp')"></closable-help-alert>

            <v-tabs v-model="activeTab" density="compact" class="mb-2">
                <v-tab value="available">
                    <v-chip color="success" size="small" class="me-1" variant="flat">
                        {{ availableItems.length }}
                    </v-chip>
                    <v-icon icon="fa-solid fa-circle-check" class="me-1" color="success"></v-icon>
                    {{ $t('MealPlan_StatusAvailable') }}
                </v-tab>
                <v-tab value="reserved">
                    <v-chip color="warning" size="small" class="me-1" variant="flat">
                        {{ reservedItems.length }}
                    </v-chip>
                    <v-icon icon="fa-solid fa-clock" class="me-1" color="warning"></v-icon>
                    {{ $t('MealPlan_StatusReserved') }}
                </v-tab>
                <v-tab value="tobuy">
                    <v-chip color="error" size="small" class="me-1" variant="flat">
                        {{ toBuyItems.length }}
                    </v-chip>
                    <v-icon icon="fa-solid fa-cart-shopping" class="me-1" color="error"></v-icon>
                    {{ $t('MealPlan_StatusToBuy') }}
                </v-tab>
                <v-tab value="all">
                    <v-chip size="small" class="me-1" variant="flat">
                        {{ store.forecastData.length }}
                    </v-chip>
                    {{ $t('All') }}
                </v-tab>
            </v-tabs>

            <v-tabs-window v-model="activeTab">
                <v-tabs-window-item value="available">
                    <forecast-list :items="availableItems" status="available"></forecast-list>
                </v-tabs-window-item>

                <v-tabs-window-item value="reserved">
                    <forecast-list :items="reservedItems" status="reserved"></forecast-list>
                </v-tabs-window-item>

                <v-tabs-window-item value="tobuy">
                    <forecast-list :items="toBuyItems" status="tobuy"></forecast-list>
                </v-tabs-window-item>

                <v-tabs-window-item value="all">
                    <forecast-list :items="store.forecastData" status="all"></forecast-list>
                </v-tabs-window-item>
            </v-tabs-window>
        </v-card-text>
    </v-card>
</template>

<script setup lang="ts">
import {computed, onMounted, PropType, ref, watch} from "vue";
import {useMealPlanStore} from "@/stores/MealPlanStore";
import {DateTime} from "luxon";
import ForecastList from "./ForecastList.vue";

const props = defineProps({
    fromDate: {
        type: Object as PropType<Date>,
        default: () => DateTime.now().toJSDate()
    },
    toDate: {
        type: Object as PropType<Date>,
        default: () => DateTime.now().plus({days: 14}).toJSDate()
    }
})

const emit = defineEmits({
    'loaded': () => true
})

const store = useMealPlanStore()
const activeTab = ref('reserved')

const availableItems = computed(() => {
    return store.forecastData.filter(e => e.status_available > 0)
})

const reservedItems = computed(() => {
    return store.forecastData.filter(e => e.status_reserved > 0)
})

const toBuyItems = computed(() => {
    return store.forecastData.filter(e => e.status_to_buy > 0)
})

function refreshForecast() {
    store.loadForecast(props.fromDate, props.toDate, true).then(() => {
        emit('loaded')
    })
}

watch([() => props.fromDate, () => props.toDate], () => {
    refreshForecast()
})

onMounted(() => {
    refreshForecast()
})
</script>

<style scoped>
</style>

