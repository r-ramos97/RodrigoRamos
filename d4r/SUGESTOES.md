# 10 sugestões para o d4r

Cada sugestão aponta onde está no código, por que importa e como começar. Ordem: impacto primeiro.
🟢 = dá para fazer com o meu PC (RX 9070 XT, RDNA4) · 🔵 = só código, sem GPU · 🟣 = precisa de outra GPU.

---

## 1. Primeira validação de RDNA4 em hardware real 🟢

**Por quê:** o README, `docs/performance.md` e as notas da 0.1.3 dizem que RDNA4 (gfx1200/gfx1201) só foi
testado em compilador e emulador (rocjitsu). Nenhum FPS, tempo de kernel ou teste de estabilidade em RDNA4
existe. O próprio emulador "não confere a aritmética FP8".

**Como:** seguir o plano de `README.md` desta pasta: estabilidade com K e M, FPS em Quality/Performance,
`NativeFp8` on/off, `PreferAccuracy`, CPU com `D4R_SHIM_BLOCKING_SYNC` (issue #7) e a tabela de custo por
kernel com `D4R_CUDA_KERNEL_PROFILE=1` + `kernels/tools/kprof.py`.

**Resultado:** uma coluna RDNA4 na tabela de `docs/native-kernels.md` e a primeira linha "testado" para
gfx1201 no README. Esforço: 1–2 fins de semana.

## 2. Ajustar os kernels nativos para RDNA4 🟢

**Por quê:** os templates (`kernels/k/pwin_wide.h`, `kernels/m/swin_block.h`) foram ajustados numa RX 7700 XT
de 54 CUs. A 9070 XT tem 64 CUs, mais vazão de matriz e FP8 nativo; o melhor número de ondas por janela
(`4·NG` em `pwin_wide.h`, `d4r_block_z`) e o `SWIN_SCALAR_BLOAD` podem ser outros.

**Como:** medir com `D4R_CUDA_KERNEL_PROFILE`, variar os parâmetros e validar com `dump_runner` + `psnr.py`.
Hoje `kernels/build.sh` lê só uma linha `// d4r-build-flags:` por arquivo; aceitar também
`// d4r-build-flags-gfx12:` permitiria flags por arquitetura sem afetar RDNA3.

**Resultado:** FPS maior em RDNA4 sem mexer na RDNA3. Esforço: médio, iterativo.

## 3. Referência RTX para medir a qualidade de imagem de verdade 🟣

**Por quê:** "1:1 image quality against RTX DLSS is not yet proven" aparece no README e nas notas da release.
Sem referência, ninguém sabe se `PreferAccuracy` realmente aproxima o resultado da NVIDIA.

**Como:** `tools/d3d12_dlss_harness.cpp` já recebe a DLL NGX como primeiro argumento, reproduz quadros
capturados (`D4R_HARNESS_REPLAY_DIR`) e salva a saída (`D4R_HARNESS_SAVE_FRAMES`). Rodar o mesmo harness no
Windows com uma RTX e o `_nvngx.dll` da NVIDIA gera os quadros de referência; depois comparar com
`kernels/tools/psnr.py` (e, idealmente, NVIDIA FLIP). Provavelmente exige pequenos ajustes no harness.

**Resultado:** um número de PSNR por modelo e modo (fast vs accuracy) em vez de "não verificado". Precisa de
alguém da comunidade com placa RTX.

## 4. Investigar o crash da Ray Reconstruction (Control) 🔵🟢

**Por quê:** `NVSDK_NGX_D3D12_GetFeatureRequirements` em `tools/d4r_nvngx_shim.cpp` (linha ~4241) responde
`FeatureSupported = 0` (suportado) para **qualquer** recurso, sem olhar qual foi pedido. Já o `CreateFeature`
só aceita Super Resolution. Um jogo que consulta Ray Reconstruction recebe "suportado", liga o recurso e
depois falha ao criá-lo. Isso bate com o relato da `SUPPORTED_GAMES.md`: Control com ray tracing pede
Ray Reconstruction e fecha sozinho.

**Como:** ler o `FeatureID` da estrutura de descoberta (segundo argumento) e responder "não implementado" para
tudo que não for Super Resolution, para o jogo cair no fallback. **Hipótese:** o OptiScaler fica no meio e
pode tratar isso antes; precisa ser testado no Control com RT ligado antes de virar PR.

**Resultado:** possível fim do crash do Control e de outros jogos que pedem Ray Reconstruction.

## 5. CI que compila os kernels para os 6 alvos e vigia registradores 🔵

**Por quê:** RDNA4 é "compile only" e mesmo assim nada compila os kernels automaticamente. O
`kernels/build.sh` já imprime `NumVgprs`, `ScratchSize` e `Occupancy` de cada kernel.

**Como:** um job num contêiner ROCm (por exemplo `rocm/dev-ubuntu-24.04`) rodando `kernels/build.sh k` e `m`
para gfx1100–gfx1103 e gfx1200–gfx1201 (com a variante `D4R_NATIVE_FP8=1`), comparando a tabela com a do
`main` e alertando quando `ScratchSize` passa de 0 (spill para memória, que derruba desempenho).

**Resultado:** regressões de RDNA4 detectadas mesmo sem ninguém ter a placa. Complementa o CI do PR desta pasta.

## 6. Suporte a jogos Vulkan (DOOM: The Dark Ages) 🔵🟢

**Por quê:** a `SUPPORTED_GAMES.md` diz que d4r só suporta DLSS em D3D12. O repositório já tem
`tools/ngx_vulkan_probe.cpp`, e o caminho interno do shim já usa Vulkan (interop do vkd3d-proton).

**Como:** implementar as entradas `NVSDK_NGX_VULKAN_*` no shim. Num jogo Vulkan as imagens já são Vulkan: dá
para exportá-las direto para HIP, possivelmente mais simples que o caminho D3D12. Esforço grande.

**Resultado:** abre a classe inteira de jogos Vulkan com DLSS.

## 7. Acabar com a compilação no primeiro uso 🔵

**Por quê:** as notas da 0.1.3 avisam que "the rebuilt runtime may compile a new kernel cache on first start"
(e o modo de precisão compila de novo). Isso causa espera e travadas no primeiro jogo.

**Como:** os kernels de textura já são compilados offline por alvo com o `d4r_emit` do ZLUDA. Estender isso a
todos os kernels que o ZLUDA ainda traduz em tempo real para K, M e E, por alvo, gerando um cache "quente"
verificado por hash, como os manifestos `d4r-kernels.txt`.

**Resultado:** primeiro lançamento sem compilação. Atenção: são binários gerados do código da NVIDIA, com as
mesmas regras de distribuição dos kernels de textura.

## 8. Relatório de compatibilidade para novas versões da DLSS 🔵

**Por quê:** os kernels nativos só valem para o PTX de 310.7/310.9. Numa versão nova, cada kernel que mudou cai
silenciosamente no caminho lento. Hoje o `d4r-check.sh` só avisa genericamente pela versão.

**Como:** `kernels/tools/kernel_manifest.py` já calcula o hash FNV-1a do PTX de cada kernel. Uma ferramenta
`dlss_compat.py NOVA.dll` listaria quais kernels nativos ainda batem, quais mudaram e o diff do PTX; o
`d4r-check.sh` poderia dizer "7 de 11 kernels nativos valem para esta DLSS".

**Resultado:** suporte mais rápido a cada versão nova da DLSS e diagnóstico claro para o usuário.

## 9. Instalador e "doutor" do d4r 🔵

**Por quê:** a instalação é manual e cada jogo tem pegadinhas listadas na `SUPPORTED_GAMES.md`: renomear
`dxgi.dll` para `version.dll` no Horizon, `sl.common.dll` no The Last of Us, `Dxgi=false` no Ratchet,
`AutoExposure` no Alan Wake 2, `WINEDLLOVERRIDES` no Cyberpunk.

**Como:** um script que lê as bibliotecas do Steam (`libraryfolders.vdf`), acha a pasta do `.exe` principal
(inclusive `<Projeto>/Binaries/Win64` da Unreal), extrai o zip, faz backup do `OptiScaler.ini` e aplica os
ajustes de cada jogo a partir de perfis em dados. Junto, um analisador do `d4r_nvngx.log` que reconhece erros
conhecidos (formato rejeitado, sem kernels nativos, GLIBC) e sugere a correção.

**Resultado:** menos issues de instalação e mais gente testando.

## 10. Benchmark automatizado com estatística e base de resultados da comunidade 🟢🔵

**Por quê:** o autor mede com MangoHud e rodadas pareadas porque a variação é de ~1% entre rodadas e ~3% por
kernel. Hoje as tabelas são montadas à mão e só existem para uma GPU.

**Como:** um script que lê os CSVs do MangoHud, calcula FPS médio, 1% e 0,1% low, faz a comparação A/B com
intervalo de confiança e grava `results/<gpu>/<jogo>.json`. As tabelas do README passam a ser geradas desses
arquivos, e cada pessoa contribui com sua GPU por PR.

**Resultado:** tabela de GPUs suportadas alimentada pela comunidade, começando pela 9070 XT.
