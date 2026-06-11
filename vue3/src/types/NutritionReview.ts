export enum ReviewStatus {
    PENDING = 'PENDING',
    APPROVED = 'APPROVED',
    REJECTED = 'REJECTED',
    AUTO_APPROVED = 'AUTO_APPROVED',
}

export enum ConfidenceLevel {
    VERY_HIGH = 90,
    HIGH = 70,
    MEDIUM = 40,
    LOW = 20,
}

export interface NutritionReviewReasons {
    reasons: string[]
    reasons_text: string[]
}

export interface NutritionReviewItem {
    ingredient_id: number
    original_text: string
    food: {
        id: number
        name: string
        fdc_id?: number
    }
    amount: number
    unit: {
        id: number
        name: string
    }
    confidence_score: string
    review_reasons: string[]
    review_reasons_text: string[]
    recipe_id?: number
    recipe_name?: string
}

export interface NutritionReviewResult {
    confidence_score: string
    needs_review: boolean
    review_status: ReviewStatus
    review_items: NutritionReviewItem[]
    missing_ingredients: NutritionReviewReasons[]
    low_confidence_ingredients: NutritionReviewReasons[]
}

export interface NutritionReviewSummary {
    total_recipes: number
    pending_review: number
    approved: number
    rejected: number
    auto_approved: number
    average_confidence: string
    current_user_role: string
    user_permissions: NutritionReviewPermissions
    low_confidence_count: number
    missing_data_count: number
}

export interface NutritionReviewPermissions {
    view_review_status: boolean
    flag_for_review: boolean
    submit_review: boolean
    approve: boolean
    reject: boolean
    edit_nutrition: boolean
    override_auto_approval: boolean
    view_all_pending: boolean
    assign_reviewer: boolean
}

export interface NutritionPendingReviewItem {
    id: number
    recipe_id: number
    recipe_name: string
    recipe_image?: string
    confidence_score: string
    needs_review: boolean
    review_status: ReviewStatus
    missing_ingredients: number
    low_confidence_ingredients: number
    review_comment?: string
    reviewed_by?: {
        id: number
        name: string
    }
    reviewed_at?: string
    created_at: string
}

export interface NutritionReviewActionRequest {
    comment?: string
}

export interface ConfidenceFilterOption {
    label: string
    value: string
    min: number
    max: number
    color: string
}

export const CONFIDENCE_FILTERS: ConfidenceFilterOption[] = [
    { label: 'all', value: 'all', min: 0, max: 100, color: 'grey' },
    { label: 'confidence_very_low', value: 'very_low', min: 0, max: 40, color: 'error' },
    { label: 'confidence_low', value: 'low', min: 40, max: 70, color: 'warning' },
    { label: 'confidence_high', value: 'high', min: 70, max: 90, color: 'info' },
    { label: 'confidence_very_high', value: 'very_high', min: 90, max: 100, color: 'success' },
]

export function getConfidenceColor(score: number | string): string {
    const num = typeof score === 'string' ? parseFloat(score) : score
    if (num >= 90) return 'success'
    if (num >= 70) return 'info'
    if (num >= 40) return 'warning'
    return 'error'
}

export function getConfidenceText(score: number | string): string {
    const num = typeof score === 'string' ? parseFloat(score) : score
    if (num >= 90) return 'confidence_very_high'
    if (num >= 70) return 'confidence_high'
    if (num >= 40) return 'confidence_medium'
    return 'confidence_low'
}
