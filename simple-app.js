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
  const tankerkoenigApology =
    "Livepreise sind gerade nicht erreichbar. Wir zeigen nahe Stationen aus dem Tankzeit-Verzeichnis.";
  const searchRadiusKm = 10;
  const ids = JSON.parse(localStorage.getItem("ids") || "[]");
  const favorites = JSON.parse(localStorage.getItem("fav") || "[]");
  const params = new URLSearchParams(window.location.search);

  let fuel = ["diesel", "e10", "e5"].includes(params.get("fuel"))
    ? params.get("fuel")
    : localStorage.getItem("tankzeit_simple_fuel") || "diesel";
  let center = null;
  let livePricesAvailable = true;
  let currentStations = [];
  let allStations = [];

  const stationsEl = document.getElementById("simple-stations");
  const statusEl = document.getElementById("simple-status");
  const locationButton = document.getElementById("simple-location");
  const sortSelect = document.getElementById("simple-sort");

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

  function safePrice(value) {
    const price = safeNumber(value);
    return price !== null && price >= 0.5 && price <= 4 ? price : null;
  }

  function setStatus(message, isError = false) {
    statusEl.textContent = message;
    statusEl.className = `simple-status is-visible${isError ? " is-error" : ""}`;
  }

  function clearStatus() {
    statusEl.className = "simple-status";
    statusEl.textContent = "";
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(`Request failed: ${response.status}`);
    const payload = await response.json();
    if (payload && payload.ok === false) throw new Error("Request failed");
    return payload;
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
    const overall = summary.overall || {};
    const distribution = summary.station_savings_distribution || {};
    const distributionSavings =
      distribution.savings_eur_per_liter ||
      overall.savings_eur_per_liter ||
      {};
    const overallSavings = overall.savings_eur_per_liter || distributionSavings;
    const medianSaving = safeNumber(distributionSavings.median) || 0;
    const maximum = safeNumber(overallSavings.maximum) || 0;
    const medianPercent = maximum > 0 ? (medianSaving / maximum) * 100 : 0;

    document.getElementById("simple-median-value").textContent =
      Math.round(medianSaving * 100);
    document.getElementById("simple-min-value").textContent =
      formatCents(overallSavings.minimum);
    document.getElementById("simple-max-value").textContent =
      formatCents(maximum);
    document.getElementById("simple-median-marker").style.left =
      `${Math.min(100, Math.max(0, medianPercent))}%`;
    document.getElementById("simple-confirmation-rate").textContent =
      formatPercent(overall.confirmation_rate);
    document.getElementById("simple-confirmation-copy").textContent =
      `${Number(overall.confirmed_station_fuels || 0).toLocaleString("de-DE")} von ${Number(overall.eligible_station_fuels || 0).toLocaleString("de-DE")} aktuellen Stations-/Kraftstoffprofilen bestätigen 11:00 als günstigsten oder gleich günstigen Zeitpunkt.`;
    document.getElementById("simple-audit-meta").textContent =
      `Nachtanalyse vom ${formatDate(summary.analysis_date)} · ${Number(overall.excluded_station_fuels || 0).toLocaleString("de-DE")} stale, unvollständige oder unplausible Profile nicht eingerechnet.`;
    renderHistogram(distribution);
    renderStatNotes(summary, distribution);
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
    const medianBinIndex = Math.min(
      bins.length - 1,
      Math.floor(
        medianSaving / (safeNumber(distribution.bin_width_eur_per_liter) || 0.05),
      ),
    );
    histogramEl.innerHTML = bins
      .map((bin, index) => {
        const count = Number(bin.count) || 0;
        const height = Math.max(2, (count / peak) * 100);
        const lower = formatCents(bin.lower);
        const upper = formatCents(bin.upper);
        return `
          <span
            class="simple-histogram-bar${index === medianBinIndex ? " is-median" : ""}"
            style="--bar-height: ${height}%"
            title="${escapeHtml(`${lower} bis ${upper}: ${count.toLocaleString("de-DE")} Tankstellen`)}"
            aria-label="${escapeHtml(`${lower} bis ${upper}: ${count.toLocaleString("de-DE")} Tankstellen`)}"
          ></span>
        `;
      })
      .join("");
    document.getElementById("simple-histogram-min").textContent =
      `${formatCents(savings.minimum)}/L`;
    document.getElementById("simple-histogram-max").textContent =
      `${formatCents(savings.maximum)}/L`;
  }

  function renderStatNotes(summary, distribution) {
    const overall = summary.overall || {};
    const savings = distribution.savings_eur_per_liter || {};
    const stationCount = Number(distribution.station_count || 0).toLocaleString(
      "de-DE",
    );
    const eligible = Number(
      overall.eligible_station_fuels || 0,
    ).toLocaleString("de-DE");
    const excluded = Number(
      overall.excluded_station_fuels || 0,
    ).toLocaleString("de-DE");
    document.getElementById("simple-stat-notes").innerHTML = `
      <li>Median ${formatCents(savings.median)}/L: Je die Hälfte der Stationen liegt darunter bzw. darüber.</li>
      <li>Verteilung: n = ${stationCount} Stationen; je Station zählt der Median der verfügbaren Kraftstoffe.</li>
      <li>Mittelwert ${formatCentsPrecise(savings.average)}/L · P95 ${formatCents(savings.p95)}/L · Minimum ${formatCents(savings.minimum)}/L · Maximum ${formatCents(savings.maximum)}/L.</li>
      <li>Regeltest: n = ${eligible} Stations-/Kraftstoffprofile · ${formatPercent(overall.confirmation_rate)} Bestätigung · Einzelprofil-Spanne ${formatCents(overall.savings_eur_per_liter?.minimum)}/L bis ${formatCents(overall.savings_eur_per_liter?.maximum)}/L · ${excluded} Profile ausgeschlossen.</li>
    `;
  }

  async function loadAdvice() {
    try {
      renderAdvice(await fetchFirstJson(adviceUrls));
    } catch (error) {
      console.warn("Advice summary unavailable", error);
      document.getElementById("simple-audit-meta").textContent =
        "Die Nachtanalyse ist momentan nicht erreichbar.";
    }
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
    if (hour < 11) {
      nowEl.textContent = `Heute ab 11:00 Uhr`;
    } else {
      nowEl.textContent = "Nächstes Fenster morgen um 11:00 Uhr";
    }
  }

  function mapsUrl(station) {
    return `https://www.google.com/maps/search/?api=1&query=${station.lat},${station.lng}`;
  }

  function chartUrl(station) {
    return `chart.html?id=${encodeURIComponent(station.id)}&fuel=${fuel}&name=${encodeURIComponent(station.name)}&lat=${station.lat}&lng=${station.lng}`;
  }

  function breakdownUrl(station) {
    return window.TankzeitFuelBreakdown.buildBreakdownUrl({
      id: station.id,
      fuel,
      name: station.name,
      brand: station.brand || "",
      price: station[fuel],
      lat: station.lat,
      lng: station.lng,
      backPath: `index.html?fuel=${fuel}`,
      backLabel: "Zurück zur Tankzeit",
    });
  }

  function normalizeStation(station) {
    const id = station.id || station.uuid;
    const lat = safeNumber(station.lat ?? station.latitude);
    const lng = safeNumber(station.lng ?? station.longitude);
    if (!id || lat === null || lng === null) return null;
    const normalized = {
      id,
      name: station.name || station.brand || "Tankstelle",
      brand: station.brand || "",
      lat,
      lng,
      dist: safeNumber(station.dist),
      diesel: safePrice(station.diesel),
      e10: safePrice(station.e10),
      e5: safePrice(station.e5),
    };
    return normalized;
  }

  function compareNumbers(left, right) {
    if (left === null && right === null) return 0;
    if (left === null) return 1;
    if (right === null) return -1;
    return left - right;
  }

  function sortStations(stations) {
    const mode = livePricesAvailable ? sortSelect.value : "distance";
    return [...stations].sort((left, right) => {
      const primary =
        mode === "price"
          ? compareNumbers(left[fuel], right[fuel])
          : compareNumbers(left.dist, right.dist);
      if (primary !== 0) return primary;
      return `${left.name} ${left.brand}`.localeCompare(
        `${right.name} ${right.brand}`,
        "de",
        { sensitivity: "base" },
      );
    });
  }

  function storeFavorite(station) {
    if (ids.includes(station.id)) return;
    ids.push(station.id);
    favorites.push({
      id: station.id,
      name: station.brand || station.name,
      lat: station.lat,
      lng: station.lng,
    });
    localStorage.setItem("ids", JSON.stringify(ids));
    localStorage.setItem("fav", JSON.stringify(favorites));
  }

  function priceMarkup(station) {
    const price = station[fuel];
    if (price === null || !livePricesAvailable) {
      return `<strong>–</strong><span>kein Livepreis</span>`;
    }
    return `<strong>${price.toFixed(3).replace(".", ",")} €</strong><span>pro Liter</span>`;
  }

  function stationLinks(station) {
    const links = [
      `<a href="${escapeHtml(chartUrl(station))}">Preisverlauf</a>`,
      `<a href="station/${encodeURIComponent(station.id)}.html">Stationsprofil</a>`,
      `<a href="${escapeHtml(mapsUrl(station))}" target="_blank" rel="noopener">Route</a>`,
    ];
    if (station[fuel] !== null && livePricesAvailable) {
      links.splice(
        1,
        0,
        `<a href="${escapeHtml(breakdownUrl(station))}">Preis erklärt</a>`,
      );
    }
    return links.join("");
  }

  function renderStations(stations) {
    allStations = stations;
    currentStations = sortStations(allStations).slice(0, 10);
    if (!currentStations.length) {
      stationsEl.innerHTML =
        '<div class="simple-empty">Keine offenen Stationen im Umkreis gefunden.</div>';
      return;
    }
    stationsEl.innerHTML = currentStations
      .map((station) => {
        const saved = ids.includes(station.id);
        const brand =
          station.brand && station.brand !== station.name
            ? ` · ${escapeHtml(station.brand)}`
            : "";
        const distance =
          station.dist === null ? "Entfernung unbekannt" : `${station.dist.toFixed(1)} km`;
        return `
          <article class="simple-station" id="station-${escapeHtml(station.id)}">
            <button
              class="simple-favorite"
              type="button"
              data-station-id="${escapeHtml(station.id)}"
              aria-label="${saved ? "Als Favorit gespeichert" : "Als Favorit speichern"}"
              ${saved ? "disabled" : ""}
            >${saved ? "♥" : "♡"}</button>
            <div class="simple-station-name">
              <a href="station/${encodeURIComponent(station.id)}.html">${escapeHtml(station.name)}</a>
              <p>${distance}${brand} · <span data-profile-for="${escapeHtml(station.id)}">11:00–11:59 empfohlen</span></p>
            </div>
            <div class="simple-station-price">${priceMarkup(station)}</div>
            <div class="simple-station-links">${stationLinks(station)}</div>
          </article>
        `;
      })
      .join("");

    stationsEl.querySelectorAll(".simple-favorite:not([disabled])").forEach(
      (button) => {
        button.addEventListener("click", () => {
          const station = currentStations.find(
            (candidate) => candidate.id === button.dataset.stationId,
          );
          if (!station) return;
          storeFavorite(station);
          button.textContent = "♥";
          button.disabled = true;
          button.setAttribute("aria-label", "Als Favorit gespeichert");
        });
      },
    );
  }

  async function fallbackStations(lat, lng) {
    const catalog = await window.TankzeitStationCatalog.loadCatalog();
    return window.TankzeitStationCatalog.findNearbyStations(catalog, lat, lng, {
      limit: 50,
      radiusKm: searchRadiusKm,
    });
  }

  async function loadStations(lat, lng) {
    center = { lat, lng };
    setStatus("Stationen und Livepreise werden geladen …");
    const url =
      "https://creativecommons.tankerkoenig.de/json/list.php" +
      `?rad=${searchRadiusKm}&type=all&sort=dist&lat=${lat}&lng=${lng}` +
      "&apikey=fe8673d1-47be-1156-77e4-040e06cb785c";
    try {
      const payload = await fetchJson(url);
      livePricesAvailable = true;
      sortSelect.querySelector('option[value="price"]').disabled = false;
      const stations = (payload.stations || [])
        .filter((station) => station.isOpen)
        .map(normalizeStation)
        .filter(Boolean)
        .filter((station) => station[fuel] !== null);
      renderStations(stations);
      clearStatus();
    } catch (error) {
      console.warn("Live station request failed", error);
      try {
        livePricesAvailable = false;
        sortSelect.value = "distance";
        sortSelect.querySelector('option[value="price"]').disabled = true;
        renderStations(
          (await fallbackStations(lat, lng))
            .map(normalizeStation)
            .filter(Boolean),
        );
        setStatus(tankerkoenigApology);
      } catch (fallbackError) {
        console.warn("Station fallback failed", fallbackError);
        stationsEl.innerHTML =
          '<div class="simple-empty">Stationen konnten nicht geladen werden.</div>';
        setStatus("Standortdaten sind momentan nicht erreichbar.", true);
      }
    }
  }

  function requestLocation() {
    if (!navigator.geolocation) {
      setStatus("Dieser Browser unterstützt keine Standortabfrage.", true);
      return;
    }
    locationButton.disabled = true;
    locationButton.textContent = "Standort wird ermittelt …";
    setStatus("Standort wird ermittelt …");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        locationButton.disabled = false;
        locationButton.textContent = "Standort aktualisieren";
        loadStations(position.coords.latitude, position.coords.longitude);
      },
      (error) => {
        locationButton.disabled = false;
        locationButton.textContent = "Standort verwenden";
        const message =
          error.code === 1
            ? "Standortzugriff wurde nicht freigegeben. Bitte erlaube ihn im Browser."
            : "Standort konnte nicht ermittelt werden. Bitte versuche es erneut.";
        setStatus(message, true);
      },
      { enableHighAccuracy: false, maximumAge: 300000, timeout: 20000 },
    );
  }

  function selectFuel(nextFuel) {
    fuel = nextFuel;
    localStorage.setItem("tankzeit_simple_fuel", fuel);
    document.querySelectorAll(".simple-fuel").forEach((button) => {
      const active = button.dataset.fuel === fuel;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    const nextParams = new URLSearchParams(window.location.search);
    nextParams.set("fuel", fuel);
    window.history.replaceState({}, "", `${window.location.pathname}?${nextParams}`);
    if (center) loadStations(center.lat, center.lng);
  }

  document.querySelectorAll(".simple-fuel").forEach((button) => {
    button.addEventListener("click", () => selectFuel(button.dataset.fuel));
  });
  sortSelect.addEventListener("change", () => renderStations(allStations));
  locationButton.addEventListener("click", requestLocation);

  selectFuel(fuel);
  updateNowState();
  window.setInterval(updateNowState, 60000);
  loadAdvice();
})();
