/* 按 PARTS 顺序加载后续脚本，后文件可用前文件暴露的全局变量（对齐上游扩展约定）。
   依赖仅浏览器原生；Three.js 由 cores/demos 在需要时按需加载。 */
(function () {
  "use strict";
  // 加载顺序：状态机/皮肤/核心动画 → 3D 演示（可选）→ 主桥。
  var PARTS = [
    "js/skins.js",
    "js/cores.js",
    "js/demos.js",
    "js/state.js"
  ];
  function loadNext(index) {
    if (index >= PARTS.length) {
      if (window.JarvisBridge && window.JarvisBridge.init) {
        window.JarvisBridge.init();
      }
      return;
    }
    var script = document.createElement("script");
    script.src = PARTS[index];
    script.onload = function () { loadNext(index + 1); };
    script.onerror = function () {
      console.warn("[HUD] 加载失败，跳过：" + PARTS[index]);
      loadNext(index + 1);
    };
    document.head.appendChild(script);
  }
  loadNext(0);
})();
