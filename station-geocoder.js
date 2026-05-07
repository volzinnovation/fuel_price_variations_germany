(function () {
  function normalizeWhitespace(value) {
    return String(value ?? "").trim().replace(/\s+/g, " ");
  }

  function normalizeSearchText(value) {
    return normalizeWhitespace(value)
      .toLowerCase()
      .replace(/\u00e4/g, "ae")
      .replace(/\u00f6/g, "oe")
      .replace(/\u00fc/g, "ue")
      .replace(/\u00df/g, "ss")
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  }

  function toFiniteNumber(value) {
    const numericValue = Number(value);
    return Number.isFinite(numericValue) ? numericValue : null;
  }

  function stationLatLng(station) {
    const lat = toFiniteNumber(station.latitude);
    const lng = toFiniteNumber(station.longitude);
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
      return null;
    }
    return { lat, lng };
  }

  function stationAddressText(station) {
    return normalizeWhitespace(
      [
        station.street,
        station.house_number,
        station.post_code,
        station.city,
      ].filter(Boolean).join(" "),
    );
  }

  function findPlaceMatches(stations, placeInput) {
    const place = normalizeSearchText(placeInput);
    const postCode = normalizeWhitespace(placeInput).match(/\b\d{5}\b/)?.[0];
    const cityInput = normalizeSearchText(
      normalizeWhitespace(placeInput).replace(/\b\d{5}\b/, ""),
    );

    return (stations || []).filter((station) => {
      const stationPostCode = normalizeWhitespace(station.post_code);
      const stationCity = normalizeSearchText(station.city);

      if (postCode && stationPostCode === postCode) {
        return !cityInput || stationCity === cityInput;
      }

      return stationCity === place;
    });
  }

  function findAddressMatches(stations, addressInput) {
    const address = normalizeSearchText(addressInput);
    return (stations || []).filter((station) => {
      const streetLine = normalizeSearchText(
        [station.street, station.house_number].filter(Boolean).join(" "),
      );
      if (!streetLine) {
        return false;
      }
      const fullAddress = normalizeSearchText(stationAddressText(station));
      return (
        streetLine === address ||
        fullAddress.includes(address) ||
        address.includes(streetLine)
      );
    });
  }

  function averageCoordinates(stations) {
    const coordinates = (stations || []).map(stationLatLng).filter(Boolean);
    if (!coordinates.length) {
      return null;
    }
    return {
      lat:
        coordinates.reduce((sum, coordinate) => sum + coordinate.lat, 0) /
        coordinates.length,
      lng:
        coordinates.reduce((sum, coordinate) => sum + coordinate.lng, 0) /
        coordinates.length,
    };
  }

  async function resolveLocation(placeInput, addressInput = "") {
    const place = normalizeWhitespace(placeInput);
    const address = normalizeWhitespace(addressInput);
    const catalog = await TankzeitStationCatalog.loadCatalog();
    const placeMatches = findPlaceMatches(catalog, place);

    if (!placeMatches.length) {
      return { ok: false, field: "place" };
    }

    const matches = address
      ? findAddressMatches(placeMatches, address)
      : placeMatches;
    if (!matches.length) {
      return { ok: false, field: "address" };
    }

    const center = averageCoordinates(matches);
    if (!center) {
      return { ok: false, field: address ? "address" : "place" };
    }

    return {
      ok: true,
      lat: center.lat,
      lng: center.lng,
      matchCount: matches.length,
      precision: address ? "address" : "place",
    };
  }

  window.TankzeitStationGeocoder = {
    resolveLocation,
  };
})();
