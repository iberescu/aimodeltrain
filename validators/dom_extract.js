// Injected into the rendered page to extract layout/text/color info.
// Returns a JSON-serializable object the Python validator consumes.
//
// Design: walk every element; an element is a "text leaf" if it has
// at least one direct child text node with non-whitespace content. For
// each text leaf we capture geometry + computed style + the nearest
// non-transparent ancestor background color (for contrast checks).

(() => {
  function parseColor(str) {
    // Returns {r,g,b,a} in 0..1 (a) and 0..255 (r,g,b), or null if unparseable.
    if (!str) return null;
    const m = str.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const parts = m[1].split(',').map(s => parseFloat(s.trim()));
    if (parts.length < 3) return null;
    return {
      r: parts[0],
      g: parts[1],
      b: parts[2],
      a: parts.length === 4 ? parts[3] : 1.0,
    };
  }

  function effectiveBg(el) {
    // Walk up looking for the first ancestor with alpha > ~0.5 background-color.
    // Falls back to white if none found (Chromium default).
    let cur = el;
    while (cur && cur !== document.documentElement.parentNode) {
      const cs = getComputedStyle(cur);
      const bg = parseColor(cs.backgroundColor);
      if (bg && bg.a > 0.5) return bg;
      cur = cur.parentElement;
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  }

  function hasDirectText(el) {
    for (const n of el.childNodes) {
      if (n.nodeType === 3 && n.nodeValue && n.nodeValue.trim().length > 0) {
        return true;
      }
    }
    return false;
  }

  function nthIndex(el) {
    // Cheap stable identifier for the report: path of nth-child indices.
    const parts = [];
    let cur = el;
    while (cur && cur.parentElement) {
      const siblings = Array.from(cur.parentElement.children);
      parts.unshift(`${cur.tagName.toLowerCase()}:${siblings.indexOf(cur)}`);
      cur = cur.parentElement;
      if (parts.length > 12) break;
    }
    return parts.join('>');
  }

  const all = document.querySelectorAll('*');
  const textLeaves = [];
  const decorativeElements = [];
  const roleElements = {}; // role -> array of {visible, rect, text}

  for (const el of all) {
    const cs = getComputedStyle(el);
    const role = el.getAttribute && el.getAttribute('data-role');
    const opacity = parseFloat(cs.opacity);
    const hidden = cs.display === 'none' || cs.visibility === 'hidden' ||
                   (!isNaN(opacity) && opacity < 0.05);
    const rect = el.getBoundingClientRect();
    const sized = rect.width > 0 && rect.height > 0;

    if (role) {
      if (!roleElements[role]) roleElements[role] = [];
      roleElements[role].push({
        path: nthIndex(el),
        tag: el.tagName.toLowerCase(),
        text: (el.textContent || '').trim().slice(0, 240),
        rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
        visible: !hidden && sized,
      });
    }

    if (hidden) continue;
    if (!sized) continue;

    if (hasDirectText(el)) {
      const color = parseColor(cs.color) || { r: 0, g: 0, b: 0, a: 1 };
      const bg = effectiveBg(el);
      textLeaves.push({
        path: nthIndex(el),
        tag: el.tagName.toLowerCase(),
        text: (el.textContent || '').trim().slice(0, 120),
        rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
        fontSizePx: parseFloat(cs.fontSize),
        fontFamily: cs.fontFamily,
        color: color,
        bg: bg,
        opacity: isNaN(opacity) ? 1 : opacity,
        zIndex: cs.zIndex,
      });
    } else {
      decorativeElements.push({
        path: nthIndex(el),
        tag: el.tagName.toLowerCase(),
        rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
      });
    }
  }

  const body = document.body;
  const bodyRect = body.getBoundingClientRect();

  return {
    canvas: {
      bodyClientW: body.clientWidth,
      bodyClientH: body.clientHeight,
      bodyBBox: { x: bodyRect.x, y: bodyRect.y, w: bodyRect.width, h: bodyRect.height },
      dataDesignType: body.getAttribute('data-design-type') || null,
      dataCanvasW: body.getAttribute('data-canvas-w') || null,
      dataCanvasH: body.getAttribute('data-canvas-h') || null,
    },
    textLeaves: textLeaves,
    decorativeElements: decorativeElements,
    roleElements: roleElements,
    scriptCount: document.querySelectorAll('script').length,
    iframeCount: document.querySelectorAll('iframe').length,
  };
})();
