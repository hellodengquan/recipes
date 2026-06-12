
<template>
    <span class="d-inline-flex flex-wrap gap-1 justify-end">
        <template v-if="showStatus('available')">
            <v-chip
                size="small"
                variant="flat"
                color="success"
                class="text-white"
            >
                <v-icon start icon="fa-solid fa-circle-check" size="12"></v-icon>
                {{ formatNumber(item.status_available) }}
                <span v-if="item.unit_name" class="opacity-80">&nbsp;{{ item.unit_name }}</span>
            </v-chip>
        </template>

        <template v-if="showStatus('reserved')">
            <v-chip
                size="small"
                variant="flat"
                color="warning"
                class="text-white"
            >
                <v-icon start icon="fa-solid fa-clock" size="12"></v-icon>
                {{ formatNumber(item.status_reserved) }}
                <span v-if="item.unit_name" class="opacity-80">&nbsp;{{ item.unit_name }}</span>
            </v-chip>
        </template>

        <template v-if="showStatus('tobuy')">
            <v-chip
                size="small"
                variant="flat"
                color="error"
                class="text-white"
            >
                <v-icon start icon="fa-solid fa-cart-shopping" size="12"></v-icon>
                {{ formatNumber(item.status_to_buy) }}
                <span v-if="item.unit_name" class="opacity-80">&nbsp;{{ item.unit_name }}</span>
            </v-chip>
        </template>
    </span>
</template>

<script setup lang="ts">
import {PropType} from "vue";
import {IForecastFoodEntry} from "@/types/MealPlanForecast";

const props = defineProps({
    item: {
        type: Object as PropType<IForecastFoodEntry>,
        required: true
    },
    status: {
        type: String as PropType<'available' | 'reserved' | 'tobuy' | 'all'>,
        default: 'all'
    }
})

function formatNumber(n: number): string {
    if (n === 0) return '0'
    if (Number.isInteger(n)) return n.toString()
    return Number(n.toFixed(2)).toString()
}

function showStatus(s: 'available' | 'reserved' | 'tobuy'): boolean {
    if (props.status === 'all') return true
    return props.status === s
}
</script>

<style scoped>
</style>

