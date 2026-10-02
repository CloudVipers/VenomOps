/* VenomOps site: theme toggle, copy buttons, install tabs, mobile menu and table-of-contents highlight. No dependencies. */
(function () {
  var root = document.documentElement;
  var KEY = 'venom-theme';

  function effectiveTheme() {
    if (root.dataset.theme) return root.dataset.theme;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  document.querySelectorAll('.theme-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var next = effectiveTheme() === 'dark' ? 'light' : 'dark';
      root.dataset.theme = next;
      try { localStorage.setItem(KEY, next); } catch (e) { /* storage can be blocked: the choice just is not remembered */ }
    });
  });

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy') ? resolve() : reject(); } catch (e) { reject(e); } finally { document.body.removeChild(ta); }
    });
  }
  function flash(btn, label) {
    var old = btn.textContent;
    btn.textContent = label; btn.classList.add('done');
    setTimeout(function () { btn.textContent = old; btn.classList.remove('done'); }, 1600);
  }
  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('.copy');
    if (!btn) return;
    var text = btn.dataset.copy;
    if (!text) {
      var fig = btn.closest('figure.code');
      var pre = fig && fig.querySelector('pre');
      text = pre ? pre.innerText.replace(/\n$/, '') : '';
    }
    copyText(text).then(function () { flash(btn, 'Copiado'); }, function () { flash(btn, 'No se pudo'); });
  });

  document.querySelectorAll('.tabs').forEach(function (tabs) {
    var buttons = tabs.querySelectorAll('[role="tab"]');
    var panels = tabs.querySelectorAll('[role="tabpanel"]');
    function select(btn) {
      buttons.forEach(function (b) { b.setAttribute('aria-selected', String(b === btn)); b.tabIndex = b === btn ? 0 : -1; });
      panels.forEach(function (p) { p.hidden = p.id !== btn.getAttribute('aria-controls'); });
    }
    buttons.forEach(function (btn, i) {
      btn.addEventListener('click', function () { select(btn); });
      btn.addEventListener('keydown', function (ev) {
        var n = ev.key === 'ArrowRight' ? i + 1 : ev.key === 'ArrowLeft' ? i - 1 : null;
        if (n === null) return;
        var target = buttons[(n + buttons.length) % buttons.length];
        target.focus(); select(target); ev.preventDefault();
      });
    });
  });

  var menu = document.querySelector('.menu-btn');
  if (menu) {
    menu.addEventListener('click', function () {
      var open = document.body.classList.toggle('menu-open');
      menu.setAttribute('aria-expanded', String(open));
    });
    document.querySelectorAll('.main-nav a').forEach(function (a) {
      a.addEventListener('click', function () { document.body.classList.remove('menu-open'); menu.setAttribute('aria-expanded', 'false'); });
    });
  }

  // Table of contents: the active entry is the last heading that has already passed under the sticky header.
  var tocLinks = document.querySelectorAll('.toc a');
  if (tocLinks.length) {
    var byId = {};
    tocLinks.forEach(function (a) { byId[a.getAttribute('href').slice(1)] = a; });
    var heads = Array.prototype.filter.call(document.querySelectorAll('.prose h2[id], .prose h3[id]'), function (h) { return byId[h.id]; });
    var current = null, ticking = false;
    var update = function () {
      ticking = false;
      var active = null;
      for (var i = 0; i < heads.length; i++) {
        if (heads[i].getBoundingClientRect().top <= 120) active = heads[i]; else break;
      }
      var link = active ? byId[active.id] : null;
      if (link === current) return;
      if (current) current.classList.remove('active');
      current = link;
      if (current) current.classList.add('active');
    };
    window.addEventListener('scroll', function () { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
    update();
  }
})();
