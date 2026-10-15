'use strict';

// This helper runs in an opaque sandbox. Feed-provided scripts remain blocked by CSP.
const parentOrigin = new URL(document.currentScript.src).origin;
window.addEventListener('message', event => {
  if (event.source !== parent || event.origin !== parentOrigin || event.data?.type !== 'nnw-reader-preferences') return;
  const {theme, fontSize} = event.data;
  if (['light', 'dark'].includes(theme)) document.documentElement.dataset.theme = theme;
  if (Number.isFinite(fontSize) && fontSize >= 15 && fontSize <= 23) {
    const ratio = scrollY / Math.max(1, document.documentElement.scrollHeight - innerHeight);
    document.documentElement.style.setProperty('--reader-size', `${fontSize}px`);
    window.scrollTo(0, ratio * Math.max(0, document.documentElement.scrollHeight - innerHeight));
  }
});

for (const image of document.querySelectorAll('.article-content img')) {
  let candidates;
  try { candidates = JSON.parse(image.dataset.imageCandidates || '[]'); }
  catch { candidates = []; }
  image.removeAttribute('data-image-candidates');
  let index = 0;
  let failed = false;
  const loadNext = () => {
    if (failed) return;
    index += 1;
    if (index < candidates.length) {
      image.src = candidates[index];
      return;
    }
    failed = true;
    image.hidden = true;
    const notice = document.createElement('div');
    notice.className = 'image-error';
    notice.textContent = '图片暂时无法加载，可前往原文查看。';
    if (candidates.length) {
      const link = document.createElement('a');
      link.href = candidates[0];
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = '打开图片';
      notice.append(link);
    }
    image.after(notice);
  };
  image.addEventListener('error', loadNext);
  if (!candidates.length || (image.complete && !image.naturalWidth)) loadNext();
}

parent.postMessage({type: 'nnw-reader-ready'}, parentOrigin);
