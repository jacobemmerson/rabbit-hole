import { getProfile, patchProfile, bulkGetSummaries, getBanditState, setBanditState, setPhiMap, getLearnedQueries, setLearnedQueries } from './db.js';
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
  const historyLen = (profile.reading_history ?? []).length;
  const params = new URLSearchParams({ history_len: historyLen });
  categories.forEach(c => params.append('categories[]', c));
  custom.forEach(c => params.append('custom[]', c));

  let groups = [];
  try {
    const res = await fetch(`/api/seed-queries?${params}`);
    groups = (await res.json()).groups ?? [];
  } catch { return; }

  // 2. Wikipedia searches from the user's network (parallel, IndexedDB-cached).
  const seen = new Set([
    ...(profile.seen_articles   ?? []),
    ...(profile.shown_articles  ?? []),
    ...(profile.current_articles ?? []),
  ]);

  // Load learned seed queries before the guard so they count as a valid source.
  const learnedData    = await getLearnedQueries(db);
  const learnedQueries = learnedData?.queries ?? [];
  const hasHistory     = (profile.reading_history ?? []).length > 0;

  // Exit only when all three candidate sources are empty — seed groups can be
  // empty once the taper kicks in (history_len ≥ 8) while learned queries and
  // history searches still provide candidates.
  if (!groups.length && !learnedQueries.length && !hasHistory) return;

  // Include history-based searches so the bandit can learn the from_history signal.
  const historySearchJobs = (profile.reading_history ?? []).map(e =>
    search(e.title, db).then(titles => ({ group: '__history__', titles, query: e.title }))
  );

  const [seedResults, historyResults, learnedResults] = await Promise.all([
    Promise.all(groups.flatMap(g =>
      g.queries.map(q => search(q, db).then(titles => ({ group: g.key, titles, query: q })))
    )),
    Promise.all(historySearchJobs),
    Promise.all(learnedQueries.map(q =>
      search(q, db).then(titles => ({ group: '__learned__', titles, query: q }))
    )),
  ]);
  const searchResults = [...seedResults, ...historyResults, ...learnedResults];

  // 3. Deduplicate into per-group buckets; track which learned query found each article.
  const groupBuckets = {};
  const titleToQuery = {};  // article title → originating learned query (reward signal)
  const allocated = new Set(seen);

  for (const { group, titles, query } of searchResults) {
    if (!groupBuckets[group]) groupBuckets[group] = [];
    for (const title of titles) {
      if (_isValidArticle(title) && !allocated.has(title)) {
        groupBuckets[group].push(title);
        allocated.add(title);
        if (group === '__learned__') titleToQuery[title] = query;
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

  // 7. Server scores via embedding model + bandit (reads cookie for trusted profile).
  const banditState = await getBanditState(db);
  let rankedTitles = [];
  try {
    const res = await fetch('/api/score', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        candidates:       candidatesWithExtracts,
        history_extracts: historyExtracts,
        bandit_state:     banditState ?? null,
        n:                FEED_SIZE,
      }),
    });
    const data = await res.json();
    rankedTitles = data.titles ?? [];
    // Store feature vectors so the client can update the bandit when feedback arrives.
    if (data.phi_map)      await setPhiMap(db, data.phi_map);
    if (data.bandit_state) await setBanditState(db, data.bandit_state);
    if (data.seed_queries?.length) {
      await setLearnedQueries(db, { queries: data.seed_queries, titleToQuery });
    }
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
