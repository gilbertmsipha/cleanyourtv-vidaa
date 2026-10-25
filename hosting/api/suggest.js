// Search suggestions for the launcher's Browser screen, which may not ask the feed itself (no CORS); the helper answers
// the same path on the home network. They come from DuckDuckGo: Brave's feed turns scripts away. Searches go to Brave.
export const config = { runtime: 'edge' };

export default async function handler(request) {
  const params = new URL(request.url).searchParams;
  const q = (params.get('q') || '').slice(0, 200), region = (params.get('kl') || '').slice(0, 10);
  let list = [];
  if (q.trim()) {
    try {
      const r = await fetch('https://duckduckgo.com/ac/?type=list&q=' + encodeURIComponent(q) + '&kl=' + encodeURIComponent(region), { headers: { 'User-Agent': 'Mozilla/5.0' } });
      const found = (await r.json())[1];
      if (Array.isArray(found)) list = found.filter((s) => typeof s === 'string').slice(0, 8);
    } catch (e) {}
  }
  return new Response(JSON.stringify(list), { headers: { 'Content-Type': 'application/json' } });
}
