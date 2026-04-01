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
    const parts = [];
    if (noonPrice !== null) parts.push(`12:00 ${noonPrice.toFixed(3)} €/l`);
    if (decreases !== null) parts.push(`${formatCount(decreases)} Senk.`);
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

  window.TankzeitStats = {
    formatClock,
    formatCount,
    formatDuration,
    formatPrice,
    isNowInTypicalMinimumWindow,
    minimumText,
    profileText,
    summaryFromStats,
    toNumber,
  };
})();
