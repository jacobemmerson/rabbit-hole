import { getProfile, getBanditState, getPhi, getLearnedQueries } from './db.js';

// φ dimension labels and accent colours
const PHI_LABELS = ['sim₀','sim₁','sim₂','sim₃','sim₄','pop','cat','hist','bias'];
const PHI_COLORS = {
  sim:  '#6366f1',
  pop:  '#f59e0b',
  cat:  '#10b981',
  hist: '#ec4899',
  bias: '#94a3b8',
};

function _color(idx) {
  if (idx <= 4) return PHI_COLORS.sim;
  if (idx === 5) return PHI_COLORS.pop;
  if (idx === 6) return PHI_COLORS.cat;
  if (idx === 7) return PHI_COLORS.hist;
  return PHI_COLORS.bias;
}

function _e(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function _pct(val, max) {
  return max > 0 ? Math.round(Math.min(Math.abs(val) / max, 1) * 100) : 0;
}

// ── CSS injected once ─────────────────────────────────────────────────────────

function _injectStyles() {
  if (document.getElementById('rh-dev-styles')) return;
  const s = document.createElement('style');
  s.id = 'rh-dev-styles';
  s.textContent = `
    #dev-panel {
      margin-top: 1.5rem;
      border: 1.5px solid #e5e7eb;
      border-radius: 12px;
      background: #fff;
      font-size: .8rem;
      overflow: hidden;
    }
    .dev-header {
      display: flex;
      align-items: center;
      gap: .6rem;
      padding: .6rem .9rem;
      background: #f9f9f7;
      border-bottom: 1.5px solid #e5e7eb;
      cursor: pointer;
      user-select: none;
    }
    .dev-badge {
      font-size: .65rem;
      font-weight: 700;
      letter-spacing: .1em;
      color: #fff;
      background: #6366f1;
      border-radius: 4px;
      padding: .1rem .4rem;
    }
    .dev-meta {
      flex: 1;
      color: #9ca3af;
      font-family: monospace;
      font-size: .75rem;
    }
    .dev-toggle-btn {
      font-size: .75rem;
      color: #9ca3af;
      background: none;
      border: none;
      cursor: pointer;
      font-family: inherit;
      padding: 0;
    }
    .dev-body { padding: .75rem .9rem; display: flex; flex-direction: column; gap: 1rem; }
    .dev-body.collapsed { display: none; }
    .dev-section-label {
      font-size: .65rem;
      font-weight: 700;
      letter-spacing: .1em;
      text-transform: uppercase;
      color: #c4c4c4;
      margin-bottom: .5rem;
    }
    /* θ weight bars */
    .dev-theta-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: .25rem .75rem;
    }
    .dev-theta-row {
      display: grid;
      grid-template-columns: 2.5rem 1fr auto;
      align-items: center;
      gap: .35rem;
    }
    .dev-dim-label {
      font-family: monospace;
      font-size: .75rem;
      color: #6b7280;
      text-align: right;
    }
    .dev-bar-track {
      height: 6px;
      background: #f3f4f6;
      border-radius: 3px;
      overflow: hidden;
      position: relative;
    }
    .dev-bar {
      height: 100%;
      border-radius: 3px;
      transition: width .3s ease;
    }
    .dev-dim-val {
      font-family: monospace;
      font-size: .72rem;
      color: #374151;
    }
    /* Seed queries */
    .dev-query-group { margin-bottom: .3rem; }
    .dev-query-key {
      font-family: monospace;
      font-size: .72rem;
      color: #9ca3af;
      display: inline-block;
      width: 6rem;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      vertical-align: top;
    }
    .dev-query-pill {
      display: inline-block;
      background: #eff6ff;
      color: #2563eb;
      border-radius: 4px;
      padding: .05rem .35rem;
      margin: .1rem .15rem .1rem 0;
      font-family: monospace;
      font-size: .72rem;
    }
    .dev-query-pill.learned {
      background: #f0fdf4;
      color: #16a34a;
    }
    /* Feed score rows */
    .dev-score-row {
      display: grid;
      grid-template-columns: 1.2rem 1fr 3.5rem auto;
      align-items: center;
      gap: .4rem;
      padding: .3rem 0;
      border-bottom: 1px solid #f3f4f6;
    }
    .dev-score-row:last-child { border-bottom: none; }
    .dev-rank {
      font-family: monospace;
      font-size: .7rem;
      color: #d1d5db;
      text-align: right;
    }
    .dev-article-title {
      font-size: .78rem;
      color: #374151;
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .dev-total-score {
      font-family: monospace;
      font-size: .72rem;
      color: #374151;
      text-align: right;
    }
    .dev-contrib-bar {
      display: flex;
      height: 8px;
      border-radius: 4px;
      overflow: hidden;
      width: 80px;
      flex-shrink: 0;
      background: #f3f4f6;
    }
    .dev-contrib-seg { height: 100%; }
  `;
  document.head.appendChild(s);
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function renderDevPanel(db) {
  _injectStyles();

  const [profile, bandit, learnedData] = await Promise.all([
    getProfile(db),
    getBanditState(db),
    getLearnedQueries(db),
  ]);

  const currentArticles = profile?.current_articles ?? [];
  const phis = Object.fromEntries(
    await Promise.all(currentArticles.map(async t => [t, await getPhi(db, t)]))
  );

  // Seed queries from server — pass history_len so the dev panel reflects
  // the actual tapered set being used, not the full cold-start set.
  const categories = profile?.selected_categories ?? [];
  const custom = (profile?.custom_interests ?? []).filter(i => i.trim() !== 'dev');
  const historyLen = (profile?.reading_history ?? []).length;
  const params = new URLSearchParams({ history_len: historyLen });
  categories.forEach(c => params.append('categories[]', c));
  custom.forEach(c => params.append('custom[]', c));
  let seedGroups = [];
  try {
    const res = await fetch(`/api/seed-queries?${params}`);
    seedGroups = (await res.json()).groups ?? [];
  } catch { /* leave empty */ }

  const theta = bandit?.theta ?? new Array(9).fill(0);
  const nUpdates = bandit?.n_updates ?? 0;
  const learnedQueries = learnedData?.queries ?? [];

  // θ delta: compare to snapshot from the previous render.
  let prevTheta = null;
  try {
    const raw = localStorage.getItem('rh_theta_prev');
    if (raw) prevTheta = JSON.parse(raw);
  } catch {}
  try { localStorage.setItem('rh_theta_prev', JSON.stringify(theta)); } catch {}

  // Per-article score breakdown
  const articleRows = currentArticles.map((title, i) => {
    const phi = phis[title];
    if (!phi) return null;
    const score = phi.reduce((s, v, j) => s + v * (theta[j] ?? 0), 0);
    const contributions = phi.map((v, j) => v * (theta[j] ?? 0));
    return { title, rank: i + 1, score, phi, contributions };
  }).filter(Boolean);

  // ── Render ────────────────────────────────────────────────────────────────
  let el = document.getElementById('dev-panel');
  if (!el) {
    el = document.createElement('div');
    el.id = 'dev-panel';
    const container = document.getElementById('panel-for-you');
    if (container) container.appendChild(el);
  }

  const maxTheta = Math.max(...theta.map(Math.abs), 0.001);

  // θ bar rows
  const thetaRows = theta.map((w, i) => {
    const pct   = _pct(w, maxTheta);
    const col   = _color(i);
    const sign  = w < 0 ? ' style="opacity:.5"' : '';
    const delta = prevTheta ? w - (prevTheta[i] ?? 0) : null;
    const deltaHtml = (delta !== null && Math.abs(delta) >= 0.0005)
      ? `<span style="color:${delta > 0 ? '#16a34a' : '#dc2626'};font-size:.68rem;margin-left:.2rem;">${delta > 0 ? '+' : ''}${delta.toFixed(3)}</span>`
      : '';
    return `
      <div class="dev-theta-row">
        <span class="dev-dim-label">${_e(PHI_LABELS[i])}</span>
        <div class="dev-bar-track">
          <div class="dev-bar" style="width:${pct}%;background:${col};"${sign}></div>
        </div>
        <span class="dev-dim-val">${w.toFixed(3)}${deltaHtml}</span>
      </div>`;
  }).join('');

  // Seed query pills
  const seedHtml = [
    ...seedGroups.map(g => {
      const pills = g.queries.map(q => `<span class="dev-query-pill">${_e(q)}</span>`).join('');
      const key = g.key.startsWith('__custom__') ? `custom` : _e(g.key);
      return `<div class="dev-query-group"><span class="dev-query-key">${key}</span>${pills}</div>`;
    }),
    learnedQueries.length ? `
      <div class="dev-query-group">
        <span class="dev-query-key">learned</span>
        ${learnedQueries.map(q => `<span class="dev-query-pill learned">${_e(q)}</span>`).join('')}
      </div>` : '',
  ].join('');

  // Article score rows
  const maxContrib = Math.max(
    ...articleRows.flatMap(r => r.contributions.map(Math.abs)), 0.001
  );

  const scoreRows = articleRows.map(r => {
    const segs = r.contributions.map((c, i) => {
      if (Math.abs(c) < 0.001) return '';
      const w = _pct(c, maxContrib);
      const col = c < 0 ? '#fca5a5' : _color(i);
      return `<div class="dev-contrib-seg" title="${_e(PHI_LABELS[i])}: ${c.toFixed(3)}" style="width:${w}%;background:${col};flex-shrink:0;"></div>`;
    }).join('');

    return `
      <div class="dev-score-row">
        <span class="dev-rank">${r.rank}</span>
        <span class="dev-article-title" title="${_e(r.title)}">${_e(r.title)}</span>
        <span class="dev-total-score">${r.score.toFixed(3)}</span>
        <div class="dev-contrib-bar">${segs}</div>
      </div>`;
  }).join('');

  const legendItems = [
    ['sim₀-₄', PHI_COLORS.sim],
    ['pop',    PHI_COLORS.pop],
    ['cat',    PHI_COLORS.cat],
    ['hist',   PHI_COLORS.hist],
    ['bias',   PHI_COLORS.bias],
  ].map(([label, col]) =>
    `<span style="display:inline-flex;align-items:center;gap:.2rem;margin-right:.5rem;">
       <span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${col};"></span>
       <span style="font-family:monospace;font-size:.68rem;color:#9ca3af;">${label}</span>
     </span>`
  ).join('');

  el.innerHTML = `
    <div class="dev-header" id="dev-header">
      <span class="dev-badge">DEV</span>
      <span class="dev-meta">updates: ${nUpdates} · history: ${historyLen} · θ·φ dims: 9</span>
      <button class="dev-toggle-btn" id="dev-toggle">▾</button>
    </div>
    <div class="dev-body" id="dev-body">

      <div>
        <div class="dev-section-label">Bandit θ weights</div>
        <div class="dev-theta-grid">${thetaRows}</div>
      </div>

      <div>
        <div class="dev-section-label">Seed queries</div>
        ${seedHtml || '<span style="color:#d1d5db;font-family:monospace;">none</span>'}
      </div>

      <div>
        <div class="dev-section-label" style="margin-bottom:.35rem;">
          Feed scores
          <span style="font-weight:400;margin-left:.5rem;">${legendItems}</span>
        </div>
        ${scoreRows || '<span style="color:#d1d5db;font-family:monospace;">no scored articles</span>'}
      </div>

    </div>
  `;

  // Collapse toggle
  const header = document.getElementById('dev-header');
  const body   = document.getElementById('dev-body');
  const btn    = document.getElementById('dev-toggle');
  const COLLAPSED_KEY = 'rh_dev_collapsed';

  function applyCollapse(collapsed) {
    body.classList.toggle('collapsed', collapsed);
    btn.textContent = collapsed ? '▸' : '▾';
  }

  applyCollapse(localStorage.getItem(COLLAPSED_KEY) === '1');

  header.addEventListener('click', () => {
    const next = !body.classList.contains('collapsed');
    applyCollapse(next);
    localStorage.setItem(COLLAPSED_KEY, next ? '1' : '0');
  });
}
