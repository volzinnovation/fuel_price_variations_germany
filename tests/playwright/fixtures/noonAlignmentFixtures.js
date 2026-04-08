const stationId = "s0000000-1111-2222-3333-444444444444";
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

function managementHourlyRows() {
  return Array.from({ length: 24 }, (_value, hour) => ({
    hour,
    count: 1,
    min: hour < 12 ? -0.05 : 0.05,
    q1: hour < 12 ? -0.05 : 0.05,
    median: hour < 12 ? -0.05 : 0.05,
    q3: hour < 12 ? -0.05 : 0.05,
    max: hour < 12 ? -0.05 : 0.05,
  }));
}

const besthours = Array.from({ length: 12 }, (_value, hour) => hour);

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
    analysis_start: managementDate,
    analysis_end: managementDate,
    analysis_days: 1,
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
  cycle_hourly: [],
  cycle_summary: {
    days: 1,
    cycle_start: managementDate,
    cycle_end: "2026-04-07",
    partial: false,
    last_label: "12",
  },
  law_effective_date: "2026-04-01",
  analysis_start: managementDate,
  analysis_end: managementDate,
  analysis_days: 1,
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
    diesel: "hourly",
    e10: "hourly",
    e5: "hourly",
  },
  fuels: {
    diesel: managementHourlyRows(),
    e10: managementHourlyRows(),
    e5: managementHourlyRows(),
  },
  brand_snapshot_label: "12:00",
  brand_snapshot_date: managementDate,
  brand_snapshot_timestamp: "2026-04-06T12:00+02:00",
  brand_distributions: {
    diesel: [],
    e10: [],
    e5: [],
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
