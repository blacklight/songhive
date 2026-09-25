import type { paths } from "./types";
import { apiRequest } from "./client";

export type StatsTop =
  paths["/api/v1/stats/top"]["get"]["responses"]["200"]["content"]["application/json"];
export type StatsBuckets =
  paths["/api/v1/stats/plays"]["get"]["responses"]["200"]["content"]["application/json"];
export type StatsGenreBuckets =
  paths["/api/v1/stats/genres-timeline"]["get"]["responses"]["200"]["content"]["application/json"];
export type StatsReleases =
  paths["/api/v1/stats/releases"]["get"]["responses"]["200"]["content"]["application/json"];
export type StatsClock =
  paths["/api/v1/stats/clock"]["get"]["responses"]["200"]["content"]["application/json"];
export type StatsTopEntry = StatsTop["tracks"][number];

export type StatsPeriodGroupBy = "day" | "week" | "month" | "year";
export type StatsReleaseGroupBy = "decade" | "year";

export interface StatsPeriod {
  /** ISO-8601 start of the listening period (defaults to `to` - 30 days). */
  from?: string;
  /** ISO-8601 end of the listening period (defaults to now). */
  to?: string;
  /** IANA timezone used for calendar/hour bucketing (defaults to UTC). */
  tz?: string;
  /** First weekday in Python convention, 0 = Monday … 6 = Sunday. */
  weekStart?: number;
}

function periodQuery(period: StatsPeriod) {
  return { from: period.from, to: period.to, tz: period.tz };
}

export function getTopStats(
  period: StatsPeriod = {},
  limit = 10,
): Promise<StatsTop> {
  return apiRequest<StatsTop>("/stats/top", {
    query: { ...periodQuery(period), limit },
  });
}

export function getPlaysStats(
  period: StatsPeriod = {},
  groupBy: StatsPeriodGroupBy = "day",
): Promise<StatsBuckets> {
  return apiRequest<StatsBuckets>("/stats/plays", {
    query: {
      ...periodQuery(period),
      group_by: groupBy,
      week_start: period.weekStart,
    },
  });
}

export function getGenresTimeline(
  period: StatsPeriod = {},
  groupBy: StatsPeriodGroupBy = "week",
): Promise<StatsGenreBuckets> {
  return apiRequest<StatsGenreBuckets>("/stats/genres-timeline", {
    query: {
      ...periodQuery(period),
      group_by: groupBy,
      week_start: period.weekStart,
    },
  });
}

export function getReleasesStats(
  period: StatsPeriod = {},
  groupBy: StatsReleaseGroupBy = "decade",
): Promise<StatsReleases> {
  return apiRequest<StatsReleases>("/stats/releases", {
    query: { ...periodQuery(period), group_by: groupBy },
  });
}

export function getClockStats(period: StatsPeriod = {}): Promise<StatsClock> {
  return apiRequest<StatsClock>("/stats/clock", {
    query: periodQuery(period),
  });
}
