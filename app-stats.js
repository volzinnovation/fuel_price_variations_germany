(() => {
  function toNumber(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function summaryFromStats(stats) {
    if (!stats || typeof stats !== "object") return null;
    const summary = stats.summary;
    return summary && typeof summary === "object" ? summary : null;
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
    if (noonPrice !== null) parts.push(`12:00 ${noonPrice.toFixed(3)} €/l`);
    if (decreases !== null) parts.push(`${formatCount(decreases)} Senk.`);
    if (increases !== null && increases > 0.05) {
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

  function relativeMinimumHours(stats) {
    if (!stats || typeof stats !== "object" || !Array.isArray(stats.hourly)) {
      return [];
    }
    const hourly = stats.hourly
      .map((row) => ({
        hour: normalizeHour(row && row.hour),
        price: toNumber(row && row.price),
      }))
      .filter((row) => row.hour !== null && row.price !== null);
    if (!hourly.length) return [];
    const minimumPrice = Math.min(...hourly.map((row) => row.price));
    const epsilon = 1e-9;
    return [...new Set(
      hourly
        .filter((row) => Math.abs(row.price - minimumPrice) <= epsilon)
        .map((row) => row.hour),
    )].sort((left, right) => left - right);
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
    const hours = relativeMinimumHours(stats);
    if (hours.length) {
      const text = relativeMinimumRangesText(hours);
      if (text) return text;
    }
    if (stats && typeof stats.text === "string" && stats.text.trim()) {
      return stats.text.trim();
    }
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
    const minuteOfDay = now.getHours() * 60 + now.getMinutes();
    const end = window.startMinutes + window.durationMinutes;
    if (end <= 1440) {
      return minuteOfDay >= window.startMinutes && minuteOfDay < end;
    }
    return minuteOfDay >= window.startMinutes || minuteOfDay < end % 1440;
  }

  function isNowInRelativeMinimumWindow(stats, now = new Date()) {
    const hours = relativeMinimumHours(stats);
    if (hours.length) {
      return hours.includes(now.getHours());
    }
    return isNowInTypicalMinimumWindow(stats, now);
  }

  window.TankzeitStats = {
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
