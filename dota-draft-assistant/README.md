# Dota Draft Assistant

Assistente de draft para Dota 2. Ele acompanha a seleção de heróis em tempo real, seja pela
**leitura da tela** ou pelo **Game State Integration (GSI)** oficial da Valve, e sugere
**counterpicks e bans** com base em quatro fontes:

1. confrontos herói contra herói e sinergias (OpenDota / STRATZ);
2. meta atual por faixa de MMR;
3. presença no cenário profissional;
4. o **perfil de draft de campeonato do coach Filipe "Astini" Astini**.

> Estado: protótipo. O motor de sugestão, o parser do GSI, a leitura de tela e a CLI têm
> testes automatizados com dados de exemplo (`pytest`). As chamadas reais às APIs ainda não
> foram testadas contra os servidores, porque o ambiente onde o código foi escrito não tinha
> acesso a eles. Rode `update-data` na sua máquina para validar.

---

## 1. Fontes de dados

### 1.1 APIs de partidas e estatísticas

| Fonte | O que oferece | Acesso | Uso no projeto |
|---|---|---|---|
| **OpenDota API** (`api.opendota.com/api`) | `/heroStats` (pick/win por faixa 1–8, `pro_pick`/`pro_win`/`pro_ban`, funções, imagens), `/heroes/{id}/matchups`, `/proMatches`, `/live`, `/constants/patch`, `/explorer` (SQL nas tabelas pro: `matches`, `picks_bans`, `teams`) | Gratuita sem chave, com limite por minuto e por dia; chave paga aumenta o limite (veja [opendota.com/api-keys](https://www.opendota.com/api-keys)) | Fonte padrão: meta, presença pro, confrontos e **drafts dos times do Astini** via Explorer |
| **STRATZ GraphQL** (`api.stratz.com/graphql`) | Estatísticas por faixa de MMR com amostras grandes de partidas ranqueadas: `heroStats.heroVsHeroMatchup` (vs e **with** = sinergia), win rate por dia, partidas, ligas | Token Bearer gratuito em [stratz.com/api](https://stratz.com/api) (login pela Steam); exige `User-Agent: STRATZ_API` | Confrontos e sinergias da sua faixa, quando há token |
| **Steam Web API** (Valve) | `IDOTA2Match_570/GetLiveLeagueGames` (drafts de campeonato **ao vivo**), `GetMatchDetails` (`picks_bans`), `GetMatchHistory`, `IEconDOTA2_570/GetHeroes` | Chave gratuita em [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) | Acompanhar drafts de campeonato ao vivo; dado oficial de reserva |
| **CDN da Steam** (`cdn.cloudflare.steamstatic.com`) | Retratos e ícones dos heróis (caminhos vêm do `/heroStats`) | Público | Templates da leitura de tela |
| **GRID** (grid.gg) | Dados oficiais de esports em tempo real, incluindo picks e bans durante o draft | Comercial, sob contrato | Opcional, para uso profissional |

### 1.2 Dados do próprio jogo (tempo real)

| Fonte | O que oferece | Limitação |
|---|---|---|
| **Game State Integration** (oficial) | JSON por HTTP local com `draft` (`team2`=Radiant, `team3`=Dire, `pickN_id`, `banN_id`, time ativo), `player`, `hero`, `map` | Configurado por um `.cfg` em `game/dota/cfg/gamestate_integration/` + opção `-gamestateintegration`. Assistindo, ele expõe todos os jogadores; **jogando, só o jogador local**, e o bloco `draft` pode vir vazio em matchmaking |
| **Leitura de tela** (captura + visão computacional) | Funciona em qualquer modo, porque lê os retratos nos slots de pick/ban | Precisa calibrar a posição dos slots uma vez por resolução/HUD |

### 1.3 Meta e referência de campeonato

| Fonte | Uso | Observação |
|---|---|---|
| **Notas de patch oficiais** (dota2.com/patches) e **Liquipedia** (`/dota2/Version`) | Saber qual patch vale e quando ele mudou | O patch atual é lido do `/constants/patch` do OpenDota. Linha 2026: 7.41 em 24/03/2026, depois 7.41a–7.41e (7.41e em 30/07/2026, segundo a Liquipedia); confira se já saiu um patch mais novo |
| **Liquipedia** | Resultados, elencos e drafts de torneios | A API MediaWiki pede User-Agent identificável e tem limite baixo; o LiquipediaDB é pago |
| **Dotabuff, Dota2ProTracker, Datdota, Spectral, tier lists** (esports.gg etc.) | Conferência manual do meta e de builds | Sem API pública; raspar esses sites viola os termos de uso, então ficam fora do código |
| **Dota Plus** (Valve) | Assistente de draft oficial, que sugere heróis pela win rate da sua faixa e considera o draft inimigo | Bom ponto de comparação para as sugestões |

---

## 2. Referência de coach: Filipe "Astini" Astini

O Astini é coach brasileiro: FURIA (2019), B8, **nouns** (top 8 no TI 2023), **Cloud9**
(top 6 no TI 2024), **PARIVISION** (de outubro/2024 até o começo de 2026) e, em 2026, head coach
da **Team Nemesis**, que disputou a EWC 2026. Ex-jogadores dizem que é ele quem monta os drafts
("ele faz nossos drafts", segundo o Gunnar, do nouns), e o estilo dele é conhecido por picks
pouco convencionais.

Como o projeto usa essa referência:

1. `coach-teams <nome>` procura o `team_id` de cada time no OpenDota (vários times têm o mesmo nome; confira pelo último jogo).
2. Você coloca os `team_id` em `config.toml` (`[[coach.stints]]`, com início e fim de cada passagem).
3. `update-coach` baixa pelo Explorer **todos os picks e bans feitos pelo time dele** em cada período e calcula:
   - picks, bans, vitórias com cada herói;
   - prioridade de **primeira fase** (ações entre as primeiras do draft, o que indica herói de prioridade);
   - **decaimento por idade** (meia-vida de 120 dias), para que drafts de patches antigos pesem menos.
4. Essa prioridade entra como o componente `coach` da nota, e a sugestão mostra
   "prioridade nos drafts de Filipe "Astini" Astini" quando o herói é recorrente nos drafts dele.

As datas das passagens em `coach.py` vêm de notícias (GosuGamers, Hawk Live, esports.ru) e
são aproximadas. Ajuste no `config.toml` se souber as datas exatas. Dá para trocar o coach
por qualquer outro editando a seção `[coach]`.

---

## 3. Arquitetura

```mermaid
flowchart LR
    subgraph Captura do draft
        GSI[GSI oficial<br/>HTTP local :3000] --> P[parse_draft]
        TELA[Captura da tela<br/>mss] --> TM[Template matching<br/>OpenCV] --> P2[read_draft]
    end
    P --> D[DraftState<br/>aliados / inimigos / bans]
    P2 --> D

    subgraph Dados - atualizados antes da partida
        OD[OpenDota] --> C[(Cache em disco<br/>data/cache)]
        ST[STRATZ] --> C
        EX[OpenDota Explorer<br/>drafts dos times do Astini] --> CO[(coach_*.json)]
    end

    C --> E[Engine]
    CO --> E
    D --> E
    E --> S[Picks e bans sugeridos<br/>com motivo de cada um]
```

**Por que cache:** durante o draft você tem cerca de 30 segundos. Por isso `update-data` baixa tudo
antes (heroStats + confrontos de todos os heróis, ~130 chamadas, dentro do limite gratuito) e
o modo ao vivo roda **offline**, sem latência de rede.

### Como a nota é calculada (`scoring.py`)

Cada componente vale pontos percentuais (pp) de win rate:

| Componente | Cálculo |
|---|---|
| `counter` | Soma, para cada inimigo, de quanto o candidato vence acima da própria win rate base. Usa **suavização bayesiana** (`prior_games`), então 3 vitórias em 3 jogos não viram "counter perfeito" |
| `synergy` | O mesmo para os aliados (dados "with" do STRATZ) |
| `meta` | (win rate na faixa − 50%) |
| `pro` | presença pick+ban no pro, normalizada de 0 a 5 |
| `coach` | prioridade nos drafts do Astini, normalizada de 0 a 5 |

`nota = Σ peso × componente`; os pesos ficam em `[weights]`. **Bans** usam a mesma conta
invertida: os heróis que mais counteram os seus picks, somados a meta, pro e coach.

### Módulos

| Arquivo | Responsabilidade |
|---|---|
| `draft_assistant/data_sources.py` | Clientes OpenDota, STRATZ e Steam, cache JSON, controle de taxa e fallback para cache antigo se a rede cair |
| `draft_assistant/scoring.py` | `MatchupTable` e `Engine` (picks, bans, motivos) |
| `draft_assistant/coach.py` | Perfil do Astini, SQL do Explorer, agregação com decaimento |
| `draft_assistant/gsi.py` | Gera o `.cfg`, servidor HTTP local com token, parser do bloco `draft` |
| `draft_assistant/screen.py` | Captura, calibração dos slots, template matching, recortes para criar templates |
| `draft_assistant/cli.py` | Comandos |

---

## 4. Como usar

```bash
pip install -r requirements.txt
cp config.example.toml config.toml        # opcional: STRATZ token, faixa, pesos, team_ids

python -m draft_assistant update-data --templates   # baixa dados e retratos (uma vez por dia)
python -m draft_assistant coach-teams nemesis       # descobre o team_id da Team Nemesis
python -m draft_assistant update-coach              # baixa os drafts dos times do Astini

# Draft digitado à mão
python -m draft_assistant suggest --enemies "am,axe,cm" --allies "bane" --role Carry
```

### Ao vivo pela tela (funciona em qualquer modo)

```bash
python -m draft_assistant calibrate --side radiant   # numa tela de draft: marque os slots com o mouse
python -m draft_assistant watch --source screen --side radiant
```

Se algum herói não for reconhecido (a arte do slot pode ser diferente do retrato do CDN), rode
`calibrate --save-crops` durante um draft, renomeie os recortes para `<hero_id>_<nome>.png`
e coloque em `data/templates/`. Eles passam a servir de template junto dos oficiais.

### Ao vivo pelo GSI (assistindo partidas, lobbies e Captains Mode)

```bash
python -m draft_assistant gsi-config --dota-dir "C:\Program Files (x86)\Steam\steamapps\common\dota 2 beta"
# Steam > Dota 2 > Propriedades > Opções de inicialização: -gamestateintegration
python -m draft_assistant watch --source gsi --token <token impresso acima>
```

Deixe a janela do assistente num segundo monitor ou em outra janela ao lado do jogo.

---

## 5. Fair play

- O assistente **só lê** a tela ou o GSI oficial: não lê a memória do jogo, não injeta overlay e não automatiza cliques.
- Ele não consulta o histórico dos jogadores adversários. Foi esse tipo de recurso, oferecido por um app do Overwolf, que a comunidade tratou como trapaça e que a Valve bloqueou no pré-jogo.
- A Valve já oferece sugestão de herói no Dota Plus. Esta ferramenta faz o mesmo tipo de análise com fontes abertas e uma referência de campeonato.

---

## 6. Próximos passos

- [ ] Validar as consultas reais (OpenDota, STRATZ) e ajustar campos do GraphQL por introspecção.
- [ ] Recomendação por **posição** (1–5) usando lane/role do STRATZ, em vez das funções genéricas.
- [ ] Ler a ordem do draft (quem escolhe agora) para alternar entre sugerir picks e bans.
- [ ] Interface gráfica simples (Tkinter/Qt) em segundo monitor.
- [ ] Acompanhar drafts de campeonato ao vivo (`GetLiveLeagueGames`) e comparar com as escolhas do Astini.

## Testes

```bash
pip install pytest
python -m pytest
```

## Fontes consultadas

- OpenDota: [mudanças na API (blog)](https://blog.opendota.com/2018/04/17/changes-to-the-api/)
- STRATZ: [stratz.com](https://stratz.com/), [wrapper MCP com exemplos de GraphQL](https://github.com/pipeworx-io/mcp-stratz), [metodologia de counters com dados do STRATZ](https://www.dota2protips.com/methodology/heroes)
- GSI: [dota2-gsi (npm)](https://www.npmjs.com/package/dota2-gsi), [Dota2GSI (NuGet)](https://www.nuget.org/packages/Dota2GSI)
- GRID: [grid.gg/data/games/dota-2](https://grid.gg/data/games/dota-2/)
- Patches: [Liquipedia: Game Versions](https://liquipedia.net/dota2/Game_Versions), [Liquipedia: 7.41e](https://liquipedia.net/dota2/7.41e)
- Meta: [esports.gg: tier list](https://esports.gg/news/dota-2/dota-2-hero-tier-list/)
- Dota Plus e Overwolf: [esports.gg](https://esports.gg/opinion/dota-2/is-dota-plus-pay-to-win-every-paid-dota-2-feature-explained)
- Astini: [GosuGamers: Team Nemesis anuncia Astini](https://www.gosugamers.net/dota2/news/78371-team-nemesis-overhaul-dota-2-roster-appoint-astini-as-head-coach-ahead-of-ewc-and-ti-2026), [GosuGamers: saída da PARIVISION](https://www.gosugamers.net/dota2/news/77670-parivision-coach-astini-departs-lineup-ahead-of-dreamleague-season-27), [entrevista no TI 2024](https://www.gosugamers.net/dota2/features/73072-astini-interview-at-ti-2024-sometimes-being-skilled-comes-with-wanting-to-freestyle-too-much), [nouns no TI 2023](https://nordot.app/1090696791189619055), [esports.ru: balanço da EWC 2026](https://esports.ru/dota-2/news/astini-podvel-itogi-vystupleniya-na-ewc2026/)
