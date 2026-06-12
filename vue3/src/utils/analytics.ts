export interface AnalyticsEvent {
    name: string
    properties?: Record<string, any>
    timestamp: Date
}

export type AnalyticsTrackFn = (event: AnalyticsEvent) => void

let _trackFn: AnalyticsTrackFn | null = null

/**
 * Set the global analytics tracking function.
 * If not set, all track() calls will fall back to console.debug.
 */
export function setAnalyticsTrackFn(fn: AnalyticsTrackFn | null): void {
    _trackFn = fn
}

/**
 * Track an analytics event.
 * If no track function is configured, falls back to console.debug.
 */
export function track(name: string, properties?: Record<string, any>): void {
    const event: AnalyticsEvent = {
        name,
        properties,
        timestamp: new Date(),
    }

    if (_trackFn) {
        try {
            _trackFn(event)
        } catch (err) {
            console.debug('[analytics] track function error:', err)
        }
    } else {
        console.debug('[analytics]', event.name, event.properties)
    }
}
