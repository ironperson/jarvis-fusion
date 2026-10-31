/* Roadster 悬停演示：车身 + 座舱 + 四轮 + 底部反重力辉光，整体上下悬浮。 */
window.JarvisDemos.register("roadster", function (THREE, group) {
  "use strict";
  var matBody = new THREE.MeshStandardMaterial({ color: 0xb03030, metalness: 0.6, roughness: 0.3 });
  var matGlass = new THREE.MeshStandardMaterial({ color: 0x9fd6ff, metalness: 0.2, roughness: 0.1, transparent: true, opacity: 0.5 });
  var matWheel = new THREE.MeshStandardMaterial({ color: 0x222833, metalness: 0.3, roughness: 0.7 });
  var matGlow = new THREE.MeshBasicMaterial({ color: 0x5ad1ff, transparent: true, opacity: 0.5 });

  // 车身
  var body = new THREE.Mesh(new THREE.BoxGeometry(3.0, 0.7, 1.4), matBody);
  body.position.y = 0.6; group.add(body);

  // 座舱
  var cabin = new THREE.Mesh(new THREE.BoxGeometry(1.4, 0.55, 1.2), matGlass);
  cabin.position.set(-0.1, 1.1, 0); group.add(cabin);

  // 四轮（半圆）
  var wheels = [];
  [[-1.0, 0.7], [1.0, 0.7], [-1.0, -0.7], [1.0, -0.7]].forEach(function (p) {
    var w = new THREE.Mesh(new THREE.CylinderGeometry(0.45, 0.45, 0.3, 18), matWheel);
    w.rotation.x = Math.PI / 2;
    w.position.set(p[0], 0.2, p[1]); group.add(w);
    wheels.push(w);
  });

  // 底部反重力辉光环
  var glow = new THREE.Mesh(new THREE.TorusGeometry(1.1, 0.18, 12, 40), matGlow);
  glow.rotation.x = Math.PI / 2; glow.position.y = -0.05; group.add(glow);

  var start = performance.now();
  body.onBeforeRender = function () {
    var t = (performance.now() - start) / 1000;
    group.position.y = Math.sin(t * 1.6) * 0.25;          // 悬浮
    var s = 0.85 + Math.sin(t * 6) * 0.2;
    glow.scale.set(s, s, 1);
    glow.material.opacity = 0.35 + Math.sin(t * 6) * 0.2;  // 辉光呼吸
    wheels.forEach(function (w) { w.rotation.z = t * 0; }); // 占位，保持车轮静态
  };
});
