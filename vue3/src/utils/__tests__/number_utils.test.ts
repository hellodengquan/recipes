import {beforeEach, describe, expect, it, vi} from 'vitest'
import {createPinia, setActivePinia} from 'pinia'
import {frac, roundDecimals, calculateFoodAmount} from '@/utils/number_utils'
import {useUserPreferenceStore} from '@/stores/UserPreferenceStore'

vi.mock('@/stores/UserPreferenceStore', () => ({
    useUserPreferenceStore: vi.fn(),
}))

function mockUserSettings(overrides: Partial<{ ingredientDecimals: number; useFractions: boolean }> = {}) {
    const defaults = {
        ingredientDecimals: 2,
        useFractions: false,
    }
    ;(useUserPreferenceStore as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
        userSettings: {...defaults, ...overrides},
    })
}

describe('frac', () => {
    it('converts integer to mixed fraction', () => {
        expect(frac(3, 16, true)).toEqual([3, 0, 1])
    })

    it('converts integer to improper fraction when mixed=false', () => {
        expect(frac(3, 16, false)).toEqual([0, 3, 1])
    })

    it('converts 0.5 to 1/2', () => {
        const [q, n, d] = frac(0.5, 16, true)
        expect(q).toBe(0)
        expect(n).toBe(1)
        expect(d).toBe(2)
    })

    it('converts 1.5 to 1 1/2 (mixed)', () => {
        const [q, n, d] = frac(1.5, 16, true)
        expect(q).toBe(1)
        expect(n).toBe(1)
        expect(d).toBe(2)
    })

    it('converts 1.5 to 3/2 (improper)', () => {
        const [q, n, d] = frac(1.5, 16, false)
        expect(q).toBe(0)
        expect(n).toBe(3)
        expect(d).toBe(2)
    })

    it('converts 0.333 with denominator limit 16', () => {
        const [q, n, d] = frac(0.333, 16, true)
        expect(q).toBe(0)
        expect(n / d).toBeCloseTo(0.333, 1)
        expect(d).toBeLessThanOrEqual(16)
    })

    it('converts pi approximation with D=16', () => {
        const [q, n, d] = frac(Math.PI, 16, true)
        expect(q).toBe(3)
        expect(n / d).toBeCloseTo(0.14159, 2)
        expect(d).toBeLessThanOrEqual(16)
    })

    it('handles zero', () => {
        expect(frac(0, 16, true)).toEqual([0, 0, 1])
    })

    it('handles negative values', () => {
        const [q, n, d] = frac(-1.25, 16, true)
        expect(d).toBeGreaterThan(0)
        const value = q + n / d
        expect(value).toBeCloseTo(-1.25, 5)
    })

    it('handles very small values', () => {
        const [q, n, d] = frac(0.001, 16, true)
        expect(q).toBe(0)
        expect(n / d).toBeGreaterThanOrEqual(0)
        expect(n / d).toBeLessThan(1)
    })

    it('respects denominator limit D=1', () => {
        expect(frac(0.7, 1, true)).toEqual([1, 0, 1])
    })

    it('handles exactly representable fractions', () => {
        expect(frac(0.25, 16, true)).toEqual([0, 1, 4])
        expect(frac(0.125, 16, true)).toEqual([0, 1, 8])
        expect(frac(0.0625, 16, true)).toEqual([0, 1, 16])
    })

    it('handles 2.333333 as 2 1/3', () => {
        const [q, n, d] = frac(2.333333, 16, true)
        expect(q).toBe(2)
        expect(n).toBe(1)
        expect(d).toBe(3)
    })
})

describe('roundDecimals', () => {
    beforeEach(() => {
        setActivePinia(createPinia())
    })

    it('defaults to 2 decimals when userSettings is empty', () => {
        ;(useUserPreferenceStore as unknown as ReturnType<typeof vi.fn>).mockReturnValue({
            userSettings: {},
        })
        expect(roundDecimals(3.14159)).toBe(3.14)
    })

    it('rounds to 0 decimals', () => {
        mockUserSettings({ingredientDecimals: 0})
        expect(roundDecimals(2.7)).toBe(3)
        expect(roundDecimals(2.3)).toBe(2)
    })

    it('rounds to 1 decimal', () => {
        mockUserSettings({ingredientDecimals: 1})
        expect(roundDecimals(2.35)).toBe(2.4)
        expect(roundDecimals(2.34)).toBe(2.3)
    })

    it('rounds to 2 decimals (default)', () => {
        mockUserSettings({ingredientDecimals: 2})
        expect(roundDecimals(1.234)).toBe(1.23)
        expect(roundDecimals(1.235)).toBe(1.24)
    })

    it('rounds to 4 decimals', () => {
        mockUserSettings({ingredientDecimals: 4})
        expect(roundDecimals(0.12345)).toBe(0.1235)
    })

    it('handles integer input', () => {
        mockUserSettings({ingredientDecimals: 2})
        expect(roundDecimals(5)).toBe(5)
    })

    it('handles zero', () => {
        mockUserSettings({ingredientDecimals: 2})
        expect(roundDecimals(0)).toBe(0)
    })

    it('handles negative numbers', () => {
        mockUserSettings({ingredientDecimals: 2})
        expect(roundDecimals(-1.234)).toBe(-1.23)
        expect(roundDecimals(-1.235)).toBe(-1.24)
    })

    it('handles very small numbers', () => {
        mockUserSettings({ingredientDecimals: 8})
        expect(roundDecimals(0.000000123)).toBe(0.00000012)
    })

    it('handles very large numbers', () => {
        mockUserSettings({ingredientDecimals: 2})
        expect(roundDecimals(999999.999)).toBe(1000000)
    })
})

