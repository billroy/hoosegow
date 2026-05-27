(function () {
  const DEFAULT_COORD_LIMIT = 100000;

  function coordKey(col, row) {
    return `${Number(col)},${Number(row)}`;
  }

  function indexToCoord(index, cols) {
    const safeCols = Number.isFinite(Number(cols)) && Number(cols) > 0 ? Math.floor(Number(cols)) : 4;
    const safeIndex = Math.max(0, Math.floor(Number(index) || 0));
    return { col: safeIndex % safeCols, row: Math.floor(safeIndex / safeCols) };
  }

  function coordToIndex(col, row, cols) {
    const safeCols = Number.isFinite(Number(cols)) && Number(cols) > 0 ? Math.floor(Number(cols)) : 4;
    return Math.floor(Number(row) || 0) * safeCols + Math.floor(Number(col) || 0);
  }

  function _cardSize(cardSize) {
    const width = Math.max(1, Number(cardSize?.width) || 220);
    const height = Math.max(1, Number(cardSize?.height) || 140);
    return { width, height };
  }

  function visibleRange(viewportOrigin, viewportPx, cardSize) {
    const size = _cardSize(cardSize);
    const originCol = Number(viewportOrigin?.col) || 0;
    const originRow = Number(viewportOrigin?.row) || 0;
    const width = Math.max(0, Number(viewportPx?.width) || 0);
    const height = Math.max(0, Number(viewportPx?.height) || 0);
    return {
      colStart: Math.floor(originCol),
      colEnd: Math.ceil(originCol + width / size.width),
      rowStart: Math.floor(originRow),
      rowEnd: Math.ceil(originRow + height / size.height),
    };
  }

  function overscanRange(range, buffer = 2) {
    const b = Math.max(0, Math.floor(Number(buffer) || 0));
    return {
      colStart: Math.floor(range.colStart) - b,
      colEnd: Math.ceil(range.colEnd) + b,
      rowStart: Math.floor(range.rowStart) - b,
      rowEnd: Math.ceil(range.rowEnd) + b,
    };
  }

  function pixelToCoord(px, py, viewportOrigin, cardSize) {
    const size = _cardSize(cardSize);
    return {
      col: Math.floor((Number(viewportOrigin?.col) || 0) + (Number(px) || 0) / size.width),
      row: Math.floor((Number(viewportOrigin?.row) || 0) + (Number(py) || 0) / size.height),
    };
  }

  function coordToPixel(col, row, viewportOrigin, cardSize) {
    const size = _cardSize(cardSize);
    return {
      x: (Number(col) - (Number(viewportOrigin?.col) || 0)) * size.width,
      y: (Number(row) - (Number(viewportOrigin?.row) || 0)) * size.height,
    };
  }

  function occupiedBounds(coords) {
    if (!Array.isArray(coords) || !coords.length) return null;
    let colMin = Infinity, colMax = -Infinity, rowMin = Infinity, rowMax = -Infinity;
    for (const c of coords) {
      const col = Number(c?.col);
      const row = Number(c?.row);
      if (!Number.isFinite(col) || !Number.isFinite(row)) continue;
      colMin = Math.min(colMin, col);
      colMax = Math.max(colMax, col);
      rowMin = Math.min(rowMin, row);
      rowMax = Math.max(rowMax, row);
    }
    if (!Number.isFinite(colMin)) return null;
    return { colMin, colMax, rowMin, rowMax };
  }

  function nearestOccupiedInDirection(origin, direction, occupiedCoords) {
    const col = Number(origin?.col) || 0;
    const row = Number(origin?.row) || 0;
    const dir = String(direction || '').toLowerCase();
    const candidates = [];
    for (const c of occupiedCoords || []) {
      const cc = Number(c?.col);
      const rr = Number(c?.row);
      if (!Number.isFinite(cc) || !Number.isFinite(rr)) continue;
      const dc = cc - col;
      const dr = rr - row;
      if ((dir === 'right' && dc <= 0) || (dir === 'left' && dc >= 0) ||
          (dir === 'down' && dr <= 0) || (dir === 'up' && dr >= 0)) {
        continue;
      }
      const sameAxis = (dir === 'right' || dir === 'left') ? dr === 0 : dc === 0;
      const primary = (dir === 'right' || dir === 'left') ? Math.abs(dc) : Math.abs(dr);
      const secondary = (dir === 'right' || dir === 'left') ? Math.abs(dr) : Math.abs(dc);
      candidates.push({ coord: c, sameAxis, primary, secondary });
    }
    candidates.sort((a, b) => {
      if (a.sameAxis !== b.sameAxis) return a.sameAxis ? -1 : 1;
      if (a.primary !== b.primary) return a.primary - b.primary;
      return a.secondary - b.secondary;
    });
    return candidates[0]?.coord || null;
  }

  function clampOriginToBounds(viewportOrigin, viewportPx, cardSize, limit = DEFAULT_COORD_LIMIT) {
    const size = _cardSize(cardSize);
    const colsVisible = Math.max(1, (Number(viewportPx?.width) || 0) / size.width);
    const rowsVisible = Math.max(1, (Number(viewportPx?.height) || 0) / size.height);
    const maxCol = limit - colsVisible + 1;
    const maxRow = limit - rowsVisible + 1;
    return {
      col: Math.max(0, Math.min(maxCol, Number(viewportOrigin?.col) || 0)),
      row: Math.max(0, Math.min(maxRow, Number(viewportOrigin?.row) || 0)),
    };
  }

  function clampOriginToOccupied(viewportOrigin, occupied, viewportPx, cardSize) {
    return clampOriginToBounds(viewportOrigin, viewportPx, cardSize);
  }

  window.GridGeometry = {
    DEFAULT_COORD_LIMIT,
    coordKey,
    indexToCoord,
    coordToIndex,
    visibleRange,
    overscanRange,
    pixelToCoord,
    coordToPixel,
    occupiedBounds,
    nearestOccupiedInDirection,
    clampOriginToBounds,
    clampOriginToOccupied,
  };
})();
