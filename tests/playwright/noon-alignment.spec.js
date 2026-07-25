const fs = require("fs");
const path = require("path");
const http = require("http");
const { test, expect } = require("@playwright/test");

const {
  managementDate,
  managementFixture,
  managementPath,
  nearbyStationsResponse,
  noonCsvByPath,
  stationId,
  stationHistoryCsv,
  stationIdPath,
  stationStatsFixture,
} = require("./fixtures/noonAlignmentFixtures");

const repoRoot = path.resolve(__dirname, "..", "..");
const MIME_TYPES = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
};

let server;
let baseUrl;

test.use({
  locale: "de-DE",
  timezoneId: "UTC",
});

test.describe.configure({ mode: "serial" });

test.beforeAll(async () => {
  server = http.createServer((request, response) => {
    serveStaticAsset(request, response);
  });

  await new Promise((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });

  const { port } = server.address();
  baseUrl = `http://127.0.0.1:${port}`;
});

test.afterAll(async () => {
  if (!server) return;
  await new Promise((resolve, reject) => {
    server.close((error) => {
      if (error) {
        reject(error);
        return;
      }
      resolve();
    });
  });
});

test("index.html renders the single-purpose per-fuel audit", async ({
  page,
}) => {
  await routeCommonResponses(page);

  await page.goto(`${baseUrl}/index.html`);
  await expect(page.locator("main > *").first()).toHaveAttribute(
    "id",
    "app-install-promo",
  );
  await expect(
    page.getByRole("heading", {
      level: 2,
      name: "Tankzeit für jede Tankstelle genau",
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Tankzeit im App Store öffnen" }),
  ).toHaveAttribute(
    "href",
    "https://apps.apple.com/de/app/tankzeit/id6759522835",
  );
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Tanke zwischen 11:00 und 11:59 Uhr.",
  );
  await expect(page.locator(".simple-clock-window path")).toHaveAttribute(
    "d",
    "M50 50 L25 6.699 A50 50 0 0 1 50 0 Z",
  );
  await expect(page.locator("#simple-median-value")).toHaveText("19");
  await expect(page.locator("#simple-max-value")).toHaveText("46 ct");
  await expect(page.locator("#simple-confirmation-copy")).toContainText(
    "bestätigen 11:00 als günstigsten Zeitraum.",
  );
  expect(await page.locator(".simple-histogram-bar").count()).toBeGreaterThan(0);
  await expect(page.locator(".simple-histogram-bar.is-median")).toHaveAttribute(
    "data-median-label",
    "Median 19 ct/L",
  );
  await page.locator('[data-analysis-fuel="e10"]').click();
  await expect(page.locator("#simple-median-value")).toHaveText("21");
  await expect(page.locator("#simple-max-value")).toHaveText("47 ct");
  await expect(page.locator(".simple-histogram-bar.is-median")).toHaveAttribute(
    "data-median-label",
    "Median 21 ct/L",
  );
  await page.locator('[data-analysis-fuel="e5"]').click();
  await expect(page.locator("#simple-max-value")).toHaveText("75 ct");
  await expect(
    page.getByRole("button", { name: "Standort verwenden" }),
  ).toHaveCount(0);
  await expect(page.locator("#stationen, .nav-bar")).toHaveCount(0);
  await expect(page.locator(".simple-footer").getByRole("link", { name: "Info" }))
    .toHaveAttribute("href", "info.html");
});

test("e10.html shows E10 tankzeit ending exactly at noon", async ({ page }) => {
  await routeFixtureResponses(page);

  await page.goto(`${baseUrl}/e10.html`);
  await page.evaluate(() => {
    handleLocation({ coords: { latitude: 52.52, longitude: 13.405 } });
  });

  await expect(page.locator(`#${stationId}-minimum`)).toHaveText(
    expectedTankzeitText(stationStatsFixture),
  );
  await expect(page.locator(`#${stationId}-profile`)).toContainText("2.050");
  await expect(page.locator("thead th")).toHaveText([
    "",
    "Name",
    "Tankzeit",
    "Preis €/l",
    "Preisverlauf",
    "Entfernung",
  ]);
  await expect(page.locator(`#${stationId} td`).nth(1).locator("a")).toHaveAttribute(
    "href",
    `station/${stationId}.html`,
  );
  await expect(page.locator(`#${stationId} td`).nth(5)).toHaveText("1.2 km");
});

test("price.html renders query-controlled station labels as text", async ({ page }) => {
  await routeCommonResponses(page);
  const payload = '<img src=x onerror="window.__xss_price=1">';

  await page.goto(
    `${baseUrl}/price.html?fuel=diesel&price=1.80&brand=${encodeURIComponent(payload)}`,
  );

  await expect(page.locator("#brent-steps")).toContainText(payload);
  await expect(page.locator("#brent-steps img")).toHaveCount(0);
  expect(await page.evaluate(() => window.__xss_price)).toBeUndefined();
});

test("e10.html rejects malformed live station fields before rendering", async ({
  page,
}) => {
  await routeMalformedStationResponse(page);

  await page.goto(`${baseUrl}/e10.html`);
  await page.evaluate(() => {
    handleLocation({ coords: { latitude: 52.52, longitude: 13.405 } });
  });

  await expect(page.locator("#stations tr")).toHaveCount(1);
  await expect(page.locator(`#${stationId}`)).toBeVisible();
  await expect(page.locator("#stations")).not.toContainText("Bad Station");
  await expect(page.locator("#stations img")).toHaveCount(0);
  expect(await page.evaluate(() => window.__xss_station)).toBeUndefined();
});

test("favoriten.html hides live prices when the live price endpoint is unavailable", async ({
  page,
}) => {
  await routeFavoriteFailureResponses(page);
  await page.addInitScript(({ favoriteId }) => {
    localStorage.setItem("ids", JSON.stringify([favoriteId]));
    localStorage.setItem(
      "fav",
      JSON.stringify([
        {
          id: favoriteId,
          name: "Noon Test Station",
          lat: 52.52,
          lng: 13.405,
        },
      ]),
    );
  }, { favoriteId: stationId });

  await page.goto(`${baseUrl}/favoriten.html`);

  await expect(page.locator("#status")).toContainText(
    "Tankzeit und Detailansichten bleiben verfügbar.",
  );
  await expect(page.locator(`#${stationId}-e10`)).toHaveText("Livepreis nicht verfügbar");
  await expect(page.locator(`#${stationId}-diesel`)).toHaveText("Livepreis nicht verfügbar");
  await expect(page.locator(`#${stationId}-profile`)).toContainText("2.050");
});

test("chart.html renders the noon-cycle profile from prior noon to current noon", async ({
  page,
}) => {
  await routeFixtureResponses(page);

  await page.goto(
    `${baseUrl}/chart.html?view=profile&id=${stationId}&fuel=e10&name=Noon%20Test%20Station`,
  );
  await expect(page.locator("#chart-title")).toContainText("Noon Test Station");
  await expect(page.locator("#chart-sub")).toContainText(
    "Zyklus 12:00 Vortag → 12:00 Tag",
  );

  const chartState = await page.evaluate(() => {
    const chart = window.Chart.getChart(document.getElementById("chart"));
    return {
      stepped: chart.data.datasets[0].stepped,
      firstLabel: chart.data.labels[0],
      midnightLabel: chart.data.labels[12],
      midnight: chart.data.datasets[0].data[12],
      nextMorning: chart.data.datasets[0].data[18],
    };
  });
  expect(chartState.stepped).toBe("before");
  expect(chartState.firstLabel).toBe("12:00");
  expect(chartState.midnightLabel).toBe("00:00");
  expect(chartState.midnight).toBe(-0.05);
  expect(chartState.nextMorning).toBe(-0.05);
});

test("management.html renders the noon-cycle with midnight in the middle", async ({ page }) => {
  await routeFixtureResponses(page);

  const managementResponse = page.waitForResponse((response) => {
    const responseUrl = new URL(response.url());
    return (
      response.request().method() === "GET" &&
      responseUrl.pathname.endsWith(managementPath) &&
      response.ok()
    );
  });

  await page.goto(`${baseUrl}/management.html?date=${managementDate}`);
  await managementResponse;
  await expect(page.locator("#date-picker")).toHaveValue(managementDate);
  await waitForManagementChart(page, "Diesel");

  await clickManagementHour(page, 0);
  await expect(page.locator("#status")).toContainText("DIESEL Zyklus 12:00:");
  await expect(page.locator("#status")).toContainText("auf Vortag 12:00-Niveau");
  await expect(page.locator("#status")).toContainText("Zeitraum 12:00 Vortag → 12:00 Tag");

  await clickManagementHour(page, 12);
  await expect(page.locator("#status")).toContainText("DIESEL Zyklus 00:00:");
  await expect(page.locator("#status")).toContainText("5.0 ct/l günstiger");
  await expect(page.locator("#status")).toContainText("Zeitraum 12:00 Vortag → 12:00 Tag");

  await clickManagementHour(page, 23);
  await expect(page.locator("#status")).toContainText("DIESEL Zyklus 11:00:");
  await expect(page.locator("#status")).toContainText("5.0 ct/l günstiger");

  await page.getByRole("tab", { name: "E10" }).click();
  await waitForManagementChart(page, "E10");
  await clickManagementHour(page, 12);
  await expect(page.locator("#status")).toContainText("E10 Zyklus 00:00:");
  await expect(page.locator("#status")).toContainText("5.0 ct/l günstiger");
});

async function routeFixtureResponses(page) {
  await page.route("https://fonts.googleapis.com/**", (route) => route.abort());
  await page.route("https://fonts.gstatic.com/**", (route) => route.abort());
  await page.route("https://cdnjs.cloudflare.com/**", (route) => route.abort());
  await page.route("https://creativecommons.tankerkoenig.de/json/list.php**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify(nearbyStationsResponse),
    });
  });
  await page.route(`**/data2/${stationIdPath}/diesel.json`, (route) => {
    route.fulfill(jsonResponse(stationStatsFixture));
  });
  await page.route(`**/data2/${stationIdPath}/e10.json`, (route) => {
    route.fulfill(jsonResponse(stationStatsFixture));
  });
  await page.route(`**/data2/${stationIdPath}/e5.json`, (route) => {
    route.fulfill(jsonResponse(stationStatsFixture));
  });
  await page.route(`**/data2/${stationIdPath}/**/history.csv`, (route) => {
    route.fulfill(csvResponse(stationHistoryCsv));
  });
  await page.route("**/data2/**/management_boxplots.json", (route) => {
    const request = route.request();
    const requestUrl = new URL(request.url());
    if (!requestUrl.pathname.endsWith(managementPath)) {
      route.fulfill({
        status: 404,
        body: "",
      });
      return;
    }
    if (request.method() === "HEAD") {
      route.fulfill({
        status: 200,
        body: "",
      });
      return;
    }
    route.fulfill(jsonResponse(managementFixture));
  });
  await page.route("**/data2/**/noon.csv", (route) => {
    const requestUrl = new URL(route.request().url());
    const dataPathStart = requestUrl.pathname.indexOf("/data2/");
    const dataPath =
      dataPathStart >= 0
        ? requestUrl.pathname.slice(dataPathStart)
        : requestUrl.pathname;
    const body = noonCsvByPath[dataPath];
    if (!body) {
      route.fulfill({
        status: 404,
        body: "",
      });
      return;
    }
    route.fulfill(csvResponse(body));
  });
}

