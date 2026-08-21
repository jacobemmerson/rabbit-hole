import { getCandidate, setCandidate, bulkGetSummaries, bulkSetSummaries } from './db.js';

const ACTION_URL    = 'https://en.wikipedia.org/w/api.php';
const PAGEVIEWS_URL = 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access';

const META_PREFIXES = ['Outline of ', 'List of ', 'Index of ', 'Lists of ', 'Glossary of '];
const SKIP_PREFIXES = [
  'Main Page', 'Special:', 'Wikipedia:', 'Help:', 'File:',
  'Portal:', 'Talk:', 'User:', 'Template:', 'Category:',
];

// ── Search ────────────────────────────────────────────────────────────────────

export async function search(query, db) {
  const cacheKey = `search:${query}`;
  const cached = await getCandidate(db, cacheKey);
  if (cached) return cached.titles;

  const params = new URLSearchParams({
    action: 'query', list: 'search', srsearch: query,
    srlimit: '10', srnamespace: '0', format: 'json', origin: '*',
  });
  try {
    const res  = await fetch(`${ACTION_URL}?${params}`);
    const data = await res.json();
    const titles = (data.query?.search ?? []).map(r => r.title);
    await setCandidate(db, cacheKey, titles);
    return titles;
  } catch {
    return [];
  }
}

// ── Summary batch fetch ───────────────────────────────────────────────────────

export async function bulkSummaries(titles, db) {
  if (!titles.length) return [];

  const cached   = await bulkGetSummaries(db, titles);
  const uncached = titles.filter(t => !cached.has(t));
  const results  = [...cached.values()];

  // prop=extracts caps at 20 extracts per request regardless of how many titles
  // are submitted (exlimit default = 20, max = 20). Batching at 20 ensures every
  // article in the batch gets its extract; larger batches silently drop the rest.
  for (let i = 0; i < uncached.length; i += 20) {
    const batch = uncached.slice(i, i + 20);
    const params = new URLSearchParams({
      action: 'query',
      titles: batch.join('|'),
      prop: 'extracts|pageimages|pageprops|info',
      exintro: '1', explaintext: '1', exlimit: 'max',
      // pilicense=any includes non-free lead images (album/film/book covers,
      // logos) that the default 'free' filter drops, leaving those cards blank.
      pithumbsize: '400', pilicense: 'any', inprop: 'url', redirects: '1',
      format: 'json', origin: '*',
    });

    try {
      const res   = await fetch(`${ACTION_URL}?${params}`);
      const data  = await res.json();
      const pages = Object.values(data.query?.pages ?? {});

      const good  = [];
      const empty = [];

      for (const page of pages) {
        if ('missing' in page) continue;
        if ('disambiguation' in (page.pageprops ?? {})) continue;
        const article = {
          title:        page.title,
          extract:      (page.extract ?? '').trim(),
          pageUrl:      page.fullurl ?? `https://en.wikipedia.org/wiki/${page.title.replace(/ /g, '_')}`,
          thumbnailUrl: page.thumbnail?.source ?? null,
        };
        (article.extract ? good : empty).push(article);
      }

      // Sentence fallback for empty extracts
      if (empty.length) {
        const fbParams = new URLSearchParams({
          action: 'query', titles: empty.map(a => a.title).join('|'),
          prop: 'extracts', exsentences: '3', explaintext: '1', exlimit: 'max',
          redirects: '1', format: 'json', origin: '*',
        });
        try {
          const fbRes  = await fetch(`${ACTION_URL}?${fbParams}`);
          const fbData = await fbRes.json();
          const fbMap  = {};
          for (const p of Object.values(fbData.query?.pages ?? {})) {
            if (!('missing' in p) && p.extract?.trim()) fbMap[p.title] = p.extract.trim();
          }
          for (const a of empty) {
            if (fbMap[a.title]) good.push({ ...a, extract: fbMap[a.title] });
          }
        } catch { /* keep what we have */ }
      }

      await bulkSetSummaries(db, good);

      // The action API follows redirects and returns canonical titles, which can differ
      // from the titles we submitted (from the Wikimedia pageviews API).
      // Parse query.redirects to build aliases under the original input titles so that
      // future cache lookups and the current summaryMap lookup both work.
      const canonToInput = new Map(
        (data.query?.redirects ?? []).map(r => [r.to, r.from])
      );
      const inputAliases = good
        .filter(a => canonToInput.has(a.title) && canonToInput.get(a.title) !== a.title)
        .map(a => ({ ...a, title: canonToInput.get(a.title) }));
      if (inputAliases.length) await bulkSetSummaries(db, inputAliases);

      results.push(...good, ...inputAliases);
    } catch { /* skip batch on network error */ }
  }

  return results;
}

