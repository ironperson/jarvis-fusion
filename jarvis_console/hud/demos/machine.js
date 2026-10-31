/* 机器拆解演示：一个可旋转/缩放的机器人核心，分解为若干部件。 */
window.JarvisDemos.register("machine", function (THREE, group) {
  var mat = new THREE.MeshStandardMaterial({ color: 0x5ad1ff, metalness: 0.6, roughness: 0.3, wireframe: false });
  var accent = new THREE.MeshStandardMaterial({ color: 0x9fd6ff, metalness: 0.4, roughness: 0.5 });
  // 躯干
  var body = new THREE.Mesh(new THREE.CylinderGeometry(1, 1.2, 2.4, 24), mat);
  group.add(body);
  // 头部
  var head = new THREE.Mesh(new THREE.SphereGeometry(0.8, 20, 20), accent);
  head.position.y = 1.8; group.add(head);
  // 双臂
  [-1.4, 1.4].forEach(function (x) {
    var arm = new THREE.Mesh(new THREE.BoxGeometry(0.4, 1.8, 0.4), mat);
    arm.position.set(x, 0.2, 0); group.add(arm);
  });
  // 底座环
  var ring = new THREE.Mesh(new THREE.TorusGeometry(1.4, 0.12, 12, 40), accent);
  ring.rotation.x = Math.PI / 2; ring.position.y = -1.4; group.add(ring);
});
