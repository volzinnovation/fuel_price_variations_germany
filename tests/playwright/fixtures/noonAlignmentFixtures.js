const stationId = "a0000000-1111-2222-3333-444444444444";
const stationIdPath = stationId.split("-").join("/");
const managementDate = "2026-04-06";
const managementPath = "/data2/2026/04/06/management_boxplots.json";
const previousManagementDate = "2026-04-05";

function stationHourlyRows() {
  return Array.from({ length: 24 }, (_value, hour) => ({
    hour,
    price: hour < 12 ? -0.05 : 0.05,
  }));
}

function stationCycleRows() {
  return Array.from({ length: 25 }, (_value, cycleHour) => {
    const clockHour = (12 + cycleHour) % 24;
    const delta = cycleHour >= 12 ? -0.05 : 0.0;
    const price = 2.05 + delta;
    const markdown = Math.max(0, -delta);
    return {
      cycle_hour: cycleHour,
      clock_hour: clockHour,
      label: String(clockHour).padStart(2, "0"),
      count: 1,
      price_min: price,
      price_avg: price,
      price_median: price,
      price_max: price,
      delta_min: delta,
      delta_avg: delta,
      delta_median: delta,
      delta_max: delta,
      markdown_min: markdown,
      markdown_avg: markdown,
      markdown_median: markdown,
      markdown_max: markdown,
    };
  });
}

function managementCycleRows() {
  return stationCycleRows().map((row) => ({
    cycle_hour: row.cycle_hour,
    clock_hour: row.clock_hour,
    label: row.label,
    count: 1,
    min: row.delta_median,
    q1: row.delta_median,
    median: row.delta_median,
    q3: row.delta_median,
    max: row.delta_median,
  }));
}

const besthours = Array.from({ length: 12 }, (_value, offset) => offset);

const stationStatsFixture = {
  hourly: stationHourlyRows(),
  text: "0 - 12h",
  besthours,
  min: -0.05,
  max: 0.05,
  minabs: 1.95,
  maxabs: 2.05,
  span: 0.1,
  daily: [
    {
      date: managementDate,
      window_kind: "full",
      window_start_timestamp: "2026-04-06T12:00+02:00",
      window_end_timestamp: "2026-04-07T11:59+02:00",
      noon_price: 2.05,
      max_price: 2.05,
      prior_reference_price: 2.0,
      prior_reference_label: "Vortag 12:00",
      max_price_delta_vs_prior: 0.05,
      post_noon_decreases: 0,
      post_noon_increases: 1,
      min_price: 1.95,
      min_timestamp: "2026-04-06T00:00+02:00",
      min_time_minutes: 0,
      min_time_text: "00:00",
      min_duration_minutes: 720,
      min_duration_text: "12h",
      daily_range: 0.1,
    },
  ],
  summary: {
    days: 1,
    law_effective_date: "2026-04-01",
    analysis_start: "2026-04-05",
    analysis_end: "2026-04-05",
    analysis_days: 2,
    partial_cycles: 0,
    full_cycles: 1,
    noon_price_avg: 2.05,
    noon_price_median: 2.05,
    max_price_avg: 2.05,
    max_price_median: 2.05,
    prior_reference_price_avg: 2.0,
    prior_reference_price_median: 2.0,
    max_price_delta_vs_prior_avg: 0.05,
    max_price_delta_vs_prior_median: 0.05,
    min_price_avg: 1.95,
    min_price_median: 1.95,
    daily_range_avg: 0.1,
    daily_range_median: 0.1,
    post_noon_decreases_avg: 0.0,
    post_noon_decreases_median: 0,
    post_noon_increases_avg: 1.0,
    post_noon_increases_median: 1,
    min_time_minutes_avg: 0,
    min_time_minutes_median: 0,
    min_time_text: "00:00",
    min_duration_minutes_avg: 720,
    min_duration_minutes_median: 720,
    min_duration_text: "12h",
  },
  cycle_hourly: stationCycleRows(),
  cycle_summary: {
    days: 1,
    cycle_start: "2026-04-05",
    cycle_end: managementDate,
    partial: false,
    last_label: "12",
  },
  law_effective_date: "2026-04-01",
  analysis_start: managementDate,
  analysis_end: managementDate,
  analysis_days: 2,
};

