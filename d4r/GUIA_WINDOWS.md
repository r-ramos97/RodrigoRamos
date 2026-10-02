# d4r no Windows 11 nativo: guia de teste (RX 9070 XT + Ryzen 7 7800X3D)

Port nativo para Windows no fork [r-ramos97/d4r](https://github.com/r-ramos97/d4r), branch
[`windows-native`](https://github.com/r-ramos97/d4r/tree/windows-native). Ele roda a DLSS oficial da NVIDIA na
RX 9070 XT sem Linux e sem Proton. O design completo está em
[`docs/windows.md`](https://github.com/r-ramos97/d4r/blob/windows-native/docs/windows.md).

> **Estado: prévia.** Todas as partes compilam no CI e passam nos testes com bibliotecas simuladas, no Windows
> (runner do GitHub) e no Wine. **Ainda não rodou numa GPU de verdade.** O seu PC faz o primeiro teste real.
> Os logs que ele gerar mostram o que ainda falta.

## Como funciona no Windows

```
jogo (D3D12) → OptiScaler (dxgi.dll) → d4r\nvngx.dll (shim, modo Windows nativo)
     → _nvngx.dll + nvngx_dlss.dll da NVIDIA (caminho CUDA)
     → d4r\nvcuda.dll (bridge nativa) → d4r\zluda\zluda_nvcuda.dll (ZLUDA) → HIP SDK da AMD
     → kernels nativos RDNA4 (gfx1201, com FP8 nativo)
nvapi64.dll + version.dll do d4r: fazem o OptiScaler e o NGX enxergarem uma GPU NVIDIA (Ada)
```

O que muda em relação ao Linux:

| | Linux/Proton | Windows nativo |
|---|---|---|
| Resultado do frame | o do próprio frame (vkd3d-proton com patch divide a command list) | o mais recente que já terminou, **1 frame de atraso** (`FrameAge = 1`) |
| Entradas e saída da DLSS | ficam na VRAM | ficam na VRAM **se o driver da AMD deixar o HIP mapear buffers D3D12**; senão passam pela RAM (~55 MB por frame em 1440p) |
| GPU usada | detectada pelo KFD | a GPU do adaptador D3D12 do jogo (o d4r ignora a iGPU do 7800X3D sozinho) |

## 1. O que instalar

1. **Driver AMD Adrenalin** atual.
2. **AMD HIP SDK para Windows**: [amd.com/en/developer/resources/rocm-hub/hip-sdk.html](https://www.amd.com/en/developer/resources/rocm-hub/hip-sdk.html).
   Use a versão mais nova que suporte a RX 9070 XT (7.x de preferência, que traz `amdhip64_7.dll`). O
   instalador define a variável `HIP_PATH`, que o d4r usa para achar o HIP. Reinicie depois de instalar.
3. **OptiScaler 0.9.4**: você vai precisar do `OptiScaler.dll` e do `OptiScaler.ini`.
4. **Dois arquivos da NVIDIA**, que o pacote não inclui:
   - `nvngx_dlss.dll` **versão 310.7 ou 310.9**. Só essas usam os kernels nativos, que são os rápidos. Muitos
     jogos trazem uma versão mais antiga, que roda, mas mais devagar.
   - `_nvngx.dll`, o runtime NGX que vem dentro do **driver NVIDIA 596.36**, a versão testada no Linux.
     Baixe o instalador do driver no site da NVIDIA, abra-o com o 7-Zip (sem instalar) e procure o
     `_nvngx.dll` lá dentro.
5. **7-Zip**, para abrir o instalador da NVIDIA.

## 2. Baixar o pacote do d4r para Windows

O CI do fork monta o pacote sozinho:

1. Abra [Actions → Windows package](https://github.com/r-ramos97/d4r/actions/workflows/windows-package.yml)
   logado na sua conta.
2. Entre no último run verde que tenha **Artifacts** e baixe **`d4r-windows`**. Dentro dele vem
   `d4r-<versão>-windows-preview.zip` com o `.sha256`.

> O pacote depende do build do ZLUDA para Windows (workflow
> [Windows](https://github.com/r-ramos97/d4r/actions/workflows/windows.yml), cerca de 1 h por causa do LLVM).
> Se ainda não houver artifact, esse build não terminou com sucesso.

## 3. Instalar num jogo

Escolha um jogo D3D12 **sem anti-cheat** da
[SUPPORTED_GAMES.md](https://github.com/r-ramos97/d4r/blob/windows-native/SUPPORTED_GAMES.md).

1. Extraia o zip na pasta do `.exe` principal do jogo. Em jogos Unreal Engine é
   `<jogo>\<Projeto>\Binaries\Win64\`. Devem aparecer `nvapi64.dll`, `version.dll`,
   `D4R_WINDOWS_README.txt` e a pasta `d4r\`.
   - Se a pasta já tiver um `version.dll` de outro mod, os dois não funcionam juntos.
2. Copie o `OptiScaler.dll` do OptiScaler 0.9.4 para a mesma pasta com o nome **`dxgi.dll`**, junto com o
   `OptiScaler.ini`.
3. Copie o `nvngx_dlss.dll` para `d4r\` e o `_nvngx.dll` para `d4r\ngx\`.
4. Abra o PowerShell na pasta do jogo e rode:
   ```powershell
   powershell -ExecutionPolicy Bypass -File d4r\setup.ps1
   ```
   O script confere tudo, gera os manifests dos kernels nativos a partir do seu `nvngx_dlss.dll` e ajusta o
   `OptiScaler.ini` (o original fica salvo como `OptiScaler.ini.d4r-backup`). Corrija o que ele apontar e rode
   de novo até ele dizer que está tudo certo.

## 4. Primeiro teste, sem o jogo (o mais importante)

```powershell
powershell -ExecutionPolicy Bypass -File d4r\test-dlss.ps1
```

O script faz duas coisas:

1. **Teste de VRAM compartilhada** (`d4r\tools\d4r-interop-probe.exe`, leva poucos segundos; o relatório vai
   para `d4r\interop-report.txt`). Ele mostra se o driver deixa o HIP e o D3D12 compartilharem memória de
   vídeo, nos dois sentidos, e se a sincronização pela GPU funciona. A última linha resume:
   - `RESULT: VRAM sharing works ...`: o caminho rápido, sem cópias pela RAM, vai funcionar nos jogos.
   - `RESULT: ... FAILS`: o d4r continua funcionando, só copia pela RAM. Me mande o relatório.
2. **DLSS em quadros sintéticos**, 1280x720 → 2560x1440, sem jogo e sem OptiScaler. Ele grava
   `d4r\test-output.raw.bmp`, o último quadro gerado: um padrão de teste nítido em movimento quer dizer que
   funciona; preto ou ruído quer dizer que não.

**A primeira execução pode travar por vários minutos** enquanto o ZLUDA compila os kernels da DLSS. O cache
fica em `%LOCALAPPDATA%\zluda`, e as próximas execuções são rápidas.

Opções: `-Model M` (DLSS 4.5), `-Model E` (DLSS 3 CNN), `-Frames 120`.

## 5. No jogo

Abra o jogo e escolha **DLSS** nas opções gráficas, começando pelo modo Quality. Na primeira vez também pode
haver uma pausa longa por causa da compilação dos kernels.

Configurações em `d4r\d4r.ini` (reinicie o jogo depois de mudar):

| Chave | O que faz |
|---|---|
| `[DLSS] Model` | `K` (DLSS 4, padrão), `M` (DLSS 4.5, melhor imagem, mais pesado), `E` (DLSS 3 CNN) |
| `[Latency] FrameAge` | `1` = menor latência; `2`–`3` = mais FPS, mais latência |
| `[Kernels] NativeFp8` | RDNA4: FP8 nativo (`on`) ou 16 bits (`off`); compare FPS e imagem |
| `[Kernels] PreferAccuracy` | `true` busca fidelidade máxima à DLSS da RTX, mais lento |
| `[Interop] VramInterop` | `true` usa VRAM compartilhada quando possível |
| `[Env] D4R_HIP_DEVICE = N` | força outra GPU (os números aparecem no log, nas linhas "HIP device") |

## 6. O que me mandar

Depois de cada teste, junte estes arquivos (todos na pasta do jogo):

- `d4r\d4r_nvngx.log`: log do d4r e da bridge, recriado a cada execução;
- `d4r_nvapi.log`: tudo o que o jogo, o OptiScaler e o NGX pediram ao NVAPI (linhas "unimplemented" mostram o
  que pode faltar);
- `d4r\interop-report.txt`;
- a saída do `d4r\setup.ps1 -CheckOnly` e do `d4r\test-dlss.ps1` (copie do PowerShell);
- `OptiScaler.log` (ponha `LogToFile=true` na seção `[Log]` do `OptiScaler.ini`);
- se funcionar: FPS com DLSS K e M em Quality, com `NativeFp8` `on` e `off`, e prints no mesmo lugar.

## Problemas prováveis e o que fazer

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| O OptiScaler não oferece DLSS | o `version.dll` do d4r não carregou o NVAPI antes do OptiScaler | confira se o `version.dll` é o do d4r (o `setup.ps1` verifica) e se nenhum outro mod substituiu |
| `loading ZLUDA ... failed` no log | HIP não encontrado | instale o HIP SDK, reinicie, ou aponte `RocmDir` em `d4r\d4r.ini` |
| `ZLUDA cuInit failed` | HIP SDK sem suporte à GPU ou driver antigo | atualize o driver e o HIP SDK |
| O jogo congela 1–3 min ao ativar a DLSS | compilação dos kernels na primeira vez | espere; nas próximas vezes é rápido |
| Imagem preta ou com ruído | kernel incorreto ou NGX recusou algo | mande os logs e o `test-output.raw.bmp` |
| Log com "HIP device 0: ... integrated" | a iGPU do 7800X3D está ativa | normal: o d4r escolhe a RX 9070 XT. Se quiser, desative a iGPU na BIOS |

**Não use em jogos com anti-cheat online**: a injeção de DLL do OptiScaler pode dar ban.

## Desinstalar

Apague `nvapi64.dll`, `version.dll`, `dxgi.dll`, `OptiScaler.ini`, `OptiScaler.log`,
`D4R_WINDOWS_README.txt` e a pasta `d4r\` da pasta do jogo. Se você já usava o OptiScaler antes, restaure o
`OptiScaler.ini.d4r-backup`. Os caches `%LOCALAPPDATA%\zluda` e `%LOCALAPPDATA%\d4r` também podem ser
apagados.
