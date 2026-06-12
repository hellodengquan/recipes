
<template>
    <div class="forecast-list">
        <v-alert v-if="items.length === 0" type="info" variant="tonal" class="mt-2 mb-2" density="compact">
            <template v-if="status === 'available'">{{ $t('MealPlan_NoAvailableItems') }}</template>
            <template v-else-if="status === 'reserved'">{{ $t('MealPlan_NoReservedItems') }}</template>
            <template v-else-if="status === 'tobuy'">{{ $t('MealPlan_NoToBuyItems') }}</template>
            <template v-else>{{ $t('MealPlan_NoItems') }}</template>
        </v-alert>

        <v-list density="compact" lines="two" class="pa-0">
            <v-list-group
                v-for="item in sortedItems"
                :key="item.food_id"
                :value="`food-${item.food_id}`"
            >
                <template #activator="{ props: groupProps }">
                    <v-list-item
                        v-bind="groupProps"
                        class="forecast-list-item"
                        :class="itemClass(item)"
                    >
                        <template #prepend>
                            <v-icon
                                :icon="statusIcon(item)"
                                :color="statusColor(item)"
                                class="me-2"
                            ></v-icon>
                        </template>

                        <v-list-item-title class="d-flex align-center">
                            <span class="font-weight-medium">{{ item.food_name }}</span>
                            <v-spacer></v-spacer>
                            <span class="text-body-2">
                                <status-badge :item="item" :status="status"></status-badge>
                            </span>
                        </v-list-item-title>

                        <v-list-item-subtitle class="d-flex align-center">
                            <span class="text-medium-emphasis">
                                <template v-if="item.total_inventory > 0">
                                    {{ $t('MealPlan_InStock') }}:
                                    <strong>{{ formatNumber(item.total_inventory) }} {{ item.unit_name || '' }}</strong>
                                </template>
                                <template v-if="item.total_required > 0">
                                    <template v-if="item.total_inventory > 0"> | </template>
                                    {{ $t('MealPlan_Required') }}:
                                    <strong>{{ formatNumber(item.total_required) }} {{ item.unit_name || '' }}</strong>
                                </template>
                            </span>
                        </v-list-item-subtitle>
                    </v-list-item>
                </template>

                <v-list-item v-if="item.usages.length === 0" class="ps-10">
                    <v-list-item-subtitle class="text-medium-emphasis">
                        {{ $t('MealPlan_NoUsage') }}
                    </v-list-item-subtitle>
                </v-list-item>

                <v-list-item
                    v-for="(usage, idx) in item.usages"
                    :key="`${item.food_id}-usage-${idx}`"
                    class="ps-10 usage-item"
                >
                    <template #prepend>
                        <v-icon
                            icon="fa-solid fa-utensils"
                            size="small"
                            class="me-2 text-medium-emphasis"
                        ></v-icon>
                    </template>

                    <v-list-item-title class="text-body-2">
                        <strong>{{ usage.recipe_name || usage.meal_plan_title || $t('MealPlan') }}</strong>
                        <span class="text-medium-emphasis ms-1">({{ usage.meal_type_name }})</span>
                    </v-list-item-title>

                    <v-list-item-subtitle class="d-flex align-center flex-wrap">
                        <v-chip
                            size="x-small"
                            variant="tonal"
                            class="me-2 mb-1"
                            color="primary"
                        >
                            <v-icon start icon="fa-solid fa-calendar" size="12"></v-icon>
                            {{ usage.from_date }}
                        </v-chip>

                        <span class="text-body-2 mb-1">
                            {{ $t('MealPlan_Need') }}:
                            <strong>{{ formatNumber(usage.required_amount) }} {{ usage.unit_name || item.unit_name || '' }}</strong>
                        </span>

                        <v-spacer></v-spacer>

                        <span class="text-body-2 mb-1 d-flex gap-2">
                            <template v-if="usage.covered_by_stock > 0">
                                <v-chip
                                    size="x-small"
                                    variant="tonal"
                                    color="warning"
                                >
                                    <v-icon start icon="fa-solid fa-clock" size="12"></v-icon>
                                    {{ $t('MealPlan_Reserved') }}: {{ formatNumber(usage.covered_by_stock) }}
                                </v-chip>
                            </template>
                            <template v-if="usage.covered_by_purchase > 0">
                                <v-chip
                                    size="x-small"
                                    variant="tonal"
                                    color="error"
                                >
                                    <v-icon start icon="fa-solid fa-cart-shopping" size="12"></v-icon>
                                    {{ $t('MealPlan_NeedToBuy') }}: {{ formatNumber(usage.covered_by_purchase) }}
                                </v-chip>
                            </template>
                        </span>
                    </v-list-item-subtitle>
                </v-list-item>
            </v-list-group>
        </v-list>
    </div>
