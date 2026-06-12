
export interface IForecastFoodUsage {
    meal_plan_id: number
    meal_plan_title: string
    recipe_id: number | null
    recipe_name: string
    meal_type_name: string
    from_date: string
    required_amount: number
    unit_name: string | null
    covered_by_stock: number
    covered_by_purchase: number
}

export interface IForecastFoodEntry {
    food_id: number
    food_name: string
    unit_name: string | null
    base_unit_name: string | null
    total_inventory: number
    total_required: number
    status_available: number
    status_reserved: number
    status_to_buy: number
    usages: IForecastFoodUsage[]
}

export interface IForecastResponse {
    count: number
    results: IForecastFoodEntry[]
}

export enum ForecastStatus {
    AVAILABLE = 'available',
    RESERVED = 'reserved',
    TO_BUY = 'to_buy',
}

