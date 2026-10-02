# d4r no Windows 11 nativo: guia para a RX 9070 XT (Ryzen 7 7800X3D)

O d4r tem um **port nativo para Windows oficial**, no branch
[`windows`](https://github.com/countervolts/d4r/tree/windows) do projeto original. Ele foi feito por um
colaborador (xdfnx-dev) e aceito pelo mantenedor no PR #11 (tag `v0.1.4`). **Ele foi testado numa RX 9070 XT
(gfx1201), a sua placa.** Este guia resume como compilar e testar esse port no seu PC.

> **A documentação oficial manda.** Se algo aqui divergir dela, siga a oficial:
> [`docs/windows-game.md`](https://github.com/countervolts/d4r/blob/windows/docs/windows-game.md) (instalar e
> rodar num jogo), [`docs/windows-rdna4-port.md`](https://github.com/countervolts/d4r/blob/windows/docs/windows-rdna4-port.md)
> (compilar e testar) e [`docs/windows-gpu-support.md`](https://github.com/countervolts/d4r/blob/windows/docs/windows-gpu-support.md)
> (GPUs e diagnóstico).

## Estado do port oficial (2 de outubro de 2026)

- DLSS 4 (modelo K) e DLSS 4.5 (modelo M) rodam na RX 9070 XT. Entradas e saída ficam na VRAM, e cada frame
  mostra o próprio resultado, sem atraso.
- Todos os testes de hardware passam. Em Silent Hill 2 em 4K com K (entrada 2259x1271 → saída 3840x2160), uma
  cena parada mediu cerca de **65 FPS**, e uma sessão de 10 minutos (35.664 frames) terminou sem nenhum erro.
- Ainda é um **pacote de desenvolvimento**. O desempenho do K continua em trabalho, e o modelo L não foi validado
  no Windows.
- **Não existe download pronto.** Você compila no seu PC, porque os kernels nativos são gerados a partir do seu
  próprio `nvngx_dlss.dll` e arquivos da NVIDIA não podem ser distribuídos.

## 1. O que instalar antes

| O quê | Para quê |
|---|---|
| Driver AMD Adrenalin atual | a GPU |
| [AMD HIP SDK 7.2 para Windows](https://www.amd.com/en/developer/resources/rocm-hub/hip-sdk.html) (instala em `C:\Program Files\AMD\ROCm\7.2`) | compilar os helpers do ZLUDA. O runtime usado pelo jogo é outro (TheRock), que o setup baixa sozinho |
| [Git for Windows](https://git-scm.com/download/win) com Git LFS | baixar o código |
| [Python 3.11 x64](https://www.python.org/downloads/windows/) (marque "Add to PATH") | ferramentas de build e manifests |
| [Visual Studio 2022 Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022) com "Desktop development with C++" (MSVC v143 e Windows SDK 10.0.26100) | compilar o OptiScaler com os patches |

O resto (llvm-mingw, Rust, CMake, Ninja, NumPy e o runtime HIP TheRock `10.2.0a20260929`) os scripts baixam
para dentro da pasta `.tools` do repositório, com hash conferido, sem instalar nada no sistema. Separe espaço em
disco e **algumas horas** para o primeiro build: o LLVM do ZLUDA é a parte demorada.

**Arquivos da NVIDIA**, que você mesmo obtém:

- `nvngx_dlss.dll` **exatamente 310.9.1**. O script do jogo confere o SHA256 e recusa outra versão.
- `_nvngx.dll` versão **32.0.16.1714**, a validada. Pela numeração da NVIDIA, ela corresponde ao driver 617.14:
  baixe o instalador no site da NVIDIA, abra com o 7-Zip sem instalar, procure o `_nvngx.dll` e confira a versão
  em Propriedades → Detalhes.

Guarde os dois numa pasta fixa, por exemplo `C:\d4r-nvidia\`.

## 2. Baixar o código

No PowerShell, numa pasta com espaço, por exemplo `C:\src`:

```powershell
git lfs install
git clone -b windows https://github.com/countervolts/d4r.git
cd d4r
git clone https://github.com/vosen/ZLUDA external/ZLUDA
git -C external/ZLUDA checkout ee2f25a180099fa42f36b2346732e1f2470a03ad
git -C external/ZLUDA submodule update --init --recursive --depth 1
git -C external/ZLUDA lfs pull
```

## 3. Compilar

Todos os comandos rodam na pasta `d4r`, em ordem. Cada script para com uma mensagem clara se faltar algo.

```powershell
# ferramentas locais (uma vez)
powershell -NoProfile -File scripts/windows/setup-windows-tools.ps1 -RuntimeProfile therock
powershell -NoProfile -File scripts/windows/setup-rust-toolchain.ps1

# ZLUDA com os patches do d4r (o LLVM leva horas na primeira vez)
powershell -NoProfile -File scripts/windows/prepare-zluda-source.ps1
powershell -NoProfile -File scripts/windows/build-zluda-llvm.ps1
powershell -NoProfile -File scripts/windows/build-zluda-helpers.ps1
powershell -NoProfile -File scripts/windows/build-zluda-windows.ps1

# OptiScaler com os patches do d4r
powershell -NoProfile -File scripts/windows/build-optiscaler-windows.ps1

# shim e diagnósticos para a sua GPU
$arch = 'gfx1201'
$diag = "$PWD\dist\windows-$arch-diagnostics"
.\scripts\windows\build-windows-rdna4.ps1 -RuntimeProfile therock -GpuArch $arch -ZludaRoot "$PWD\dist\zluda-windows-final" -BuildDirectory "$PWD\build\windows-$arch" -InstallDirectory $diag
```

## 4. Testar no seu hardware, sem jogo

```powershell
.\scripts\windows\test-windows-rdna4.ps1 -RuntimeProfile therock -PackageRoot $diag -ZludaRoot "$PWD\dist\zluda-windows-final" -NgxCore "C:\d4r-nvidia\_nvngx.dll" -DlssDll "C:\d4r-nvidia\nvngx_dlss.dll"
```

Passou se terminar com código 0 e mostrar `PASS HIP`, `PASS CUDA`, `PASS INTEROP_LIFETIME`, `PASS INTEROP` e
`PASS NGX_INIT ... sr_available=1`. Se falhar, ele imprime o caminho de **um ZIP** com tudo o que é preciso para
diagnosticar.

## 5. Montar o pacote do jogo

```powershell
.\scripts\windows\stage-native-k.ps1 -DlssDll "C:\d4r-nvidia\nvngx_dlss.dll" -PackageRoot $diag
.\scripts\windows\stage-native-m.ps1 -DlssDll "C:\d4r-nvidia\nvngx_dlss.dll" -PackageRoot $diag
.\scripts\windows\package-windows-game.ps1 -DiagnosticRoot $diag -GpuArch $arch -PackageRoot "$PWD\dist\windows-$arch-game" -ArchivePath "$PWD\dist\windows-$arch-game.zip"
```

O pacote fica em `dist\windows-gfx1201-game`, sem nenhum arquivo da NVIDIA dentro.

## 6. Rodar um jogo

Escolha um jogo D3D12 **sem anti-cheat**. Na primeira vez, rode com os diagnósticos ligados:

```powershell
.\dist\windows-gfx1201-game\windows-game.ps1 -GameExe "D:\Jogos\...\Binaries\Win64\Jogo-Win64-Shipping.exe" -NgxCore "C:\d4r-nvidia\_nvngx.dll" -DlssDll "C:\d4r-nvidia\nvngx_dlss.dll" -Preset 11 -ValidateOutput -CaptureExceptions
```

- O script confere a GPU antes de mexer nos arquivos do jogo e faz backup dos originais.
- Ele instala o OptiScaler como `dxgi.dll`. No jogo, escolha DLSS nas opções gráficas.
- `-Preset 11` é o DLSS 4 (K); `-Preset 13` é o DLSS 4.5 (M), mais bonito e mais pesado.
- **A primeira vez pode travar por minutos** enquanto o ZLUDA compila os kernels. Depois fica rápido.
- Para medir FPS, rode sem `-ValidateOutput` e sem os perfis de diagnóstico.
- Ao fechar o jogo, o script imprime o caminho de um ZIP com os logs. Ele tem caminhos de arquivos, que podem
  incluir o seu nome de usuário do Windows.

Para **desfazer** a instalação no jogo:

```powershell
.\dist\windows-gfx1201-game\windows-game.ps1 -Action restore -GameExe "D:\Jogos\...\Jogo-Win64-Shipping.exe"
```

## A iGPU do 7800X3D

O port escolhe a GPU do HIP pela mesma placa que o jogo usa no D3D12 (pela LUID), então a iGPU não deve atrapalhar.
Se algo apontar para a GPU errada, desative a iGPU na BIOS e teste de novo.

## Se der problema

- Guarde o ZIP que o script imprime. Ele tem stdout/stderr, versões de DLLs, driver e logs do OptiScaler.
- Relate no projeto original: na [issue #10](https://github.com/countervolts/d4r/issues/10), onde o pessoal está
  testando o Windows, ou numa issue nova. Diga a GPU, o jogo, o comando usado e anexe o ZIP. Lembre que o ZIP tem
  caminhos com o seu nome de usuário.
- **Não use em jogos com anti-cheat online:** a injeção de DLL do OptiScaler pode dar ban.

## E o port alternativo do fork `r-ramos97/d4r`?

O branch `windows-native` do seu fork foi uma implementação paralela, feita antes de sabermos do port oficial. Ele
nunca rodou numa GPU real. Fica como arquivo: **use o port oficial acima**. O que dele ainda pode ajudar o projeto
original está em [CONTRIBUICOES_WINDOWS.md](CONTRIBUICOES_WINDOWS.md).
