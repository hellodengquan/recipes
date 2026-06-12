import {Food, Ingredient, Recipe, ShoppingListEntry, Unit} from "@/openapi";

/**
 * enum of different options a shopping list can be grouped by
 */
export enum ShoppingGroupingOptions {
    CATEGORY = 'Category',
    CREATED_BY = 'CreatedBy',
    RECIPE = 'Recipe',
}

/**
 * Top level structure for calculated (grouped categories/foods/entries) shopping list
 * build by the ShoppingStore for usage in UI
 */
export interface IShoppingList {
    categories: Map<string, IShoppingListCategory>
}

/**
 * category in shopping list with its associated foods
 */
export interface IShoppingListCategory {
    name: string,
    foods: Map<number, IShoppingListFood>,
}

/**
 * Aggregation level for shopping list food entries
 * 0: Not aggregated - each entry shown individually
 * 1: Semi-aggregated - grouped by unit + checked + delayed state
 * 2: Fully aggregated - grouped by unit only (all states combined)
 */
export enum AggregationLevel {
    NONE = 0,
    SEMI = 1,
    FULL = 2,
}

/**
 * food in shopping list with its associated entries
 */
export interface IShoppingListFood {
    food: Food,
    entries: Map<number, ShoppingListEntry>,
    aggregatedAmounts: ShoppingLineAmount[],
    aggregationLevel: AggregationLevel,
    aggregateHistory: IAggregateSnapshot[],
}

/**
 * snapshot of aggregate state for undo functionality
 */
export interface IAggregateSnapshot {
    timestamp: Date,
    aggregationLevel: AggregationLevel,
}

/**
 * result of performing an aggregate operation
 */
export type AggregateOperationResult = {
    success: boolean,
    foodId: number,
    previousState: IAggregateSnapshot,
}

export type ShoppingLineAmount = {
    key: string,
    amount: number,
    unit: Unit,
    checked: boolean,
    delayed: boolean,
}

/**
 * flat representation of a shopping list entry used for exports
 */
export interface IShoppingExportEntry {
    amount: number,
    unit: string,
    food: string,
}

/**
 * data holder for sync queue items containing lists of ShoppingListEntry id's
 */
export interface IShoppingSyncQueueEntry {
    ids: number[],
    checked: boolean,
    status: 'waiting' | 'syncing' | 'syncing_failed_before' | 'waiting_failed_before',
}

/**
 * stats object calculated for the main structure and all subcategories
 */
export type ShoppingListStats = {
    countChecked: number,
    countUnchecked: number,
    countCheckedFood: number,
    countUncheckedFood: number,
    countUncheckedDelayed: number,
}

/**
 * different history entries used by history/undo functions to determine what to do
 * CREATED: ShoppingListEntry was created
 * CHECKED: ShoppingListEntry was checked
 * UNCHECKED: ShoppingListEntry check was removed
 * DELAY: ShoppingListEntry was postponed/delayed
 * UNDELAY: ShoppingListEntry delay was removed
 */
export type ShoppingOperationHistoryType = 'CHECKED' | 'UNCHECKED' | 'DELAY' | 'UNDELAY' | 'IGNORE' | 'UNIGNORE' | 'CREATE' | 'DESTROY'

/**
 * history event consisting of a type and affected entries
 */
export type ShoppingOperationHistoryEntry = {
    type: ShoppingOperationHistoryType,
    entries: ShoppingListEntry[]
}

export type ShoppingDialogRecipe = {
    recipe: Recipe
    entries: ShoppingDialogRecipeEntry[]
}

export type ShoppingDialogRecipeEntry = {
    amount: number,
    unit: Unit|null,
    food: Food|null,
    ingredient: Ingredient|null,
    checked: boolean,
}