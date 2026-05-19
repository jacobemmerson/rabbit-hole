import { getProfile, patchProfile, bulkGetSummaries } from './db.js';
import { search, bulkSummaries } from './wikipedia.js';

const FEED_SIZE     = 8;
const MAX_CANDIDATES = 30;
const META_PREFIXES  = ['Outline of ', 'List of ', 'Index of ', 'Lists of ', 'Glossary of '];

// ── Date helpers (replaces server-side reset.py) ──────────────────────────────

export function getLocalDate(timezone) {
  try {
    // en-CA locale returns YYYY-MM-DD which matches the server's ISO format.
    return new Intl.DateTimeFormat('en-CA', { timeZone: timezone }).format(new Date());
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

export function needsReset(profile) {
  if (!profile?.last_generated) return true;
  return getLocalDate(profile.timezone ?? 'UTC') !== profile.last_generated;
}

// ── Rendering ─────────────────────────────────────────────────────────────────

export function makeCard(a) {
  return `<a href="/article/${encodeURIComponent(a.title)}" class="article-card">` +
         `<h2>${a.title}</h2>` +
         `<p>${a.extract.slice(0, 200)}${a.extract.length > 200 ? '…' : ''}</p>` +
         `</a>`;
}

export function renderCards(articles) {
  const feed = document.getElementById('feed');
  if (!feed) return;
  feed.innerHTML = articles.length
    ? articles.map(makeCard).join('')
    : '<p class="grid-message">No articles available right now.</p>';
}

function _isValidArticle(title) {
  return !META_PREFIXES.some(p => title.startsWith(p));
}

// ── Feed generation ───────────────────────────────────────────────────────────

export async function generateFeed(db) {
  const profile = await getProfile(db);
  if (!profile) return;

  const categories = profile.selected_categories ?? [];
  const custom     = profile.custom_interests    ?? [];

  // 1. Fetch today's seed queries from the server (pure computation, no Wikipedia I/O).
  const params = new URLSearchParams();
  categories.forEach(c => params.append('categories[]', c));
  custom.forEach(c => params.append('custom[]', c));

  let groups = [];
  try {
    const res = await fetch(`/api/seed-queries?${params}`);
    groups = (await res.json()).groups ?? [];
  } catch { return; }

  if (!groups.length) return;

  // 2. Wikipedia searches from the user's network (parallel, IndexedDB-cached).
  const seen = new Set([
    ...(profile.seen_articles   ?? []),
    ...(profile.shown_articles  ?? []),
    ...(profile.current_articles ?? []),
  ]);

  const searchJobs = groups.flatMap(g =>
    g.queries.map(q => search(q, db).then(titles => ({ group: g.key, titles })))
  );
  const searchResults = await Promise.all(searchJobs);

  // 3. Deduplicate into per-group buckets.
  const groupBuckets = {};
  const allocated = new Set(seen);

  for (const { group, titles } of searchResults) {
    if (!groupBuckets[group]) groupBuckets[group] = [];
    for (const title of titles) {
      if (_isValidArticle(title) && !allocated.has(title)) {
        groupBuckets[group].push(title);
        allocated.add(title);
      }
    }
  }

  // 4. Flatten to at most MAX_CANDIDATES, preserving group tag.
  const allCandidates = Object.entries(groupBuckets)
    .flatMap(([group, titles]) => titles.map(t => ({ title: t, group })))
    .slice(0, MAX_CANDIDATES);

  if (!allCandidates.length) return;

  // 5. Fetch extracts for candidates (from user's network, IndexedDB-cached).
  const candidateSummaries = await bulkSummaries(allCandidates.map(c => c.title), db);
  const summaryMap = new Map(candidateSummaries.map(s => [s.title, s]));

  const candidatesWithExtracts = allCandidates
    .filter(c => summaryMap.get(c.title)?.extract)
    .map(c => ({ title: c.title, extract: summaryMap.get(c.title).extract, group: c.group }));

  if (!candidatesWithExtracts.length) return;

  // 6. Fetch reading-history extracts from IndexedDB so the server can embed them.
  const historyTitles = (profile.reading_history ?? []).map(e => e.title);
  const historyMap    = await bulkGetSummaries(db, historyTitles);
  const historyExtracts = {};
  for (const [title, s] of historyMap) {
    if (s.extract) historyExtracts[title] = s.extract;
  }

  // 7. Server scores via embedding model (reads cookie for trusted rating/scroll weights).
  let rankedTitles = [];
  try {
    const res = await fetch('/api/score', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ candidates: candidatesWithExtracts, history_extracts: historyExtracts, n: FEED_SIZE }),
    });
    rankedTitles = (await res.json()).titles ?? [];
  } catch {
    // Fallback: take first FEED_SIZE candidates by group order.
    rankedTitles = candidatesWithExtracts.slice(0, FEED_SIZE).map(c => c.title);
  }

  if (!rankedTitles.length) return;

  // 8. Fetch final summaries (mostly cache hits) and render in server-ranked order.
  // bulkSummaries does not preserve input order, so re-sort by rankedTitles.
  const finalSummaries = await bulkSummaries(rankedTitles, db);
  const finalMap = new Map(finalSummaries.map(s => [s.title, s]));
  renderCards(rankedTitles.map(t => finalMap.get(t)).filter(Boolean));

  // 9. Persist new feed state to IndexedDB.
  const today = getLocalDate(profile.timezone ?? 'UTC');
  await patchProfile(db, (p) => {
    p.current_articles = rankedTitles;
    p.last_generated   = today;
    const newShown = [...new Set([...(p.shown_articles ?? []), ...rankedTitles])];
    p.shown_articles   = newShown.slice(-42);
    return p;
  });
}

// ── Refresh (archive current → shown, then regenerate) ────────────────────────

export async function doRefresh(db, refreshBtn) {
  if (refreshBtn) { refreshBtn.disabled = true; refreshBtn.textContent = 'Loading…'; }
  try {
    await patchProfile(db, (p) => {
      const newShown = [...new Set([...(p.shown_articles ?? []), ...(p.current_articles ?? [])])];
      p.shown_articles  = newShown.slice(-42);
      p.current_articles = [];
      return p;
    });
    await generateFeed(db);
  } catch (err) {
    console.error('Refresh failed:', err);
  } finally {
    if (refreshBtn) { refreshBtn.disabled = false; refreshBtn.textContent = 'Refresh ↺'; }
  }
}
