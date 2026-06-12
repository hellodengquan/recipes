export interface AnalyticsEvent {
    name: string
    properties?: Record<string, any>
    timestamp: Date
}

export type AnalyticsTrackFn = (event: AnalyticsEvent) => void
export type AnalyticsFlushFn = (events: AnalyticsEvent[]) => Promise<void> | void

const DEFAULT_BUFFER_SIZE = 5
const DEFAULT_FLUSH_INTERVAL_MS = 2000
const MAX_RETRY_ATTEMPTS = 3
const INITIAL_RETRY_DELAY_MS = 500

let _trackFn: AnalyticsTrackFn | null = null
let _flushFn: AnalyticsFlushFn | null = null
let _eventBuffer: AnalyticsEvent[] = []
let _flushTimerId: ReturnType<typeof setTimeout> | null = null
let _isFlushing = false
let _beforeunloadListener: ((e: BeforeUnloadEvent) => void) | null = null

/**
 * Set the global analytics tracking function for single events.
 * If only this is set (no flushFn), events will be sent individually when flushed.
 */
export function setAnalyticsTrackFn(fn: AnalyticsTrackFn | null): void {
    _trackFn = fn
}

/**
 * Set the global analytics flush function for batch events.
 * If set, takes precedence over trackFn for batch operations.
 */
export function setAnalyticsFlushFn(fn: AnalyticsFlushFn | null): void {
    _flushFn = fn
}

/**
 * Start listening for beforeunload to flush remaining events.
 * Called automatically on first track() call.
 */
function ensureBeforeunloadListener(): void {
    if (_beforeunloadListener || typeof window === 'undefined') return

    _beforeunloadListener = () => {
        flushSync()
    }
    window.addEventListener('beforeunload', _beforeunloadListener)
}

/**
 * Remove the beforeunload listener (primarily for testing cleanup).
 */
export function removeBeforeunloadListener(): void {
    if (_beforeunloadListener && typeof window !== 'undefined') {
        window.removeEventListener('beforeunload', _beforeunloadListener)
        _beforeunloadListener = null
    }
}

/**
 * Schedule a flush to occur after the default interval.
 * If there's already a pending timer, does nothing.
 */
function scheduleFlush(): void {
    if (_flushTimerId !== null) return

    _flushTimerId = setTimeout(() => {
        _flushTimerId = null
        flush().catch(err => {
            console.warn('[analytics] scheduled flush failed:', err)
        })
    }, DEFAULT_FLUSH_INTERVAL_MS)
}

/**
 * Cancel any pending flush timer.
 */
function cancelScheduledFlush(): void {
    if (_flushTimerId !== null) {
        clearTimeout(_flushTimerId)
        _flushTimerId = null
    }
}

/**
 * Send buffered events using the configured flush function with retry logic.
 * Retries up to MAX_RETRY_ATTEMPTS with exponential backoff.
 * If all retries fail, events are discarded and a warning is logged.
 */
export async function flush(): Promise<void> {
    if (_isFlushing) return
    if (_eventBuffer.length === 0) {
        cancelScheduledFlush()
        return
    }

    _isFlushing = true
    cancelScheduledFlush()

    const events = [..._eventBuffer]
    _eventBuffer = []

    try {
        await sendWithRetry(events)
    } catch (err) {
        console.warn('[analytics] flush failed after max retries, dropping', events.length, 'events:', err)
    } finally {
        _isFlushing = false
        if (_eventBuffer.length > 0) {
            scheduleFlush()
        }
    }
}

/**
 * Synchronous version of flush for use in beforeunload handler.
 * Uses sendWithoutRetry to avoid async operations during page unload.
 */
export function flushSync(): void {
    if (_eventBuffer.length === 0) return
    cancelScheduledFlush()

    const events = [..._eventBuffer]
    _eventBuffer = []

    try {
        sendWithoutRetry(events)
    } catch (err) {
        console.warn('[analytics] sync flush failed, dropping', events.length, 'events:', err)
    }
}

/**
 * Send events with exponential backoff retry logic.
 */
async function sendWithRetry(events: AnalyticsEvent[]): Promise<void> {
    let lastError: unknown = null

    for (let attempt = 0; attempt <= MAX_RETRY_ATTEMPTS; attempt++) {
        try {
            await sendEvents(events)
            return
        } catch (err) {
            lastError = err
            if (attempt < MAX_RETRY_ATTEMPTS) {
                const delayMs = INITIAL_RETRY_DELAY_MS * Math.pow(2, attempt)
                await new Promise(resolve => setTimeout(resolve, delayMs))
            }
        }
    }

    throw lastError
}

/**
 * Send events without retry (for sync flush).
 */
function sendWithoutRetry(events: AnalyticsEvent[]): void {
    try {
        if (_flushFn) {
            const result = _flushFn(events)
            if (result instanceof Promise) {
                result.catch(err => console.debug('[analytics] async flushFn in sync mode failed:', err))
            }
        } else if (_trackFn) {
            events.forEach(event => {
                try {
                    _trackFn!(event)
                } catch (err) {
                    console.debug('[analytics] track function error:', err)
                }
            })
        } else {
            events.forEach(event => {
                console.debug('[analytics]', event.name, event.properties)
            })
        }
    } catch (err) {
        console.debug('[analytics] send without retry failed:', err)
    }
}

/**
 * Core send function that dispatches events to the appropriate handler.
 */
async function sendEvents(events: AnalyticsEvent[]): Promise<void> {
    if (_flushFn) {
        await Promise.resolve(_flushFn(events))
    } else if (_trackFn) {
        events.forEach(event => _trackFn!(event))
    } else {
        events.forEach(event => {
            console.debug('[analytics]', event.name, event.properties)
        })
    }
}

/**
 * Track an analytics event.
 * Adds event to buffer and flushes when buffer is full or interval elapses.
 */
export function track(name: string, properties?: Record<string, any>): void {
    ensureBeforeunloadListener()

    const event: AnalyticsEvent = {
        name,
        properties,
        timestamp: new Date(),
    }

    _eventBuffer.push(event)

    if (_eventBuffer.length >= DEFAULT_BUFFER_SIZE) {
        flush().catch(err => {
            console.warn('[analytics] buffer full flush failed:', err)
        })
    } else {
        scheduleFlush()
    }
}

/**
 * Reset all internal state (primarily for testing).
 * Clears buffer, cancels timers, removes listeners.
 */
export function resetAnalytics(): void {
    cancelScheduledFlush()
    _eventBuffer = []
    _isFlushing = false
    _trackFn = null
    _flushFn = null
    removeBeforeunloadListener()
}

/**
 * Get current buffer size (primarily for testing).
 */
export function getBufferSize(): number {
    return _eventBuffer.length
}

/**
 * Get current buffer contents (primarily for testing).
 */
export function getBufferContents(): AnalyticsEvent[] {
    return [..._eventBuffer]
}
