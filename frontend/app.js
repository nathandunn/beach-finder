/* Beach Finder frontend -- plain JS, no build step, no dependencies.
 *
 * Flow: a remembered place (localStorage) searches straight away; otherwise
 * ask for geolocation on load -> fetch /api/beaches -> render a ranked list.
 * v0.5 adds search by place name (Open-Meteo geocoder, no key) with a
 * "remember this place" option, so nobody has to answer the location prompt
 * twice. Geolocation denial or failure falls back to the place search and a
 * manual lat/lon form (plus example-city shortcuts), never a dead end.
 *
 * Card layout/interaction (click-to-expand, score chips, weather-item
 * rows, hourly-forecast list) and the sort/max-distance control bar are
 * ported from the Oregon Beach App's UX (apps/beach/frontend) -- behavior
 * borrowed, not code; units and internal state stay this app's own
 * (km, not miles; client-side sort/filter over an already-fetched list,
 * not React state).
 *
 * SPEC v0.4 adds water-type filter chips (Ocean/Lake/River, multi-select)
 * next to the existing sort/max-distance controls, a badge on each card,
 * and per-chip counts -- all client-side over the `water_type` field the
 * backend attaches to every beach. v0.6: Ocean only by default, an Other
 * chip for beaches the map data couldn't settle, a freshness line (the
 * server keeps each place's answer for 30 minutes), and a quiet re-fetch
 * when the water types were still being checked when the answer came.
 */
