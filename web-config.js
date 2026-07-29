(function () {
  const masterRaw =
    "https://raw.githubusercontent.com/volzinnovation/tankzeit.de/master/";

  window.TankzeitWebConfig = Object.freeze({
    adviceUrl: `${masterRaw}data/simple/latest.json`,
    localAdviceUrl: "data/simple/latest.json",
    dataBase: `${masterRaw}data2/`,
    stationsUrl: `${masterRaw}data/stations.json`,
    brentUrl: `${masterRaw}data/brent.json`,
  });
})();
