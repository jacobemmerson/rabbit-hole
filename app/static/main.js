import { openDB, getProfile, setProfile, patchProfile, bulkGetSummaries } from './db.js';
import { topDailySummaries } from './wikipedia.js';
import { needsReset, generateFeed, doRefresh, makeCard, renderCards } from './feed.js';

// ── IndexedDB init ────────────────────────────────────────────────────────────
// __RH_INIT__ is injected by the server on GET / so JS can seed IndexedDB
// without reading the httpOnly cookie.

const db = await openDB();

if (typeof window.__RH_INIT__ !== 'undefined') {
  const existing = await getProfile(db);
  if (!existing) {
    // First load after onboarding or on a new device — seed from server data.
    await setProfile(db, {
      timezone:           window.__RH_INIT__.timezone           ?? 'UTC',
      last_generated:     null,
      selected_categories: window.__RH_INIT__.selected_categories ?? [],
      current_articles:   [],
      seen_articles:      [],
      shown_articles:     [],
      reading_history:    window.__RH_INIT__.reading_history     ?? [],
      custom_interests:   window.__RH_INIT__.custom_interests    ?? [],
    });
  } else {
    // Sync cookie fields back into IndexedDB profile on each visit so both
    // stay consistent even if one was updated by another tab or device.
    await patchProfile(db, (p) => {
      p.timezone            = window.__RH_INIT__.timezone            ?? p.timezone;
      p.selected_categories = window.__RH_INIT__.selected_categories ?? p.selected_categories;
      p.custom_interests    = window.__RH_INIT__.custom_interests    ?? p.custom_interests;
      p.reading_history     = window.__RH_INIT__.reading_history     ?? p.reading_history;
      return p;
    });
  }
}

// ── Feed page ─────────────────────────────────────────────────────────────────

const feedEl = document.getElementById('feed');

