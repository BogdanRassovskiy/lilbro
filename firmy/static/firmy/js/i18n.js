(function () {
  function getLang() {
    try {
      return localStorage.getItem("ui_lang") || "ru";
    } catch (e) {
      return "ru";
    }
  }

  function setLang(lang) {
    try {
      localStorage.setItem("ui_lang", lang);
    } catch (e) {}
  }

  function applyDict(dict) {
    var nodes = document.querySelectorAll("[data-i18n]");
    nodes.forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      if (!key) return;
      var val = dict[key];
      if (typeof val === "string") el.textContent = val;
    });

    var placeholders = document.querySelectorAll("[data-i18n-placeholder]");
    placeholders.forEach(function (el) {
      var key = el.getAttribute("data-i18n-placeholder");
      if (!key) return;
      var val = dict[key];
      if (typeof val === "string") el.setAttribute("placeholder", val);
    });

    var titles = document.querySelectorAll("[data-i18n-title]");
    titles.forEach(function (el) {
      var key = el.getAttribute("data-i18n-title");
      if (!key) return;
      var val = dict[key];
      if (typeof val === "string") el.setAttribute("title", val);
    });

    var ariaLabels = document.querySelectorAll("[data-i18n-aria]");
    ariaLabels.forEach(function (el) {
      var key = el.getAttribute("data-i18n-aria");
      if (!key) return;
      var val = dict[key];
      if (typeof val === "string") el.setAttribute("aria-label", val);
    });
  }

  function fetchDict(lang) {
    return fetch("/static/firmy/i18n/" + lang + ".json", {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }).then(function (r) {
      if (!r.ok) throw new Error("i18n_load_failed");
      return r.json();
    });
  }

  function init() {
    var lang = getLang();
    var switchers = document.querySelectorAll("[data-lang-switch]");
    switchers.forEach(function (el) {
      if ("value" in el) el.value = lang;
      el.addEventListener("change", function () {
        var next = (el.value || "ru").toLowerCase();
        if (next !== "ru" && next !== "cs") next = "ru";
        setLang(next);
        window.location.reload();
      });
    });

    fetchDict(lang)
      .then(function (dict) {
        window.I18N = {
          lang: lang,
          dict: dict || {},
          t: function (key, fallback) {
            var v = (dict || {})[key];
            return typeof v === "string" ? v : (fallback || key);
          },
        };
        document.documentElement.setAttribute("lang", lang);
        applyDict(dict || {});
      })
      .catch(function () {
        window.I18N = {
          lang: "ru",
          dict: {},
          t: function (key, fallback) {
            return fallback || key;
          },
        };
      });
  }

  init();
})();

