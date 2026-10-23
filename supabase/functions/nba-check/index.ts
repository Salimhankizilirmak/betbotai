// NBA veri kaynakları bu Edge Function'ın ağından erişilebilir mi?
// stats.nba.com bulut IP'lerini engelliyor; cdn.nba.com (statik JSON) ve ESPN alternatif olarak denenir.

const BROWSER_HEADERS: Record<string, string> = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
  Accept: "application/json, text/plain, */*",
  "Accept-Language": "en-US,en;q=0.9",
  Origin: "https://www.nba.com",
  Referer: "https://www.nba.com/",
};

// NBA sezonu Ekim'de başlar: Ekim 2026 - Eylül 2027 => "2026-27" (seasons.py ile aynı kural)
function nbaSeason(d = new Date()): string {
  const y = d.getUTCMonth() + 1 >= 10 ? d.getUTCFullYear() : d.getUTCFullYear() - 1;
  return `${y}-${String((y + 1) % 100).padStart(2, "0")}`;
}

async function probe(name: string, url: string, summarize: (j: any) => unknown, timeoutMs = 15_000) {
  const t0 = Date.now();
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { headers: BROWSER_HEADERS, signal: ctrl.signal });
    const ms = Date.now() - t0;
    if (!res.ok) return { name, ok: false, status: res.status, ms };
    return { name, ok: true, ms, summary: summarize(await res.json()) };
  } catch (e) {
    return { name, ok: false, error: `${(e as Error).name}: ${(e as Error).message}`.slice(0, 120), ms: Date.now() - t0 };
  } finally {
    clearTimeout(timer);
  }
}

Deno.serve(async () => {
  const season = nbaSeason();
  const results = await Promise.all([
    probe("stats.nba.com playergamelogs",
      `https://stats.nba.com/stats/playergamelogs?Season=${season}&SeasonType=Regular%20Season&LeagueID=00`,
      (j) => ({ rows: j?.resultSets?.[0]?.rowSet?.length ?? 0 }), 12_000),
    probe("cdn.nba.com scoreboard",
      "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json",
      (j) => ({ gameDate: j?.scoreboard?.gameDate, games: j?.scoreboard?.games?.length ?? 0 })),
    probe("cdn.nba.com schedule",
      "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
      (j) => ({ season: j?.leagueSchedule?.seasonYear, gameDates: j?.leagueSchedule?.gameDates?.length ?? 0 })),
    probe("espn scoreboard",
      "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
      (j) => ({ events: j?.events?.length ?? 0, day: j?.day?.date })),
  ]);
  return new Response(JSON.stringify({ season, results }), { headers: { "Content-Type": "application/json" } });
});
