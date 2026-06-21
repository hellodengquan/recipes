import {DateTime} from "luxon";

export function prepareMealPlanDateForTransport(localCalendarDate: Date): Date {
    const dt = DateTime.fromJSDate(localCalendarDate)
    return DateTime.utc(
        dt.year, dt.month, dt.day,
        dt.hour, dt.minute, dt.second, dt.millisecond
    ).toJSDate()
}

export function restoreMealPlanDateFromTransport(transportDate: Date): Date {
    const utcDt = DateTime.fromJSDate(transportDate).toUTC()
    return DateTime.fromObject({
        year: utcDt.year,
        month: utcDt.month,
        day: utcDt.day,
        hour: utcDt.hour,
        minute: utcDt.minute,
        second: utcDt.second,
        millisecond: utcDt.millisecond,
    }, {zone: 'local'}).toJSDate()
}

export function prepareMealPlanDateForTransportOptional(date: Date | null | undefined): Date | undefined {
    if (date == null) return undefined
    return prepareMealPlanDateForTransport(date)
}

export function restoreMealPlanDateFromTransportOptional(date: Date | null | undefined): Date | undefined {
    if (date == null) return undefined
    return restoreMealPlanDateFromTransport(date)
}

export function normalizeMealPlanDatesForWrite<T extends { fromDate: Date; toDate?: Date }>(obj: T): T {
    return {
        ...obj,
        fromDate: prepareMealPlanDateForTransport(obj.fromDate),
        toDate: prepareMealPlanDateForTransportOptional(obj.toDate),
    } as T
}

export function normalizeMealPlanDatesForRead<T extends { fromDate: Date; toDate?: Date }>(obj: T): T {
    return {
        ...obj,
        fromDate: restoreMealPlanDateFromTransport(obj.fromDate),
        toDate: restoreMealPlanDateFromTransportOptional(obj.toDate),
    } as T
}

/**
 * shifts a range of dates/any array of dates by the number of days given in the day modifier (can be positive or negative)
 * @param dateRange array of dates
 * @param dayModifier number of days to modify array of dates with
 * @return dateRange array of dates modified
 */
export function shiftDateRange(dateRange: Date[], dayModifier: number) {
    let newDateRange: Date[] = []
    dateRange.forEach(date => {
        newDateRange.push(DateTime.fromJSDate(date).plus({'days': dayModifier}).toJSDate())
    })
    return newDateRange
}

/**
 * adjust the length of a date range by either adding or removing the given number of days
 * when adding days to an empty date Range it starts with the current date
 * @param dateRange array of dates
 * @param dayModifier number of days to modify array of dates with
 * @return dateRange sorted array of dates modified
 */
export function adjustDateRangeLength(dateRange: Date[], dayModifier: number) {
    dateRange = dateRange.sort((a: Date, b: Date) => a.getTime() - b.getTime());
    if (dayModifier < 0 && dateRange.length > 1) {
        dateRange.splice(dateRange.length - Math.abs(dayModifier), Math.abs(dayModifier))
    } else {
        if (dateRange.length == 0) {
            dateRange.push(new Date())
        } else {
            let lastDate = DateTime.fromJSDate(dateRange[dateRange.length - 1])
            for (let i = 0; i < dayModifier; i++) {
                dateRange.push(lastDate.plus({'days': (i + 1)}).toJSDate())
            }
        }
    }
    return dateRange
}