(function () {
  "use strict";

  var API_BASE = "/api";
  // Place-name search: Open-Meteo's free geocoder (no key, CORS-enabled).
  var GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search";
  var SAVED_PLACE_KEY = "beachFinder.savedPlace";

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
    placeForm: document.getElementById("place-form"),
    placeInput: document.getElementById("place-input"),
    placeResults: document.getElementById("place-results"),
    placeMessage: document.getElementById("place-message"),
    rememberPlace: document.getElementById("remember-place"),
    savedPlace: document.getElementById("saved-place"),
    savedPlaceLabel: document.getElementById("saved-place-label"),
    savedPlaceUse: document.getElementById("saved-place-use"),
    savedPlaceForget: document.getElementById("saved-place-forget"),
    unitsToggle: document.getElementById("units-toggle"),
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

    chipOcean: document.getElementById("chip-ocean"),
    chipLake: document.getElementById("chip-lake"),
    chipRiver: document.getElementById("chip-river"),
    chipOther: document.getElementById("chip-other"),
    freshnessNote: document.getElementById("freshness-note"),
    filterNote: document.getElementById("filter-note"),
  };

  // Water-type filter chips (SPEC v0.4): label + emoji, shared between the
  // chip button text and the card badge so both always agree.
  var WATER_TYPE_LABELS = {
    ocean: "🌊 Ocean",
    lake: "🏞️ Lake",
    river: "🏕️ River",
    other: "❓ Other",
  };

  var loadingTimer = null;
  var lastCoords = null;
  var lastPlaceLabel = null;  // the name the current results were searched from, if any
  // Distances in miles for searches in the US or UK (by the geocoder's country code, or a
  // bounding-box guess for raw coordinates); a toggle in the control bar overrides it.
  var useMiles = false;
  var KM_TO_MI = 0.621371;

  // Sort/filter state -- applied client-side over the last fetched
  // response, no re-fetch needed (mirrors Oregon's App.tsx useEffect
  // filter/sort pipeline, just without React state).
  var currentData = null;
  var sortBy = "distance"; // "distance" | "arrival" | "north" | "south" | "west" | "east"
  var maxDistanceKm = null;

  // Water-type chip state (SPEC v0.4) -- multi-select. v0.6: ocean only by
  // default ("Other" = beaches whose water the map data couldn't settle).
  // Reset for every new search (a fresh location shouldn't inherit a
  // filter the user set up for a different area).
  var DEFAULT_WATER_FILTERS = { ocean: true, lake: false, river: false, other: false };
  var waterFilters = Object.assign({}, DEFAULT_WATER_FILTERS);
  var refetchTimer = null;

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
      { at: 40000, text: "Still working — a place searched for the first time can take up to a minute while the map servers answer…" },
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
    if (useMiles) {
      var mi = km * KM_TO_MI;
      return mi < 0.1 ? Math.round(mi * 5280) + " ft" : mi.toFixed(1) + " mi";
    }
    return km < 1 ? Math.round(km * 1000) + " m" : km.toFixed(1) + " km";
  }

  function formatRoundDistance(km) {
    return useMiles ? Math.round(km * KM_TO_MI) + " mi" : Math.round(km) + " km";
  }

  function inUsOrUk(lat, lon) {
    var us48 = lat >= 24 && lat <= 50 && lon >= -125 && lon <= -66;
    var alaska = lat >= 51 && lat <= 72 && lon >= -170 && lon <= -129;
    var hawaii = lat >= 18 && lat <= 23 && lon >= -161 && lon <= -154;
    var uk = lat >= 49.8 && lat <= 61 && lon >= -8.7 && lon <= 2;
    return us48 || alaska || hawaii || uk;
  }

  function chooseUnits(lat, lon, countryCode) {
    if (countryCode) useMiles = countryCode === "US" || countryCode === "GB";
    else useMiles = inUsOrUk(lat, lon);
    if (els.unitsToggle) els.unitsToggle.textContent = useMiles ? "Show km" : "Show miles";
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

  function waterBucket(beach) {
    var type = beach.water_type;
    return type === "ocean" || type === "lake" || type === "river" ? type : "other";
  }

  // Each beach shows only when its own chip is active; "unknown" beaches
  // live under the Other chip rather than vanishing or sneaking in.
  function passesWaterFilter(beach) {
    return !!waterFilters[waterBucket(beach)];
  }

  function waterBadgeHtml(beach) {
    var type = beach.water_type;
    if (type !== "ocean" && type !== "lake" && type !== "river") return "";
    return '<span class="water-badge water-badge-' + type + '">' + WATER_TYPE_LABELS[type] + "</span>";
  }

  // Chip counts reflect the beaches currently passing the max-distance
  // filter (but before the water-type filter itself is applied) -- so
  // toggling a chip always shows "how many of what's in range right now"
  // rather than a static count from the very first fetch.
  function updateWaterChipCounts(distanceFilteredBeaches) {
    var counts = { ocean: 0, lake: 0, river: 0, other: 0 };
    distanceFilteredBeaches.forEach(function (b) {
      counts[waterBucket(b)]++;
    });
    els.chipOcean.textContent = WATER_TYPE_LABELS.ocean + " (" + counts.ocean + ")";
    els.chipLake.textContent = WATER_TYPE_LABELS.lake + " (" + counts.lake + ")";
    els.chipRiver.textContent = WATER_TYPE_LABELS.river + " (" + counts.river + ")";
    els.chipOther.textContent = WATER_TYPE_LABELS.other + " (" + counts.other + ")";
    return counts;
  }

  function chipElements() {
    return { ocean: els.chipOcean, lake: els.chipLake, river: els.chipRiver, other: els.chipOther };
  }

  function resetWaterFilters() {
    waterFilters = Object.assign({}, DEFAULT_WATER_FILTERS);
    var chips = chipElements();
    Object.keys(chips).forEach(function (type) {
      chips[type].classList.toggle("active", !!waterFilters[type]);
    });
  }

  function showAllWaterTypes() {
    waterFilters = { ocean: true, lake: true, river: true, other: true };
    var chips = chipElements();
    Object.keys(chips).forEach(function (type) {
      chips[type].classList.add("active");
    });
    applyFiltersAndRender();
  }

  // When the water filter hides everything that is in range, say so and
  // offer the lot -- an empty list with "25 found" above it reads as broken.
  function updateFilterNote(counts, shown, inRange) {
    if (shown > 0 || inRange === 0) {
      els.filterNote.classList.add("hidden");
      els.filterNote.innerHTML = "";
      return;
    }
    var text;
    if (currentData && currentData.water_types_pending) {
      text = "Still working out which of these " + inRange + " are ocean beaches — the list will fill in shortly.";
    } else if (counts.ocean === 0 && waterFilters.ocean && !waterFilters.lake && !waterFilters.river && !waterFilters.other) {
      text = "None of the " + inRange + " beaches in range is on the ocean.";
    } else {
      text = "The water-type filter hides all " + inRange + " beaches in range.";
    }
    els.filterNote.innerHTML = escapeHtml(text) + ' <button type="button" class="btn btn-link" id="show-all-water">Show all ' + inRange + "</button>";
    els.filterNote.classList.remove("hidden");
    document.getElementById("show-all-water").addEventListener("click", showAllWaterTypes);
  }

  function formatAge(seconds) {
    if (seconds < 60) return "just now";
    var m = Math.round(seconds / 60);
    return m + (m === 1 ? " minute ago" : " minutes ago");
  }

  // v0.6: the server keeps each place's answer for 30 minutes and only
  // fetches again after that, so say how old what you're looking at is.
  function updateFreshnessNote(data) {
    if (!data || !data.fetched_at) {
      els.freshnessNote.textContent = "";
      return;
    }
    var mins = Math.round((data.stale_after_seconds || 1800) / 60);
    var text = "Conditions fetched " + formatAge(data.age_seconds || 0) + " · refreshed after " + mins + " minutes";
    if (data.water_types_pending) text += " · water types still being checked…";
    els.freshnessNote.textContent = text;
  }

  // The answer came back before the map servers had settled ocean vs lake
  // vs river. The server patches its stored copy when they do, so ask
  // again in a little while (served from the store, so it costs nothing).
  function scheduleWaterTypeRefetch(data) {
    clearTimeout(refetchTimer);
    if (!data || !data.water_types_pending || !lastCoords) return;
    var coords = lastCoords;
    refetchTimer = setTimeout(function () {
      if (!currentData || lastCoords !== coords) return;
      var url = API_BASE + "/beaches?lat=" + encodeURIComponent(coords.lat) + "&lon=" + encodeURIComponent(coords.lon);
      fetch(url)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (fresh) {
          if (!fresh || !currentData || lastCoords !== coords) return;
          currentData = fresh;
          applyFiltersAndRender();
          updateFreshnessNote(fresh);
          if (fresh.water_types_pending) scheduleWaterTypeRefetch(fresh);
        })
        .catch(function () {});
    }, 15000);
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
      (beach.city ? '<span class="beach-city">, ' + escapeHtml(beach.city) + "</span>" : "") +
      waterBadgeHtml(beach);

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

    // Map to the location — the only real identity an unnamed beach has.
    var mapBox = document.createElement("div");
    mapBox.className = "beach-map";
    var mapLinks = document.createElement("div");
    mapLinks.className = "map-links";
    var gmaps = "https://www.google.com/maps/dir/?api=1&destination=" + beach.lat + "," + beach.lon;
    var osm = "https://www.openstreetmap.org/?mlat=" + beach.lat + "&mlon=" + beach.lon + "#map=15/" + beach.lat + "/" + beach.lon;
    mapLinks.innerHTML =
      '<a href="' + gmaps + '" target="_blank" rel="noopener">\uD83E\uDDED Directions</a>' +
      '<a href="' + osm + '" target="_blank" rel="noopener">View on OpenStreetMap</a>';
    details.appendChild(mapBox);
    details.appendChild(mapLinks);

    // Map interactions must not collapse the card.
    mapBox.addEventListener("click", function (e) { e.stopPropagation(); });
    mapLinks.addEventListener("click", function (e) { e.stopPropagation(); });

    li.appendChild(header);
    li.appendChild(details);

    var mapInited = false;
    li.addEventListener("click", function () {
      var isCollapsed = details.classList.toggle("collapsed");
      expandHint.textContent = isCollapsed ? "Click to see weather details ↓" : "Click to collapse ↑";
      // Lazy-init Leaflet only on first expand: no tile requests for cards never opened,
      // and Leaflet mis-sizes inside a hidden container, so init after it is visible.
      if (!isCollapsed && !mapInited && typeof L !== "undefined") {
        mapInited = true;
        var map = L.map(mapBox, { scrollWheelZoom: false, attributionControl: true })
          .setView([beach.lat, beach.lon], 14);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 18,
          attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        }).addTo(map);
        L.marker([beach.lat, beach.lon]).addTo(map)
          .bindPopup(escapeHtml(beach.name) + "<br>" + formatDistance(beach.distance_km) + " away");
        setTimeout(function () { map.invalidateSize(); }, 0);
      }
    });

    return li;
  }

  function renderBeachList(beaches) {
    els.beachList.innerHTML = "";
    beaches.forEach(function (beach, index) {
      els.beachList.appendChild(buildBeachCard(beach, index));
    });

    els.resultsSummary.textContent =
      (lastPlaceLabel ? "Near " + lastPlaceLabel + " · " : "") +
      beaches.length +
      " of " +
      currentData.beaches.length +
      (currentData.beaches.length === 1 ? " beach" : " beaches") +
      " shown · searched within " +
      formatRoundDistance(currentData.searched_radius_km);
  }

  function applyFiltersAndRender() {
    if (!currentData) return;
    var cap = maxDistanceKm;
    var withinDistance = currentData.beaches.filter(function (b) {
      return cap == null || b.distance_km <= cap;
    });

    var counts = updateWaterChipCounts(withinDistance);

    var filtered = withinDistance.filter(passesWaterFilter);
    updateFilterNote(counts, filtered.length, withinDistance.length);
    var sorted = filtered.slice().sort(function (a, b) {
      switch (sortBy) {
        case "arrival": return b.scores.arrival - a.scores.arrival;
        case "north": return b.lat - a.lat;
        case "south": return a.lat - b.lat;
        case "west": return a.lon - b.lon;
        case "east": return b.lon - a.lon;
        default: return a.distance_km - b.distance_km;
      }
    });
    renderBeachList(sorted);
  }

  function updateMaxDistanceValueLabel(value, maxKm) {
    els.maxDistanceValue.textContent = value >= maxKm ? "All" : formatRoundDistance(value);
  }

  function updateDistanceLabels(maxKm) {
    els.distanceLabelMin.textContent = formatRoundDistance(1);
    els.distanceLabelMid.textContent = formatRoundDistance(maxKm / 2);
    els.distanceLabelMax.textContent = formatRoundDistance(maxKm) + " (All)";
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
    resetWaterFilters();
    applyFiltersAndRender();
    updateFreshnessNote(data);
    scheduleWaterTypeRefetch(data);
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

  function search(lat, lon, label, countryCode) {
    lastCoords = { lat: lat, lon: lon };
    lastPlaceLabel = label || null;
    chooseUnits(lat, lon, countryCode || null);
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

    // Show a loading state immediately so the user knows something is happening
    // while the browser permission prompt is pending or geolocation is resolving.
    els.loadingMessage.textContent = "Detecting your location…";
    showOnly(els.loadingPanel);

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
            message = "The browser didn't come back with a position (desktops without GPS often don't). Search a place above, enter coordinates below, or pick a city.";
            break;
          default:
            message = "Couldn't determine your location. Search a place above, enter coordinates below, or pick a city.";
        }
        // Return to location panel before showing the fallback message
        showOnly(els.locationPanel);
        showManualFallback(message);
      },
      { timeout: 20000, maximumAge: 10 * 60 * 1000, enableHighAccuracy: false }
    );
  }

  // --- place search + remembered place (v0.5) ---

  function loadSavedPlace() {
    try {
      var raw = window.localStorage.getItem(SAVED_PLACE_KEY);
      if (!raw) return null;
      var p = JSON.parse(raw);
      if (typeof p.lat !== "number" || typeof p.lon !== "number" || !p.label) return null;
      return p;
    } catch (e) {
      return null;
    }
  }

  function savePlace(place) {
    try {
      window.localStorage.setItem(SAVED_PLACE_KEY, JSON.stringify(place));
    } catch (e) {
      /* private mode or storage blocked: the search still works, it just isn't remembered */
    }
    renderSavedPlace();
  }

  function forgetPlace() {
    try {
      window.localStorage.removeItem(SAVED_PLACE_KEY);
    } catch (e) {}
    renderSavedPlace();
  }

  function renderSavedPlace() {
    var p = loadSavedPlace();
    els.savedPlace.classList.toggle("hidden", !p);
    if (p) els.savedPlaceLabel.textContent = p.label;
  }

  function placeLabel(r) {
    var parts = [r.name];
    if (r.admin1 && r.admin1 !== r.name) parts.push(r.admin1);
    if (r.country) parts.push(r.country);
    return parts.join(", ");
  }

  function usePlace(place) {
    if (els.rememberPlace.checked) savePlace(place);
    search(place.lat, place.lon, place.label, place.cc || null);
  }

  function geocode(query) {
    els.placeResults.innerHTML = "";
    els.placeResults.classList.add("hidden");
    els.placeMessage.textContent = "Looking up \u201c" + query + "\u201d\u2026";
    els.placeMessage.classList.remove("hidden");
    var url = GEOCODE_URL + "?name=" + encodeURIComponent(query) + "&count=6&language=en&format=json";
    return fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error("geocoder answered " + r.status);
        return r.json();
      })
      .then(function (data) {
        var results = (data && data.results) || [];
        if (results.length === 0) {
          els.placeMessage.textContent = "No place called \u201c" + query + "\u201d found. Try adding a state or country.";
          return;
        }
        // one clear answer: go straight there; several: let them choose
        var places = results.map(function (r) {
          return { label: placeLabel(r), lat: r.latitude, lon: r.longitude, cc: r.country_code || null };
        });
        if (places.length === 1) {
          els.placeMessage.classList.add("hidden");
          usePlace(places[0]);
          return;
        }
        els.placeMessage.textContent = "Which one?";
        places.forEach(function (p) {
          var b = document.createElement("button");
          b.type = "button";
          b.textContent = p.label;
          b.addEventListener("click", function () {
            els.placeMessage.classList.add("hidden");
            els.placeResults.classList.add("hidden");
            usePlace(p);
          });
          els.placeResults.appendChild(b);
        });
        els.placeResults.classList.remove("hidden");
      })
      .catch(function (err) {
        els.placeMessage.textContent = "Couldn\u2019t look that up (" + err.message + "). Try again, or use coordinates below.";
        els.manualFallback.classList.remove("hidden");
      });
  }

  function buildExampleCityButtons() {
    EXAMPLE_CITIES.forEach(function (city) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = city.label;
      btn.addEventListener("click", function () {
        usePlace({ label: city.label, lat: city.lat, lon: city.lon });
      });
      els.exampleCities.appendChild(btn);
    });
  }

  function resetToLocationPanel() {
    showOnly(els.locationPanel);
  }

  // --- wire up events ---

  els.useMyLocationBtn.addEventListener("click", requestGeolocation);

  els.placeForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = els.placeInput.value.trim();
    if (q.length < 2) return;
    geocode(q);
  });
  els.savedPlaceUse.addEventListener("click", function () {
    var p = loadSavedPlace();
    if (p) search(p.lat, p.lon, p.label, p.cc || null);
  });
  els.savedPlaceForget.addEventListener("click", forgetPlace);
  els.unitsToggle.addEventListener("click", function () {
    useMiles = !useMiles;
    els.unitsToggle.textContent = useMiles ? "Show km" : "Show miles";
    if (currentData) {
      updateDistanceLabels(Number(els.maxDistanceSlider.max));
      updateMaxDistanceValueLabel(Number(els.maxDistanceSlider.value), Number(els.maxDistanceSlider.max));
      applyFiltersAndRender();
    }
  });

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

  [
    { el: els.chipOcean, type: "ocean" },
    { el: els.chipLake, type: "lake" },
    { el: els.chipRiver, type: "river" },
    { el: els.chipOther, type: "other" },
  ].forEach(function (chip) {
    chip.el.addEventListener("click", function () {
      waterFilters[chip.type] = !waterFilters[chip.type];
      chip.el.classList.toggle("active", waterFilters[chip.type]);
      applyFiltersAndRender();
    });
  });

  buildExampleCityButtons();
  renderSavedPlace();

  // A remembered place searches straight away. Otherwise (v0.6) show the
  // place search at once rather than sitting on a browser position request
  // that, on a desktop without GPS, may take 10 s to fail -- "Use my
  // location" is right there for anyone who wants it.
  var saved = loadSavedPlace();
  if (saved) {
    els.placeInput.value = saved.label;
    search(saved.lat, saved.lon, saved.label, saved.cc || null);
  } else {
    showOnly(els.locationPanel);
    showManualFallback("Type a town above and tap Find (tick the box to remember it), use your location, or pick a city.");
    try { els.placeInput.focus(); } catch (e) {}
  }
})();
