(function () {
  const STATIONS_URL = "data/stations.json";
  let stationCatalogPromise = null;

  function toFiniteNumber(value) {
    const numericValue = Number(value);
    return Number.isFinite(numericValue) ? numericValue : null;
  }

  function haversineDistanceKm(lat1, lng1, lat2, lng2) {
    const earthRadiusKm = 6371;
    const toRadians = (value) => (value * Math.PI) / 180;
    const dLat = toRadians(lat2 - lat1);
    const dLng = toRadians(lng2 - lng1);
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRadians(lat1)) *
        Math.cos(toRadians(lat2)) *
        Math.sin(dLng / 2) ** 2;
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return earthRadiusKm * c;
  }

  async function loadCatalog() {
    if (!stationCatalogPromise) {
      stationCatalogPromise = fetch(STATIONS_URL).then((response) => {
        if (!response.ok) {
          throw new Error(`Request failed: ${response.status}`);
        }
        return response.json();
      });
    }
    return stationCatalogPromise;
  }

  function findNearbyStations(stations, lat, lng, options = {}) {
    const limit = Number.isFinite(options.limit) ? options.limit : 10;
    const radiusKm = Number.isFinite(options.radiusKm) ? options.radiusKm : 10;

    return (stations || [])
      .map((station) => {
        const stationLat = toFiniteNumber(station.latitude);
        const stationLng = toFiniteNumber(station.longitude);
        if (!Number.isFinite(stationLat) || !Number.isFinite(stationLng)) {
          return null;
        }

        return {
          id: station.uuid,
          name: station.name || station.brand || "Tankstelle",
          brand: station.brand || "",
          lat: stationLat,
          lng: stationLng,
          dist: haversineDistanceKm(lat, lng, stationLat, stationLng),
        };
      })
      .filter(Boolean)
      .filter((station) => station.dist <= radiusKm)
      .sort((left, right) => {
        if (left.dist !== right.dist) return left.dist - right.dist;
        return `${left.name} ${left.brand}`.localeCompare(
          `${right.name} ${right.brand}`,
          "de",
          { sensitivity: "base" },
        );
      })
      .slice(0, limit);
  }

  window.TankzeitStationCatalog = {
    findNearbyStations,
    loadCatalog,
  };
})();
