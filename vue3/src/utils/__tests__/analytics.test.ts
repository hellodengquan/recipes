import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { track, setAnalyticsTrackFn } from '../analytics'
import type { AnalyticsEvent } from '../analytics'

describe('analytics 工具模块', () => {
  let consoleDebugSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    setAnalyticsTrackFn(null)
    consoleDebugSpy = vi.spyOn(console, 'debug').mockImplementation(() => {})
  })

  afterEach(() => {
    consoleDebugSpy.mockRestore()
  })

  describe('缺接口降级日志', () => {
    it('未设置 track 函数时应该降级到 console.debug', () => {
      track('test.event', { foo: 'bar' })

      expect(console.debug).toHaveBeenCalled()
      const callArgs = (console.debug as any).mock.calls[0]
      expect(callArgs[0]).toBe('[analytics]')
      expect(callArgs[1]).toBe('test.event')
      expect(callArgs[2]).toEqual({ foo: 'bar' })
    })

    it('未设置 track 函数且无 properties 时也应该输出日志', () => {
      track('test.event')

      expect(console.debug).toHaveBeenCalled()
      const callArgs = (console.debug as any).mock.calls[0]
      expect(callArgs[1]).toBe('test.event')
    })
  })

  describe('自定义 track 函数', () => {
    it('设置 track 函数后应该调用该函数而非 console.debug', () => {
      const mockTrack = vi.fn()
      setAnalyticsTrackFn(mockTrack)

      track('test.event', { foo: 'bar' })

      expect(mockTrack).toHaveBeenCalled()
      expect(console.debug).not.toHaveBeenCalled()

      const event: AnalyticsEvent = mockTrack.mock.calls[0][0]
      expect(event.name).toBe('test.event')
      expect(event.properties).toEqual({ foo: 'bar' })
      expect(event.timestamp).toBeInstanceOf(Date)
    })

    it('track 函数抛出异常时应该捕获并输出 debug 日志', () => {
      const mockTrack = vi.fn().mockImplementation(() => {
        throw new Error('track error')
      })
      setAnalyticsTrackFn(mockTrack)

      expect(() => track('test.event')).not.toThrow()
      expect(console.debug).toHaveBeenCalled()
    })

    it('设置为 null 后应该恢复降级模式', () => {
      const mockTrack = vi.fn()
      setAnalyticsTrackFn(mockTrack)
      setAnalyticsTrackFn(null)

      track('test.event')

      expect(mockTrack).not.toHaveBeenCalled()
      expect(console.debug).toHaveBeenCalled()
    })
  })

  describe('事件结构', () => {
    it('事件应该包含正确的字段', () => {
      const mockTrack = vi.fn()
      setAnalyticsTrackFn(mockTrack)

      const beforeTime = Date.now()
      track('test.event', { value: 42 })
      const afterTime = Date.now()

      const event: AnalyticsEvent = mockTrack.mock.calls[0][0]
      expect(event.name).toBe('test.event')
      expect(event.properties).toEqual({ value: 42 })
      expect(event.timestamp.getTime()).toBeGreaterThanOrEqual(beforeTime)
      expect(event.timestamp.getTime()).toBeLessThanOrEqual(afterTime)
    })
  })
})
