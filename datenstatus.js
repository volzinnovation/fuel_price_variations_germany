const FILES = [
  { key: "stations", label: "Stationskatalog", path: "data/stations.json", type: "json" },
  { key: "noon", label: "Mittagspreise", path: "data/noon.csv", type: "csv" },
  { key: "midnight", label: "Mitternachtspreise", path: "data/midnight.csv", type: "csv" },
  { key: "brent", label: "Brent-Referenz", path: "data/brent.json", type: "json" },
];

const numberFormat = new Intl.NumberFormat("de-DE");
const dateTimeFormat = new Intl.DateTimeFormat("de-DE", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

function formatNumber(value) {
  const number = Number(value || 0);
  return numberFormat.format(Number.isFinite(number) ? number : 0);
}

function formatTimestamp(value) {
  const parsed = new Date(String(value || ""));
  return Number.isNaN(parsed.getTime()) ? "n/a" : dateTimeFormat.format(parsed);
}

function showStatus(message, error = false) {
  const element = document.getElementById("data-status-message");
  element.textContent = message;
  element.style.display = message ? "block" : "none";
  element.classList.toggle("error", error);
}

async function fetchText(file) {
  const response = await fetch(file.path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${file.path} lieferte ${response.status}`);
  }
  return {
    file,
    text: await response.text(),
    lastModified: response.headers.get("last-modified") || "",
  };
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (char === '"' && quoted && next === '"') {
      cell += '"';
      index += 1;
      continue;
    }
    if (char === '"') {
      quoted = !quoted;
      continue;
    }
    if (char === "," && !quoted) {
      row.push(cell);
      cell = "";
      continue;
    }
    if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && next === "\n") {
        index += 1;
      }
      row.push(cell);
      if (row.some((value) => value !== "")) {
        rows.push(row);
      }
      row = [];
      cell = "";
      continue;
    }
    cell += char;
  }

  if (cell || row.length) {
    row.push(cell);
    if (row.some((value) => value !== "")) {
      rows.push(row);
    }
  }

  return rows;
}

function latestTimestampFromCsv(rows) {
  const [header = [], ...dataRows] = rows;
  const timestampIndexes = header
    .map((name, index) => ({ name, index }))
    .filter(({ name }) => name === "last_update" || name.endsWith("_last_update"))
    .map(({ index }) => index);
  let latest = "";
  let latestTime = 0;

  dataRows.forEach((row) => {
    timestampIndexes.forEach((index) => {
      const value = row[index];
      const parsed = new Date(String(value || ""));
      if (!Number.isNaN(parsed.getTime()) && parsed.getTime() > latestTime) {
        latest = value;
        latestTime = parsed.getTime();
      }
    });
  });

  return latest;
}

function summarizeFile({ file, text, lastModified }) {
  if (file.type === "json") {
    const payload = JSON.parse(text);
    if (file.key === "stations") {
      return {
        ...file,
        ok: true,
        rows: Array.isArray(payload) ? payload.length : 0,
        latest: lastModified,
        note: "Tankstellen im lokalen Fallback-Katalog",
      };
    }
    return {
      ...file,
      ok: true,
      rows: 1,
      latest: payload.generated_at || payload.brent_as_of || lastModified,
      note: payload.brent_usd_per_barrel
        ? `${payload.brent_usd_per_barrel} USD/Barrel, ${payload.usd_per_eur} USD/EUR`
        : "JSON geladen",
    };
  }

  const rows = parseCsv(text);
  return {
    ...file,
    ok: true,
    rows: Math.max(0, rows.length - 1),
    latest: latestTimestampFromCsv(rows) || lastModified,
    note: rows[0]?.join(", ") || "CSV geladen",
  };
}

function renderKpis(summaries) {
  const stations = summaries.find((summary) => summary.key === "stations")?.rows || 0;
  const noon = summaries.find((summary) => summary.key === "noon")?.rows || 0;
  const midnight = summaries.find((summary) => summary.key === "midnight")?.rows || 0;
  const brent = summaries.find((summary) => summary.key === "brent");
  const cards = [
    ["Stationen", formatNumber(stations), "lokaler Katalog"],
    ["Mittagspreise", formatNumber(noon), "Station-Fuel Anker um 12:00"],
    ["Mitternachtspreise", formatNumber(midnight), "Referenz fuer Tagesstart"],
    ["Brent erzeugt", formatTimestamp(brent?.latest), brent?.note || "n/a"],
  ];

  document.getElementById("data-status-kpis").innerHTML = cards
    .map(
      ([label, value, detail]) => `
        <article class="data-status-kpi">
          <span>${label}</span>
          <strong>${value}</strong>
          <p>${detail}</p>
        </article>
      `,
    )
    .join("");
}

function renderFiles(summaries, failures) {
  const failed = new Map(failures.map((failure) => [failure.file.key, failure]));
  document.getElementById("data-file-list").innerHTML = FILES
    .map((file) => {
      const summary = summaries.find((item) => item.key === file.key);
      const failure = failed.get(file.key);
      const ok = Boolean(summary) && !failure;
      return `
        <div class="data-file-row ${ok ? "ok" : "error"}">
          <span>${ok ? "OK" : "Fehlt"}</span>
          <div>
            <strong>${file.label}</strong>
            <small>${file.path}</small>
          </div>
        </div>
      `;
    })
    .join("");
}

function renderAnchors(summaries) {
  document.getElementById("data-anchor-body").innerHTML = summaries
    .map(
      (summary) => `
        <tr>
          <td>${summary.label}</td>
          <td>${formatNumber(summary.rows)}</td>
          <td>${formatTimestamp(summary.latest)}</td>
          <td>${summary.note}</td>
        </tr>
      `,
    )
    .join("");
}

async function init() {
  const results = await Promise.allSettled(FILES.map(fetchText));
  const summaries = [];
  const failures = [];

  results.forEach((result, index) => {
    if (result.status === "fulfilled") {
      try {
        summaries.push(summarizeFile(result.value));
      } catch (error) {
        failures.push({ file: FILES[index], error });
      }
    } else {
      failures.push({ file: FILES[index], error: result.reason });
    }
  });

  renderKpis(summaries);
  renderFiles(summaries, failures);
  renderAnchors(summaries);

  if (failures.length) {
    showStatus(`${failures.length} Datenpaket(e) konnten nicht gelesen werden.`, true);
  } else {
    showStatus("Alle oeffentlichen Datenpakete wurden erfolgreich gelesen.");
  }
}

init().catch((error) => {
  showStatus(`Datenstatus konnte nicht aufgebaut werden: ${error.message}`, true);
});
