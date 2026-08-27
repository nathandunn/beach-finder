/* Beach Finder frontend -- plain JS, no build step, no dependencies.
 *
 * Flow: ask for geolocation on load -> fetch /api/beaches -> render a
 * ranked list. Geolocation denial or failure falls back to a manual
 * lat/lon form (plus example-city shortcuts), never a dead end.
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
  };

  var loadingTimer = null;
  var lastCoords = null;

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

  function scoreClass(score) {
    if (score >= 80) return "score-great";
    if (score >= 60) return "score-good";
    if (score >= 40) return "score-fair";
    return "score-poor";
  }

  function formatDistance(km) {
    return km < 1 ? Math.round(km * 1000) + " m" : km.toFixed(1) + " km";
  }

  function renderBeaches(data) {
    els.beachList.innerHTML = "";

    data.beaches.forEach(function (beach, index) {
      var li = document.createElement("li");
      li.className = "beach-card";

      var cls = scoreClass(beach.score);

      li.innerHTML =
        '<div class="beach-name"><span class="beach-rank">' +
        (index + 1) +
        "</span>" +
        escapeHtml(beach.name) +
        "</div>" +
        '<div class="beach-distance">' +
        formatDistance(beach.distance_km) +
        " away</div>" +
        '<div class="score-meter-row">' +
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
        '<div class="beach-conditions">' +
        escapeHtml(beach.conditions.summary) +
        "</div>";

      els.beachList.appendChild(li);
    });

    els.resultsSummary.textContent =
      data.count +
      (data.count === 1 ? " beach found" : " beaches found") +
      " within " +
      Math.round(data.searched_radius_km) +
      " km";

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

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str == null ? "" : String(str);
    return div.innerHTML;
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

  buildExampleCityButtons();

  // Ask for geolocation as soon as the page loads, per spec -- the manual
  // fallback panel stays available underneath in case it's denied.
  requestGeolocation();
})();
