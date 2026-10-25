// 메인 화면 작업물: 방문할 때마다 전체 작업물 중 무작위로 골라 보여줌
(() => {
  const grid = document.querySelector("[data-shuffle]");
  if (!grid) return;
  const show = parseInt(grid.dataset.shuffle, 10) || 12;
  // 숨겨 둔 카드(<div hidden><a class="card">)를 꺼내 모든 카드를 한 목록으로
  const cards = [...grid.querySelectorAll(".card")];
  grid.querySelectorAll(":scope > div[hidden]").forEach((d) => d.remove());
  for (let i = cards.length - 1; i > 0; i--) {          // Fisher–Yates 섞기
    const j = Math.floor(Math.random() * (i + 1));
    [cards[i], cards[j]] = [cards[j], cards[i]];
  }
  const frag = document.createDocumentFragment();
  cards.slice(0, show).forEach((c, i) => {
    const img = c.querySelector("img");
    if (img && i < 4) img.loading = "eager";
    frag.appendChild(c);
  });
  grid.replaceChildren(frag);
})();
