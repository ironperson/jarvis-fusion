/* 3D 全息演示加载器：优先用本地 vendored three.min.js（离线），缺失则回退 CDN。
   每个演示在 hud/demos/<name>.js 中注册到 window.JarvisDemos.registry[name]。
   手势：拖拽旋转、滚轮缩放、双击退出。 */
window.JarvisDemos = (function () {
  "use strict";

  var registry = {};
  var current = null;      // { dispose: fn }
  var renderer = null, scene = null, camera = null, group = null;
  var canvas = null, layer = null;
  var threeReady = false, pending = null;
  var angle = { x: 0, y: 0 }, autoRotate = true, scale = 1;

  function ensureThree(cb) {
    if (window.THREE) { threeReady = true; cb(true); return; }
    injectScript("vendor/three.min.js", function (ok) {
      if (ok && window.THREE) { threeReady = true; cb(true); return; }
      // 回退 CDN（仅当在线时；离线则演示不可用，已在前端静默降级）。
      injectScript("https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js", function (ok2) {
        threeReady = !!(ok2 && window.THREE);
        cb(threeReady);
      });
    });
  }

  function injectScript(src, cb) {
    var s = document.createElement("script");
    s.src = src;
    s.onload = function () { cb(true); };
    s.onerror = function () { cb(false); };
    document.head.appendChild(s);
  }

  function ensureRenderer() {
    if (renderer || !window.THREE || !canvas) return;
    renderer = new window.THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    resize();
    window.addEventListener("resize", resize);
    camera = new window.THREE.PerspectiveCamera(50, 1, 0.1, 100);
    camera.position.set(0, 1.5, 6);
    scene = new window.THREE.Scene();
    var light = new window.THREE.PointLight(0x9fd6ff, 1.2);
    light.position.set(5, 5, 5);
    scene.add(light);
    scene.add(new window.THREE.AmbientLight(0x335577, 0.7));
    bindGestures();
    animate();
  }

  function resize() {
    if (!renderer || !canvas) return;
    var w = canvas.clientWidth || window.innerWidth;
    var h = canvas.clientHeight || window.innerHeight;
    renderer.setSize(w, h, false);
    if (camera) { camera.aspect = w / h; camera.updateProjectionMatrix(); }
  }

  function animate() {
    requestAnimationFrame(animate);
    if (!renderer) return;
    if (group && autoRotate) group.rotation.y += 0.01;
    if (group) { group.rotation.x = angle.x; group.rotation.y += angle.y; }
    renderer.render(scene, camera);
  }

  function bindGestures() {
    var dragging = false, lastX = 0, lastY = 0;
    canvas.addEventListener("mousedown", function (e) { dragging = true; lastX = e.clientX; lastY = e.clientY; autoRotate = false; });
    window.addEventListener("mouseup", function () { dragging = false; });
    window.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      angle.y += (e.clientX - lastX) * 0.01;
      angle.x += (e.clientY - lastY) * 0.01;
      lastX = e.clientX; lastY = e.clientY;
    });
    canvas.addEventListener("wheel", function (e) {
      e.preventDefault();
      scale = Math.max(0.4, Math.min(3, scale - e.deltaY * 0.001));
      if (group) group.scale.setScalar(scale);
    }, { passive: false });
    canvas.addEventListener("dblclick", function () { unload(); });
  }

  function load(name, cv, lv) {
    canvas = cv; layer = lv;
    ensureThree(function (ready) {
      if (!ready) { if (layer) layer.classList.add("hidden"); return; }
      ensureRenderer();
      if (registry[name]) { buildScene(name); return; }
      // 懒加载演示脚本（file:// 下用 <script> 注入，规避 ES module CORS）。
      injectScript("demos/" + name + ".js", function () {
        if (registry[name]) buildScene(name);
        else if (layer) layer.classList.add("hidden");
      });
    });
  }

  function buildScene(name) {
    unload(); // 清旧场景
    var factory = registry[name];
    if (!factory) { if (layer) layer.classList.add("hidden"); return; }
    group = new window.THREE.Group();
    factory(window.THREE, group);
    scene.add(group);
    autoRotate = true; angle = { x: 0, y: 0 }; scale = 1;
    if (layer) layer.classList.remove("hidden");
  }

  function unload() {
    if (group && scene) {
      scene.remove(group);
      group.traverse(function (o) {
        if (o.geometry) o.geometry.dispose();
        if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); }
      });
      group = null;
    }
    if (layer) layer.classList.add("hidden");
  }

  return {
    registry: registry,
    register: function (name, fn) { registry[name] = fn; },
    load: load,
    unload: unload
  };
})();
