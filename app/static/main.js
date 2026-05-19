// Feed page: reload when restored from browser back/forward cache so history is always fresh
if (document.getElementById('feed')) {
  window.addEventListener('pageshow', (e) => {
    if (e.persisted) window.location.reload();
  });
}

// Feed page: tab switching with scroll-snap + swipe support
const panelContainer = document.getElementById('panel-container');
const tabBtnForYou = document.getElementById('tab-btn-for-you');
const tabBtnTopDaily = document.getElementById('tab-btn-top-daily');

if (panelContainer && tabBtnForYou) {
  let topDailyLoaded = false;

  function makeCard(a) {
    return `<a href="/article/${encodeURIComponent(a.title)}" class="article-card">` +
           `<h2>${a.title}</h2>` +
           `<p>${a.extract.slice(0, 200)}${a.extract.length > 200 ? '…' : ''}</p>` +
           `</a>`;
  }

  async function loadTopDaily() {
    if (topDailyLoaded) return;
    const grid = document.getElementById('feed-top-daily');
    try {
      const res = await fetch('/api/top-daily');
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      grid.innerHTML = data.articles.length
        ? data.articles.map(makeCard).join('')
        : '<p class="grid-message">Top articles unavailable right now.</p>';
      topDailyLoaded = true;
    } catch (err) {
      grid.innerHTML = '<p class="grid-message">Failed to load top articles.</p>';
      console.error('Top daily load failed:', err);
    }
  }

  // Tab buttons scroll the container to the right panel
  tabBtnForYou.addEventListener('click', () => {
    panelContainer.scrollTo({ left: 0, behavior: 'smooth' });
  });

  tabBtnTopDaily.addEventListener('click', () => {
    panelContainer.scrollTo({ left: panelContainer.offsetWidth, behavior: 'smooth' });
  });

  // IntersectionObserver updates the active button, saves state, and triggers lazy-load
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

  // Restore the last active tab when returning from an article page.
  // Uses setTimeout so the panel container is sized before we set scrollLeft.
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

// Onboarding: detect and inject timezone before form submit
const onboardForm = document.getElementById('onboard-form');
if (onboardForm) {
  const tzInput = document.getElementById('timezone-input');
  tzInput.value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

  onboardForm.addEventListener('submit', (e) => {
    const nCats = onboardForm.querySelectorAll('input[name="selected_categories"]:checked').length;
    const nCustom = onboardForm.querySelectorAll('input[name="custom_interests"]').length;
    if (nCats + nCustom < 1) {
      e.preventDefault();
      document.getElementById('validation-msg').style.display = 'block';
    }
  });
}

// Feed: refresh button calls POST /api/refresh and re-renders cards
const refreshBtn = document.getElementById('refresh-btn');
if (refreshBtn) {
  async function doRefresh() {
    refreshBtn.disabled = true;
    refreshBtn.textContent = 'Loading…';
    try {
      const res = await fetch('/api/refresh', { method: 'POST' });
      if (!res.ok) throw new Error(res.statusText);
      const data = await res.json();
      const feed = document.getElementById('feed');
      feed.innerHTML = data.articles.map(a =>
        `<a href="/article/${encodeURIComponent(a.title)}" class="article-card">` +
        `<h2>${a.title}</h2>` +
        `<p>${a.extract.slice(0, 200)}${a.extract.length > 200 ? '…' : ''}</p>` +
        `</a>`
      ).join('');
    } catch (err) {
      console.error('Refresh failed:', err);
    } finally {
      refreshBtn.disabled = false;
      refreshBtn.textContent = 'Refresh ↺';
    }
  }

  refreshBtn.addEventListener('click', doRefresh);

  // On a daily reset the server serves stale articles immediately and sets this flag.
  // Auto-trigger so the new feed swaps in without requiring a manual click.
  const feed = document.getElementById('feed');
  if (feed && feed.dataset.autoRefresh) doRefresh();
}

// Article page: show one-time rating hint on first visit
const ratingHint = document.getElementById('rh-rating-hint');
if (ratingHint && !localStorage.getItem('rh_rating_hint')) {
  ratingHint.style.display = 'flex';
  document.getElementById('rh-hint-dismiss').addEventListener('click', () => {
    ratingHint.style.display = 'none';
    localStorage.setItem('rh_rating_hint', '1');
    // Briefly pulse the thumbs widget so the user knows where to look
    const tw = document.getElementById('thumbs-widget');
    if (tw) {
      tw.style.transition = 'transform 0.2s ease, opacity 0.2s ease';
      tw.style.transform = 'scale(1.4)';
      tw.style.opacity = '1';
      setTimeout(() => { tw.style.transform = 'scale(1)'; }, 300);
    }
  });
}

// Article page: thumbs rating widget (👍 👎 → stored as 5, 1)
const thumbsWidget = document.getElementById('thumbs-widget');
if (thumbsWidget) {
  const articleTitle = document.getElementById('article-meta').dataset.title;
  let savedRating = parseInt(thumbsWidget.dataset.saved) || null;

  function renderThumbs(selected) {
    thumbsWidget.querySelectorAll('.thumb').forEach(btn => {
      btn.style.opacity = (parseInt(btn.dataset.rating) === selected) ? '1' : '0.35';
    });
  }

  thumbsWidget.querySelectorAll('.thumb').forEach(btn => {
    btn.addEventListener('click', async () => {
      const rating = parseInt(btn.dataset.rating);
      savedRating = rating;
      renderThumbs(rating);
      try {
        await fetch('/api/feedback', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: articleTitle, rating }),
        });
      } catch (err) {
        console.error('Feedback failed:', err);
      }
    });
  });
}

// Article page: scroll depth milestone tracking
const articleMeta = document.getElementById('article-meta');
if (articleMeta) {
  const articleTitle = articleMeta.dataset.title;
  const milestones = [25, 50, 75, 100];
  const reached = new Set();

  function getScrollPercent() {
    const el = document.documentElement;
    const scrollTop = el.scrollTop || document.body.scrollTop;
    const scrollHeight = el.scrollHeight - el.clientHeight;
    return scrollHeight > 0 ? Math.round((scrollTop / scrollHeight) * 100) : 100;
  }

  window.addEventListener('scroll', () => {
    const pct = getScrollPercent();
    for (const milestone of milestones) {
      if (pct >= milestone && !reached.has(milestone)) {
        reached.add(milestone);
        fetch('/api/scroll', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: articleTitle, depth: milestone }),
        }).catch(err => console.error('Scroll ping failed:', err));
      }
    }
  }, { passive: true });

  // Fire immediately in case the page is short enough to be fully visible
  window.dispatchEvent(new Event('scroll'));

  // Wikipedia collapsible elements (quickfacts, hatnotes, etc.)
  // These rely on Wikipedia's own JS which we strip — wire them up natively.
  document.querySelectorAll('.mw-collapsible').forEach(el => {
    const toggle = el.querySelector('.mw-collapsible-toggle');
    const content = el.querySelector('.mw-collapsible-content');
    if (!toggle || !content) return;
    toggle.style.cursor = 'pointer';
    // Start collapsed if Wikipedia marked it that way
    if (el.classList.contains('mw-collapsed')) {
      content.style.display = 'none';
    }
    toggle.addEventListener('click', () => {
      const hidden = content.style.display === 'none';
      content.style.display = hidden ? '' : 'none';
      el.classList.toggle('mw-collapsed', !hidden);
    });
  });
}
