// Reveal provider chunks continuously, keeping the display within ~100ms of
// incoming text rather than delaying the completed reply with a long queue.
export function createStreamingText(render, requestFrame = requestAnimationFrame, cancelFrame = cancelAnimationFrame) {
  let target = "", displayed = "", frame = null, framesLeft = 6;
  function tick() {
    frame = null;
    const rest = Array.from(target.slice(displayed.length));
    displayed += rest.slice(0, Math.max(1, Math.ceil(rest.length / Math.max(1, framesLeft)))).join("");
    framesLeft--;
    render(displayed);
    if (displayed !== target) frame = requestFrame(tick);
  }
  return {
    set(text) {
      target = String(text || "");
      framesLeft = 6;
      if (!target.startsWith(displayed)) displayed = "";
      if (frame === null && displayed !== target) frame = requestFrame(tick);
    },
    stop() { if (frame !== null) cancelFrame(frame); frame = null; target = ""; displayed = ""; },
  };
}
