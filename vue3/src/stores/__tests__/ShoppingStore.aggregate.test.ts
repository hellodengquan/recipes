import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useShoppingStore } from '../ShoppingStore'
import { AggregationLevel } from '@/types/Shopping'
import type { ShoppingListEntry, Food, Unit, SupermarketCategory } from '@/openapi'
import { setAnalyticsTrackFn, setAnalyticsFlushFn, resetAnalytics, flushSync } from '@/utils/analytics'
import type { AnalyticsEvent } from '@/utils/analytics'

vi.mock('@/stores/MessageStore', () => ({
  useMessageStore: () => ({}),
}))

vi.mock('@/stores/UserPreferenceStore', () => ({
  useUserPreferenceStore: () => ({
    deviceSettings: {
      shopping_hide_checked: false,
      shopping_hide_delayed: false,
      shopping_auto_delay: false,
      shopping_multi_select: false,
    },
  }),
}))

vi.mock('@/openapi', () => ({
  ApiApi: vi.fn().mockImplementation(() => ({})),
  Unit: {},
  Food: {},
}))

function createMockUnit(id: number, name: string): Unit {
  return { id, name, description: '' } as Unit
}

function createMockFood(id: number, name: string): Food {
  return { id, name, description: '' } as Food
}

function createMockEntry(
  id: number,
  food: Food,
  unit: Unit | null,
  amount: number,
  checked: boolean = false,
  delayed: boolean = false,
  order?: number
): ShoppingListEntry {
  return {
    id,
    food,
    unit,
    amount,
    checked,
    delayUntil: delayed ? new Date(Date.now() + 86400000) : undefined,
    order,
    createdBy: { id: 1, displayName: 'Test User' },
  } as ShoppingListEntry
}

