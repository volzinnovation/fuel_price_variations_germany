(() => {
  const BERLIN_TIME_ZONE = "Europe/Berlin";
  const LAW_RESET_DATE = "2026-04-01";

  function toNumber(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function berlinTimeParts(date = new Date()) {
    const formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone: BERLIN_TIME_ZONE,
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    const parts = Object.create(null);
    formatter.formatToParts(date).forEach((part) => {
      if (part.type !== "literal") {
        parts[part.type] = part.value;
      }
    });
    return {
      hour: Number(parts.hour),
      minute: Number(parts.minute),
    };
  }

  function summaryFromStats(stats) {
    if (!stats || typeof stats !== "object") return null;
    const summary = stats.summary;
    return summary && typeof summary === "object" ? summary : null;
  }

  function hasNoonResetSummary(stats) {
    const summary = summaryFromStats(stats);
    if (!summary) return false;
    return [
      "noon_price_avg",
      "noon_price_median",
      "post_noon_decreases_avg",
      "post_noon_decreases_median",
      "post_noon_increases_avg",
      "post_noon_increases_median",
      "min_time_text",
      "min_duration_text",
    ].some((key) => {
      const value = summary[key];
      if (typeof value === "string") return value.trim().length > 0;
      return value !== null && value !== undefined;
    });
  }

  function isPostLawProfile(stats) {
    if (!stats || typeof stats !== "object") return false;
    const summary = summaryFromStats(stats);
    const values = [
      stats.analysis_end,
      stats.analysis_start,
      summary?.analysis_end,
      summary?.analysis_start,
    ];
    return values.some(
      (value) => typeof value === "string" && value.trim() >= LAW_RESET_DATE,
    );
  }

  function pickNumber(...values) {
    for (const value of values) {
      const parsed = toNumber(value);
      if (parsed !== null) return parsed;
    }
    return null;
  }

  function formatPrice(value) {
    const amount = toNumber(value);
    if (amount === null) return null;
    return `${amount.toFixed(3)} €/l`;
  }

  function formatCount(value) {
    const count = toNumber(value);
    if (count === null) return null;
    if (Math.abs(count - Math.round(count)) < 0.05) {
      return String(Math.round(count));
    }
    return count.toFixed(1);
  }

  function formatClock(minutes) {
    const value = toNumber(minutes);
    if (value === null) return null;
    const rounded = ((Math.round(value) % 1440) + 1440) % 1440;
    const hours = String(Math.floor(rounded / 60)).padStart(2, "0");
    const mins = String(rounded % 60).padStart(2, "0");
    return `${hours}:${mins}`;
  }

  function formatDuration(minutes) {
    const value = toNumber(minutes);
    if (value === null) return null;
    const total = Math.max(0, Math.round(value));
    const hours = Math.floor(total / 60);
    const mins = total % 60;
    if (hours && mins) return `${hours}h ${String(mins).padStart(2, "0")}m`;
    if (hours) return `${hours}h`;
    return `${mins} min`;
  }

  function profileText(stats, fallback = "Details") {
    const summary = summaryFromStats(stats);
    if (!summary) return fallback;
    const noonPrice = pickNumber(summary.noon_price_median, summary.noon_price_avg);
    const decreases = pickNumber(
      summary.post_noon_decreases_avg,
      summary.post_noon_decreases_median,
    );
    const increases = pickNumber(
      summary.post_noon_increases_avg,
      summary.post_noon_increases_median,
    );
    const parts = [];
    if (noonPrice !== null) parts.push(`${noonPrice.toFixed(3)} €/l`);
    if (decreases !== null) parts.push(`${formatCount(decreases)} Senk.`);
    if (increases !== null && increases > 1.05) {
      parts.push(`${formatCount(increases)} Erh.`);
    }
    return parts.length ? parts.join(" · ") : fallback;
  }

  function minimumText(stats, fallback = "-") {
    const summary = summaryFromStats(stats);
    if (!summary) return fallback;
    const timeText =
      summary.min_time_text ||
      formatClock(pickNumber(summary.min_time_minutes_median, summary.min_time_minutes_avg));
    const durationText =
      summary.min_duration_text ||
      formatDuration(
        pickNumber(
          summary.min_duration_minutes_median,
          summary.min_duration_minutes_avg,
        ),
      );
    if (timeText && durationText) return `${timeText} Uhr · ${durationText}`;
    if (timeText) return `${timeText} Uhr`;
    if (durationText) return durationText;
    return fallback;
  }

  function normalizeHour(value) {
    const hour = toNumber(value);
    if (hour === null) return null;
    return ((Math.round(hour) % 24) + 24) % 24;
  }

  function uniqueSortedHours(values) {
    return [...new Set(
      values
        .map((value) => normalizeHour(value))
        .filter((hour) => hour !== null),
    )].sort((left, right) => left - right);
  }

  function bestHours(stats) {
    if (!stats || typeof stats !== "object" || !Array.isArray(stats.besthours)) {
      return [];
    }
    return uniqueSortedHours(stats.besthours);
  }

  function chartCycleSeries(stats) {
    return cycleMarkdownSeries(stats, { includeClosingNoon: false }).map((row) => ({
      cycleHour: row.cycleHour,
      hour: row.clockHour,
      displayValue: -row.markdown,
    }));
  }

  function chartReferenceSeries(stats) {
    if (!stats || typeof stats !== "object" || !Array.isArray(stats.hourly)) {
      return [];
    }
    return stats.hourly
      .map((row) => {
        const hour = normalizeHour(row && row.hour);
        const price = toNumber(row && row.price);
        if (hour === null || price === null) return null;
        return {
          hour,
          displayValue: price,
        };
      })
      .filter((row) => row !== null)
      .sort((left, right) => left.hour - right.hour);
  }

  function tankzeitSeries(stats) {
    const referenceSeries = chartReferenceSeries(stats);
    if (referenceSeries.length) {
      return {
        kind: "clock",
        rows: referenceSeries,
      };
    }
    const cycleSeries = chartCycleSeries(stats);
    if (cycleSeries.length) {
      return {
        kind: "cycle",
        rows: cycleSeries,
      };
    }
    return {
      kind: null,
      rows: [],
    };
  }

  function relativeMinimumHours(stats) {
    if (isPostLawProfile(stats)) {
      return bestHours(stats);
    }
    const { rows } = tankzeitSeries(stats);
    if (!rows.length) {
      const storedBestHours = bestHours(stats);
      if (storedBestHours.length) return storedBestHours;
      return [];
    }
    const minimumValue = Math.min(...rows.map((row) => row.displayValue));
    const epsilon = 0.0005;
    return uniqueSortedHours(
      rows
        .filter((row) => Math.abs(row.displayValue - minimumValue) <= epsilon)
        .map((row) => row.hour),
    );
  }

  function relativeMinimumRangesText(hours) {
    if (!hours.length) return null;
    const ranges = [];
    let start = hours[0];
    let end = hours[0];
    for (let index = 1; index < hours.length; index += 1) {
      const hour = hours[index];
      if (hour === end + 1) {
        end = hour;
        continue;
      }
      ranges.push([start, end]);
      start = hour;
      end = hour;
    }
    ranges.push([start, end]);
    return ranges
      .map(([rangeStart, rangeEnd]) => `${rangeStart} - ${rangeEnd + 1}h`)
      .join(", ");
  }

  function relativeMinimumText(stats, fallback = "-") {
    if (hasNoonResetSummary(stats)) {
      const minimum = minimumText(stats, "");
      if (minimum) return minimum;
    }
    if (isPostLawProfile(stats)) {
      const storedBestHours = bestHours(stats);
      if (storedBestHours.length) {
        const text = relativeMinimumRangesText(storedBestHours);
        if (text) return text;
      }
      if (stats && typeof stats === "object" && typeof stats.text === "string" && stats.text.trim()) {
        return stats.text.trim();
      }
      return fallback;
    }
    const series = tankzeitSeries(stats);
    if (series.rows.length) {
      const minimumValue = Math.min(...series.rows.map((row) => row.displayValue));
      const epsilon = 0.0005;
      const minimumRows = series.rows.filter(
        (row) => Math.abs(row.displayValue - minimumValue) <= epsilon,
      );
      if (series.kind === "cycle") {
        if (minimumRows.length >= 24) return "0 - 24h";
        const ranges = [];
        let rangeStart = minimumRows[0];
        let previous = minimumRows[0];
        for (let index = 1; index < minimumRows.length; index += 1) {
          const row = minimumRows[index];
          if (row.cycleHour === previous.cycleHour + 1) {
            previous = row;
            continue;
          }
          ranges.push([rangeStart.hour, (previous.hour + 1) % 24]);
          rangeStart = row;
          previous = row;
        }
        ranges.push([rangeStart.hour, (previous.hour + 1) % 24]);
        return ranges.map(([start, end]) => `${start} - ${end}h`).join(", ");
      }
      const text = relativeMinimumRangesText(
        uniqueSortedHours(minimumRows.map((row) => row.hour)),
      );
      if (text) return text;
    }
    const storedBestHours = bestHours(stats);
    if (storedBestHours.length) {
      const text = relativeMinimumRangesText(storedBestHours);
      if (text) return text;
    }
    if (stats && typeof stats.text === "string" && stats.text.trim()) {
      return stats.text.trim();
    }
    const minimum = minimumText(stats, "");
    if (minimum) return minimum;
    return fallback;
  }

  function minimumWindow(stats) {
    const summary = summaryFromStats(stats);
    if (!summary) return null;
    const startMinutes = pickNumber(
      summary.min_time_minutes_median,
      summary.min_time_minutes_avg,
    );
    const durationMinutes = pickNumber(
      summary.min_duration_minutes_median,
      summary.min_duration_minutes_avg,
    );
    if (startMinutes === null || durationMinutes === null) return null;
    return {
      startMinutes: ((Math.round(startMinutes) % 1440) + 1440) % 1440,
      durationMinutes: Math.max(0, Math.round(durationMinutes)),
    };
  }

  function isNowInTypicalMinimumWindow(stats, now = new Date()) {
    const window = minimumWindow(stats);
    if (!window || window.durationMinutes <= 0) return false;
    const berlinNow = berlinTimeParts(now);
    const minuteOfDay = berlinNow.hour * 60 + berlinNow.minute;
    const end = window.startMinutes + window.durationMinutes;
    if (end <= 1440) {
      return minuteOfDay >= window.startMinutes && minuteOfDay < end;
    }
    return minuteOfDay >= window.startMinutes || minuteOfDay < end % 1440;
  }

  function isNowInRelativeMinimumWindow(stats, now = new Date()) {
    if (hasNoonResetSummary(stats)) {
      return isNowInTypicalMinimumWindow(stats, now);
    }
    const hours = relativeMinimumHours(stats);
    if (hours.length) {
      return hours.includes(berlinTimeParts(now).hour);
    }
    return isNowInTypicalMinimumWindow(stats, now);
  }

  function cycleMarkdownValue(row) {
    if (!row || typeof row !== "object") return null;
    return pickNumber(
      row.markdown_median,
      row.markdown_avg,
      row.markdown_max,
      row.markdown_min,
    );
  }

  function cycleMarkdownSeries(stats, options = {}) {
    if (!stats || typeof stats !== "object" || !Array.isArray(stats.cycle_hourly)) {
      return [];
    }
    const includeClosingNoon = options.includeClosingNoon !== false;
    return stats.cycle_hourly
      .map((row) => {
        const cycleHour = toNumber(row && row.cycle_hour);
        const clockHour = normalizeHour(
          row && row.clock_hour != null ? row.clock_hour : row && row.label,
        );
        const markdown = cycleMarkdownValue(row);
        if (cycleHour === null || clockHour === null || markdown === null) {
          return null;
        }
        return {
          cycleHour,
          clockHour,
          label:
            typeof row.label === "string" && row.label.trim()
              ? row.label.trim()
              : String(clockHour).padStart(2, "0"),
          markdown,
          count: toNumber(row && row.count),
        };
      })
      .filter((row) => row !== null)
      .filter((row) => includeClosingNoon || row.cycleHour < 24)
      .sort((left, right) => left.cycleHour - right.cycleHour);
  }

  window.TankzeitStats = {
    bestHours,
    cycleMarkdownSeries,
    formatClock,
    formatCount,
    formatDuration,
    formatPrice,
    isNowInRelativeMinimumWindow,
    isNowInTypicalMinimumWindow,
    minimumText,
    profileText,
    relativeMinimumText,
    summaryFromStats,
    toNumber,
  };
})();
