import { openDB, getProfile, setProfile, patchProfile, bulkGetSummaries, getBanditState, setBanditState, getPhi } from './db.js';
import { topDailySummaries } from './wikipedia.js';
import { needsReset, generateFeed, doRefresh, makeCard, renderCards } from './feed.js';
import { renderDevPanel } from './dev.js';

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
      const newCats   = window.__RH_INIT__.selected_categories ?? p.selected_categories;
      const newCustom = window.__RH_INIT__.custom_interests    ?? p.custom_interests;
      // If the user's interests changed (e.g. re-onboarded), force a feed
      // regeneration by clearing last_generated so needsReset() returns true.
      const catsChanged   = JSON.stringify([...newCats].sort())   !== JSON.stringify([...(p.selected_categories ?? [])].sort());
      const customChanged = JSON.stringify(newCustom) !== JSON.stringify(p.custom_interests ?? []);
      p.timezone            = window.__RH_INIT__.timezone ?? p.timezone;
      p.selected_categories = newCats;
      p.custom_interests    = newCustom;
      p.reading_history     = window.__RH_INIT__.reading_history ?? p.reading_history;
      if (catsChanged || customChanged) p.last_generated = null;
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

  // Apply any pending bandit update stored synchronously during a pagehide.
  // This runs before feed generation so the updated θ is used immediately.
  const _pendingRaw = localStorage.getItem('rh_pending_bandit');
  if (_pendingRaw) {
    localStorage.removeItem('rh_pending_bandit');
    try {
      const _pending = JSON.parse(_pendingRaw);
      const _res = await fetch('/api/bandit/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(_pending),
      });
      const _data = await _res.json();
      if (_data.bandit_state) await setBanditState(db, _data.bandit_state);
    } catch {}
  }

  // Render cached articles instantly, then refresh if the day has rolled over.
  if (profile?.current_articles?.length) {
    const cached = await bulkGetSummaries(db, profile.current_articles);
    const ordered = profile.current_articles.map(t => cached.get(t)).filter(Boolean);
    if (ordered.length) renderCards(ordered);
  }

  if (needsReset(profile)) {
    await doRefresh(db, null);
  }

  // ── Dev panel toggle ────────────────────────────────────────────────────────
  const DEV_KEY  = 'rh_dev_mode';
  const isDevOn  = () => localStorage.getItem(DEV_KEY) === '1';
  const devBtn   = document.getElementById('dev-mode-btn');

  async function _showDevPanel() {
    try { await renderDevPanel(db); } catch (err) { console.error('Dev panel failed:', err); }
  }

  if (devBtn) {
    const _syncDevBtn = () => devBtn.classList.toggle('active', isDevOn());
    devBtn.addEventListener('click', async () => {
      localStorage.setItem(DEV_KEY, isDevOn() ? '0' : '1');
      if (isDevOn()) {
        await _showDevPanel();
      } else {
        document.getElementById('dev-panel')?.remove();
        localStorage.removeItem('rh_theta_prev');
      }
      _syncDevBtn();
    });
    _syncDevBtn();
  }

  if (isDevOn()) await _showDevPanel();

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
    refreshBtn.addEventListener('click', async () => {
      await doRefresh(db, refreshBtn);
      if (isDevOn()) await _showDevPanel();
    });
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
          // db.close() initiates the close but in-flight transactions may still be
          // running. onblocked fires if the connection isn't fully shut yet; resolving
          // here lets the delete proceed once those transactions drain.
          req.onblocked = resolve;
        });
        // 4. Wipe localStorage (article rewards, pending bandit update, dev state…).
        localStorage.clear();
        // 5. Return to onboarding.
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

  // ── Cumulative bandit reward (sent once on exit) ───────────────────────────
  // Pre-fetch phi and bandit state synchronously so they're available in the
  // pagehide handler, which cannot await async IDB reads.
  let _pendingReward     = 0.3;   // baseline click reward
  let _hasExplicitRating = false;
  let _updateSent        = false;
  const [_phi, _banditState] = await Promise.all([getPhi(db, canonical), getBanditState(db)]);

  // High-water reward for this article across all visits.  Guards against
  // repeated click-and-back inflating the bandit — we only send an update when
  // this visit produces a strictly stronger signal than any previous one.
  const _RW_KEY = 'rh_article_rewards';
  let _prevReward = -1;
  try {
    const _rw = JSON.parse(localStorage.getItem(_RW_KEY) || '{}');
    _prevReward = _rw[canonical] ?? -1;
  } catch {}

  // ── One-time rating hint ────────────────────────────────────────────────────
  const ratingHint = document.getElementById('rh-rating-hint');
  if (ratingHint && !localStorage.getItem('rh_rating_hint')) {
    ratingHint.style.display = 'flex';
    document.getElementById('rh-hint-dismiss')?.addEventListener('click', () => {
      ratingHint.style.display = 'none';
      localStorage.setItem('rh_rating_hint', '1');
      // Pulse the pill to draw attention to it after dismissing the hint.
      const pill = document.getElementById('thumbs-widget');
      if (pill) {
        pill.classList.add('pulse');
        setTimeout(() => pill.classList.remove('pulse'), 400);
      }
    });
  }

  // ── Thumbs rating widget ────────────────────────────────────────────────────
  const thumbsWidget = document.getElementById('thumbs-widget');
  if (thumbsWidget) {
    const indicator = thumbsWidget.querySelector('.pill-indicator');
    const toggleBtn = thumbsWidget.querySelector('.pill-toggle');

    function renderThumbs(selected) {
      thumbsWidget.querySelectorAll('.thumb').forEach(btn => {
        btn.style.opacity = (parseInt(btn.dataset.rating) === selected) ? '1' : '0.35';
      });
    }
    function minimizePill() { thumbsWidget.classList.add('minimized'); }
    function expandPill()   { thumbsWidget.classList.remove('minimized'); }

    // Seed indicator from any pre-existing rating (article revisit).
    const _savedRating = parseInt(thumbsWidget.dataset.saved || '0');
    if (_savedRating && indicator) indicator.textContent = _savedRating === 5 ? '👍' : '👎';

    // Show immediately — a rAF ensures the initial hidden state is painted first
    // so the slide-up transition actually plays.
    requestAnimationFrame(() => thumbsWidget.classList.add('visible'));

    // Toggle button: collapse ↔ expand.
    toggleBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      thumbsWidget.classList.contains('minimized') ? expandPill() : minimizePill();
    });

    // Tapping the pill while minimized expands it.
    thumbsWidget.addEventListener('click', () => {
      if (thumbsWidget.classList.contains('minimized')) expandPill();
    });

    thumbsWidget.querySelectorAll('.thumb').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();  // don't trigger the pill's expand handler
        const rating = parseInt(btn.dataset.rating);
        renderThumbs(rating);

        // Update indicator and auto-minimize after brief feedback pause.
        if (indicator) indicator.textContent = rating === 5 ? '👍' : '👎';
        setTimeout(minimizePill, 600);

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

        // Accumulate rating into pending reward — sent on exit.
        _pendingReward     = rating === 5 ? 1.0 : 0.0;
        _hasExplicitRating = true;
      });
    });
  }

  // ── Deferred bandit update — fires once when the user leaves the article ────
  window.addEventListener('pagehide', () => {
    if (!_phi || !_banditState || _updateSent) return;
    if (_pendingReward <= _prevReward) return;  // no stronger signal than a prior visit
    _updateSent = true;
    // Persist the new high-water reward before the page unloads.
    try {
      const _rw = JSON.parse(localStorage.getItem(_RW_KEY) || '{}');
      _rw[canonical] = _pendingReward;
      localStorage.setItem(_RW_KEY, JSON.stringify(_rw));
    } catch {}
    const payload = { phi: _phi, reward: _pendingReward, bandit_state: _banditState };
    // localStorage.setItem is synchronous — it survives page unload reliably,
    // unlike IDB writes.  The feed page picks this up on next load and applies
    // it before generating the feed so the updated θ is used immediately.
    try { localStorage.setItem('rh_pending_bandit', JSON.stringify(payload)); } catch {}
    // Also fire a keepalive fetch; if the response arrives before teardown,
    // IDB is updated and the localStorage entry is cleared (no double-apply).
    fetch('/api/bandit/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true,
    }).then(r => r.json()).then(data => {
      if (data.bandit_state) {
        setBanditState(db, data.bandit_state);
        localStorage.removeItem('rh_pending_bandit');
      }
    }).catch(() => {});
  });

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

        // Accumulate scroll reward — takes effect only if no explicit rating given.
        // Math.max keeps the 75 % peak (≈0.90) from being downgraded by the
        // 100 % milestone (≈0.67) if the user keeps scrolling.
        if (milestone >= 75 && !_hasExplicitRating) {
          _pendingReward = Math.max(_pendingReward, 0.65 + 0.25 * Math.exp(-0.1 * (milestone - 75)));
        }
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
