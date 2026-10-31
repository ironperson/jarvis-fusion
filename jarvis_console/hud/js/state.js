/* 主桥：连接 ws://127.0.0.1:8765，驱动四态、字幕、系统状态与 3D 演示。 */
window.JarvisBridge = (function () {
  "use strict";

  var core = null;
  var socket = null;
  var lastFrame = 0;
  var subtitleEl, engineEl, sysEl, micEl, demoLayer, textInput, demoCanvas;

  function setCssState(state) {
    var skin = (window.JarvisSkins && window.__jarvisSkin) || "jarvis";
    var color = window.JarvisSkins
      ? window.JarvisSkins.mapStateToSkin(skin, state)
      : "#3fa9ff";
    document.documentElement.style.setProperty("--jarvis-state", color);
  }

  function updateSys(sys) {
    if (!sys || !sysEl) return;
    var spans = sysEl.querySelectorAll("span");
    spans.forEach(function (sp) {
      var k = sp.getAttribute("data-k");
      if (k === "cpu") sp.textContent = "CPU " + Math.round(sys.cpu || 0) + "%";
      if (k === "mem") sp.textContent = "MEM " + Math.round(sys.mem || 0) + "%";
      if (k === "net") {
        var up = (sys.net_up || 0) / 1048576, down = (sys.net_down || 0) / 1048576;
        sp.textContent = "NET ↓" + down.toFixed(1) + " ↑" + up.toFixed(1) + " MB/s";
      }
      if (k === "battery") {
        if (sys.battery != null && sys.battery >= 0) {
          sp.textContent = "BAT " + Math.round(sys.battery) + "%" + (sys.on_ac ? "⚡" : "");
          sp.className = (sys.battery < 20 && !sys.on_ac) ? "warn" : "";
        } else {
          sp.textContent = "BAT N/A";
        }
      }
    });
  }

  function showSubtitle(text, isFinal) {
    if (!subtitleEl) return;
    subtitleEl.textContent = text || "";
    subtitleEl.style.opacity = text ? "1" : "0";
  }

  function setEngine(name) {
    if (engineEl) engineEl.textContent = "引擎 · " + (name || "--");
  }

  function setDemo(name) {
    if (window.JarvisDemos) window.JarvisDemos.load(name, demoCanvas, demoLayer);
  }

  function onMessage(data) {
    if (!data || typeof data !== "object") return;
    var state = data.state;
    if (state === "_sys") { updateSys(data.sys); return; }
    if (state === "_subtitle") { showSubtitle(data.subtitle, data.is_final); return; }
    if (state === "_demo") { setDemo(data.demo); return; }
    if (state === "_skin") { applySkin(data.skin); return; }
    // 四态切换
    if (core) core.msg({ state: state, skin: data.skin });
    setCssState(state);
    if (state === "idle") showSubtitle("", true);
    if (micEl) {
      var label = { idle: "待命 · Hey Jarvis", listening: "聆听中…", thinking: "思考中…", speaking: "播报中…" };
      micEl.textContent = label[state] || "待命";
    }
    if (data.engine) setEngine(data.engine);
  }

  function applySkin(skinId) {
    if (window.JarvisSkins) window.JarvisSkins.applySkin(skinId || "jarvis");
  }

  function connect() {
    try {
      socket = new WebSocket("ws://127.0.0.1:8765");
      socket.onmessage = function (ev) {
        try { onMessage(JSON.parse(ev.data)); } catch (e) {}
      };
      socket.onclose = function () { setTimeout(connect, 1500); };
      socket.onerror = function () { setTimeout(connect, 1500); };
    } catch (e) {
      setTimeout(connect, 1500);
    }
  }

  function loop(ts) {
    var dt = lastFrame ? (ts - lastFrame) / 1000 : 0;
    lastFrame = ts;
    if (core) core.frame(Math.min(0.05, dt));
    requestAnimationFrame(loop);
  }

  function init() {
    var canvas = document.getElementById("coreCanvas");
    subtitleEl = document.getElementById("subtitle");
    engineEl = document.getElementById("engineBadge");
    sysEl = document.getElementById("sysStatus");
    micEl = document.getElementById("micState");
    demoLayer = document.getElementById("demoLayer");
    demoCanvas = document.getElementById("demoCanvas");
    textInput = document.getElementById("textInput");
    if (canvas && window.JarvisCores) core = new window.JarvisCores.Core(canvas);
    if (textInput) {
      textInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && textInput.value.trim()) {
          if (socket && socket.readyState === 1) {
            socket.send(JSON.stringify({ type: "text", text: textInput.value.trim() }));
          }
          textInput.value = "";
        }
      });
    }
    requestAnimationFrame(loop);
    connect();
  }

  return { init: init, onMessage: onMessage };
})();