const managementFixture = {
  snapshot_date: managementDate,
  generated_at: "2026-04-07T08:00:00+02:00",
  analysis_start: managementDate,
  analysis_end: managementDate,
  station_counts: {
    diesel: 1,
    e10: 1,
    e5: 1,
  },
  view_modes: {
    diesel: "cycle",
    e10: "cycle",
    e5: "cycle",
  },
  bucket_counts: {
    diesel: 25,
    e10: 25,
    e5: 25,
  },
  fuels: {
    diesel: managementCycleRows(),
    e10: managementCycleRows(),
    e5: managementCycleRows(),
  },
  brand_snapshot_label: "12:00-Referenz",
  brand_snapshot_date: managementDate,
  brand_snapshot_timestamp: "2026-04-06T12:00+02:00",
  brand_distributions: {
    diesel: [],
    e10: [],
    e5: [],
  },
  noon_reference_bucket_minutes: 15,
  noon_reference_histograms: {
    diesel: [
      {
        bucket_minute: 735,
        bucket_label: "12:15",
        count: 1,
        stations: 1,
        increase_count: 1,
        fallback_count: 0,
        share: 1,
      },
    ],
    e10: [
      {
        bucket_minute: 720,
        bucket_label: "12:00",
        count: 1,
        stations: 1,
        increase_count: 0,
        fallback_count: 1,
        share: 1,
      },
    ],
    e5: [
      {
        bucket_minute: 720,
        bucket_label: "12:00",
        count: 1,
        stations: 1,
        increase_count: 0,
        fallback_count: 1,
        share: 1,
      },
    ],
  },
  noon_reference_summaries: {
    diesel: {
      stations: 1,
      bucket_minutes: 15,
      peak_bucket_label: "12:15",
      peak_bucket_count: 1,
      peak_bucket_share: 1,
      increase_stations: 1,
      fallback_stations: 0,
      delayed_increase_stations: 1,
    },
    e10: {
      stations: 1,
      bucket_minutes: 15,
      peak_bucket_label: "12:00",
      peak_bucket_count: 1,
      peak_bucket_share: 1,
      increase_stations: 0,
      fallback_stations: 1,
      delayed_increase_stations: 0,
    },
    e5: {
      stations: 1,
      bucket_minutes: 15,
      peak_bucket_label: "12:00",
      peak_bucket_count: 1,
      peak_bucket_share: 1,
      increase_stations: 0,
      fallback_stations: 1,
      delayed_increase_stations: 0,
    },
  },
};

const nearbyStationsResponse = {
  ok: true,
  license: "test",
  data: "MTS-K",
  status: "ok",
  stations: [
    {
      id: stationId,
      name: "Noon Test Station",
      brand: "TEST",
      street: "Testweg",
      houseNumber: "1",
      place: "Berlin",
      lat: 52.52,
      lng: 13.405,
      dist: 1.2,
      isOpen: true,
      diesel: 1.95,
      e10: 1.85,
      e5: 1.9,
    },
  ],
};

const stationHistoryCsv = `date,price,last_update
2026-04-05,2.050,2026-04-05T12:00:00+02:00
2026-04-06,2.050,2026-04-06T12:00:00+02:00
`;

const noonCsvByPath = {
  "/data2/2026/04/05/noon.csv": `station_uuid,diesel,e5,e10,last_update
${stationId},2.000,1.950,1.900,2026-04-05T12:00:00+02:00
`,
  "/data2/2026/04/06/noon.csv": `station_uuid,diesel,e5,e10,last_update
${stationId},2.000,1.950,1.900,2026-04-06T12:00:00+02:00
`,
};

module.exports = {
  besthours,
  managementDate,
  managementFixture,
  managementPath,
  nearbyStationsResponse,
  noonCsvByPath,
  previousManagementDate,
  stationId,
  stationHistoryCsv,
  stationIdPath,
  stationStatsFixture,
};