async function routeFavoriteFailureResponses(page) {
  await routeCommonResponses(page);
  await page.route("https://creativecommons.tankerkoenig.de/json/prices.php**", (route) => {
    route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify({
        ok: false,
        status: "error",
        message: "Key existiert nicht oder ist deaktiviert",
      }),
    });
  });
}

async function routeMalformedStationResponse(page) {
  await routeCommonResponses(page);
  await page.route("https://creativecommons.tankerkoenig.de/json/list.php**", (route) => {
    const safeStation = nearbyStationsResponse.stations[0];
    route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify({
        ...nearbyStationsResponse,
        stations: [
          {
            ...safeStation,
            id: 'bad" autofocus onfocus="window.__xss_station=1',
            name: 'Bad Station <img src=x onerror="window.__xss_station=1">',
            brand: "EVIL",
            lat: '52.52" onclick="window.__xss_station=1',
            lng: 13.405,
            diesel: '<img src=x onerror="window.__xss_station=1">',
            e10: '<img src=x onerror="window.__xss_station=1">',
          },
          safeStation,
        ],
      }),
    });
  });
}

async function routeCommonResponses(page) {
  await page.route("https://fonts.googleapis.com/**", (route) => route.abort());
  await page.route("https://fonts.gstatic.com/**", (route) => route.abort());
  await page.route("https://cdnjs.cloudflare.com/**", (route) => route.abort());
  await page.route(`**/data2/${stationIdPath}/diesel.json`, (route) => {
    route.fulfill(jsonResponse(stationStatsFixture));
  });
  await page.route(`**/data2/${stationIdPath}/e10.json`, (route) => {
    route.fulfill(jsonResponse(stationStatsFixture));
  });
  await page.route(`**/data2/${stationIdPath}/e5.json`, (route) => {
    route.fulfill(jsonResponse(stationStatsFixture));
  });
  await page.route(`**/data2/${stationIdPath}/**/history.csv`, (route) => {
    route.fulfill(csvResponse(stationHistoryCsv));
  });
}

