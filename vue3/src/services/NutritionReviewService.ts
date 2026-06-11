import { getCookie } from '@/utils/cookie'
import type {
    NutritionReviewResult,
    NutritionReviewSummary,
    NutritionPendingReviewItem,
    NutritionReviewActionRequest,
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

    async flagRecipeForReview(recipeId: number, data?: NutritionReviewActionRequest): Promise<NutritionReviewResult> {
        const response = await fetch(`${BASE_URL}/recipe/${recipeId}/nutrition_flag/`, {
            method: 'POST',
            headers: getHeaders(),
            credentials: 'same-origin',
            body: data ? JSON.stringify(data) : undefined,
        })
        return handleResponse<NutritionReviewResult>(response)
    },

    async approveRecipeNutrition(recipeId: number, data?: NutritionReviewActionRequest): Promise<NutritionReviewResult> {
        const response = await fetch(`${BASE_URL}/recipe/${recipeId}/nutrition_approve/`, {
            method: 'POST',
            headers: getHeaders(),
            credentials: 'same-origin',
            body: data ? JSON.stringify(data) : undefined,
        })
        return handleResponse<NutritionReviewResult>(response)
    },

    async rejectRecipeNutrition(recipeId: number, data?: NutritionReviewActionRequest): Promise<NutritionReviewResult> {
        const response = await fetch(`${BASE_URL}/recipe/${recipeId}/nutrition_reject/`, {
            method: 'POST',
            headers: getHeaders(),
            credentials: 'same-origin',
            body: data ? JSON.stringify(data) : undefined,
        })
        return handleResponse<NutritionReviewResult>(response)
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
}

export default NutritionReviewService
