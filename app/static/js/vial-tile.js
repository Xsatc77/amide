// Active vial cards: line the periodic-table tile up with the text beside it. The blank between the symbol (Rt) and the amount (10 mg) is centred
// on the card's concentration line ("10 mg · 5 mg/mL"), kept inside the blank label area of the vial picture so the tile never leaves the label.
(() => {
  const LABEL_TOP = 0.30, LABEL_BOTTOM = 0.77;      // the blank label's span of the vial picture, as a fraction of its height
  const NUDGE_DOWN = 4;                             // px below the concentration line's centre: it reads as centred on the vial's label
  const BOTTLE_CENTRE = 0.47;                       // the glass body's centre across the picture (it sits a little left of the picture's middle)

  function align(card) {
    const art = card.querySelector(".vial-card-art");
    const tile = art && art.querySelector(".element-tile");
    const line = card.querySelector(".vial-conc");
    const symbol = tile && tile.querySelector(".el-symbol");
    const amount = tile && tile.querySelector(".el-amount");
    if (!tile || !line || !symbol || !amount) return;
    tile.style.top = "50%";                          // measure from a known place
    const artBox = art.getBoundingClientRect();
    const tileBox = tile.getBoundingClientRect();
    const gap = (symbol.getBoundingClientRect().bottom + amount.getBoundingClientRect().top) / 2;     // the blank between the two
    const gapInTile = gap - tileBox.top;
    const lineBox = line.getBoundingClientRect();
    let top = (lineBox.top + lineBox.height / 2) - artBox.top - gapInTile + NUDGE_DOWN;                  // tile top that puts the gap just under the line's middle
    const min = artBox.height * LABEL_TOP, max = artBox.height * LABEL_BOTTOM - tileBox.height;
    top = Math.min(Math.max(top, min), Math.max(min, max));
    tile.style.top = `${top}px`;
    tile.style.left = `${BOTTLE_CENTRE * 100}%`;
    tile.style.transform = "translateX(-50%)";
  }

  function alignAll() { document.querySelectorAll(".vial-card").forEach(align); }

  alignAll();
  window.addEventListener("resize", alignAll);
  window.addEventListener("load", alignAll);
  document.querySelectorAll(".vial-card-pic").forEach((img) => img.addEventListener("load", alignAll));
})();
