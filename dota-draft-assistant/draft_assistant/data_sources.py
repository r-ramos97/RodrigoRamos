"""Clientes das fontes de dados: OpenDota (REST), STRATZ (GraphQL) e Steam Web API.

Tudo passa por um cache em disco para que o uso ao vivo, durante o draft, não dependa
da rede nem gaste o limite diário das APIs.
"""

import hashlib
import json
import time
from pathlib import Path

import requests

USER_AGENT = "dota-draft-assistant/0.1 (+https://github.com/r-ramos97/RodrigoRamos)"
STEAM_CDN = "https://cdn.cloudflare.steamstatic.com"


class JsonCache:
    def __init__(self, directory: Path, ttl_seconds: float = 24 * 3600):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds

    def _path(self, key: str) -> Path:
        safe = hashlib.sha1(key.encode()).hexdigest()[:16]
        return self.directory / f"{safe}.json"

    def get(self, key: str, allow_stale: bool = False):
        path = self._path(key)
        if not path.exists():
            return None
        if not allow_stale and time.time() - path.stat().st_mtime > self.ttl:
            return None
        return json.loads(path.read_text(encoding="utf-8"))["data"]

    def put(self, key: str, data) -> None:
        self._path(key).write_text(json.dumps({"key": key, "data": data}), encoding="utf-8")


class _HttpClient:
    min_interval = 1.0  # segundos entre chamadas, para respeitar o limite por minuto

    def __init__(self, cache: JsonCache, session: requests.Session | None = None):
        self.cache = cache
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self._last_call = 0.0

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _cached(self, key: str, fetch, offline: bool = False):
        data = self.cache.get(key)
        if data is not None:
            return data
        if offline:
            return self.cache.get(key, allow_stale=True)
        try:
            self._throttle()
            data = fetch()
        except requests.RequestException:
            stale = self.cache.get(key, allow_stale=True)
            if stale is not None:
                return stale
            raise
        self.cache.put(key, data)
        return data


class OpenDotaClient(_HttpClient):
    """https://docs.opendota.com — sem chave funciona no plano gratuito (limite por minuto e por dia)."""

    base_url = "https://api.opendota.com/api"

    def __init__(self, cache: JsonCache, api_key: str | None = None, **kwargs):
        super().__init__(cache, **kwargs)
        self.api_key = api_key

    def _get(self, path: str, params: dict | None = None, offline: bool = False):
        params = dict(params or {})
        key = f"opendota:{path}:{json.dumps(params, sort_keys=True)}"
        if self.api_key:
            params["api_key"] = self.api_key

        def fetch():
            resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()

        return self._cached(key, fetch, offline=offline)

    def hero_stats(self, offline: bool = False) -> list[dict]:
        """Pick/win por faixa de MMR (1=Herald ... 8=Immortal), pro_pick/pro_win/pro_ban, roles, imagens."""
        return self._get("/heroStats", offline=offline)

    def matchups(self, hero_id: int, offline: bool = False) -> list[dict]:
        """[{hero_id, games_played, wins}] — `wins` são as vitórias de `hero_id` da URL contra cada herói."""
        return self._get(f"/heroes/{int(hero_id)}/matchups", offline=offline)

    def patches(self, offline: bool = False) -> list[dict]:
        return self._get("/constants/patch", offline=offline)

    def pro_matches(self, less_than_match_id: int | None = None) -> list[dict]:
        params = {"less_than_match_id": less_than_match_id} if less_than_match_id else None
        return self._get("/proMatches", params)

    def live(self) -> list[dict]:
        return self._get("/live")

    def explorer(self, sql: str, offline: bool = False) -> list[dict]:
        """SQL somente leitura sobre as tabelas de partidas profissionais (matches, picks_bans, teams...)."""
        return self._get("/explorer", {"sql": sql}, offline=offline)["rows"]


class StratzClient(_HttpClient):
    """https://api.stratz.com/graphql — exige token Bearer gratuito (stratz.com/api, login pela Steam).

    Os nomes dos campos abaixo seguem o schema público do STRATZ; confirme por introspecção
    (`__type(name: "HeroStatsQuery")`) se uma consulta falhar com "Cannot query field".
    """

    url = "https://api.stratz.com/graphql"

    HERO_MATCHUP_QUERY = """
    query HeroMatchup($heroId: Short!, $bracket: [RankBracketBasicEnum]) {
      heroStats {
        heroVsHeroMatchup(heroId: $heroId, bracketBasicIds: $bracket) {
          advantage {
            vs { heroId2 synergy matchCount winCount }
            with { heroId2 synergy matchCount winCount }
          }
        }
      }
    }
    """

    def __init__(self, cache: JsonCache, token: str, **kwargs):
        super().__init__(cache, **kwargs)
        self.session.headers["Authorization"] = f"Bearer {token}"
        # O STRATZ bloqueia clientes sem este User-Agent.
        self.session.headers["User-Agent"] = "STRATZ_API"

    def query(self, gql: str, variables: dict | None = None, offline: bool = False):
        key = f"stratz:{gql}:{json.dumps(variables or {}, sort_keys=True)}"

        def fetch():
            resp = self.session.post(self.url, json={"query": gql, "variables": variables or {}}, timeout=30)
            resp.raise_for_status()
            body = resp.json()
            if body.get("errors"):
                raise requests.RequestException(str(body["errors"]))
            return body["data"]

        return self._cached(key, fetch, offline=offline)

    def hero_matchup(self, hero_id: int, bracket: str | None = None, offline: bool = False) -> dict:
        """Vs e com (sinergia) de um herói, numa faixa como "DIVINE_IMMORTAL" ou "LEGEND_ANCIENT"."""
        variables = {"heroId": int(hero_id), "bracket": [bracket] if bracket else None}
        data = self.query(self.HERO_MATCHUP_QUERY, variables, offline=offline)
        return data["heroStats"]["heroVsHeroMatchup"]["advantage"][0]


class SteamClient(_HttpClient):
    """Steam Web API oficial da Valve — chave em https://steamcommunity.com/dev/apikey."""

    base_url = "https://api.steampowered.com"

    def __init__(self, cache: JsonCache, api_key: str, **kwargs):
        super().__init__(cache, **kwargs)
        self.api_key = api_key

    def _get(self, path: str, params: dict | None = None):
        params = dict(params or {})
        key = f"steam:{path}:{json.dumps(params, sort_keys=True)}"
        params["key"] = self.api_key

        def fetch():
            resp = self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()

        return self._cached(key, fetch)

    def live_league_games(self) -> list[dict]:
        """Partidas de liga ao vivo com picks/bans em andamento (útil para acompanhar campeonatos)."""
        return self._get("/IDOTA2Match_570/GetLiveLeagueGames/v1/")["result"]["games"]

    def match_details(self, match_id: int) -> dict:
        return self._get("/IDOTA2Match_570/GetMatchDetails/v1/", {"match_id": int(match_id)})["result"]
