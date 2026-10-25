// 상세페이지를 어디까지 봤는지 왼쪽 설명 영역에 표시
(() => {
  const sheet = document.querySelector(".work__sheet");
  const bar = document.querySelector(".work__progress span");
  if (!sheet || !bar) return;
  let ticking = false;
  const update = () => {
    const r = sheet.getBoundingClientRect();
    const total = r.height - window.innerHeight;
    const done = total > 0 ? Math.min(1, Math.max(0, -r.top / total)) : 1;
    bar.style.width = `${(done * 100).toFixed(1)}%`;
    ticking = false;
  };
  const onScroll = () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } };
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  update();
})();
