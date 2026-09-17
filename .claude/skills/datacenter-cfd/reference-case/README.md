# Caso de referência: rack IT + fan wall (validado)

Sala de 6m (comprimento do fluxo) x 4m x 3m. Fan wall na face x=0 (patch `fanwall`,
1.8 m/s), retorno na face x=6 (patch `return`). Um rack de 0.6x1.0x2.0m modelado como
zona porosa com Darcy-Forchheimer (bloqueia fluxo lateral, permite passagem frente-fundo)
e fonte de calor de 5000 W injetada no campo de entalpia (`h`).

## Rodar
```bash
apt-get install -y openfoam openfoam-examples   # se ainda não instalado
./Allrun
```
Convergência esperada: ~800 iterações, ~90s numa malha de 72k células.

## Resultado esperado (validado)
- Ar entra a 17.85°C (291K)
- Dentro do rack: média 18.7°C, pico 19.5°C
- Corredor quente logo atrás do rack: ~19.3°C
- Temperatura sobe monotonicamente frio → rack → quente (fisicamente consistente)

## Parâmetros para ajustar
| O que mudar | Onde | Efeito |
|---|---|---|
| Carga térmica do rack | `constant/fvOptions` → `injectionRateSuSp { h (VALOR 0); }` | ΔT do rack |
| Vazão do fan wall | `0/U` → `fanwall` → `value uniform (VELOCIDADE 0 0)` | vazão total, ΔT inverso |
| Posição/tamanho do rack | `system/topoSetDict` → `box` | onde o "IT" fica na sala |
| Resistência do rack | `constant/fvOptions` → `d`/`f` | queda de pressão através do equipamento |
| Malha mais fina/grosseira | `system/blockMeshDict` → `(60 40 30)` | precisão vs. tempo de solução |
