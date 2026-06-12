import { describe, it, expect, beforeEach, vi } from 'vitest'
import { aggregateShoppingEntriesByUnit, aggregateShoppingEntriesByUnitAndState } from '../logic_utils'
import type { ShoppingListEntry, Food, Unit } from '@/openapi'

vi.mock('@/stores/UserPreferenceStore', () => ({
  useUserPreferenceStore: () => ({
    deviceSettings: {
      shopping_hide_checked: false,
      shopping_hide_delayed: false,
    },
  }),
}))

function createMockUnit(id: number, name: string): Unit {
  return {
    id,
    name,
    description: '',
  } as Unit
}

function createMockFood(id: number, name: string): Food {
  return {
    id,
    name,
    description: '',
  } as Food
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

describe('aggregateShoppingEntriesByUnit', () => {
  const gramUnit = createMockUnit(1, 'g')
  const pieceUnit = createMockUnit(2, 'Stück')
  const tomato = createMockFood(1, 'Tomate')
  const onion = createMockFood(2, 'Zwiebel')

  describe('场景1: 单食材跨清单累加', () => {
    it('应该正确累加相同食材和单位的多个条目', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(1)
      expect(result[0].amount).toBe(800)
      expect(result[0].unit?.id).toBe(gramUnit.id)
      expect(result[0].checked).toBe(false)
      expect(result[0].delayed).toBe(false)
    })

    it('应该正确累加三个以上相同食材和单位的条目', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 200, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
        createMockEntry(3, tomato, gramUnit, 500, false, false, 3),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(1)
      expect(result[0].amount).toBe(1000)
    })

    it('应该忽略数量为0或负数的条目', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 0, false, false, 2),
        createMockEntry(3, tomato, gramUnit, -100, false, false, 3),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(1)
      expect(result[0].amount).toBe(500)
    })
  })

  describe('场景2: 不同单位不被错误合并', () => {
    it('相同食材但不同单位的条目不应该被合并', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, pieceUnit, 3, false, false, 2),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(2)
      expect(result[0].unit?.id).toBe(gramUnit.id)
      expect(result[0].amount).toBe(500)
      expect(result[1].unit?.id).toBe(pieceUnit.id)
      expect(result[1].amount).toBe(3)
    })

    it('应该正确处理 null 单位的条目', () => {
      const entries = [
        createMockEntry(1, tomato, null, 2, false, false, 1),
        createMockEntry(2, tomato, null, 3, false, false, 2),
        createMockEntry(3, tomato, gramUnit, 500, false, false, 3),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(2)
      const nullUnitEntry = result.find(r => r.unit === null)
      const gramUnitEntry = result.find(r => r.unit?.id === gramUnit.id)

      expect(nullUnitEntry?.amount).toBe(5)
      expect(gramUnitEntry?.amount).toBe(500)
    })

    it('相同单位不同顺序的条目应该正确累加', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 5),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 1),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(1)
      expect(result[0].amount).toBe(800)
    })
  })

  describe('场景3: 勾选状态在聚合后仍随原条目保留', () => {
    it('所有条目都勾选时，聚合后应该显示为勾选', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, true, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, true, false, 2),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(1)
      expect(result[0].amount).toBe(800)
      expect(result[0].checked).toBe(true)
    })

    it('部分条目勾选时，聚合后应该显示为未勾选（逻辑与）', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, true, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(1)
      expect(result[0].amount).toBe(800)
      expect(result[0].checked).toBe(false)
    })

    it('所有条目都未勾选时，聚合后应该显示为未勾选', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(1)
      expect(result[0].checked).toBe(false)
    })

    it('任何条目延迟时，聚合后应该显示为延迟（逻辑或）', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, true, 2),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(1)
      expect(result[0].amount).toBe(800)
      expect(result[0].delayed).toBe(true)
    })

    it('所有条目都不延迟时，聚合后应该显示为不延迟', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(1)
      expect(result[0].delayed).toBe(false)
    })

    it('勾选和延迟状态应该独立处理', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, true, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, true, true, 2),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(1)
      expect(result[0].amount).toBe(800)
      expect(result[0].checked).toBe(true)
      expect(result[0].delayed).toBe(true)
    })
  })

  describe('排序逻辑', () => {
    it('应该按最小 order 值排序', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 5),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 1),
        createMockEntry(3, tomato, pieceUnit, 3, false, false, 3),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(2)
      expect(result[0].unit?.id).toBe(gramUnit.id)
      expect(result[1].unit?.id).toBe(pieceUnit.id)
    })

    it('order 未定义的条目应该排在后面', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, undefined),
        createMockEntry(2, tomato, pieceUnit, 3, false, false, 1),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(2)
      expect(result[0].unit?.id).toBe(pieceUnit.id)
      expect(result[1].unit?.id).toBe(gramUnit.id)
    })

    it('order 相同时应该按单位名称排序', () => {
      const entries = [
        createMockEntry(1, tomato, pieceUnit, 3, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 500, false, false, 1),
      ]

      const result = aggregateShoppingEntriesByUnit(entries)

      expect(result).toHaveLength(2)
      expect(result[0].unit?.name).toBe('g')
      expect(result[1].unit?.name).toBe('Stück')
    })
  })

  describe('aggregateShoppingEntriesByUnitAndState', () => {
    it('应该按单位+勾选状态+延迟状态分别聚合并保留独立分组', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, true, false, 2),
        createMockEntry(3, tomato, gramUnit, 200, true, true, 3),
        createMockEntry(4, tomato, pieceUnit, 2, false, false, 4),
      ]

      const result = aggregateShoppingEntriesByUnitAndState(entries)

      expect(result).toHaveLength(4)
      expect(result.find(r => r.unit?.id === gramUnit.id && !r.checked && !r.delayed)?.amount).toBe(500)
      expect(result.find(r => r.unit?.id === gramUnit.id && r.checked && !r.delayed)?.amount).toBe(300)
      expect(result.find(r => r.unit?.id === gramUnit.id && r.checked && r.delayed)?.amount).toBe(200)
      expect(result.find(r => r.unit?.id === pieceUnit.id && !r.checked && !r.delayed)?.amount).toBe(2)
    })

    it('相同单位相同状态的条目应该被合并', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 300, false, false, 2),
      ]

      const result = aggregateShoppingEntriesByUnitAndState(entries)

      expect(result).toHaveLength(1)
      expect(result[0].amount).toBe(800)
      expect(result[0].checked).toBe(false)
      expect(result[0].delayed).toBe(false)
    })

    it('不同单位的条目即使状态相同也不应该被合并', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, pieceUnit, 3, false, false, 2),
      ]

      const result = aggregateShoppingEntriesByUnitAndState(entries)

      expect(result).toHaveLength(2)
    })

    it('应该按原始顺序排序', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 5),
        createMockEntry(2, tomato, pieceUnit, 3, false, false, 1),
        createMockEntry(3, tomato, gramUnit, 200, true, false, 3),
      ]

      const result = aggregateShoppingEntriesByUnitAndState(entries)

      expect(result).toHaveLength(3)
      expect(result[0].unit?.id).toBe(pieceUnit.id)
      expect(result[1].unit?.id).toBe(gramUnit.id)
      expect(result[1].checked).toBe(true)
      expect(result[2].unit?.id).toBe(gramUnit.id)
      expect(result[2].checked).toBe(false)
    })

    it('数量为0或负数的条目应该被忽略', () => {
      const entries = [
        createMockEntry(1, tomato, gramUnit, 500, false, false, 1),
        createMockEntry(2, tomato, gramUnit, 0, false, false, 2),
        createMockEntry(3, tomato, gramUnit, -100, false, false, 3),
      ]

      const result = aggregateShoppingEntriesByUnitAndState(entries)

      expect(result).toHaveLength(1)
      expect(result[0].amount).toBe(500)
    })
  })
})
