/* 四态核心动画：在 #coreCanvas 上绘制环 + 态特效。接口对齐上游 cores.js：
   frame(dt) 每帧调用；msg(m) 接收状态消息；dispose() 释放。 */
window.JarvisCores = (function () {
  "use strict";

  var STATE_COLORS = {
    idle: "#3fa9ff", listening: "#39e0d0", thinking: "#9a6bff", speaking: "#ffcf6b"
  };

  function Core(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.state = "idle";
    this.color = STATE_COLORS.idle;
    this.t = 0;
    this.pulse = 0;
    this.targetPulse = 0;
    this._resize();
    var self = this;
    window.addEventListener("resize", function () { self._resize(); });
  }

  Core.prototype._resize = function () {
    var size = Math.min(this.canvas.clientWidth, this.canvas.clientHeight) || 480;
    var dpr = window.devicePixelRatio || 1;
    this.canvas.width = size * dpr;
    this.canvas.height = size * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.size = size;
  };

  Core.prototype.msg = function (m) {
    if (!m || !m.state) return;
    if (m.skin && window.JarvisSkins) window.JarvisSkins.applySkin(m.skin);
    var s = m.state;
    if (STATE_COLORS[s]) {
      this.state = s;
      var skin = (window.JarvisSkins && window.__jarvisSkin) || "jarvis";
      this.color = window.JarvisSkins
        ? window.JarvisSkins.mapStateToSkin(skin, s)
        : STATE_COLORS[s];
    }
    this.targetPulse = (s === "listening") ? 1 : (s === "thinking" ? 0.6 : 0.3);
  };

  Core.prototype.frame = function (dt) {
    this.t += dt;
    this.pulse += (this.targetPulse - this.pulse) * Math.min(1, dt * 4);
    var ctx = this.ctx, size = this.size, cx = size / 2, cy = size / 2;
    ctx.clearRect(0, 0, size, size);

    var baseR = size * 0.30;
    // 外环（呼吸）
    var breathe = baseR + Math.sin(this.t * 1.6) * 6 * (0.4 + this.pulse);
    ctx.save();
    ctx.strokeStyle = this.color;
    ctx.globalAlpha = 0.85;
    ctx.lineWidth = 3;
    ctx.shadowColor = this.color;
    ctx.shadowBlur = 18;
    ctx.beginPath();
    ctx.arc(cx, cy, breathe, 0, Math.PI * 2);
    ctx.stroke();

    // 旋转弧（聆听/思考时更快）
    var speed = this.state === "thinking" ? 3.2 : (this.state === "listening" ? 2.4 : 0.8);
    var arcLen = this.state === "speaking" ? Math.PI * 1.4 : Math.PI * 0.7;
    ctx.globalAlpha = 1;
    ctx.lineWidth = 5;
    ctx.beginPath();
    ctx.arc(cx, cy, baseR - 10, this.t * speed, this.t * speed + arcLen);
    ctx.stroke();

    // 内核（思考态旋流 / 说话态扩散）
    if (this.state === "thinking") {
      for (var i = 0; i < 3; i++) {
        var r = baseR * (0.2 + 0.18 * i) + Math.sin(this.t * 3 + i) * 4;
        ctx.globalAlpha = 0.5 - i * 0.12;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.stroke();
      }
    } else if (this.state === "speaking") {
      var wave = Math.abs(Math.sin(this.t * 6)) * baseR * 0.5;
      ctx.globalAlpha = 0.5;
      ctx.beginPath();
      ctx.arc(cx, cy, baseR * 0.5 + wave, 0, Math.PI * 2);
      ctx.stroke();
    } else {
      ctx.globalAlpha = 0.4 + this.pulse * 0.4;
      ctx.beginPath();
      ctx.arc(cx, cy, baseR * 0.22, 0, Math.PI * 2);
      ctx.fillStyle = this.color;
      ctx.fill();
    }
    ctx.restore();
  };

  Core.prototype.dispose = function () {};

  return { Core: Core };
})();