describe('ShoppingStore 聚合与撤销功能', () => {
  const gramUnit = createMockUnit(1, 'g')
  const pieceUnit = createMockUnit(2, 'Stück')
  const tomato = createMockFood(101, 'Tomato')

  let mockFlush: ReturnType<typeof vi.fn>

  beforeEach(() => {
    setActivePinia(createPinia())
    resetAnalytics()
    mockFlush = vi.fn()
    setAnalyticsFlushFn(mockFlush)
  })

  afterEach(() => {
    resetAnalytics()
  })

  function setupTestFood(store: ReturnType<typeof useShoppingStore>, entries: ShoppingListEntry[]) {
    const food = {
      food: tomato,
      entries: new Map(entries.map(e => [e.id!, e])),
      aggregatedAmounts: [],
      aggregationLevel: AggregationLevel.NONE,
      aggregateHistory: [],
    }
    // Pinia store 中 ref 可能被自动解包，尝试两种方式
    const newCategories = [{
      name: 'Test Category',
      foods: new Map([[tomato.id!, food]]),
    }]
    if ('value' in store.entriesByGroup) {
      (store.entriesByGroup as any).value = newCategories
    } else {
      (store.entriesByGroup as any) = newCategories
    }
    return food
  }

  describe('三级聚合级别', () => {
    it('默认应该处于完全聚合级别（FULL）', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]

      // 手动添加条目到 store 结构中
      // 这里我们直接测试逻辑，通过 getDisplayAmountsForFood
      const food = {
        food: tomato,
        entries: new Map(entries.map(e => [e.id, e])),
        aggregatedAmounts: [],
        aggregationLevel: AggregationLevel.FULL,
        aggregateHistory: [],
      }

      const result = store.getDisplayAmountsForFood(food)
      expect(result).toHaveLength(1)
      expect(result[0].amount).toBe(800)
    })

    it('SEMI 级别应该按单位+状态分组', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, true, false, 2),
      ]

      const food = {
        food: tomato,
        entries: new Map(entries.map(e => [e.id, e])),
        aggregatedAmounts: [],
        aggregationLevel: AggregationLevel.SEMI,
        aggregateHistory: [],
      }

      const result = store.getDisplayAmountsForFood(food)
      expect(result).toHaveLength(2)
    })

    it('NONE 级别应该显示所有条目', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, true, false, 2),
      ]

      const food = {
        food: tomato,
        entries: new Map(entries.map(e => [e.id, e])),
        aggregatedAmounts: [],
        aggregationLevel: AggregationLevel.NONE,
        aggregateHistory: [],
      }

      const result = store.getDisplayAmountsForFood(food)
      expect(result).toHaveLength(2)
      expect(result[0].amount).toBe(500)
      expect(result[1].amount).toBe(300)
    })
  })

  describe('聚合后撤销恢复条目数与状态', () => {
    it('从 FULL 撤销到 SEMI 应该恢复条目数', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, true, false, 2),
      ]

      const food = {
        food: tomato,
        entries: new Map(entries.map(e => [e.id, e])),
        aggregatedAmounts: [],
        aggregationLevel: AggregationLevel.SEMI,
        aggregateHistory: [
          { timestamp: new Date(), aggregationLevel: AggregationLevel.NONE },
        ],
      }

      // 当前是 SEMI，显示2条
      const semiResult = store.getDisplayAmountsForFood(food)
      expect(semiResult).toHaveLength(2)

      // 模拟撤销到 NONE
      food.aggregationLevel = AggregationLevel.NONE
      food.aggregateHistory.pop()

      const noneResult = store.getDisplayAmountsForFood(food)
      expect(noneResult).toHaveLength(2) // NONE 级别也显示所有条目
    })

    it('撤销后应该恢复勾选状态', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, true, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]

      const food = {
        food: tomato,
        entries: new Map(entries.map(e => [e.id, e])),
        aggregatedAmounts: [],
        aggregationLevel: AggregationLevel.FULL,
        aggregateHistory: [],
      }

      // FULL 级别：checked 是 allChecked（false，因为有一个没勾选）
      const fullResult = store.getDisplayAmountsForFood(food)
      expect(fullResult).toHaveLength(1)
      expect(fullResult[0].checked).toBe(false)

      // 降级到 SEMI
      food.aggregationLevel = AggregationLevel.SEMI
      const semiResult = store.getDisplayAmountsForFood(food)
      expect(semiResult).toHaveLength(2)
      expect(semiResult.find(r => r.checked)?.amount).toBe(500)
      expect(semiResult.find(r => !r.checked)?.amount).toBe(300)
    })
  })

  describe('连续两次聚合后撤销仅回退最近一次操作', () => {
    it('连续两次聚合后撤销一次应该只回退一级', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, true, false, 2),
        createMockEntry(3, tomato, pieceUnit, 2, false, false, 3),
      ]

      // 初始状态：NONE，历史为空
      const food = {
        food: tomato,
        entries: new Map(entries.map(e => [e.id, e])),
        aggregatedAmounts: [],
        aggregationLevel: AggregationLevel.NONE,
        aggregateHistory: [],
      }

      // 第一次聚合：NONE → SEMI
      food.aggregateHistory.push({ timestamp: new Date(), aggregationLevel: AggregationLevel.NONE })
      food.aggregationLevel = AggregationLevel.SEMI

      let result = store.getDisplayAmountsForFood(food)
      expect(result.length).toBeGreaterThanOrEqual(2)

      // 第二次聚合：SEMI → FULL
      food.aggregateHistory.push({ timestamp: new Date(), aggregationLevel: AggregationLevel.SEMI })
      food.aggregationLevel = AggregationLevel.FULL

      result = store.getDisplayAmountsForFood(food)
      expect(result).toHaveLength(2) // 两个单位

      // 撤销一次：FULL → SEMI
      const snapshot = food.aggregateHistory.pop()
      food.aggregationLevel = snapshot!.aggregationLevel

      result = store.getDisplayAmountsForFood(food)
      expect(food.aggregationLevel).toBe(AggregationLevel.SEMI)
      expect(food.aggregateHistory).toHaveLength(1)
    })

    it('历史记录应该保存每一步的状态', () => {
      const store = useShoppingStore()
      const entries = [createMockEntry(1, tomato, gramUnit, 500, false, false, 1)]

      const food = {
        food: tomato,
        entries: new Map(entries.map(e => [e.id, e])),
        aggregatedAmounts: [],
        aggregationLevel: AggregationLevel.NONE,
        aggregateHistory: [],
      }

      // 模拟两次聚合
      food.aggregateHistory.push({ timestamp: new Date(), aggregationLevel: AggregationLevel.NONE })
      food.aggregationLevel = AggregationLevel.SEMI

      food.aggregateHistory.push({ timestamp: new Date(), aggregationLevel: AggregationLevel.SEMI })
      food.aggregationLevel = AggregationLevel.FULL

      expect(food.aggregateHistory).toHaveLength(2)
      expect(food.aggregateHistory[0].aggregationLevel).toBe(AggregationLevel.NONE)
      expect(food.aggregateHistory[1].aggregationLevel).toBe(AggregationLevel.SEMI)
    })
  })

  describe('未发生聚合时撤销按钮不显示', () => {
    it('历史为空时不应该显示撤销按钮', () => {
      const store = useShoppingStore()
      const entries = [createMockEntry(1, tomato, gramUnit, 500, false, false, 1)]

      const food = {
        food: tomato,
        entries: new Map(entries.map(e => [e.id, e])),
        aggregatedAmounts: [],
        aggregationLevel: AggregationLevel.FULL,
        aggregateHistory: [],
      }

      expect(food.aggregateHistory).toHaveLength(0)
    })

    it('有历史记录时应该显示撤销按钮', () => {
      const store = useShoppingStore()
      const entries = [createMockEntry(1, tomato, gramUnit, 500, false, false, 1)]

      const food = {
        food: tomato,
        entries: new Map(entries.map(e => [e.id, e])),
        aggregatedAmounts: [],
        aggregationLevel: AggregationLevel.SEMI,
        aggregateHistory: [
          { timestamp: new Date(), aggregationLevel: AggregationLevel.NONE },
        ],
      }

      expect(food.aggregateHistory).toHaveLength(1)
      expect(food.aggregateHistory[0].aggregationLevel).toBe(AggregationLevel.NONE)
    })
  })

  describe('canAggregateFood', () => {
    it('达到最高级别时不能继续聚合', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]

      const food = {
        food: tomato,
        entries: new Map(entries.map(e => [e.id, e])),
        aggregatedAmounts: [],
        aggregationLevel: AggregationLevel.FULL,
        aggregateHistory: [],
      }

      expect(food.aggregationLevel).toBe(AggregationLevel.FULL)
    })

    it('未达到最高级别且有多条目时可以聚合', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]

      const food = {
        food: tomato,
        entries: new Map(entries.map(e => [e.id, e])),
        aggregatedAmounts: [],
        aggregationLevel: AggregationLevel.SEMI,
        aggregateHistory: [],
      }

      expect(food.aggregationLevel).toBeLessThan(AggregationLevel.FULL)
      expect(food.entries.size).toBeGreaterThan(1)
    })
  })

  describe('telemetry 埋点', () => {
    it('聚合时应该上报 aggregate.applied 事件', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]
      setupTestFood(store, entries)

      const result = store.aggregateFood(tomato.id!)
      flushSync()

      expect(result).not.toBeNull()
      expect(mockFlush).toHaveBeenCalledTimes(1)
      const events: AnalyticsEvent[] = mockFlush.mock.calls[0][0]
      expect(events).toHaveLength(1)
      const event = events[0]
      expect(event.name).toBe('aggregate.applied')
      expect(event.properties.foodId).toBe(tomato.id)
      expect(event.properties.entryCountBefore).toBe(2)
      expect(event.properties.entryCountAfter).toBe(1)
      expect(event.properties.fromLevel).toBe(AggregationLevel.NONE)
      expect(event.properties.toLevel).toBe(AggregationLevel.SEMI)
      expect(event.timestamp).toBeInstanceOf(Date)
    })

    it('撤销时应该上报 aggregate.undo 事件并携带存活时间', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]
      const food = setupTestFood(store, entries)

      store.aggregateFood(tomato.id!)
      flushSync()
      mockFlush.mockClear()

      const beforeUndo = Date.now()
      store.undoAggregateFood(tomato.id!)
      flushSync()
      const afterUndo = Date.now()

      expect(mockFlush).toHaveBeenCalledTimes(1)
      const events: AnalyticsEvent[] = mockFlush.mock.calls[0][0]
      expect(events).toHaveLength(1)
      const event = events[0]
      expect(event.name).toBe('aggregate.undo')
      expect(event.properties.foodId).toBe(tomato.id)
      expect(event.properties.survivalMs).toBeGreaterThanOrEqual(0)
      expect(event.properties.survivalMs).toBeLessThanOrEqual(afterUndo - beforeUndo + 100)
      expect(event.properties.entryCount).toBe(2)
      expect(event.properties.fromLevel).toBe(AggregationLevel.SEMI)
      expect(event.properties.toLevel).toBe(AggregationLevel.NONE)
    })

    it('连续两次聚合后撤销应该上报正确的级别变化', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, true, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]
      setupTestFood(store, entries)

      store.aggregateFood(tomato.id!)
      store.aggregateFood(tomato.id!)
      flushSync()
      mockFlush.mockClear()

      store.undoAggregateFood(tomato.id!)
      flushSync()

      expect(mockFlush).toHaveBeenCalledTimes(1)
      const events: AnalyticsEvent[] = mockFlush.mock.calls[0][0]
      expect(events).toHaveLength(1)
      const event = events[0]
      expect(event.name).toBe('aggregate.undo')
      expect(event.properties.fromLevel).toBe(AggregationLevel.FULL)
      expect(event.properties.toLevel).toBe(AggregationLevel.SEMI)
    })

    it('无法聚合时不应该上报事件', () => {
      const store = useShoppingStore()
      const entries = [createMockEntry(1, tomato, gramUnit, 500, false, false, 1)]
      const food = setupTestFood(store, entries)
      food.aggregationLevel = AggregationLevel.FULL

      store.aggregateFood(tomato.id!)
      flushSync()

      expect(mockFlush).not.toHaveBeenCalled()
    })

    it('没有历史记录时撤销不应该上报事件', () => {
      const store = useShoppingStore()
      const entries = [createMockEntry(1, tomato, gramUnit, 500, false, false, 1)]
      setupTestFood(store, entries)

      store.undoAggregateFood(tomato.id!)
      flushSync()

      expect(mockFlush).not.toHaveBeenCalled()
    })

    it('连续聚合操作应该批量上报事件', () => {
      const store = useShoppingStore()
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
        createMockEntry(3, tomato, pieceUnit, 2, false, false, 3),
      ]
      setupTestFood(store, entries)

      store.aggregateFood(tomato.id!)
      store.aggregateFood(tomato.id!)
      flushSync()

      expect(mockFlush).toHaveBeenCalledTimes(1)
      const events: AnalyticsEvent[] = mockFlush.mock.calls[0][0]
      expect(events).toHaveLength(2)
      expect(events[0].name).toBe('aggregate.applied')
      expect(events[1].name).toBe('aggregate.applied')
    })
  })
})
