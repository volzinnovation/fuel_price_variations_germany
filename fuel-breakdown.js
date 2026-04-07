(() => {
  const VAT_RATE = 0.19;
  const CO2_PRICE_MAX_EUR_PER_TON = 65;
  const CO2_PRICE_FLOOR_EUR_PER_TON = 55;
  const EBV_EUR_PER_TON = 3.56;

  const FUELS = {
    diesel: {
      key: "diesel",
      label: "Diesel",
      energyTaxPerLiter: 0.4704,
      co2KgPerLiter: 2.627,
      ebvDensityTonsPerM3: 0.845,
      defaultBackPath: "index.html",
      defaultBackLabel: "Zurück zu Diesel",
    },
    e10: {
      key: "e10",
      label: "E10",
      energyTaxPerLiter: 0.6545,
      co2KgPerLiter: 2.309,
      ebvDensityTonsPerM3: 0.755,
      defaultBackPath: "e10.html",
      defaultBackLabel: "Zurück zu E10",
    },
    e5: {
      key: "e5",
      label: "E5",
      energyTaxPerLiter: 0.6545,
      co2KgPerLiter: 2.330,
      ebvDensityTonsPerM3: 0.755,
      defaultBackPath: "e10.html",
      defaultBackLabel: "Zurück zu E10",
    },
  };

  function toNumber(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function fuelConfig(fuel) {
    return FUELS[String(fuel || "").toLowerCase()] || FUELS.diesel;
  }

  function formatEuroPerLiter(value, decimals = 3) {
    const amount = toNumber(value);
    if (amount === null) return "-";
    return `${new Intl.NumberFormat("de-DE", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(amount)} €/l`;
  }

  function formatCentPerLiter(value, decimals = 1) {
    const amount = toNumber(value);
    if (amount === null) return "-";
    return `${new Intl.NumberFormat("de-DE", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(amount * 100)} ct/l`;
  }

  function formatPercent(value, decimals = 1) {
    const amount = toNumber(value);
    if (amount === null) return "-";
    return `${new Intl.NumberFormat("de-DE", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(amount)} %`;
  }

  function ebvPerLiter(config) {
    return (EBV_EUR_PER_TON * config.ebvDensityTonsPerM3) / 1000;
  }

  function buildBreakdownUrl({
    id,
    fuel,
    name,
    brand,
    price,
    lat,
    lng,
    backPath,
    backLabel,
  }) {
    const params = new URLSearchParams();
    const config = fuelConfig(fuel);

    params.set("fuel", config.key);
    if (id) params.set("id", id);
    if (name) params.set("name", name);
    if (brand) params.set("brand", brand);
    if (price !== undefined && price !== null && price !== "") {
      params.set("price", String(price));
    }
    if (lat !== undefined && lat !== null && lat !== "") {
      params.set("lat", String(lat));
    }
    if (lng !== undefined && lng !== null && lng !== "") {
      params.set("lng", String(lng));
    }
    if (backPath) params.set("back", backPath);
    if (backLabel) params.set("backLabel", backLabel);

    return `price.html?${params.toString()}`;
  }

  function calculate(fuel, grossPrice) {
    const config = fuelConfig(fuel);
    const gross = toNumber(grossPrice);
    if (gross === null) return null;

    const vat = gross - gross / (1 + VAT_RATE);
    const energyTax = config.energyTaxPerLiter;
    const co2 = (config.co2KgPerLiter * CO2_PRICE_MAX_EUR_PER_TON) / 1000;
    const ebv = ebvPerLiter(config);
    const regulated = energyTax + co2 + ebv + vat;
    const market = gross - regulated;
    const co2GrossFloor = (config.co2KgPerLiter * CO2_PRICE_FLOOR_EUR_PER_TON * (1 + VAT_RATE)) / 1000;
    const co2GrossCeiling = (config.co2KgPerLiter * CO2_PRICE_MAX_EUR_PER_TON * (1 + VAT_RATE)) / 1000;

    const components = [
      {
        key: "market",
        label: "Produkt, Rohöl, Raffinerie, Transport, Vertrieb & Marge",
        detail:
          "Restposten vor Steuern und Abgaben. Echter Gewinn ist darin nicht separat beobachtbar.",
        value: market,
      },
      {
        key: "energy-tax",
        label: "Energiesteuer",
        detail: "Fixer Steuersatz je Liter nach Energiesteuergesetz.",
        value: energyTax,
      },
      {
        key: "co2",
        label: `CO2-Abgabe 2026 bei ${CO2_PRICE_MAX_EUR_PER_TON} €/t`,
        detail: "Mit dem Höchstwert des aktuellen gesetzlichen Preiskorridors gerechnet.",
        value: co2,
      },
      {
        key: "ebv",
        label: "Erdölbevorratung",
        detail: "Beitrag an den EBV auf Literbasis umgelegt.",
        value: ebv,
      },
      {
        key: "vat",
        label: "Umsatzsteuer",
        detail: "19 % auf den gesamten Vorsteuerpreis, also auch auf andere Abgaben.",
        value: vat,
      },
    ].map((component) => ({
      ...component,
      share: gross > 0 ? (component.value / gross) * 100 : 0,
    }));

    return {
      fuel: config,
      grossPrice: gross,
      vat,
      regulated,
      market,
      components,
      co2PriceMaxEurPerTon: CO2_PRICE_MAX_EUR_PER_TON,
      co2PriceFloorEurPerTon: CO2_PRICE_FLOOR_EUR_PER_TON,
      co2GrossFloor,
      co2GrossCeiling,
      co2GrossSpread: co2GrossCeiling - co2GrossFloor,
      stateShare: gross > 0 ? (regulated / gross) * 100 : 0,
      marketShare: gross > 0 ? (market / gross) * 100 : 0,
    };
  }

  window.TankzeitFuelBreakdown = {
    VAT_RATE,
    CO2_PRICE_MAX_EUR_PER_TON,
    CO2_PRICE_FLOOR_EUR_PER_TON,
    toNumber,
    fuelConfig,
    formatEuroPerLiter,
    formatCentPerLiter,
    formatPercent,
    buildBreakdownUrl,
    calculate,
  };
})();