describe('calculateFoodAmount', () => {
    beforeEach(() => {
        setActivePinia(createPinia())
    })

    describe('without fractions (decimal mode)', () => {
        it('scales by factor 1', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: false})
            expect(calculateFoodAmount(100, 1, false)).toBe(100)
        })

        it('scales up by integer factor', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: false})
            expect(calculateFoodAmount(100, 2, false)).toBe(200)
        })

        it('scales down by fractional factor', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: false})
            expect(calculateFoodAmount(100, 0.5, false)).toBe(50)
        })

        it('scales by fractional factor with rounding', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: false})
            expect(calculateFoodAmount(1, 0.333, false)).toBe(0.33)
        })

        it('handles zero amount', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: false})
            expect(calculateFoodAmount(0, 5, false)).toBe(0)
        })

        it('handles negative amount', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: false})
            expect(calculateFoodAmount(-50, 2, false)).toBe(-100)
        })

        it('respects ingredientDecimals=0', () => {
            mockUserSettings({ingredientDecimals: 0, useFractions: false})
            expect(calculateFoodAmount(100, 0.333, false)).toBe(33)
        })

        it('respects ingredientDecimals=4', () => {
            mockUserSettings({ingredientDecimals: 4, useFractions: false})
            expect(calculateFoodAmount(1, 0.12345, false)).toBe(0.1235)
        })
    })

    describe('with fractions', () => {
        it('returns whole number when result is integer', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: true})
            const result = calculateFoodAmount(1, 2, true)
            expect(result).toBe('2')
            expect(typeof result).toBe('string')
            expect(result).not.toContain('<sup>')
        })

        it('returns mixed fraction for 1.5', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: true})
            const result = calculateFoodAmount(1, 1.5, true) as string
            expect(result).toContain('<sup>1</sup>')
            expect(result).toContain('<sub>2</sub>')
            expect(result).toContain('1')
            expect(result).toContain('&frasl;')
        })

        it('returns simple fraction for 0.5', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: true})
            const result = calculateFoodAmount(1, 0.5, true) as string
            expect(result).toContain('<sup>1</sup>')
            expect(result).toContain('<sub>2</sub>')
            expect(result).not.toContain('1 ')
        })

        it('returns 1/3 for 0.333', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: true})
            const result = calculateFoodAmount(1, 0.333, true) as string
            expect(result).toContain('<sup>1</sup>')
            expect(result).toContain('<sub>3</sub>')
        })

        it('falls back to decimal when frac returns [0,0,1] (cannot be represented)', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: true})
            const result = calculateFoodAmount(0, 1, true)
            expect(typeof result).toBe('number')
            expect(result).toBe(0)
        })

        it('scales fractional amount by factor', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: true})
            const result = calculateFoodAmount(0.5, 3, true) as string
            expect(result).toContain('<sup>1</sup>')
            expect(result).toContain('<sub>2</sub>')
            expect(result).toContain('1')
        })

        it('handles very small fractional amounts', () => {
            mockUserSettings({ingredientDecimals: 6, useFractions: true})
            const result = calculateFoodAmount(0.0625, 1, true)
            expect(result).toBeTruthy()
        })

        it('handles 2.25 as 2 1/4', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: true})
            const result = calculateFoodAmount(1, 2.25, true) as string
            expect(result).toContain('2')
            expect(result).toContain('<sup>1</sup>')
            expect(result).toContain('<sub>4</sub>')
        })
    })

    describe('precision edge cases', () => {
        it('handles floating point imprecision: 0.1 + 0.2', () => {
            mockUserSettings({ingredientDecimals: 10, useFractions: false})
            const result = calculateFoodAmount(0.1, 3, false)
            expect(result).toBeCloseTo(0.3, 10)
        })

        it('handles very large scaling', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: false})
            expect(calculateFoodAmount(1e6, 1e6, false)).toBe(1e12)
        })

        it('handles very small scaling within precision', () => {
            mockUserSettings({ingredientDecimals: 10, useFractions: false})
            expect(calculateFoodAmount(0.001, 0.001, false)).toBeCloseTo(0.000001, 6)
        })

        it('handles extremely small scaling rounding to zero', () => {
            mockUserSettings({ingredientDecimals: 2, useFractions: false})
            expect(calculateFoodAmount(0.001, 0.001, false)).toBe(0)
        })

        it('handles identity: amount * 1 / 1 = amount', () => {
            mockUserSettings({ingredientDecimals: 6, useFractions: false})
            const amounts = [0, 1, 0.5, 3.14159, 100, 0.001]
            for (const a of amounts) {
                expect(calculateFoodAmount(a, 1, false)).toBeCloseTo(a, 5)
            }
        })
    })
})
