/* Beach Finder frontend -- plain JS, no build step, no dependencies.
 *
 * Flow: ask for geolocation on load -> fetch /api/beaches -> render a
 * ranked list. Geolocation denial or failure falls back to a manual
 * lat/lon form (plus example-city shortcuts), never a dead end.
 *
 * Card layout/interaction (click-to-expand, score chips, weather-item
 * rows, hourly-forecast list) and the sort/max-distance control bar are
 * ported from the Oregon Beach App's UX (apps/beach/frontend) -- behavior
 * borrowed, not code; units and internal state stay this app's own
 * (km, not miles; client-side sort/filter over an already-fetched list,
 * not React state).
 */
(function () {
  "use strict";

  var API_BASE = "/api";

  var EXAMPLE_CITIES = [
    { label: "Newport, OR", lat: 44.6368, lon: -124.0535 },
    { label: "Santa Monica, CA", lat: 34.0195, lon: -118.4912 },
    { label: "Miami Beach, FL", lat: 25.7907, lon: -80.13 },
    { label: "Honolulu, HI", lat: 21.2793, lon: -157.8292 },
    { label: "Bondi, Sydney AU", lat: -33.8908, lon: 151.2743 },
    { label: "Denver, CO (inland)", lat: 39.7392, lon: -104.9903 },
  ];

  // 8-point compass abbreviation -> spelled-out word, same table shape as
  // the Oregon app's BeachCard getWindDirection (there it translated NWS's
  // letter codes; here it translates the backend's degrees-derived
  // compass point).
  var WIND_DIRECTION_WORDS = {
    N: "North",
    NE: "Northeast",
    E: "East",
    SE: "Southeast",
    S: "South",
    SW: "Southwest",
    W: "West",
    NW: "Northwest",
  };

  var els = {
    locationPanel: document.getElementById("location-panel"),
    manualFallback: document.getElementById("manual-fallback"),
    fallbackMessage: document.getElementById("fallback-message"),
    useMyLocationBtn: document.getElementById("use-my-location"),
    manualForm: document.getElementById("manual-form"),
    manualLat: document.getElementById("manual-lat"),
    manualLon: document.getElementById("manual-lon"),
    exampleCities: document.getElementById("example-cities"),

    loadingPanel: document.getElementById("loading-panel"),
    loadingMessage: document.getElementById("loading-message"),

    errorPanel: document.getElementById("error-panel"),
    errorMessage: document.getElementById("error-message"),
    retryBtn: document.getElementById("retry-btn"),

    emptyPanel: document.getElementById("empty-panel"),
    emptyMessage: document.getElementById("empty-message"),
    emptyRetryBtn: document.getElementById("empty-retry-btn"),

    resultsPanel: document.getElementById("results-panel"),
    resultsSummary: document.getElementById("results-summary"),
    changeLocationBtn: document.getElementById("change-location-btn"),
    beachList: document.getElementById("beach-list"),

    maxDistanceSlider: document.getElementById("max-distance-slider"),
    maxDistanceValue: document.getElementById("max-distance-value"),
    distanceLabelMin: document.getElementById("distance-label-min"),
    distanceLabelMid: document.getElementById("distance-label-mid"),
    distanceLabelMax: document.getElementById("distance-label-max"),
    sortSelect: document.getElementById("sort-select"),
  };

  var loadingTimer = null;
  var lastCoords = null;

  // Sort/filter state -- applied client-side over the last fetched
  // response, no re-fetch needed (mirrors Oregon's App.tsx useEffect
  // filter/sort pipeline, just without React state).
  var currentData = null;
  var sortBy = "distance"; // "distance" | "arrival"
  var maxDistanceKm = null;

  function showOnly(panel) {
    [
      els.locationPanel,
      els.loadingPanel,
      els.errorPanel,
      els.emptyPanel,
      els.resultsPanel,
    ].forEach(function (p) {
      p.classList.toggle("hidden", p !== panel);
    });
  }

  function startLoadingMessages() {
    var stages = [
      { at: 0, text: "Searching for beaches near you…" },
      { at: 5000, text: "Still searching — checking a wider radius…" },
      { at: 12000, text: "Widening the search further — this area may be sparse on beaches…" },
      { at: 22000, text: "Almost there — fetching live weather for every beach found…" },
    ];
    var start = Date.now();
    els.loadingMessage.textContent = stages[0].text;
    clearInterval(loadingTimer);
    loadingTimer = setInterval(function () {
      var elapsed = Date.now() - start;
      var current = stages[0];
      for (var i = 0; i < stages.length; i++) {
        if (elapsed >= stages[i].at) current = stages[i];
      }
      els.loadingMessage.textContent = current.text;
    }, 1000);
  }

  function stopLoadingMessages() {
    clearInterval(loadingTimer);
    loadingTimer = null;
  }

  // Current-conditions score meter: unchanged 4-tier classes already used
  // by the existing meter (spec: color classes should match the meter
  // already present).
  function scoreClass(score) {
    if (score >= 80) return "score-great";
    if (score >= 60) return "score-good";
    if (score >= 40) return "score-fair";
    return "score-poor";
  }

  // Score chips (Arrival / +1h / +3h): Oregon's three-tier thresholds,
  // reusing the same color tokens as the meter above (excellent -> great,
  // good -> fair's warm tone, poor -> poor) so the palette stays coherent.
  function chipScoreClass(score) {
    if (score >= 80) return "chip-excellent";
    if (score >= 60) return "chip-good";
    return "chip-poor";
  }

  function formatDistance(km) {
    return km < 1 ? Math.round(km * 1000) + " m" : km.toFixed(1) + " km";
  }

  // Oregon's formatTime, unchanged shape ("Xh Ym" / "Ym").
  function formatDriveTime(minutes) {
    var m = Math.max(0, Math.round(minutes || 0));
    var hours = Math.floor(m / 60);
    var mins = m % 60;
    if (hours > 0) return hours + "h " + mins + "m";
    return mins + "m";
  }

  function windDirectionWord(abbrev) {
    return WIND_DIRECTION_WORDS[abbrev] || abbrev || "";
  }

  // Open-Meteo's hourly `time` strings are naive ISO ("2026-08-27T07:00",
  // no offset) representing GMT (the backend doesn't set a `timezone`
  // param). Treat them as UTC explicitly, then let the browser render in
  // the viewer's local time -- close enough to "beach local time" for most
  // users and avoids guessing the beach's actual timezone.
  function formatHourlyTime(iso) {
    if (!iso) return "";
    var withZone = /Z$/.test(iso) ? iso : iso + "Z";
    var d = new Date(withZone);
    if (isNaN(d.getTime())) return iso;
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
  }

  function weatherItemHtml(label, value) {
    return '<div class="weather-item"><span>' + label + "</span><span>" + escapeHtml(value) + "</span></div>";
  }

  function hourlyRowHtml(hour) {
    return (
      '<div class="hourly-row">' +
      '<span class="hourly-time">' +
      formatHourlyTime(hour.time) +
      "</span>" +
      "<span>" +
      Math.round(hour.temperature_f) +
      "°F, " +
      Math.round(hour.wind_mph) +
      "mph, " +
      hour.precipitation_mm.toFixed(1) +
      "mm rain, " +
      Math.round(hour.cloud_cover_pct) +
      "% cloud</span>" +
      "</div>"
    );
  }

  function buildScoreChip(label, score) {
    var chip = document.createElement("div");
    chip.className = "score-chip " + chipScoreClass(score);
    chip.innerHTML =
      '<span class="score-chip-label">' + label + '</span><span class="score-chip-value">' + score + "</span>";
    return chip;
  }

  function buildDetailsHtml(beach) {
    var c = beach.conditions;
    var cls = scoreClass(beach.score);

    var weatherRows =
      weatherItemHtml("🌡️ Temperature", Math.round(c.temperature_f) + "°F") +
      weatherItemHtml("💨 Wind", Math.round(c.wind_mph) + " mph " + windDirectionWord(c.wind_compass)) +
      weatherItemHtml("🌧️ Precipitation", c.precipitation_mm.toFixed(1) + " mm") +
      weatherItemHtml("☁️ Cloud Cover", Math.round(c.cloud_cover_pct) + "%") +
      weatherItemHtml("💧 Humidity", Math.round(c.humidity_pct) + "%");

    var hourlyRows =
      beach.hourly_forecast && beach.hourly_forecast.length
        ? beach.hourly_forecast.map(hourlyRowHtml).join("")
        : '<div class="hourly-row"><span>No hourly forecast available</span></div>';

    return (
      '<div class="current-score-row">' +
      '<span class="current-score-label">Current conditions</span>' +
      '<div class="score-meter"><div class="score-meter-fill ' +
      cls +
      '" style="width:' +
      beach.score +
      '%"></div></div>' +
      '<span class="score-value ' +
      cls +
      '">' +
      beach.score +
      "/100</span>" +
      "</div>" +
      '<div class="weather-details">' +
      weatherRows +
      "</div>" +
      '<div class="hourly-forecast">' +
      '<div class="hourly-forecast-title">Next hours</div>' +
      hourlyRows +
      "</div>" +
      '<div class="expand-indicator" style="text-align:center;margin-top:10px;">Click to collapse ↑</div>'
    );
  }

  function buildBeachCard(beach, index) {
    var li = document.createElement("li");
    li.className = "beach-card";

    var header = document.createElement("div");
    header.className = "beach-card-header";

    var info = document.createElement("div");
    info.className = "beach-info";

    var nameLine = document.createElement("div");
    nameLine.className = "beach-name";
    nameLine.innerHTML =
      '<span class="beach-rank">' +
      (index + 1) +
      "</span>" +
      escapeHtml(beach.name) +
      (beach.city ? '<span class="beach-city">, ' + escapeHtml(beach.city) + "</span>" : "");

    var distanceLine = document.createElement("div");
    distanceLine.className = "beach-distance";
    distanceLine.textContent =
      "📍 " + formatDistance(beach.distance_km) + " away • 🚗 " + formatDriveTime(beach.drive_time_minutes);

    var expandHint = document.createElement("div");
    expandHint.className = "expand-indicator";
    expandHint.textContent = "Click to see weather details ↓";

    info.appendChild(nameLine);
    info.appendChild(distanceLine);
    info.appendChild(expandHint);

    var scoresContainer = document.createElement("div");
    scoresContainer.className = "scores-container";
    scoresContainer.appendChild(buildScoreChip("Arrival", beach.scores.arrival));
    scoresContainer.appendChild(buildScoreChip("+1h", beach.scores.plus1h));
    scoresContainer.appendChild(buildScoreChip("+3h", beach.scores.plus3h));

    header.appendChild(info);
    header.appendChild(scoresContainer);

    var details = document.createElement("div");
    details.className = "beach-details collapsed";
    details.innerHTML = buildDetailsHtml(beach);

    li.appendChild(header);
    li.appendChild(details);

    li.addEventListener("click", function () {
      var isCollapsed = details.classList.toggle("collapsed");
      expandHint.textContent = isCollapsed ? "Click to see weather details ↓" : "Click to collapse ↑";
    });

    return li;
  }

  function renderBeachList(beaches) {
    els.beachList.innerHTML = "";
    beaches.forEach(function (beach, index) {
      els.beachList.appendChild(buildBeachCard(beach, index));
    });

    els.resultsSummary.textContent =
      beaches.length +
      " of " +
      currentData.beaches.length +
      (currentData.beaches.length === 1 ? " beach" : " beaches") +
      " shown · searched within " +
      Math.round(currentData.searched_radius_km) +
      " km";
  }

  function applyFiltersAndRender() {
    if (!currentData) return;
    var cap = maxDistanceKm;
    var filtered = currentData.beaches.filter(function (b) {
      return cap == null || b.distance_km <= cap;
    });
    var sorted = filtered.slice().sort(function (a, b) {
      if (sortBy === "arrival") {
        return b.scores.arrival - a.scores.arrival;
      }
      return a.distance_km - b.distance_km;
    });
    renderBeachList(sorted);
  }

  function updateMaxDistanceValueLabel(value, maxKm) {
    els.maxDistanceValue.textContent = value >= maxKm ? "All" : Math.round(value) + " km";
  }

  function updateDistanceLabels(maxKm) {
    els.distanceLabelMin.textContent = "1 km";
    els.distanceLabelMid.textContent = Math.round(maxKm / 2) + " km";
    els.distanceLabelMax.textContent = maxKm + " km (All)";
  }

  function initControlsForData(data) {
    var maxKm = Math.max(1, Math.round(data.searched_radius_km || 1));
    els.maxDistanceSlider.min = "1";
    els.maxDistanceSlider.max = String(maxKm);
    els.maxDistanceSlider.value = String(maxKm);
    maxDistanceKm = maxKm;
    updateDistanceLabels(maxKm);
    updateMaxDistanceValueLabel(maxKm, maxKm);
  }

  function renderBeaches(data) {
    currentData = data;
    initControlsForData(data);
    applyFiltersAndRender();
    showOnly(els.resultsPanel);
  }

  function renderEmpty(data) {
    var ceilingNote = data.ceiling_reached
      ? "We searched all the way out to the 500-mile limit and came up empty. You may be quite far from the coast."
      : "No beaches turned up in this area.";
    els.emptyMessage.textContent = ceilingNote;
    showOnly(els.emptyPanel);
  }

  function renderError(message) {
    els.errorMessage.textContent = message;
    showOnly(els.errorPanel);
  }

  function search(lat, lon) {
    lastCoords = { lat: lat, lon: lon };
    showOnly(els.loadingPanel);
    startLoadingMessages();

    var url = API_BASE + "/beaches?lat=" + encodeURIComponent(lat) + "&lon=" + encodeURIComponent(lon);

    fetch(url)
      .then(function (response) {
        if (!response.ok) {
          return response
            .json()
            .catch(function () {
              return null;
            })
            .then(function (body) {
              var detail = body && body.detail ? JSON.stringify(body.detail) : response.statusText;
              throw new Error("The server couldn't process that location (" + response.status + "): " + detail);
            });
        }
        return response.json();
      })
      .then(function (data) {
        stopLoadingMessages();
        if (!data.beaches || data.beaches.length === 0) {
          renderEmpty(data);
        } else {
          renderBeaches(data);
        }
      })
      .catch(function (err) {
        stopLoadingMessages();
        renderError(
          "Something went wrong reaching the beach search: " + err.message + ". Check your connection and try again."
        );
      });
  }

  function showManualFallback(message) {
    els.fallbackMessage.textContent = message;
    els.manualFallback.classList.remove("hidden");
  }

  function requestGeolocation() {
    if (typeof navigator === "undefined" || !("geolocation" in navigator)) {
      showManualFallback("Your browser doesn't support location lookup. Enter a latitude/longitude below, or pick a city.");
      return;
    }

    navigator.geolocation.getCurrentPosition(
      function (position) {
        search(position.coords.latitude, position.coords.longitude);
      },
      function (error) {
        var message;
        switch (error.code) {
          case error.PERMISSION_DENIED:
            message = "Location access was denied. No problem — enter coordinates below, or pick a city.";
            break;
          case error.TIMEOUT:
            message = "Location lookup timed out. Enter coordinates below, or pick a city.";
            break;
          default:
            message = "Couldn't determine your location. Enter coordinates below, or pick a city.";
        }
        showManualFallback(message);
      },
      { timeout: 10000, maximumAge: 5 * 60 * 1000 }
    );
  }

  function buildExampleCityButtons() {
    EXAMPLE_CITIES.forEach(function (city) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = city.label;
      btn.addEventListener("click", function () {
        search(city.lat, city.lon);
      });
      els.exampleCities.appendChild(btn);
    });
  }

  function resetToLocationPanel() {
    showOnly(els.locationPanel);
  }

  // --- wire up events ---

  els.useMyLocationBtn.addEventListener("click", requestGeolocation);

  els.manualForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var lat = parseFloat(els.manualLat.value);
    var lon = parseFloat(els.manualLon.value);
    if (isNaN(lat) || isNaN(lon)) return;
    search(lat, lon);
  });

  els.retryBtn.addEventListener("click", function () {
    if (lastCoords) {
      search(lastCoords.lat, lastCoords.lon);
    } else {
      resetToLocationPanel();
    }
  });

  els.emptyRetryBtn.addEventListener("click", resetToLocationPanel);
  els.changeLocationBtn.addEventListener("click", resetToLocationPanel);

  els.maxDistanceSlider.addEventListener("input", function () {
    var val = Number(els.maxDistanceSlider.value);
    maxDistanceKm = val;
    updateMaxDistanceValueLabel(val, Number(els.maxDistanceSlider.max));
    applyFiltersAndRender();
  });

  els.sortSelect.addEventListener("change", function () {
    sortBy = els.sortSelect.value;
    applyFiltersAndRender();
  });

  buildExampleCityButtons();

  // Ask for geolocation as soon as the page loads, per spec -- the manual
  // fallback panel stays available underneath in case it's denied.
  requestGeolocation();
})();
