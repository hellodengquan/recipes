import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  track,
  setAnalyticsTrackFn,
  setAnalyticsFlushFn,
  resetAnalytics,
  getBufferSize,
  getBufferContents,
  flush,
  flushSync,
} from '../analytics'
import type { AnalyticsEvent } from '../analytics'

describe('analytics 工具模块', () => {
  let consoleDebugSpy: ReturnType<typeof vi.spyOn>
  let consoleWarnSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    vi.useFakeTimers()
    resetAnalytics()
    consoleDebugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
    consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.useRealTimers()
    consoleDebugSpy.mockRestore()
    consoleWarnSpy.mockRestore()
    resetAnalytics()
  })

  describe('缓冲区基本功能', () => {
    it('事件应该先进入缓冲区而不是立即发送', () => {
      const mockTrack = vi.fn()
      setAnalyticsTrackFn(mockTrack)

      track('test.event', { foo: 'bar' })

      expect(mockTrack).not.toHaveBeenCalled()
      expect(getBufferSize()).toBe(1)
    })

    it('缓冲区内容应该正确', () => {
      track('event.1')
      track('event.2', { data: 'test' })

      const contents = getBufferContents()
      expect(contents).toHaveLength(2)
      expect(contents[0].name).toBe('event.1')
      expect(contents[1].name).toBe('event.2')
      expect(contents[1].properties).toEqual({ data: 'test' })
      expect(contents[0].timestamp).toBeInstanceOf(Date)
    })
  })

  describe('场景1：5条事件触发flush', () => {
    it('缓冲区达到5条事件时应该立即触发flush', async () => {
      const mockFlush = vi.fn().mockResolvedValue(undefined)
      setAnalyticsFlushFn(mockFlush)

      for (let i = 1; i <= 4; i++) {
        track(`event.${i}`)
      }
      expect(getBufferSize()).toBe(4)
      expect(mockFlush).not.toHaveBeenCalled()

      track('event.5')

      await vi.runAllTimersAsync()

      expect(getBufferSize()).toBe(0)
      expect(mockFlush).toHaveBeenCalledTimes(1)
      const events: AnalyticsEvent[] = mockFlush.mock.calls[0][0]
      expect(events).toHaveLength(5)
      expect(events.map(e => e.name)).toEqual(['event.1', 'event.2', 'event.3', 'event.4', 'event.5'])
    })

    it('超过5条事件时应该分次flush', async () => {
      const mockFlush = vi.fn().mockResolvedValue(undefined)
      setAnalyticsFlushFn(mockFlush)

      for (let i = 1; i <= 7; i++) {
        track(`event.${i}`)
      }

      // 先让第一次 flush 完成
      await vi.advanceTimersByTimeAsync(0)

      expect(mockFlush).toHaveBeenCalledTimes(1)
      expect(mockFlush.mock.calls[0][0]).toHaveLength(5)
      expect(getBufferSize()).toBe(2)

      // 然后推进时间触发第二次 flush
      mockFlush.mockClear()
      await vi.advanceTimersByTimeAsync(2000)

      expect(mockFlush).toHaveBeenCalledTimes(1)
      expect(mockFlush.mock.calls[0][0]).toHaveLength(2)
      expect(getBufferSize()).toBe(0)
    })

    it('flush 时应该使用 flushFn 批量发送', async () => {
      const mockFlush = vi.fn().mockResolvedValue(undefined)
      setAnalyticsFlushFn(mockFlush)

      for (let i = 1; i <= 5; i++) {
        track(`event.${i}`)
      }

      await vi.runAllTimersAsync()

      expect(mockFlush).toHaveBeenCalledTimes(1)
      const events: AnalyticsEvent[] = mockFlush.mock.calls[0][0]
      expect(events).toHaveLength(5)
    })

    it('没有 flushFn 时应该使用 trackFn 逐个发送', async () => {
      const mockTrack = vi.fn()
      setAnalyticsTrackFn(mockTrack)

      for (let i = 1; i <= 5; i++) {
        track(`event.${i}`)
      }

      await vi.runAllTimersAsync()

      expect(mockTrack).toHaveBeenCalledTimes(5)
    })

    it('没有任何上报函数时应该降级到 console.debug', async () => {
      for (let i = 1; i <= 5; i++) {
        track(`event.${i}`)
      }

      await vi.runAllTimersAsync()

      expect(consoleDebugSpy).toHaveBeenCalledTimes(5)
    })
  })

  describe('场景2：超时触发flush', () => {
    it('2秒内缓冲区未满时应该自动触发flush', async () => {
      const mockFlush = vi.fn().mockResolvedValue(undefined)
      setAnalyticsFlushFn(mockFlush)

      track('event.1')
      track('event.2')
      expect(getBufferSize()).toBe(2)
      expect(mockFlush).not.toHaveBeenCalled()

      await vi.advanceTimersByTimeAsync(1999)
      expect(mockFlush).not.toHaveBeenCalled()

      await vi.advanceTimersByTimeAsync(1)
      expect(mockFlush).toHaveBeenCalledTimes(1)
      expect(getBufferSize()).toBe(0)
      expect(mockFlush.mock.calls[0][0]).toHaveLength(2)
    })

    it('flush 后新事件应该重新调度定时器', async () => {
      const mockFlush = vi.fn().mockResolvedValue(undefined)
      setAnalyticsFlushFn(mockFlush)

      for (let i = 1; i <= 5; i++) {
        track(`event.${i}`)
      }
      await vi.runAllTimersAsync()
      mockFlush.mockClear()

      track('event.6')
      expect(getBufferSize()).toBe(1)

      await vi.advanceTimersByTimeAsync(2000)
      expect(mockFlush).toHaveBeenCalledTimes(1)
      expect(mockFlush.mock.calls[0][0]).toHaveLength(1)
    })

    it('定时器不会重复调度', async () => {
      const mockFlush = vi.fn().mockResolvedValue(undefined)
      setAnalyticsFlushFn(mockFlush)

      track('event.1')
      track('event.2')
      track('event.3')

      await vi.advanceTimersByTimeAsync(2000)

      expect(mockFlush).toHaveBeenCalledTimes(1)
    })
  })

  describe('场景3：重试退避达到上限', () => {
    it('flush 失败时应该指数退避重试最多3次', async () => {
      const mockFlush = vi.fn().mockRejectedValue(new Error('network error'))
      setAnalyticsFlushFn(mockFlush)
      vi.spyOn(console, 'warn').mockImplementation(() => {})

      for (let i = 1; i <= 5; i++) {
        track(`event.${i}`)
      }

      await vi.runAllTimersAsync()

      expect(mockFlush).toHaveBeenCalledTimes(4)
      const delays = [
        0,
        500,
        1000,
        2000,
      ]
      expect(mockFlush).toHaveBeenCalledTimes(4)
    })

    it('重试失败后应该输出 console.warn 并丢弃事件', async () => {
      const mockFlush = vi.fn().mockRejectedValue(new Error('network error'))
      setAnalyticsFlushFn(mockFlush)

      for (let i = 1; i <= 5; i++) {
        track(`event.${i}`)
      }

      await vi.runAllTimersAsync()

      expect(consoleWarnSpy).toHaveBeenCalled()
      const warnArgs = (console.warn as any).mock.calls[0]
      expect(warnArgs[0]).toBe('[analytics] flush failed after max retries, dropping')
      expect(warnArgs[1]).toBe(5)
      expect(getBufferSize()).toBe(0)
    })

    it('指数退避时间间隔应该正确', async () => {
      const mockFlush = vi.fn().mockRejectedValue(new Error('network error'))
      setAnalyticsFlushFn(mockFlush)

      track('event.1')
      track('event.2')
      track('event.3')
      track('event.4')
      track('event.5')

      await vi.advanceTimersByTimeAsync(0)
      expect(mockFlush).toHaveBeenCalledTimes(1)

      await vi.advanceTimersByTimeAsync(500)
      expect(mockFlush).toHaveBeenCalledTimes(2)

      await vi.advanceTimersByTimeAsync(1000)
      expect(mockFlush).toHaveBeenCalledTimes(3)

      await vi.advanceTimersByTimeAsync(2000)
      expect(mockFlush).toHaveBeenCalledTimes(4)
    })

    it('重试成功后应该停止重试', async () => {
      let attempt = 0
      const mockFlush = vi.fn().mockImplementation(() => {
        attempt++
        if (attempt <= 2) {
          return Promise.reject(new Error('temporary error'))
        }
        return Promise.resolve()
      })
      setAnalyticsFlushFn(mockFlush)

      for (let i = 1; i <= 5; i++) {
        track(`event.${i}`)
      }

      await vi.runAllTimersAsync()

      expect(mockFlush).toHaveBeenCalledTimes(3)
      expect(consoleWarnSpy).not.toHaveBeenCalledWith(
        expect.stringContaining('flush failed after max retries')
      )
    })
  })

  describe('场景4：卸载时强制flush', () => {
    it('手动调用 flushSync 应该同步清空缓冲区', () => {
      const mockFlush = vi.fn()
      setAnalyticsFlushFn(mockFlush)

      track('event.1')
      track('event.2')
      expect(getBufferSize()).toBe(2)

      flushSync()

      expect(getBufferSize()).toBe(0)
      expect(mockFlush).toHaveBeenCalledTimes(1)
      expect(mockFlush.mock.calls[0][0]).toHaveLength(2)
    })

    it('flushSync 应该使用 flushFn 批量发送', () => {
      const mockFlush = vi.fn()
      const mockTrack = vi.fn()
      setAnalyticsFlushFn(mockFlush)
      setAnalyticsTrackFn(mockTrack)

      track('event.1')
      track('event.2')

      flushSync()

      expect(mockFlush).toHaveBeenCalledTimes(1)
      expect(mockTrack).not.toHaveBeenCalled()
    })

    it('flushSync 没有 flushFn 时应该使用 trackFn', () => {
      const mockTrack = vi.fn()
      setAnalyticsTrackFn(mockTrack)

      track('event.1')
      track('event.2')

      flushSync()

      expect(mockTrack).toHaveBeenCalledTimes(2)
    })

    it('flushSync 缓冲区为空时不做任何操作', () => {
      const mockFlush = vi.fn()
      setAnalyticsFlushFn(mockFlush)

      flushSync()

      expect(mockFlush).not.toHaveBeenCalled()
    })
  })

  describe('beforeunload 事件监听', () => {
    it('首次 track 时应该注册 beforeunload 监听器', () => {
      const addEventListenerSpy = vi.spyOn(window, 'addEventListener')

      track('event.1')

      expect(addEventListenerSpy).toHaveBeenCalledWith(
        'beforeunload',
        expect.any(Function)
      )
      addEventListenerSpy.mockRestore()
    })

    it('多次 track 不会重复注册监听器', () => {
      const addEventListenerSpy = vi.spyOn(window, 'addEventListener')

      track('event.1')
      track('event.2')
      track('event.3')

      expect(addEventListenerSpy).toHaveBeenCalledTimes(1)
      addEventListenerSpy.mockRestore()
    })
  })

  describe('flush 并发保护', () => {
    it('flush 进行中时再次调用 flush 应该被忽略', async () => {
      let flushResolve: () => void
      const flushPromise = new Promise<void>(resolve => {
        flushResolve = resolve
      })
      const mockFlush = vi.fn().mockReturnValue(flushPromise)
      setAnalyticsFlushFn(mockFlush)

      for (let i = 1; i <= 5; i++) {
        track(`event.${i}`)
      }

      const firstFlush = flush()
      const secondFlush = flush()

      flushResolve!()
      await firstFlush
      await secondFlush

      expect(mockFlush).toHaveBeenCalledTimes(1)
    })
  })

  describe('resetAnalytics', () => {
    it('应该重置所有内部状态', () => {
      const mockFlush = vi.fn()
      setAnalyticsFlushFn(mockFlush)

      track('event.1')
      track('event.2')

      expect(getBufferSize()).toBe(2)

      resetAnalytics()

      expect(getBufferSize()).toBe(0)
      track('event.3')
      expect(getBufferSize()).toBe(1)
    })
  })
})
