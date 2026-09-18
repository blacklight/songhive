/*
 * Songhive embed script.
 *
 * Renders ``<div class="songhive-embed" data-type="..." data-id="...">``
 * placeholders as Songhive embed pages inside an iframe, then keeps the
 * iframe height in sync with the embedded content via postMessage.
 *
 * Usage:
 *   <div class="songhive-embed" data-type="track" data-id="<id>"></div>
 *   <script async src="https://<instance>/embed.js" charset="utf-8"></script>
 */
(function () {
  "use strict";

  var VALID_TYPES = {
    track: true,
    album: true,
    artist: true,
    playlist: true,
    library: true,
  };

  // Keep in sync with embedIframeHeight() in src/utils/embed.ts.
  var DEFAULT_HEIGHTS = { track: 152 };
  var DEFAULT_COLLECTION_HEIGHT = 420;

  var scriptEl = document.currentScript;
  if (!scriptEl) {
    var scripts = document.getElementsByTagName("script");
    for (var i = scripts.length - 1; i >= 0; i--) {
      if (/\/embed\.js(?:[?#]|$)/.test(scripts[i].src || "")) {
        scriptEl = scripts[i];
        break;
      }
    }
  }

  var origin = "";
  try {
    origin = scriptEl ? new URL(scriptEl.src, window.location.href).origin : "";
  } catch {
    origin = "";
  }
  if (!origin) return;

  var rendered = [];

  function iframeHeight(type) {
    return DEFAULT_HEIGHTS[type] || DEFAULT_COLLECTION_HEIGHT;
  }

  function renderPlaceholder(el) {
    if (el.getAttribute("data-songhive-rendered")) return;
    var type = el.getAttribute("data-type");
    var id = el.getAttribute("data-id");
    if (!type || !id || !VALID_TYPES[type]) return;
    el.setAttribute("data-songhive-rendered", "1");

    var iframe = document.createElement("iframe");
    iframe.src =
      origin + "/embed/" + encodeURIComponent(type) + "/" + encodeURIComponent(id);
    iframe.width = "100%";
    iframe.height = String(iframeHeight(type));
    iframe.setAttribute("frameborder", "0");
    iframe.setAttribute("loading", "lazy");
    iframe.setAttribute("allow", "encrypted-media; autoplay");
    iframe.setAttribute("scrolling", "no");
    iframe.style.width = "100%";
    iframe.style.border = "0";
    iframe.style.overflow = "hidden";
    iframe.style.display = "block";
    el.appendChild(iframe);
    rendered.push(iframe);
  }

  function renderAll(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll(
      ".songhive-embed, [data-songhive-embed]",
    );
    for (var i = 0; i < nodes.length; i++) {
      renderPlaceholder(nodes[i]);
    }
  }

  window.addEventListener("message", function (event) {
    if (event.origin !== origin) return;
    var data = event.data;
    if (
      !data ||
      data.source !== "songhive-embed" ||
      data.type !== "resize" ||
      typeof data.height !== "number"
    ) {
      return;
    }
    for (var i = 0; i < rendered.length; i++) {
      if (rendered[i].contentWindow === event.source) {
        rendered[i].style.height = Math.max(0, data.height) + "px";
        rendered[i].height = String(Math.max(0, data.height));
      }
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      renderAll();
    });
  } else {
    renderAll();
  }
})();
