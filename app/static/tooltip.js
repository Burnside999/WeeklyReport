'use strict';
// Shared interaction only; field copy lives in tips.js.
window.WeeklyReportTooltip = (() => {
  const bubble = document.createElement('div');
  bubble.id = 'field-tooltip';
  bubble.className = 'field-tooltip';
  bubble.setAttribute('role', 'tooltip');
  bubble.hidden = true;
  document.body.append(bubble);
  let active = null, timer, anchorRect;

  function hide() {
    clearTimeout(timer);
    active?.setAttribute('aria-expanded', 'false');
    active?.removeAttribute('aria-describedby');
    active = null;
    bubble.hidden = true;
  }

  function show(button) {
    hide();
    active = button;
    (button.closest('dialog') || document.body).append(bubble);
    bubble.textContent = button.dataset.tip;
    bubble.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    button.setAttribute('aria-describedby', bubble.id);
    const r = button.getBoundingClientRect(), v = window.visualViewport;
    anchorRect = r;
    const left = v?.offsetLeft || 0, top = v?.offsetTop || 0;
    const width = v?.width || innerWidth, height = v?.height || innerHeight;
    bubble.style.width = Math.min(240, width - 24) + 'px';
    const b = bubble.getBoundingClientRect();
    bubble.style.left = Math.max(left + 12, Math.min(r.left, left + width - b.width - 12)) + 'px';
    const y = r.bottom + 6 + b.height <= top + height - 12 ? r.bottom + 6 : r.top - b.height - 6;
    bubble.style.top = Math.max(top + 12, Math.min(y, top + height - b.height - 12)) + 'px';
  }

  function attach(control, name, text) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'tip-trigger';
    button.textContent = '?';
    button.dataset.tip = text;
    button.setAttribute('aria-label', name + '说明');
    button.setAttribute('aria-expanded', 'false');
    const label = control.closest('label') || (control.matches('legend,h2') ? control : null);
    if (!label) return;
    let heading = label.querySelector(':scope > .label-heading');
    if (!heading) {
      heading = document.createElement('span');
      heading.className = 'label-heading';
      const text = document.createElement('span');
      text.className = 'label-text';
      for (const child of [...label.childNodes]) if (child.nodeType === Node.TEXT_NODE) text.append(child);
      heading.append(text);
      label.prepend(heading);
    }
    heading.append(button);
    let pointerFocus = false;
    button.addEventListener('pointerenter', event => {
      if (event.pointerType !== 'mouse') return;
      clearTimeout(timer);
      timer = setTimeout(() => { if (button.matches(':hover')) show(button); }, 300);
    });
    button.addEventListener('pointerleave', event => { if (event.pointerType === 'mouse') hide(); });
    button.addEventListener('pointerdown', () => { pointerFocus = true; });
    button.addEventListener('pointercancel', () => { pointerFocus = false; hide(); });
    button.addEventListener('focus', () => { if (!pointerFocus) show(button); });
    button.addEventListener('blur', () => { pointerFocus = false; hide(); });
    button.addEventListener('keydown', () => { pointerFocus = false; });
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      pointerFocus = false;
      if (active === button) hide(); else show(button);
    });
  }

  document.addEventListener('pointerdown', event => {
    if (active && !active.contains(event.target)) hide();
  }, true);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && active) {
      event.preventDefault();
      event.stopPropagation();
      hide();
    }
  }, true);
  // A hint must never float over the user's next action.
  addEventListener('scroll', () => {
    if (!active) return;
    const r = active.getBoundingClientRect();
    // Ignore a queued scroll event when focus/hover already measured the final position.
    if (r.top !== anchorRect.top || r.left !== anchorRect.left) hide();
  }, true);
  addEventListener('resize', hide);
  window.visualViewport?.addEventListener('resize', hide);
  return Object.freeze({attach});
})();
