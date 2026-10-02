# Contribuindo com o d4r (countervolts/d4r)

Anotações para continuar no PC principal. Projeto analisado no commit `f0d1a65` (release 0.1.3, 2026-10-01).

## O que é

O d4r roda a DLSS oficial da NVIDIA (`nvngx_dlss.dll`) em GPUs AMD Radeon, no Linux com Proton:

```
jogo (D3D12) → OptiScaler → d4r_nvngx.dll (shim NGX) → NGX + nvngx_dlss.dll (caminho CUDA)
            → nvcuda.dll (bridge Wine) → ZLUDA (CUDA sobre ROCm/HIP) → kernels nativos RDNA3/RDNA4
```

- Código principal: `tools/d4r_nvngx_shim.cpp` (~4900 linhas) e `tools/wine_nvcuda_bridge.c` (~2900 linhas).
- Kernels HIP escritos à mão: `kernels/k` (DLSS 4), `kernels/m` (DLSS 4.5), `kernels/tex`.
- Patches para ZLUDA e vkd3d-proton: `patches/`.
- Só foi testado de verdade numa **RX 7700 XT (gfx1101)**. RDNA4 (RX 9060/9070) só foi testado em emulador.

## Onde o seu hardware ajuda mais

| GPU no PC principal | Como contribuir |
|---|---|
| **RX 9070 / 9070 XT / 9060 (RDNA4)** | Maior impacto: ninguém testou em hardware real. FPS, qualidade de imagem, FP8 nativo (`NativeFp8`) vs. widened, e se o driver aguenta. |
| **RX 7900 / 7600 / outras RDNA3** | Dados de FPS e de jogos em GPUs que ainda não têm medições (só a 7700 XT tem). |
| **NVIDIA RTX** | Gerar imagens de referência de DLSS real. A paridade de imagem com RTX está marcada como "não verificada" no README e nas notas da 0.1.3. |
| RDNA2 ou mais antiga / sem GPU dedicada | Não roda; dá para ajudar com scripts, docs, CI e testes de CPU. |

## Plano para o meu PC (Ryzen 7 7800X3D, 1×16 GB DDR5-4800, RX 9070 XT 16 GB, Windows 11)

A RX 9070 XT é `gfx1201`: o d4r a suporta só em emulador, então qualquer resultado real é novidade.

### 0. Linux em dual boot (o d4r não roda no Windows nem no WSL)

- Precisa de Linux nativo: o d4r usa Proton, `/dev/kfd` e Vulkan do driver real.
- Recomendado: **CachyOS** (base Arch, o mesmo ambiente do autor), num SSD separado ou numa partição de
  ~200 GB (sistema + 1–2 jogos). O Windows continua intacto.
- Jogos ficam num disco Linux (ext4/btrfs). Biblioteca Steam em NTFS costuma dar problema com Proton.
- Instale GE-Proton 11 (pelo ProtonUp-Qt) e o MangoHud.

### 1. Testar a release (sem compilar nada)

1. Escolha um jogo da `SUPPORTED_GAMES.md` que você tenha (o autor mede no SILENT HILL Townfall).
2. Extraia o zip da release na pasta do `.exe` do jogo e use as opções de lançamento
   `PROTON_FORCE_NVAPI=1 DXVK_NVAPI_GPU_ARCH=AD100 %command%`.
3. `sh d4r/d4r-check.sh` → deve mostrar `GPU gfx1201: native kernels present`.
   - Com a iGPU do 7800X3D ativa na BIOS, este é o teste real do patch abaixo: a versão da release
     pode mostrar a iGPU (`gfx1036`); a corrigida deve mostrar `gfx1201`.
4. Jogue 10+ minutos com DLSS 4 (K) em Quality. Guarde `d4r/d4r_nvngx.log`.

### 2. Medições que ninguém tem

