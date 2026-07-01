(() => {
  const DATA_BASE = "data2/";
  const TANKERKOENIG_API_KEY = "fe8673d1-47be-1156-77e4-040e06cb785c";
  const TANKERKOENIG_APOLOGY =
    "Entschuldigung, Tankerkönig ist derzeit nicht erreichbar. Bitte versuche es später erneut.";
  const STORAGE_KEY = "tankzeit_mission_state_v1";
  const DEFAULT_STATE = {
    xp: 0,
    scans: 0,
    streak: 0,
    lastClaimDay: null,
    claimed: {},
    dailyClaims: {},
    badges: [],
  };
  const FUEL_LABELS = {
    diesel: "Diesel",
    e10: "E10",
    e5: "E5",
  };
  const CITY_PRESETS = {
    berlin: { label: "Berlin", lat: 52.52, lng: 13.405 },
    hamburg: { label: "Hamburg", lat: 53.5511, lng: 9.9937 },
    munich: { label: "München", lat: 48.1372, lng: 11.5756 },
    cologne: { label: "Köln", lat: 50.9375, lng: 6.9603 },
    frankfurt: { label: "Frankfurt", lat: 50.1109, lng: 8.6821 },
    stuttgart: { label: "Stuttgart", lat: 48.7758, lng: 9.1829 },
    leipzig: { label: "Leipzig", lat: 51.3397, lng: 12.3731 },
  };

  const elements = {
    subtitle: document.getElementById("mission-subtitle"),
    city: document.getElementById("mission-city"),
    fuelButtons: [...document.querySelectorAll("[data-mission-fuel]")],
    radius: document.getElementById("mission-radius"),
    locationButton: document.getElementById("mission-location-btn"),
    refreshButton: document.getElementById("mission-refresh-btn"),
    status: document.getElementById("mission-status"),
    board: document.getElementById("mission-board"),
    xp: document.getElementById("mission-xp"),
    level: document.getElementById("mission-level"),
    progress: document.getElementById("mission-progress"),
    progressLabel: document.getElementById("mission-progress-label"),
    scoreDial: document.getElementById("mission-score-dial"),
    bestScore: document.getElementById("mission-best-score"),
    bestStation: document.getElementById("mission-best-station"),
    stationCount: document.getElementById("mission-station-count"),
    nowCount: document.getElementById("mission-now-count"),
    liveMode: document.getElementById("mission-live-mode"),
    streak: document.getElementById("mission-streak"),
    badges: document.getElementById("mission-badges"),
    target: document.getElementById("mission-target"),
    dailyPanel: document.querySelector(".mission-daily"),
    dailyTitle: document.getElementById("mission-daily-title"),
    dailyDetail: document.getElementById("mission-daily-detail"),
    dailyProgress: document.getElementById("mission-daily-progress"),
    dailyProgressFill: document.getElementById("mission-daily-progress-fill"),
    dailyReward: document.getElementById("mission-daily-reward"),
    shareButton: document.getElementById("mission-share-btn"),
  };

  let missionState = loadMissionState();
  let currentRows = [];
  let currentFuel = "diesel";
  let currentCenter = CITY_PRESETS.berlin;
  let locationCenter = null;
  let livePricesAvailable = false;

  function loadMissionState() {
    try {
      const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
      return {
        ...DEFAULT_STATE,
        ...parsed,
        claimed:
          parsed && typeof parsed.claimed === "object" && parsed.claimed
            ? parsed.claimed
            : {},
        dailyClaims:
          parsed && typeof parsed.dailyClaims === "object" && parsed.dailyClaims
            ? parsed.dailyClaims
            : {},
        badges: Array.isArray(parsed?.badges) ? parsed.badges : [],
      };
    } catch (_err) {
      return { ...DEFAULT_STATE };
    }
  }

  function saveMissionState() {
    const claimedEntries = Object.entries(missionState.claimed || {}).slice(-600);
    const dailyEntries = Object.entries(missionState.dailyClaims || {}).slice(-180);
    missionState.claimed = Object.fromEntries(claimedEntries);
    missionState.dailyClaims = Object.fromEntries(dailyEntries);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(missionState));
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function hrefForHtml(value) {
    return String(value ?? "").replaceAll("&", "&amp;");
  }

  function toFiniteNumber(value) {
    const numericValue = Number(value);
    return Number.isFinite(numericValue) ? numericValue : null;
  }

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function todayKey(date = new Date()) {
    const formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Europe/Berlin",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    const parts = Object.fromEntries(
      formatter
        .formatToParts(date)
        .filter((part) => part.type !== "literal")
        .map((part) => [part.type, part.value]),
    );
    return `${parts.year}-${parts.month}-${parts.day}`;
  }

  function dayDelta(left, right) {
    const leftDate = new Date(`${left}T00:00:00Z`);
    const rightDate = new Date(`${right}T00:00:00Z`);
    return Math.round((rightDate - leftDate) / 86400000);
  }

  function berlinHour(date = new Date()) {
    const formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Berlin",
      hour: "2-digit",
      hourCycle: "h23",
    });
    return Number(formatter.format(date));
  }

  function hashString(value) {
    return [...String(value)].reduce(
      (hash, character) =>
        (Math.imul(31, hash) + character.charCodeAt(0)) >>> 0,
      2166136261,
    );
  }

  function selectedChallengeAreaKey() {
    if (elements.city.value === "location" && locationCenter) return "location";
    return elements.city.value || "berlin";
  }

  function stationDetailUrl(stationId) {
    return `station/${stationId}.html`;
  }

  function chartUrl(station, fuel) {
    return `chart.html?id=${station.id}&fuel=${fuel}&name=${encodeURIComponent(
      station.name,
    )}&lat=${station.lat}&lng=${station.lng}`;
  }

  function formatDistance(distance) {
    const value = toFiniteNumber(distance);
    return value === null ? "-" : `${value.toFixed(1)} km`;
  }

  function formatPrice(price) {
    const value = toFiniteNumber(price);
    return value === null ? "Timing-Profil" : `${value.toFixed(3)} €/l`;
  }

  function formatSavings(cents, liters = 45) {
    const value = toFiniteNumber(cents);
    if (value === null || value <= 0) return "-";
    return `${((value / 100) * liters).toFixed(2)} €`;
  }

  function setStatus(message, type = "info") {
    if (!elements.status) return;
    elements.status.textContent = message;
    elements.status.className = `status mission-status ${type === "error" ? "error" : ""}`;
    elements.status.style.display = "block";
  }

  function clearStatus() {
    if (!elements.status) return;
    elements.status.style.display = "none";
  }

  function setBusy(isBusy) {
    [elements.refreshButton, elements.locationButton].forEach((button) => {
      if (button) button.disabled = isBusy;
    });
    if (elements.refreshButton) {
      elements.refreshButton.textContent = isBusy ? "Scan läuft..." : "Scan starten";
    }
  }

  function isTankerkoenigRequest(url) {
    return url.includes("tankerkoenig.de");
  }

  async function fetchJson(url) {
    let response;
    try {
      response = await fetch(url);
    } catch (err) {
      if (isTankerkoenigRequest(url)) {
        throw new Error(TANKERKOENIG_APOLOGY);
      }
      throw err;
    }
    if (!response.ok) {
      if (isTankerkoenigRequest(url)) {
        throw new Error(TANKERKOENIG_APOLOGY);
      }
      throw new Error(`Request failed: ${response.status}`);
    }
    const payload = await response.json();
    if (isTankerkoenigRequest(url) && payload && payload.ok === false) {
      throw new Error(TANKERKOENIG_APOLOGY);
    }
    return payload;
  }

  function normalizeStation(station) {
    const lat = toFiniteNumber(station.lat ?? station.latitude);
    const lng = toFiniteNumber(station.lng ?? station.longitude);
    const id = String(station.id ?? station.uuid ?? "").trim();
    if (!id || lat === null || lng === null) return null;
    return {
      id,
      name: station.name || station.brand || "Tankstelle",
      brand: station.brand || "",
      lat,
      lng,
      dist: toFiniteNumber(station.dist),
      diesel: toFiniteNumber(station.diesel),
      e10: toFiniteNumber(station.e10),
      e5: toFiniteNumber(station.e5),
      isOpen: station.isOpen !== false,
    };
  }

  async function loadLocalStations(center, radiusKm) {
    const catalog = await TankzeitStationCatalog.loadCatalog();
    return TankzeitStationCatalog.findNearbyStations(catalog, center.lat, center.lng, {
      limit: 48,
      radiusKm,
    })
      .map(normalizeStation)
      .filter(Boolean);
  }

  async function loadLiveStations(center, radiusKm, fuel) {
    const url =
      `https://creativecommons.tankerkoenig.de/json/list.php?rad=${radiusKm}` +
      `&type=all&sort=dist&lat=${center.lat}&lng=${center.lng}&apikey=${TANKERKOENIG_API_KEY}`;
    const payload = await fetchJson(url);
    return (payload.stations || [])
      .map(normalizeStation)
      .filter(Boolean)
      .filter((station) => station.isOpen && station[fuel] !== null)
      .slice(0, 48);
  }

  async function loadStats(station, fuel) {
    const statsUrl = `${DATA_BASE}${station.id.split("-").join("/")}/${fuel}.json`;
    try {
      return await fetchJson(statsUrl);
    } catch (err) {
      console.warn("Mission stats unavailable", station.id, err);
      return null;
    }
  }

  function nextBestHourDistance(stats) {
    if (!stats) return null;
    const hours = TankzeitStats.bestHours(stats);
    if (!hours.length) return null;
    const currentHour = berlinHour();
    for (let offset = 0; offset < 24; offset += 1) {
      if (hours.includes((currentHour + offset) % 24)) return offset;
    }
    return null;
  }

  function currentWindowLabel(stats) {
    const nextWindow = nextBestHourDistance(stats);
    if (nextWindow === 0) return "Jetzt-Fenster";
    if (nextWindow === 1) return "in 1 Stunde";
    if (nextWindow !== null && nextWindow <= 4) return `in ${nextWindow} Stunden`;
    return "Timing-Scout";
  }

  function livePriceScore(station, fuel, prices) {
    const price = station[fuel];
    if (price === null || !prices.length) return 0;
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    if (Math.abs(max - min) < 0.001) return 42;
    return Math.round(80 * (1 - (price - min) / (max - min)));
  }

  function scoreStation(station, stats, fuel, prices) {
    const spanCents = clamp((toFiniteNumber(stats?.span) || 0) * 100, 0, 35);
    const nowGood = stats ? TankzeitStats.isNowInRelativeMinimumWindow(stats) : false;
    const nextWindow = nextBestHourDistance(stats);
    const timingPoints = nowGood
      ? 120
      : nextWindow !== null
        ? clamp(104 - nextWindow * 9, 30, 96)
        : 32;
    const spreadPoints = Math.round(clamp(spanCents * 4.2, 0, 92));
    const distancePoints = Math.round(clamp(30 - (station.dist || 0) * 2.2, 0, 30));
    const pricePoints = livePriceScore(station, fuel, prices);
    const score = Math.round(timingPoints + spreadPoints + distancePoints + pricePoints);
    const minimumText = stats
      ? TankzeitStats.relativeMinimumText(stats, stats.text || "-")
      : "-";
    return {
      ...station,
      fuel,
      stats,
      score,
      nowGood,
      nextWindow,
      spanCents,
      timingPoints,
      spreadPoints,
      distancePoints,
      pricePoints,
      price: station[fuel],
      minimumText,
      profileText: stats ? TankzeitStats.profileText(stats, "Profil") : "Profil",
      windowLabel: currentWindowLabel(stats),
      estimatedSavings: formatSavings(spanCents),
    };
  }

  function awardFor(row) {
    return Math.max(30, Math.round(row.score / 3) + (row.nowGood ? 25 : 0));
  }

  function claimKey(row) {
    return `${todayKey()}|${row.fuel}|${row.id}`;
  }

  function isClaimed(row) {
    return Boolean(missionState.claimed?.[claimKey(row)]);
  }

  function dailyRewardKey(challenge) {
    return `${todayKey()}|${selectedChallengeAreaKey()}|${currentFuel}|${challenge.id}`;
  }

  function isDailyRewardClaimed(challenge) {
    return Boolean(missionState.dailyClaims?.[dailyRewardKey(challenge)]);
  }

  function claimedRowsForCurrentMission(rows) {
    return rows.filter((row) => isClaimed(row));
  }

  function buildDailyChallenge(rows) {
    const today = todayKey();
    const areaKey = selectedChallengeAreaKey();
    const seed = hashString(`${today}|${areaKey}|${currentFuel}`);
    let type = ["score", "triple", "now", "podium"][seed % 4];
    const claimedRows = claimedRowsForCurrentMission(rows);

    if (!rows.length) {
      return {
        id: "scan",
        title: "Mission scannen",
        detail: "Starte einen Scan, um das heutige Ziel und den Bonus freizuschalten.",
        reward: 100,
        progress: 0,
        target: 1,
        unit: "Scan",
        completed: false,
      };
    }

    if (type === "now" && !rows.some((row) => row.nowGood)) {
      type = "podium";
    }

    if (type === "triple") {
      const target = Math.min(3, Math.max(1, rows.length));
      const unit = target === 1 ? "Ziel" : "Ziele";
      return {
        id: "triple",
        title: target >= 3 ? "Dreierjagd" : "Zieljagd",
        detail: `Sammle ${target} ${unit} in dieser ${FUEL_LABELS[currentFuel]}-Mission.`,
        reward: 150,
        progress: Math.min(target, claimedRows.length),
        target,
        unit,
        completed: claimedRows.length >= target,
      };
    }

    if (type === "now") {
      const progress = claimedRows.filter((row) => row.nowGood).length;
      return {
        id: "now",
        title: "Fensterjagd",
        detail: "Sammle ein Ziel, das gerade in seinem Tankzeitfenster liegt.",
        reward: 140,
        progress: Math.min(1, progress),
        target: 1,
        unit: "Fenster",
        completed: progress >= 1,
      };
    }

    if (type === "podium") {
      const podiumIds = new Set(rows.slice(0, 3).map((row) => row.id));
      const progress = claimedRows.filter((row) => podiumIds.has(row.id)).length;
      return {
        id: "podium",
        title: "Podium sichern",
        detail: "Sammle eines der drei besten Ziele aus dem aktuellen Ranking.",
        reward: 120,
        progress: Math.min(1, progress),
        target: 1,
        unit: "Podium",
        completed: progress >= 1,
      };
    }

    const threshold = Math.max(120, Math.round((rows[0]?.score || 160) * 0.9));
    const progress = claimedRows.filter((row) => row.score >= threshold).length;
    return {
      id: "score",
      title: "Score-Hunt",
      detail: `Sammle ein Ziel mit mindestens ${threshold} Punkten.`,
      reward: 130,
      progress: Math.min(1, progress),
      target: 1,
      unit: "Score",
      completed: progress >= 1,
    };
  }

  function claimDailyRewardIfReady(rows) {
    const challenge = buildDailyChallenge(rows);
    if (!challenge.completed || isDailyRewardClaimed(challenge)) return 0;
    missionState.xp += challenge.reward;
    missionState.dailyClaims[dailyRewardKey(challenge)] = {
      reward: challenge.reward,
      claimedAt: new Date().toISOString(),
    };
    addBadge("daily");
    if (levelFromXp(missionState.xp) >= 3) addBadge("level3");
    saveMissionState();
    return challenge.reward;
  }

  function addBadge(id) {
    if (!missionState.badges.includes(id)) {
      missionState.badges.push(id);
    }
  }

  function updateStreakForToday() {
    const today = todayKey();
    if (missionState.lastClaimDay === today) return;
    missionState.streak =
      missionState.lastClaimDay && dayDelta(missionState.lastClaimDay, today) === 1
        ? missionState.streak + 1
        : 1;
    missionState.lastClaimDay = today;
  }

  function claimRow(row) {
    if (isClaimed(row)) return;
    const award = awardFor(row);
    updateStreakForToday();
    missionState.xp += award;
    missionState.claimed[claimKey(row)] = award;
    addBadge("first");
    if (row.nowGood) addBadge("window");
    if (livePricesAvailable) addBadge("live");
    if (missionState.streak >= 3) addBadge("streak");
    if (levelFromXp(missionState.xp) >= 3) addBadge("level3");
    saveMissionState();
    const dailyBonus = claimDailyRewardIfReady(currentRows);
    renderProgress();
    renderBadges();
    renderDailyChallenge(currentRows);
    renderRows(currentRows);
    setStatus(
      dailyBonus
        ? `+${award} XP für ${row.name}. Tageschallenge +${dailyBonus} XP.`
        : `+${award} XP für ${row.name}.`,
    );
  }

  function levelFromXp(xp) {
    return Math.floor(xp / 250) + 1;
  }

  function renderProgress() {
    const level = levelFromXp(missionState.xp);
    const levelBase = (level - 1) * 250;
    const nextLevel = level * 250;
    const levelProgress = clamp((missionState.xp - levelBase) / 250, 0, 1);
    elements.xp.textContent = String(missionState.xp);
    elements.level.textContent = `Level ${level}`;
    elements.progress.style.width = `${Math.round(levelProgress * 100)}%`;
    elements.progressLabel.textContent = `${missionState.xp - levelBase} / ${
      nextLevel - levelBase
    } XP`;
    elements.scoreDial.style.setProperty(
      "--score-percent",
      String(Math.round(levelProgress * 100)),
    );
    elements.streak.textContent = `${missionState.streak || 0} Tage`;
  }

  function badgeCatalog() {
    return {
      first: { label: "Erste Mission", meta: "XP gesammelt" },
      window: { label: "Fensterjäger", meta: "im Bestzeitfenster" },
      live: { label: "Live-Radar", meta: "Preisrank gefunden" },
      streak: { label: "Serie", meta: "3 Tage aktiv" },
      level3: { label: "Level 3", meta: "750 XP" },
      daily: { label: "Tagesziel", meta: "Bonus geholt" },
    };
  }

  function renderBadges() {
    const catalog = badgeCatalog();
    const badges = missionState.badges.length
      ? missionState.badges
      : ["first", "window", "live"];
    elements.badges.innerHTML = badges
      .map((id) => {
        const badge = catalog[id];
        if (!badge) return "";
        const earned = missionState.badges.includes(id);
        return `<span class="mission-badge ${earned ? "earned" : ""}">
          <strong>${escapeHtml(badge.label)}</strong>
          <small>${escapeHtml(badge.meta)}</small>
        </span>`;
      })
      .join("");
  }

  function renderDailyChallenge(rows) {
    if (!elements.dailyPanel) return;
    const challenge = buildDailyChallenge(rows);
    const progressRatio = clamp(challenge.progress / challenge.target, 0, 1);
    const rewardClaimed = isDailyRewardClaimed(challenge);
    elements.dailyTitle.textContent = challenge.title;
    elements.dailyDetail.textContent = challenge.detail;
    elements.dailyProgress.textContent = `${challenge.progress} / ${challenge.target} ${challenge.unit}`;
    elements.dailyProgressFill.style.width = `${Math.round(progressRatio * 100)}%`;
    elements.dailyReward.textContent = rewardClaimed
      ? "Bonus gesichert"
      : `${challenge.reward} XP Bonus`;
    elements.dailyPanel.classList.toggle("is-complete", challenge.completed);
  }

  function dailyShareText() {
    const challenge = buildDailyChallenge(currentRows);
    const topRow = currentRows[0];
    const state = challenge.completed
      ? "geschafft"
      : `${challenge.progress}/${challenge.target} ${challenge.unit}`;
    const topTarget = topRow
      ? `${topRow.name} mit ${topRow.score} Punkten`
      : "noch kein Ziel";
    return [
      `Tankzeit Mission ${todayKey()}: ${challenge.title} ${state}.`,
      `Top-Ziel: ${topTarget}.`,
      "https://tankzeit.de/mission.html",
    ].join(" ");
  }

  async function shareDailyChallenge() {
    const text = dailyShareText();
    try {
      if (navigator.share) {
        await navigator.share({
          title: "Tankzeit Mission",
          text,
          url: "https://tankzeit.de/mission.html",
        });
        return;
      }
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        setStatus("Mission-Text wurde kopiert.");
        return;
      }
      throw new Error("Share unavailable");
    } catch (err) {
      if (err?.name === "AbortError") return;
      console.warn("Mission share failed", err);
      setStatus("Teilen ist in diesem Browser gerade nicht verfügbar.", "error");
    }
  }

  function renderSummary(rows) {
    const topRow = rows[0] || null;
    const nowCount = rows.filter((row) => row.nowGood).length;
    elements.bestScore.textContent = topRow ? String(topRow.score) : "0";
    elements.bestStation.textContent = topRow ? topRow.name : "Noch kein Ziel";
    elements.stationCount.textContent = String(rows.length);
    elements.nowCount.textContent = String(nowCount);
    elements.liveMode.textContent = livePricesAvailable ? "Livepreise" : "Timing";
    elements.target.textContent = topRow
      ? `${FUEL_LABELS[currentFuel]} · ${topRow.windowLabel} · ${formatDistance(
          topRow.dist,
        )}`
      : "Scan starten";
  }

  function renderRows(rows) {
    if (!rows.length) {
      elements.board.innerHTML =
        '<div class="mission-empty">Keine Mission im aktuellen Radius gefunden.</div>';
      return;
    }
    elements.board.innerHTML = rows
      .map((row, index) => {
        const claimed = isClaimed(row);
        const detailHref = hrefForHtml(stationDetailUrl(row.id));
        const chartHref = hrefForHtml(chartUrl(row, row.fuel));
        return `<article class="mission-target ${row.nowGood ? "is-now" : ""}">
          <div class="mission-target-rank">${index + 1}</div>
          <div class="mission-target-main">
            <div class="mission-target-title">
              <h2><a href="${detailHref}">${escapeHtml(row.name)}</a></h2>
              <span>${escapeHtml(row.brand || FUEL_LABELS[row.fuel])}</span>
            </div>
            <div class="mission-target-tags">
              <span>${escapeHtml(row.windowLabel)}</span>
              <span>${escapeHtml(formatPrice(row.price))}</span>
              <span>${escapeHtml(formatDistance(row.dist))}</span>
              <span>${escapeHtml(row.minimumText)}</span>
            </div>
            <div class="mission-target-split">
              <a href="${chartHref}">${escapeHtml(row.profileText)}</a>
              <span>${escapeHtml(row.estimatedSavings)} auf 45 l</span>
            </div>
          </div>
          <div class="mission-target-score">
            <strong>${row.score}</strong>
            <span>PTS</span>
          </div>
          <button
            class="mission-claim"
            type="button"
            data-claim-id="${escapeHtml(row.id)}"
            ${claimed ? "disabled" : ""}
          >${claimed ? "Gesammelt" : `+${awardFor(row)} XP`}</button>
        </article>`;
      })
      .join("");

    elements.board.querySelectorAll("[data-claim-id]").forEach((button) => {
      button.addEventListener("click", () => {
        const row = currentRows.find((entry) => entry.id === button.dataset.claimId);
        if (row) claimRow(row);
      });
    });
  }

  function renderControls() {
    elements.fuelButtons.forEach((button) => {
      const isActive = button.dataset.missionFuel === currentFuel;
      button.classList.toggle("active", isActive);
      button.setAttribute("aria-pressed", isActive ? "true" : "false");
    });
    const cityLabel =
      elements.city.value === "location" && locationCenter
        ? "Standort"
        : CITY_PRESETS[elements.city.value]?.label || currentCenter.label;
    elements.subtitle.textContent = `${cityLabel} · ${
      FUEL_LABELS[currentFuel]
    } · ${elements.radius.value} km`;
  }

  function renderMission(rows) {
    currentRows = rows;
    renderProgress();
    renderBadges();
    renderDailyChallenge(rows);
    renderSummary(rows);
    renderRows(rows);
    renderControls();
  }

  function selectedCenter() {
    if (elements.city.value === "location" && locationCenter) {
      return locationCenter;
    }
    return CITY_PRESETS[elements.city.value] || CITY_PRESETS.berlin;
  }

  async function loadMission(options = {}) {
    const shouldCountScan = options.countScan !== false;
    const radiusKm = Number(elements.radius.value) || 15;
    currentCenter = selectedCenter();
    renderControls();
    setBusy(true);
    setStatus("Mission wird gescannt...");
    try {
      let stations = [];
      livePricesAvailable = false;
      try {
        stations = await loadLiveStations(currentCenter, radiusKm, currentFuel);
        livePricesAvailable = true;
      } catch (err) {
        if (err && err.message !== TANKERKOENIG_APOLOGY) {
          console.warn("Live mission scan failed", err);
        }
        stations = await loadLocalStations(currentCenter, radiusKm);
      }

      const prices = stations
        .map((station) => station[currentFuel])
        .filter((price) => price !== null);
      const scoredRows = (
        await Promise.all(
          stations.slice(0, 24).map(async (station) => {
            const stats = await loadStats(station, currentFuel);
            if (!stats) return null;
            return scoreStation(station, stats, currentFuel, prices);
          }),
        )
      )
        .filter(Boolean)
        .sort((left, right) => {
          if (right.score !== left.score) return right.score - left.score;
          return (left.dist || 0) - (right.dist || 0);
        })
        .slice(0, 12);

      if (shouldCountScan) {
        missionState.scans += 1;
        saveMissionState();
      }
      renderMission(scoredRows);
      if (livePricesAvailable) {
        clearStatus();
      } else {
        setStatus("Timing-Modus aktiv. Livepreise sind derzeit nicht verfügbar.");
      }
    } catch (err) {
      console.warn("Mission scan failed", err);
      elements.board.innerHTML =
        '<div class="mission-empty">Mission konnte nicht geladen werden.</div>';
      renderSummary([]);
      setStatus("Mission konnte nicht geladen werden. Bitte später erneut versuchen.", "error");
    } finally {
      setBusy(false);
    }
  }

  function requestLocation() {
    if (!navigator.geolocation) {
      setStatus("Standort ist in diesem Browser nicht verfügbar.", "error");
      return;
    }
    setBusy(true);
    setStatus("Standort wird ermittelt...");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        locationCenter = {
          label: "Standort",
          lat: position.coords.latitude,
          lng: position.coords.longitude,
        };
        elements.city.value = "location";
        loadMission();
      },
      (err) => {
        console.warn("Mission geolocation failed", err);
        setBusy(false);
        setStatus("Standort konnte nicht ermittelt werden.", "error");
      },
      { enableHighAccuracy: false, maximumAge: 300000, timeout: 15000 },
    );
  }

  elements.city.addEventListener("change", () => loadMission());
  elements.radius.addEventListener("change", () => loadMission());
  elements.refreshButton.addEventListener("click", () => loadMission());
  elements.locationButton.addEventListener("click", requestLocation);
  elements.shareButton?.addEventListener("click", shareDailyChallenge);
  elements.fuelButtons.forEach((button) => {
    button.addEventListener("click", () => {
      currentFuel = button.dataset.missionFuel;
      loadMission();
    });
  });

  renderMission([]);
  loadMission({ countScan: false });
})();
