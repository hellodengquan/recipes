import { getCookie } from '@/utils/cookie'
import type {
    NutritionReviewResult,
    NutritionReviewSummary,
    NutritionPendingReviewItem,
    NutritionReviewItem,
} from '@/types/NutritionReview'

const BASE_URL = '/api'

function getHeaders(): Record<string, string> {
    return {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken') || '',
    }
}

async function handleResponse<T>(response: Response): Promise<T> {
    if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }))
        throw new Error(errorData.detail || `HTTP error! status: ${response.status}`)
    }
    return response.json() as Promise<T>
}

export const NutritionReviewService = {
    async getRecipeReview(recipeId: number): Promise<NutritionReviewResult> {
        const response = await fetch(`${BASE_URL}/recipe/${recipeId}/nutrition_review/`, {
            method: 'GET',
            headers: getHeaders(),
            credentials: 'same-origin',
        })
        return handleResponse<NutritionReviewResult>(response)
    },

    async flagRecipeForReview(recipeId: number, data?: { comment?: string; reason?: string; confidence_score?: number }): Promise<NutritionReviewResult> {
        const body: Record<string, unknown> = {}
        if (data?.comment !== undefined) {
            body.reason = data.comment
        }
        if (data?.reason !== undefined) {
            body.reason = data.reason
        }
        if (data?.confidence_score !== undefined) {
            body.confidence_score = data.confidence_score
        }

        const response = await fetch(`${BASE_URL}/recipe/${recipeId}/nutrition_flag/`, {
            method: 'POST',
            headers: getHeaders(),
            credentials: 'same-origin',
            body: Object.keys(body).length > 0 ? JSON.stringify(body) : undefined,
        })

        const result = await handleResponse<Record<string, unknown>>(response)
        return this.getRecipeReview(recipeId)
    },

    async approveRecipeNutrition(recipeId: number, data?: { comment?: string }): Promise<NutritionReviewResult> {
        const body = data?.comment ? JSON.stringify({ comment: data.comment }) : undefined

        const response = await fetch(`${BASE_URL}/recipe/${recipeId}/nutrition_approve/`, {
            method: 'POST',
            headers: getHeaders(),
            credentials: 'same-origin',
            body,
        })

        await handleResponse<Record<string, unknown>>(response)
        return this.getRecipeReview(recipeId)
    },

    async rejectRecipeNutrition(recipeId: number, data?: { comment?: string }): Promise<NutritionReviewResult> {
        const body = data?.comment ? JSON.stringify({ comment: data.comment }) : undefined

        const response = await fetch(`${BASE_URL}/recipe/${recipeId}/nutrition_reject/`, {
            method: 'POST',
            headers: getHeaders(),
            credentials: 'same-origin',
            body,
        })

        await handleResponse<Record<string, unknown>>(response)
        return this.getRecipeReview(recipeId)
    },

    async getReviewSummary(): Promise<NutritionReviewSummary> {
        const response = await fetch(`${BASE_URL}/nutrition-review/summary/`, {
            method: 'GET',
            headers: getHeaders(),
            credentials: 'same-origin',
        })
        return handleResponse<NutritionReviewSummary>(response)
    },

    async getPendingReviews(params?: {
        page?: number
        page_size?: number
        confidence_min?: number
        confidence_max?: number
        review_status?: string
    }): Promise<{
        count: number
        results: NutritionPendingReviewItem[]
        next?: string
        previous?: string
    }> {
        const url = new URL(`${BASE_URL}/nutrition-review/pending/`, window.location.origin)
        if (params) {
            Object.entries(params).forEach(([key, value]) => {
                if (value !== undefined && value !== null) {
                    url.searchParams.append(key, String(value))
                }
            })
        }

        const response = await fetch(url.toString(), {
            method: 'GET',
            headers: getHeaders(),
            credentials: 'same-origin',
        })
        return handleResponse<{
            count: number
            results: NutritionPendingReviewItem[]
            next?: string
            previous?: string
        }>(response)
    },

    async getAllPendingReviewItems(params?: {
        confidence_min?: number
        confidence_max?: number
    }): Promise<NutritionReviewItem[]> {
        const pending = await this.getPendingReviews({
            page_size: 100,
            confidence_min: params?.confidence_min,
            confidence_max: params?.confidence_max,
        })

        const allItems: NutritionReviewItem[] = []

        for (const pendingItem of pending.results) {
            if (!pendingItem.recipe_id) continue

            try {
                const detail = await this.getRecipeReview(pendingItem.recipe_id)
                const itemsWithRecipe = detail.review_items.map(item => ({
                    ...item,
                    recipe_id: pendingItem.recipe_id,
                    recipe_name: pendingItem.recipe_name,
                }))
                allItems.push(...itemsWithRecipe)
            } catch (err) {
                console.error(`Failed to load review for recipe ${pendingItem.recipe_id}:`, err)
            }
        }

        return allItems
    },
}

export default NutritionReviewService