| Teste | Como | Por que importa |
|---|---|---|
| Funciona e é estável? | K e M, 10+ min cada | RDNA4 nunca rodou em hardware |
| FPS K e M em Quality e Performance | MangoHud, mesmo trajeto, rodadas lado a lado | não há nenhum FPS de RDNA4 |
| `NativeFp8 = on` vs `off` | `[Kernels]` no `d4r.ini` | FP8 nativo só foi validado em emulador |
| Qualidade de imagem FP8 on/off (M) | prints no mesmo ponto | o emulador não confere FP8 de verdade |
| `PreferAccuracy = true` | FPS antes/depois | custo nunca medido, nem na RDNA3 |
| Uso de CPU (issue #7, reportada em RDNA4) | `D4R_SHIM_BLOCKING_SYNC = 1` em `[Env]`, compare CPU% no MangoHud | o autor pediu confirmação |
| Custo por kernel | `D4R_CUDA_KERNEL_PROFILE = 1` em `[Env]`; o log da bridge sai no stderr (`PROTON_LOG=1` → `~/steam-<appid>.log`); depois `python3 kernels/tools/kprof.py LOG FRAMES` | permite fazer a tabela de `docs/native-kernels.md` para RDNA4 |
| FSR 4 nativo como base | mesmo trajeto | é a comparação que o autor usa |

### Observações sobre o hardware

- **16 GB em canal único**: suficiente para testar a release. Para compilar ZLUDA/LLVM, use
  `CARGO_BUILD_JOBS=4` e zram/swap de 16 GB ou mais. Em cenas limitadas pela CPU, o canal único derruba
  FPS; para comparações A/B no mesmo PC não importa, mas informe a RAM nos relatos.
- Sempre informe: GPU, distro, kernel, versão do Mesa, GE-Proton, jogo, modelo (E/K/M), modo, resolução.

## Usar o PC principal a partir do notebook

Opção mais simples: abra uma sessão do Claude Code **no PC principal** e controle pelo app:

```sh
# no PC principal, dentro da pasta de trabalho
claude remote-control
```

A sessão aparece no app do Claude (celular/notebook) e roda com a GPU do PC principal.

Alternativa: SSH (por exemplo via Tailscale) do notebook para o PC principal.

## Primeiro passo no PC principal (Linux)

```sh
git clone https://github.com/countervolts/d4r.git && cd d4r
bash scripts/check_environment.sh        # mostra GPU, ROCm, Vulkan, Proton
python3 -m unittest discover -s tests -v # testes de CPU (11 testes, passam)
```

Para só **testar** (sem compilar): baixe o zip da release, extraia na pasta do `.exe` do jogo,
selecione GE-Proton 11 e use as opções de lançamento
`PROTON_FORCE_NVAPI=1 DXVK_NVAPI_GPU_ARCH=AD100 %command%`. Depois rode `sh d4r/d4r-check.sh`.

Para medir FPS do jeito do autor: MangoHud com log por frame, mesmo trajeto, rodadas lado a lado
(veja `docs/performance.md`). Logs do shim ficam em `~/.cache/d4r-dlss-captures/`.

## Melhorias encontradas (sem precisar de GPU)

1. **`packaging/d4r-check.sh` mostra a GPU errada com iGPU + placa dedicada** — corrigido em
   [`patches/0001-d4r-check-report-the-GPU-the-bridge-actually-uses.patch`](patches/0001-d4r-check-report-the-GPU-the-bridge-actually-uses.patch).
   A bridge escolhe a GPU com mais SIMDs (e respeita `D4R_GPU_ARCH`); o check pegava a primeira.
   Testado com uma topologia KFD falsa (iGPU gfx1036 + gfx1201): antes reportava `gfx1036`, agora `gfx1201`.
   Para aplicar no seu fork:
   ```sh
   git am /caminho/para/0001-d4r-check-report-the-GPU-the-bridge-actually-uses.patch
   ```
2. **Não há CI** (sem `.github/workflows`). Um workflow rodando `tests/` e `sh -n`/shellcheck nos scripts
   pegaria regressões sem precisar de GPU.
3. **`scripts/check_environment.sh` depende de `rg` (ripgrep)**, que não está nos requisitos; sem ele, as
   seções de GPU e Vulkan só mostram `rg: command not found`. E a lista de pacotes só funciona no Arch (`pacman`).

## Como enviar

O autor aceita issues no GitHub (e Discord `._ayo`). Fluxo: fork → branch → commit → pull request.
Para relatos de hardware, inclua: GPU e `gfx`, distro e kernel, versão do GE-Proton, jogo, modelo
(E/K/M), modo de qualidade, FPS e o `d4r_nvngx.log`.