// ── Image fallback ──────────────────────────────────────────────────────────────

// When pageimages returns no thumbnail even with pilicense=any (pages that have
// images but no designated "page image"), fall back to the page's first gallery
// image via the REST media-list endpoint.
export async function firstPageImage(title) {
  try {
    const slug = encodeURIComponent(title.replace(/ /g, '_'));
    const res = await fetch(`https://en.wikipedia.org/api/rest_v1/page/media-list/${slug}`);
    if (!res.ok) return null;
    const data = await res.json();
    const images = (data.items ?? []).filter(i => i.type === 'image' && i.srcset?.length);
    const lead = images.find(i => i.showInGallery) ?? images[0];
    if (!lead) return null;
    const src = lead.srcset[0].src;
    return src.startsWith('//') ? `https:${src}` : src;
  } catch {
    return null;
  }
}

// Fill in thumbnails for any summaries still missing one, then persist the
// backfilled images to the cache so the fallback runs at most once per article.
// Mutates the passed summary objects in place and returns them.
export async function fillMissingImages(db, summaries) {
  const missing = summaries.filter(s => s && !s.thumbnailUrl);
  if (!missing.length) return summaries;
  await Promise.all(missing.map(async (s) => {
    const url = await firstPageImage(s.title);
    if (url) s.thumbnailUrl = url;
  }));
  const fixed = missing.filter(s => s.thumbnailUrl);
  if (fixed.length) await bulkSetSummaries(db, fixed);
  return summaries;
}

// ── Top Daily ─────────────────────────────────────────────────────────────────

// Normalise a title for fuzzy matching against dash/quote formatting differences.
function _norm(t) {
  return t.toLowerCase()
    .replace(/[–—−]/g, '-')
    .replace(/['']/g, "'")
    .trim();
}

export async function topDailySummaries(db) {
  try {
    const d = new Date(Date.now() - 86_400_000); // yesterday
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');

    const res  = await fetch(`${PAGEVIEWS_URL}/${y}/${m}/${day}`);
    const data = await res.json();
    const raw  = data.items?.[0]?.articles ?? [];

    const candidates = raw
      .map(a => a.article.replace(/_/g, ' '))
      .filter(t => !SKIP_PREFIXES.some(p => t.startsWith(p)))
      .filter(t => !META_PREFIXES.some(p => t.startsWith(p)))
      .slice(0, 30);

    const summaries  = await bulkSummaries(candidates, db);
    // bulkSummaries does not preserve input order (parallel IDB reads + API batching).
    // Re-order by candidates (popularity rank) before slicing.
    // Primary lookup: exact title match (handles aliased redirect entries too).
    // Fallback: normalised match for minor formatting differences (dash variants, etc.).
    const summaryMap = new Map(summaries.map(s => [s.title, s]));
    const normMap    = new Map(summaries.map(s => [_norm(s.title), s]));
    const result = candidates
      .map(t => summaryMap.get(t) ?? normMap.get(_norm(t)))
      .filter(Boolean)
      .slice(0, 8);
    await fillMissingImages(db, result);
    return result;
  } catch {
    return [];
  }
}
