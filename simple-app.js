(function () {
  const config = window.TankzeitWebConfig || {};
  const localAdviceUrl = config.localAdviceUrl || "data/simple/latest.json";
  const isLocalHost = ["localhost", "127.0.0.1", "::1"].includes(
    window.location.hostname,
  );
  const adviceUrls = (
    isLocalHost
      ? [localAdviceUrl, config.adviceUrl]
      : [config.adviceUrl, localAdviceUrl]
  ).filter(Boolean);
  const fuelLabels = { diesel: "Diesel", e10: "E10", e5: "E5" };

  let adviceSummary = null;
  let analysisFuel = "diesel";

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function safeNumber(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(`Request failed: ${response.status}`);
    return response.json();
  }

  async function fetchFirstJson(urls) {
    let lastError;
    for (const url of urls) {
      try {
        return await fetchJson(url, { cache: "no-store" });
      } catch (error) {
        lastError = error;
      }
    }
    throw lastError || new Error("No data URL configured.");
  }

  function formatCents(eur) {
    return `${Math.round((safeNumber(eur) || 0) * 100)} ct`;
  }

  function formatCentsPrecise(eur) {
    return `${new Intl.NumberFormat("de-DE", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 1,
    }).format((safeNumber(eur) || 0) * 100)} ct`;
  }

  function formatPercent(value) {
    return new Intl.NumberFormat("de-DE", {
      style: "percent",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(safeNumber(value) || 0);
  }

  function formatDate(value) {
    if (!value) return "–";
    const [year, month, day] = String(value).split("-").map(Number);
    return new Intl.DateTimeFormat("de-DE", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    }).format(new Date(Date.UTC(year, month - 1, day)));
  }

  function renderAdvice(summary) {
    const stats = summary.fuels?.[analysisFuel] || {};
    const distribution = summary.savings_distributions?.[analysisFuel] || {};
    const savings =
      distribution.savings_eur_per_liter ||
      stats.savings_eur_per_liter ||
      {};
    const medianSaving = safeNumber(savings.median) || 0;
    const maximum = safeNumber(savings.maximum) || 0;
    const medianPercent = maximum > 0 ? (medianSaving / maximum) * 100 : 0;
    const fuelLabel = fuelLabels[analysisFuel];

    document.getElementById("simple-metric-scope").textContent = fuelLabel;
    document.getElementById("simple-median-value").textContent =
      Math.round(medianSaving * 100);
    document.getElementById("simple-min-value").textContent =
      formatCents(savings.minimum);
    document.getElementById("simple-max-value").textContent =
      formatCents(maximum);
    document.getElementById("simple-median-marker").style.left =
      `${Math.min(100, Math.max(0, medianPercent))}%`;
    document.getElementById("simple-confirmation-rate").textContent =
      formatPercent(stats.confirmation_rate);
    document.getElementById("simple-confirmation-copy").textContent =
      `${Number(stats.confirmed_station_fuels || 0).toLocaleString("de-DE")} von ${Number(stats.eligible_station_fuels || 0).toLocaleString("de-DE")} aktuellen Tankstellen bestätigen 11:00 als günstigsten Zeitraum.`;
    document.getElementById("simple-audit-meta").textContent =
      `Nachtanalyse vom ${formatDate(summary.analysis_date)} · ${Number(stats.excluded_station_fuels || 0).toLocaleString("de-DE")} veraltete, unvollständige oder unplausible Tankstellen nicht eingerechnet.`;
    document.getElementById("simple-histogram-unit").textContent =
      "Tankstellen";
    renderHistogram(distribution);
    renderStatNotes(stats, distribution);
  }

  function renderHistogram(distribution) {
    const histogramEl = document.getElementById("simple-histogram");
    const bins = Array.isArray(distribution.bins) ? distribution.bins : [];
    const savings = distribution.savings_eur_per_liter || {};
    if (!bins.length) {
      histogramEl.innerHTML =
        '<span class="simple-histogram-empty">Verteilung nicht verfügbar</span>';
      return;
    }

    const peak = Math.max(...bins.map((bin) => Number(bin.count) || 0), 1);
    const medianSaving = safeNumber(savings.median) || 0;
    const binWidth =
      safeNumber(distribution.bin_width_eur_per_liter) || 0.05;
    const medianBinIndex = Math.min(
      bins.length - 1,
      Math.floor(medianSaving / binWidth),
    );
    histogramEl.innerHTML = bins
      .map((bin, index) => {
        const count = Number(bin.count) || 0;
        const height = Math.max(2, (count / peak) * 100);
        const lower = formatCents(bin.lower);
        const upper = formatCents(bin.upper);
        const medianLabel = `Median ${formatCents(medianSaving)}/L`;
        return `
          <span
            class="simple-histogram-bar${index === medianBinIndex ? " is-median" : ""}"
            style="--bar-height: ${height}%"
            data-median-label="${index === medianBinIndex ? escapeHtml(medianLabel) : ""}"
            title="${escapeHtml(`${lower} bis ${upper}: ${count.toLocaleString("de-DE")} Tankstellen`)}"
            aria-label="${escapeHtml(`${lower} bis ${upper}: ${count.toLocaleString("de-DE")} Tankstellen`)}"
          ></span>
        `;
      })
      .join("");
    histogramEl.setAttribute(
      "aria-label",
      `Histogramm der Ersparnis für ${fuelLabels[analysisFuel]}; ${formatCents(savings.median)} pro Liter Median`,
    );
    document.getElementById("simple-histogram-min").textContent =
      `${formatCents(savings.minimum)}/L`;
    document.getElementById("simple-histogram-max").textContent =
      `${formatCents(savings.maximum)}/L`;
  }

  function renderStatNotes(stats, distribution) {
    const savings = distribution.savings_eur_per_liter || {};
    const sampleSize = Number(distribution.sample_size || 0).toLocaleString(
      "de-DE",
    );
    const eligible = Number(
      stats.eligible_station_fuels || 0,
    ).toLocaleString("de-DE");
    const excluded = Number(
      stats.excluded_station_fuels || 0,
    ).toLocaleString("de-DE");
    document.getElementById("simple-stat-notes").innerHTML = `
      <li>Median ${formatCents(savings.median)}/L: Je die Hälfte der Tankstellen liegt darunter bzw. darüber.</li>
      <li>Verteilung: n = ${sampleSize} Tankstellen; jede Tankstelle zählt einmal.</li>
      <li>Mittelwert ${formatCentsPrecise(savings.average)}/L · P95 ${formatCents(savings.p95)}/L · Minimum ${formatCents(savings.minimum)}/L · Maximum ${formatCents(savings.maximum)}/L.</li>
      <li>Regeltest: n = ${eligible} Tankstellen · ${formatPercent(stats.confirmation_rate)} Bestätigung · ${excluded} ausgeschlossen · Fenster 12:00–12:59 Vortag → 11:00–11:59 Folgetag.</li>
    `;
  }

  async function loadAdvice() {
    try {
      adviceSummary = await fetchFirstJson(adviceUrls);
      renderAdvice(adviceSummary);
    } catch (error) {
      console.warn("Advice summary unavailable", error);
      document.getElementById("simple-audit-meta").textContent =
        "Die Nachtanalyse ist momentan nicht erreichbar.";
    }
  }

  function selectAnalysisFuel(nextFuel) {
    analysisFuel = nextFuel;
    document.querySelectorAll(".simple-analysis-fuel").forEach((button) => {
      const active = button.dataset.analysisFuel === analysisFuel;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    if (adviceSummary) renderAdvice(adviceSummary);
  }

  function berlinTimeParts() {
    const parts = new Intl.DateTimeFormat("de-DE", {
      timeZone: "Europe/Berlin",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date());
    return Object.fromEntries(
      parts
        .filter((part) => part.type !== "literal")
        .map((part) => [part.type, Number(part.value)]),
    );
  }

  function updateNowState() {
    const nowEl = document.getElementById("simple-now");
    const { hour, minute } = berlinTimeParts();
    if (hour === 11) {
      nowEl.textContent = `Jetzt tanken · noch ${60 - minute} Min.`;
      nowEl.classList.add("is-active");
      return;
    }
    nowEl.classList.remove("is-active");
    nowEl.textContent =
      hour < 11
        ? "Heute ab 11:00 Uhr"
        : "Nächstes Fenster morgen um 11:00 Uhr";
  }

  document.querySelectorAll(".simple-analysis-fuel").forEach((button) => {
    button.addEventListener("click", () =>
      selectAnalysisFuel(button.dataset.analysisFuel),
    );
  });

  updateNowState();
  window.setInterval(updateNowState, 60000);
  loadAdvice();
})();
