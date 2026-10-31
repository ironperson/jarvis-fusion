/* Optimus 行走演示：人形躯干 + 头 + 双臂 + 双腿，双臂/双腿交替摆动模拟步态。 */
window.JarvisDemos.register("optimus", function (THREE, group) {
  "use strict";
  var matSkin = new THREE.MeshStandardMaterial({ color: 0xd8e0ea, metalness: 0.6, roughness: 0.35 });
  var matAccent = new THREE.MeshStandardMaterial({ color: 0x5ad1ff, metalness: 0.4, roughness: 0.4, emissive: 0x10384f, emissiveIntensity: 0.5 });

  // 躯干
  var torso = new THREE.Mesh(new THREE.BoxGeometry(1.0, 1.4, 0.5), matSkin);
  torso.position.y = 1.4; group.add(torso);
  var chest = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.4, 0.1), matAccent);
  chest.position.set(0, 1.7, 0.28); group.add(chest);

  // 头
  var head = new THREE.Mesh(new THREE.SphereGeometry(0.4, 20, 20), matSkin);
  head.position.y = 2.4; group.add(head);
  var visor = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.12, 0.05), matAccent);
  visor.position.set(0, 2.42, 0.36); group.add(visor);

  // 双臂（以肩为支点摆动）
  function makeArm(x) {
    var pivot = new THREE.Group();
    pivot.position.set(x, 2.0, 0);
    var arm = new THREE.Mesh(new THREE.BoxGeometry(0.25, 1.2, 0.25), matSkin);
    arm.position.y = -0.6; pivot.add(arm);
    group.add(pivot);
    return pivot;
  }
  var armL = makeArm(-0.7), armR = makeArm(0.7);

  // 双腿（以髋为支点摆动）
  function makeLeg(x) {
    var pivot = new THREE.Group();
    pivot.position.set(x, 0.7, 0);
    var leg = new THREE.Mesh(new THREE.BoxGeometry(0.3, 1.2, 0.3), matSkin);
    leg.position.y = -0.6; pivot.add(leg);
    group.add(pivot);
    return pivot;
  }
  var legL = makeLeg(-0.3), legR = makeLeg(0.3);

  var start = performance.now();
  torso.onBeforeRender = function () {
    var t = (performance.now() - start) / 1000;
    var swing = Math.sin(t * 3) * 0.5;
    armL.rotation.x = swing;
    armR.rotation.x = -swing;
    legL.rotation.x = -swing;
    legR.rotation.x = swing;
    group.position.y = Math.abs(Math.sin(t * 3)) * 0.08; // 行走重心起伏
  };
});
