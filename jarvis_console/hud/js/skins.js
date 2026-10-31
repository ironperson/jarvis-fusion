/* 8 套皮肤：每套定义四态（idle/listening/thinking/speaking）的核心色与辉光。
   结构对齐上游 GDDXX-Jarvis 的 SKINS（roles/core/coreKind），此处用纯色映射实现。 */
window.JarvisSkins = (function () {
  "use strict";

  // roles: idle/listening/thinking/speaking
  var SKINS = [
    { id: "jarvis", label: "经典贾维斯", core: {
        idle: "#3fa9ff", listening: "#39e0d0", thinking: "#9a6bff", speaking: "#ffcf6b" } },
    { id: "iron", label: "钢铁红", core: {
        idle: "#ff5a5a", listening: "#ff8a5a", thinking: "#ff5ad0", speaking: "#ffd15a" } },
    { id: "ocean", label: "深海蓝", core: {
        idle: "#2f6bff", listening: "#2fd6ff", thinking: "#3f9aff", speaking: "#7affea" } },
    { id: "matrix", label: "矩阵绿", core: {
        idle: "#26ff8c", listening: "#7dff5a", thinking: "#26ffd0", speaking: "#caff5a" } },
    { id: "gold", label: "黄金管家", core: {
        idle: "#caa64a", listening: "#e0c060", thinking: "#b07aff", speaking: "#ffd15a" } },
    { id: "rose", label: "蔷薇粉", core: {
        idle: "#ff6fa0", listening: "#ff9ad0", thinking: "#c06bff", speaking: "#ffc15a" } },
    { id: "ice", label: "寒冰银", core: {
        idle: "#9fd6ff", listening: "#c8f0ff", thinking: "#7fa8ff", speaking: "#ffe0a0" } },
    { id: "amber", label: "琥珀橙", core: {
        idle: "#ff9a3f", listening: "#ffc36b", thinking: "#ff6fae", speaking: "#ffe06b" } }
  ];

  var byId = {};
  SKINS.forEach(function (s) { byId[s.id] = s; });

  function mapStateToSkin(skinId, state) {
    var skin = byId[skinId] || SKINS[0];
    return (skin.core && skin.core[state]) || skin.core.idle;
  }

  function applySkin(skinId) {
    window.__jarvisSkin = skinId || "jarvis";
  }

  return {
    SKINS: SKINS,
    byId: byId,
    mapStateToSkin: mapStateToSkin,
    applySkin: applySkin
  };
})();