function jsonResponse(payload) {
  return {
    status: 200,
    contentType: "application/json; charset=utf-8",
    body: JSON.stringify(payload),
  };
}

function csvResponse(body) {
  return {
    status: 200,
    contentType: "text/csv; charset=utf-8",
    body,
  };
}

function expectedTankzeitText(stats) {
  const summary = stats && stats.summary;
  if (summary && (summary.min_time_text || summary.min_duration_text)) {
    if (summary.min_time_text && summary.min_duration_text) {
      return `${summary.min_time_text} Uhr · ${summary.min_duration_text}`;
    }
    if (summary.min_time_text) {
      return `${summary.min_time_text} Uhr`;
    }
    return summary.min_duration_text;
  }

  const hourlyRows = (stats && stats.hourly) || [];
  const minimum = Math.min(...hourlyRows.map((row) => Number(row.price)));
  const hours = hourlyRows
    .filter((row) => Number(row.price) === minimum)
    .map((row) => Number(row.hour))
    .sort((left, right) => left - right);

  const ranges = [];
  let start = hours[0];
  let end = hours[0];

  for (const hour of hours.slice(1)) {
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

async function clickManagementHour(page, hour) {
  await page.locator("#plot").evaluate((element, hourIndex) => {
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      throw new Error("Management plot canvas is not visible.");
    }

    const plotLeft = 72;
    const plotRight = 18;
    const plotWidth = rect.width - plotLeft - plotRight;
    const bucketCount = 25;
    const clientX =
      rect.left + plotLeft + ((hourIndex + 0.5) * plotWidth) / bucketCount;
    const clientY = rect.top + 32;

    element.dispatchEvent(
      new MouseEvent("click", {
        bubbles: true,
        cancelable: true,
        clientX,
        clientY,
      }),
    );
  }, hour);
}

async function waitForManagementChart(page, fuelLabel) {
  await expect(page.locator("#title")).toHaveText(
    `Preisänderung zur 12:00-Referenz · ${fuelLabel}`,
  );
  await page.waitForFunction(() => {
    const canvas = document.getElementById("plot");
    if (!canvas) return false;
    const rect = canvas.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  });
}

function serveStaticAsset(request, response) {
  const requestUrl = new URL(request.url, "http://127.0.0.1");
  let pathname = decodeURIComponent(requestUrl.pathname);
  if (pathname === "/") pathname = "/index.html";

  const resolvedPath = path.resolve(repoRoot, `.${pathname}`);
  if (!resolvedPath.startsWith(repoRoot)) {
    response.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Forbidden");
    return;
  }

  let filePath = resolvedPath;
  if (fs.existsSync(filePath) && fs.statSync(filePath).isDirectory()) {
    filePath = path.join(filePath, "index.html");
  }

  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Not Found");
    return;
  }

  const extname = path.extname(filePath).toLowerCase();
  const contentType = MIME_TYPES[extname] || "application/octet-stream";
  response.writeHead(200, { "Content-Type": contentType });

  if (request.method === "HEAD") {
    response.end();
    return;
  }

  fs.createReadStream(filePath).pipe(response);
}
