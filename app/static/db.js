const DB_NAME = 'rabbit-hole';
const DB_VERSION = 1;
export const CANDIDATE_TTL = 86_400_000;  // 24 h
export const SUMMARY_TTL   = 21_600_000;  // 6 h

export function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      for (const name of ['profile', 'candidates', 'summaries', 'bandit', 'phi']) {
        if (!db.objectStoreNames.contains(name)) db.createObjectStore(name);
      }
    };
    req.onsuccess = (e) => resolve(e.target.result);
    req.onerror  = ()  => reject(req.error);
  });
}


function _get(db, store, key) {
  return new Promise((resolve, reject) => {
    const req = db.transaction(store, 'readonly').objectStore(store).get(key);
    req.onsuccess = () => resolve(req.result ?? null);
    req.onerror  = ()  => reject(req.error);
  });
}

function _put(db, store, key, value) {
  return new Promise((resolve, reject) => {
    const req = db.transaction(store, 'readwrite').objectStore(store).put(value, key);
    req.onsuccess = () => resolve();
    req.onerror  = ()  => reject(req.error);
  });
}

const EMPTY_PROFILE = () => ({
  timezone: 'UTC',
  last_generated: null,
  selected_categories: [],
  current_articles: [],
  seen_articles: [],
  shown_articles: [],
  reading_history: [],
  custom_interests: [],
});

export const getProfile = (db) => _get(db, 'profile', 'singleton');
export const setProfile = (db, p) => _put(db, 'profile', 'singleton', p);

export async function patchProfile(db, fn) {
  const current = await getProfile(db) ?? EMPTY_PROFILE();
  const updated = fn(current);
  await setProfile(db, updated);
  return updated;
}

export async function getCandidate(db, query) {
  const entry = await _get(db, 'candidates', query);
  if (!entry || Date.now() - entry.cachedAt > CANDIDATE_TTL) return null;
  return entry;
}

export const setCandidate = (db, query, titles) =>
  _put(db, 'candidates', query, { titles, cachedAt: Date.now() });

export async function getSummary(db, title) {
  const entry = await _get(db, 'summaries', title);
  if (!entry || Date.now() - entry.cachedAt > SUMMARY_TTL) return null;
  return entry;
}

export const setSummary = (db, title, data) =>
  _put(db, 'summaries', title, { ...data, cachedAt: Date.now() });

export async function bulkGetSummaries(db, titles) {
  const map = new Map();
  await Promise.all(titles.map(async (t) => {
    const s = await getSummary(db, t);
    if (s) map.set(t, s);
  }));
  return map;
}

export async function bulkSetSummaries(db, summaries) {
  await Promise.all(summaries.map(s => setSummary(db, s.title, s)));
}

// ── Bandit state ──────────────────────────────────────────────────────────────

export const getBanditState = (db) => _get(db, 'bandit', 'singleton');
export const setBanditState = (db, state) => _put(db, 'bandit', 'singleton', state);

// ── Per-article feature vectors (populated by /api/score, used for updates) ──

export const getPhi    = (db, title)       => _get(db, 'phi', title);
export const setPhi    = (db, title, phi)  => _put(db, 'phi', title, phi);
export async function setPhiMap(db, phiMap) {
  await Promise.all(Object.entries(phiMap).map(([title, phi]) => setPhi(db, title, phi)));
}
