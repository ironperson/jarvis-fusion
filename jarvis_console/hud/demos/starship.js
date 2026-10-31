/* 星舰发射回收演示：中央箭体 + 整流罩 + 一对助推器 + 喷焰。
   通过 mesh.onBeforeRender 做轻微上升/喷焰脉动，无需改 loader。 */
window.JarvisDemos.register("starship", function (THREE, group) {
  "use strict";
  var matBody = new THREE.MeshStandardMaterial({ color: 0xdfe9f2, metalness: 0.7, roughness: 0.25 });
  var matAccent = new THREE.MeshStandardMaterial({ color: 0x5ad1ff, metalness: 0.5, roughness: 0.4, emissive: 0x123b55, emissiveIntensity: 0.6 });
  var matFlame = new THREE.MeshBasicMaterial({ color: 0xffb05a, transparent: true, opacity: 0.85 });

  // 中央箭体
  var body = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.7, 3.0, 28), matBody);
  group.add(body);

  // 整流罩（锥）
  var nose = new THREE.Mesh(new THREE.ConeGeometry(0.55, 0.9, 28), matAccent);
  nose.position.y = 1.95; group.add(nose);

  // 一对助推器
  var boosters = [];
  [-0.95, 0.95].forEach(function (x) {
    var b = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.32, 2.4, 20), matBody);
    b.position.set(x, -0.2, 0); group.add(b);
    var tip = new THREE.Mesh(new THREE.ConeGeometry(0.28, 0.5, 20), matAccent);
    tip.position.set(x, 1.1, 0); group.add(tip);
    var flame = new THREE.Mesh(new THREE.ConeGeometry(0.22, 0.9, 16), matFlame);
    flame.position.set(x, -1.5, 0); flame.rotation.x = Math.PI; group.add(flame);
    boosters.push({ base: b.position.y, tip: tip.position.y, flame: flame });
  });

  // 中央喷焰
  var coreFlame = new THREE.Mesh(new THREE.ConeGeometry(0.45, 1.4, 18), matFlame);
  coreFlame.position.y = -2.0; coreFlame.rotation.x = Math.PI; group.add(coreFlame);

  var start = performance.now();
  body.onBeforeRender = function () {
    var t = (performance.now() - start) / 1000;
    // 整体微上浮动效
    group.position.y = Math.sin(t * 1.2) * 0.15;
    // 喷焰脉动
    var pulse = 0.8 + Math.sin(t * 12) * 0.25;
    coreFlame.scale.set(pulse, 0.9 + Math.sin(t * 9) * 0.3, pulse);
    boosters.forEach(function (b) {
      b.flame.scale.set(pulse, 0.9 + Math.sin(t * 11 + 1) * 0.3, pulse);
    });
  };
});