</template>

<script setup lang="ts">
import {computed, PropType} from "vue";
import {IForecastFoodEntry} from "@/types/MealPlanForecast";
import StatusBadge from "./StatusBadge.vue";

const props = defineProps({
    items: {
        type: Array as PropType<IForecastFoodEntry[]>,
        required: true,
        default: () => []
    },
    status: {
        type: String as PropType<'available' | 'reserved' | 'tobuy' | 'all'>,
        default: 'all'
    }
})

const sortedItems = computed(() => {
    return [...props.items].sort((a, b) => {
        if (props.status === 'tobuy') {
            return (b.status_to_buy - a.status_to_buy) || b.food_name.localeCompare(a.food_name)
        }
        if (props.status === 'reserved') {
            return (b.status_reserved - a.status_reserved) || b.food_name.localeCompare(a.food_name)
        }
        if (props.status === 'available') {
            return (b.status_available - a.status_available) || b.food_name.localeCompare(a.food_name)
        }
        return b.food_name.localeCompare(a.food_name)
    })
})

function formatNumber(n: number): string {
    if (n === 0) return '0'
    if (Number.isInteger(n)) return n.toString()
    return Number(n.toFixed(2)).toString()
}

function statusIcon(item: IForecastFoodEntry): string {
    if (props.status === 'available') return 'fa-solid fa-circle-check'
    if (props.status === 'reserved') return 'fa-solid fa-clock'
    if (props.status === 'tobuy') return 'fa-solid fa-cart-shopping'
    if (item.status_to_buy > 0) return 'fa-solid fa-cart-shopping'
    if (item.status_reserved > 0) return 'fa-solid fa-clock'
    return 'fa-solid fa-circle-check'
}

function statusColor(item: IForecastFoodEntry): string {
    if (props.status === 'available') return 'success'
    if (props.status === 'reserved') return 'warning'
    if (props.status === 'tobuy') return 'error'
    if (item.status_to_buy > 0) return 'error'
    if (item.status_reserved > 0) return 'warning'
    return 'success'
}

function itemClass(item: IForecastFoodEntry): Record<string, boolean> {
    return {
        'border-start-success': props.status === 'available' || (props.status === 'all' && item.status_to_buy === 0 && item.status_reserved === 0),
        'border-start-warning': props.status === 'reserved' || (props.status === 'all' && item.status_reserved > 0 && item.status_to_buy === 0),
        'border-start-error': props.status === 'tobuy' || (props.status === 'all' && item.status_to_buy > 0),
    }
}
</script>

<style scoped>
.forecast-list-item {
    border-start: 3px solid transparent;
    padding-inline-start: 8px !important;
}

.border-start-success {
    border-start-color: rgb(var(--v-theme-success)) !important;
}

.border-start-warning {
    border-start-color: rgb(var(--v-theme-warning)) !important;
}

.border-start-error {
    border-start-color: rgb(var(--v-theme-error)) !important;
}

.usage-item {
    padding-inline-start: 56px !important;
}
</style>