if (feedEl) {
  // Reload when restored from bfcache so history is always fresh.
  window.addEventListener('pageshow', (e) => { if (e.persisted) window.location.reload(); });

  const profile = await getProfile(db);

  // Render cached articles instantly, then refresh if the day has rolled over.
  if (profile?.current_articles?.length) {
    const cached = await bulkGetSummaries(db, profile.current_articles);
    const ordered = profile.current_articles.map(t => cached.get(t)).filter(Boolean);
    if (ordered.length) renderCards(ordered);
  }

  if (needsReset(profile)) {
    await doRefresh(db, null);
  }

  // ── Tab switching ───────────────────────────────────────────────────────────
  const panelContainer = document.getElementById('panel-container');
  const tabBtnForYou   = document.getElementById('tab-btn-for-you');
  const tabBtnTopDaily = document.getElementById('tab-btn-top-daily');

  if (panelContainer && tabBtnForYou) {
    let topDailyLoaded = false;

    async function loadTopDaily() {
      if (topDailyLoaded) return;
      const grid = document.getElementById('feed-top-daily');
      try {
        const articles = await topDailySummaries(db);
        grid.innerHTML = articles.length
          ? articles.map(makeCard).join('')
          : '<p class="grid-message">Top articles unavailable right now.</p>';
        topDailyLoaded = true;
      } catch (err) {
        grid.innerHTML = '<p class="grid-message">Failed to load top articles.</p>';
        console.error('Top daily load failed:', err);
      }
    }

    tabBtnForYou.addEventListener('click', () => {
      panelContainer.scrollTo({ left: 0, behavior: 'smooth' });
    });
    tabBtnTopDaily.addEventListener('click', () => {
      panelContainer.scrollTo({ left: panelContainer.offsetWidth, behavior: 'smooth' });
    });

    const tabObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach(entry => {
          if (entry.intersectionRatio >= 0.5) {
            const isTopDaily = entry.target.id === 'panel-top-daily';
            tabBtnForYou.classList.toggle('active', !isTopDaily);
            tabBtnTopDaily.classList.toggle('active', isTopDaily);
            localStorage.setItem('rh_active_tab', isTopDaily ? 'top-daily' : 'for-you');
            if (isTopDaily) loadTopDaily();
          }
        });
      },
      { threshold: 0.5, root: panelContainer }
    );

    tabObserver.observe(document.getElementById('panel-for-you'));
    tabObserver.observe(document.getElementById('panel-top-daily'));

    if (localStorage.getItem('rh_active_tab') === 'top-daily') {
      tabBtnForYou.classList.remove('active');
      tabBtnTopDaily.classList.add('active');
      loadTopDaily();
      setTimeout(() => {
        panelContainer.style.scrollBehavior = 'auto';
        panelContainer.scrollLeft = panelContainer.offsetWidth;
        panelContainer.style.scrollBehavior = '';
      }, 0);
    }
  }

  // ── Refresh button ──────────────────────────────────────────────────────────
  const refreshBtn = document.getElementById('refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => doRefresh(db, refreshBtn));
  }

  // ── Clear data modal ────────────────────────────────────────────────────────
  const clearModal     = document.getElementById('clear-modal');
  const clearOpenBtn   = document.getElementById('clear-data-btn');
  const clearCancelBtn = document.getElementById('clear-cancel-btn');
  const clearConfirmBtn = document.getElementById('clear-confirm-btn');

  if (clearModal && clearOpenBtn) {
    clearOpenBtn.addEventListener('click', () => clearModal.classList.add('open'));
    clearCancelBtn.addEventListener('click', () => clearModal.classList.remove('open'));
    clearModal.addEventListener('click', (e) => {
      if (e.target === clearModal) clearModal.classList.remove('open');
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') clearModal.classList.remove('open');
    });

    clearConfirmBtn.addEventListener('click', async () => {
      clearConfirmBtn.disabled = true;
      clearConfirmBtn.textContent = 'Clearing…';
      try {
        // 1. Delete the httpOnly signed cookie via the server.
        await fetch('/api/reset', { method: 'POST' });
        // 2. Close the open connection so deleteDatabase isn't blocked by this tab.
        db.close();
        // 3. Wipe IndexedDB entirely.
        await new Promise((resolve, reject) => {
          const req = indexedDB.deleteDatabase('rabbit-hole');
          req.onsuccess = resolve;
          req.onerror   = () => reject(req.error);
        });
        // 3. Return to onboarding.
        window.location.href = '/onboarding';
      } catch (err) {
        console.error('Clear failed:', err);
        clearConfirmBtn.disabled = false;
        clearConfirmBtn.textContent = 'Clear everything';
      }
    });
  }
}

// ── Onboarding ────────────────────────────────────────────────────────────────

const onboardForm = document.getElementById('onboard-form');
if (onboardForm) {
  document.getElementById('timezone-input').value =
    Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

  onboardForm.addEventListener('submit', (e) => {
    const nCats   = onboardForm.querySelectorAll('input[name="selected_categories"]:checked').length;
    const nCustom = onboardForm.querySelectorAll('input[name="custom_interests"]').length;
    if (nCats + nCustom < 1) {
      e.preventDefault();
      document.getElementById('validation-msg').style.display = 'block';
    }
  });
}

// ── Article page ──────────────────────────────────────────────────────────────

