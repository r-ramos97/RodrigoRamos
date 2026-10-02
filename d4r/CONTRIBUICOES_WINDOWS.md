# O que o port alternativo pode trazer para o port Windows oficial

Comparação entre o branch `windows-native` do fork [r-ramos97/d4r](https://github.com/r-ramos97/d4r/tree/windows-native)
(nunca rodou numa GPU real) e o branch [`windows`](https://github.com/countervolts/d4r/tree/windows) do projeto
original (validado na RX 9070 XT). Situação em 2 de outubro de 2026.

## O que o oficial já resolve (não vale reenviar)

| Recurso | Port oficial | Port do fork |
|---|---|---|
| Entradas e saída na VRAM (buffers e fences D3D12 compartilhados com o HIP) | sim, validado no hardware | sim, só em testes simulados |
| Resultado do próprio frame | divide a command list do jogo nos limites gravados | espera na GPU dentro da command list (experimental) |
| Conversão de formatos na GPU | sim, validada no gfx1201 | sim, só no WARP |
| Identidade NVIDIA (NVAPI) para o OptiScaler e o NGX | sim | sim |
| HIP ausente ou GPU errada | o instalador verifica antes de mexer no jogo | `cuInit` falha com mensagem |
| Diagnóstico | um ZIP por falha, com minidump | `collect-logs.ps1` |
| Instalação no jogo | `windows-game.ps1`, com backup e restauração | `setup.ps1` |

O shim do fork é outra arquitetura (uma extensão do shim do Proton), então o código dele não se encaixa no
`tools/windows` oficial. Os dois bugs corrigidos no fork em 2 de outubro (largura das cópias 2D e barreiras do modo
mesmo-frame) são dessa arquitetura e não existem no oficial.

## Candidatos a contribuição

O projeto original **não tem nenhum CI** (nenhum `.github/workflows`). Hoje tudo é validado manualmente no PC
do desenvolvedor. É aí que a experiência do fork ajuda sem duplicar nada:

1. **Checagem de sintaxe dos scripts PowerShell** (`scripts/windows/*.ps1`, mais de 40 scripts) num job Linux
   com `pwsh`. Custa segundos. No fork, essa checagem achou um erro que impedia o `setup.ps1` de abrir.
2. **Build do ZLUDA para Windows no GitHub Actions**, com o LLVM em cache pelo sccache (no fork, ~17 minutos com
   cache), mais o teste `test-zluda-target-compilation.ps1`, que não precisa de GPU nem de arquivos da NVIDIA.
   Pegaria regressões nos patches do ZLUDA e do LLVM antes de alguém testar no hardware.
3. **Build do shim e dos diagnósticos** (`build-windows-rdna4.ps1`) num runner `windows-2022`, só para compilar.
4. **Validação D3D12 sem GPU:** no fork, as command lists gravadas pelo shim rodam no WARP (o renderizador por
   software do Windows) com o debug layer do D3D12 e mocks de NGX, ZLUDA e HIP. Isso achou 16 erros de barreira que
   nenhum teste simulado tinha pegado. Adaptar ao `tools/windows` oficial dá mais trabalho, então fica por último.

## Como fazer

- Antes de abrir PRs, pergunte numa issue do projeto original se o mantenedor (countervolts) e o xdfnx-dev, que
  tem acesso de escrita ao branch `windows`, querem CI. Comece pelo item 1, que é o menor.
- Um PR pequeno por item, contra o branch `windows`.
- Para o Claude preparar os PRs, abra uma sessão nova do Claude Code com `countervolts/d4r` como repositório. Esta
  sessão já tem o fork, que se chama `d4r` também, e os dois não podem ficar juntos.
