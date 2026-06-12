import {ShoppingListEntry, Space, Unit} from "@/openapi";
import {IShoppingListCategory, IShoppingListFood, ShoppingLineAmount} from "@/types/Shopping";
import {DeviceSettings} from "@/types/settings";
import {useUserPreferenceStore} from "@/stores/UserPreferenceStore.ts";

// -------------- SHOPPING RELATED ----------------------

/**
 * determines if an entry should be visible to the user based on its delayed/checked state and the current device settings
 * @param entry entry for which visibility should be determined
 * @param deviceSettings user device settings based on which entry visibility is controlled
 */
export function isEntryVisible(entry: ShoppingListEntry, deviceSettings: DeviceSettings) {
    let entryVisible = true
    if (isDelayed(entry) && !deviceSettings.shopping_show_delayed_entries) {
        entryVisible = false
    }
    if (entry.checked && !deviceSettings.shopping_show_checked_entries) {
        entryVisible = false
    }

    // if no list is selected show all entries
    // if -1 is selected show entries without shopping lists
    // otherwise check if at least one of the entries lists is selected
    if(deviceSettings.shopping_selected_shopping_lists.length > 0){
        if(!(deviceSettings.shopping_selected_shopping_lists.includes(-1) && entry.shoppingLists?.length == 0) && !deviceSettings.shopping_selected_shopping_lists.some(sl => (entry.shoppingLists?.findIndex(eSl => eSl.id == sl) != -1))){
            entryVisible = false
        }
    }
    return entryVisible
}

/**
 * loops through all entries of a shopping list food and determines if it should be visible based on the isEntryVisible function
 * @param slf shopping list food holder
 * @param deviceSettings user device settings based on which entry visibility is controlled
 */
export function isShoppingListFoodVisible(slf: IShoppingListFood, deviceSettings: DeviceSettings) {
    let foodVisible = false
    slf.entries.forEach(entry => {
        foodVisible = foodVisible || isEntryVisible(entry, deviceSettings)
    })
    return foodVisible
}

/**
 * determine if a shopping list entry is delayed
 * @param entry
 */
export function isDelayed(entry: ShoppingListEntry) {
    // this function is needed because the openapi typescript fetch client always replaces null with undefined, so delayUntil cant be
    // set back to null once it has been delayed once. This will hopefully be fixed at some point, until then un-delaying will set the date to 1997-1-1 00:00
    return entry.delayUntil != null && entry.delayUntil > new Date()
}

/**
 * determine if any entry in a given IShoppingListFood is delayed, if so return true
 */
export function isShoppingListFoodDelayed(slf: IShoppingListFood) {
    let hasDelayedEntry = false
    slf.entries.forEach(sle => {
        hasDelayedEntry = hasDelayedEntry || isDelayed(sle)
    })
    return hasDelayedEntry
}

/**
 * determines if a category has entries that should be visible
 * @param category
 */
export function isShoppingCategoryVisible(category: IShoppingListCategory) {
    console.log('checking if category is visible')
    let categoryVisible = false
    category.foods.forEach(food => {
        if(isShoppingListFoodVisible(food, useUserPreferenceStore().deviceSettings)){
            categoryVisible = true
        }
    })

    return categoryVisible
}

/**
 * Aggregates shopping list entries by unit, summing amounts while preserving checked and delayed states.
 * Entries with the same unit are merged regardless of their individual checked/delayed states.
 * Checked state: true only if ALL entries are checked (logical AND)
 * Delayed state: true if ANY entry is delayed (logical OR)
 * @param entries Array of ShoppingListEntry to aggregate
 * @returns Array of aggregated ShoppingLineAmount
 */
export function aggregateShoppingEntriesByUnit(entries: ShoppingListEntry[]): ShoppingLineAmount[] {
    const unitGroups = new Map<number | null, {
        amount: number;
        unit: Unit | null;
        allChecked: boolean;
        anyDelayed: boolean;
        minOrder: number;
    }>();

    entries.forEach(entry => {
        if (entry.amount <= 0) return;

        const unitKey = entry.unit?.id ?? null;
        const existing = unitGroups.get(unitKey);

        if (existing) {
            existing.amount += entry.amount;
            existing.allChecked = existing.allChecked && (entry.checked ?? false);
            existing.anyDelayed = existing.anyDelayed || isDelayed(entry);
            if ((entry.order ?? Number.MAX_SAFE_INTEGER) < existing.minOrder) {
                existing.minOrder = entry.order ?? Number.MAX_SAFE_INTEGER;
            }
        } else {
            unitGroups.set(unitKey, {
                amount: entry.amount,
                unit: entry.unit ?? null,
                allChecked: entry.checked ?? false,
                anyDelayed: isDelayed(entry),
                minOrder: entry.order ?? Number.MAX_SAFE_INTEGER,
            });
        }
    });

    return Array.from(unitGroups.entries())
        .sort((a, b) => {
            if (a[1].minOrder !== b[1].minOrder) {
                return a[1].minOrder - b[1].minOrder;
            }
            return (a[1].unit?.name ?? '').localeCompare(b[1].unit?.name ?? '');
        })
        .map(([key, group]) => ({
            key: String(key),
            amount: group.amount,
            unit: group.unit,
            checked: group.allChecked,
            delayed: group.anyDelayed,
        }));
}

// -------------- SPACE RELATED ----------------------

/**
 * checks if the given space is above any of the configured limits
 * @param space space to check limit for
 */
export function isSpaceAboveLimit(space: Space) {
    return isSpaceAboveUserLimit(space) || isSpaceAboveRecipeLimit(space) || isSpaceAboveStorageLimit(space)
}

/**
 * checks if the given space is above the user limit
 * @param space space to check limit for
 */
export function isSpaceAboveUserLimit(space: Space) {
    return space.userCount > space.maxUsers && space.maxUsers > 0
}

/**
 * checks if the given space is above the recipe limit
 * @param space space to check limit for
 */
export function isSpaceAboveRecipeLimit(space: Space) {
    return space.recipeCount > space.maxRecipes && space.maxRecipes > 0
}

/**
 * checks if the given space is at the recipe limit
 * @param space space to check limit for
 */
export function isSpaceAtRecipeLimit(space: Space) {
    return space.recipeCount >= space.maxRecipes && space.maxRecipes > 0
}

/**
 * checks if the given space is above the file storage limit
 * @param space space to check limit for
 */
export function isSpaceAboveStorageLimit(space: Space) {
    return space.fileSizeMb > space.maxFileStorageMb && space.maxFileStorageMb > 0
}