const articleMeta = document.getElementById('article-meta');
if (articleMeta) {
  const canonical  = articleMeta.dataset.title;
  const urlParams  = new URLSearchParams(window.location.search);
  const fromTitle  = urlParams.get('from')?.replace(/_/g, ' ') ?? null;

  // Mirror the server's reading-history and seen-articles update into IndexedDB.
  await patchProfile(db, (p) => {
    if (!p.seen_articles.includes(canonical)) {
      p.seen_articles.push(canonical);
    }
    if (!p.reading_history.some(e => e.title === canonical)) {
      p.reading_history.push({ title: canonical, rating: null, scroll_depth: 0, source: fromTitle });
    }
    return p;
  });

  // ── One-time rating hint ────────────────────────────────────────────────────
  const ratingHint = document.getElementById('rh-rating-hint');
  if (ratingHint && !localStorage.getItem('rh_rating_hint')) {
    ratingHint.style.display = 'flex';
    document.getElementById('rh-hint-dismiss').addEventListener('click', () => {
      ratingHint.style.display = 'none';
      localStorage.setItem('rh_rating_hint', '1');
      const tw = document.getElementById('thumbs-widget');
      if (tw) {
        tw.style.transition = 'transform 0.2s ease, opacity 0.2s ease';
        tw.style.transform  = 'scale(1.4)';
        tw.style.opacity    = '1';
        setTimeout(() => { tw.style.transform = 'scale(1)'; }, 300);
      }
    });
  }

  // ── Thumbs rating widget ────────────────────────────────────────────────────
  const thumbsWidget = document.getElementById('thumbs-widget');
  if (thumbsWidget) {
    function renderThumbs(selected) {
      thumbsWidget.querySelectorAll('.thumb').forEach(btn => {
        btn.style.opacity = (parseInt(btn.dataset.rating) === selected) ? '1' : '0.35';
      });
    }

    thumbsWidget.querySelectorAll('.thumb').forEach(btn => {
      btn.addEventListener('click', async () => {
        const rating = parseInt(btn.dataset.rating);
        renderThumbs(rating);

        // Optimistic IndexedDB update.
        await patchProfile(db, (p) => {
          const entry = p.reading_history.find(e => e.title === canonical);
          if (entry) entry.rating = rating;
          else p.reading_history.push({ title: canonical, rating, scroll_depth: 0, source: null });
          return p;
        });

        // Persist to signed cookie via server.
        fetch('/api/feedback', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: canonical, rating }),
        }).catch(err => console.error('Feedback failed:', err));
      });
    });
  }

  // ── Scroll depth tracking ───────────────────────────────────────────────────
  const milestones = [25, 50, 75, 100];
  const reached    = new Set();

  function getScrollPercent() {
    const el = document.documentElement;
    const scrollTop    = el.scrollTop || document.body.scrollTop;
    const scrollHeight = el.scrollHeight - el.clientHeight;
    return scrollHeight > 0 ? Math.round((scrollTop / scrollHeight) * 100) : 100;
  }

  window.addEventListener('scroll', async () => {
    const pct = getScrollPercent();
    for (const milestone of milestones) {
      if (pct >= milestone && !reached.has(milestone)) {
        reached.add(milestone);

        // Optimistic IndexedDB update.
        await patchProfile(db, (p) => {
          const entry = p.reading_history.find(e => e.title === canonical);
          if (entry) entry.scroll_depth = Math.max(entry.scroll_depth, milestone);
          else p.reading_history.push({ title: canonical, rating: null, scroll_depth: milestone, source: null });
          return p;
        });

        // Persist to signed cookie via server.
        fetch('/api/scroll', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: canonical, depth: milestone }),
        }).catch(err => console.error('Scroll ping failed:', err));
      }
    }
  }, { passive: true });

  window.dispatchEvent(new Event('scroll'));

  // ── Wikipedia collapsibles ──────────────────────────────────────────────────
  document.querySelectorAll('.mw-collapsible').forEach(el => {
    const toggle  = el.querySelector('.mw-collapsible-toggle');
    const content = el.querySelector('.mw-collapsible-content');
    if (!toggle || !content) return;
    toggle.style.cursor = 'pointer';
    if (el.classList.contains('mw-collapsed')) content.style.display = 'none';
    toggle.addEventListener('click', () => {
      const hidden = content.style.display === 'none';
      content.style.display = hidden ? '' : 'none';
      el.classList.toggle('mw-collapsed', !hidden);
    });
  });
}
