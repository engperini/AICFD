---
name: datacenter-cfd
description: Use this skill whenever the user wants to build, run, or troubleshoot a CFD (computational fluid dynamics) simulation of a data center, server room, or any rack/CRAC/CRAH airflow-cooling problem using open-source tools (OpenFOAM). Covers geometry simplification from BIM/IFC/Revit models into CFD-ready abstractions, porous-media modeling of racks and floor tiles, fan/CRAC boundary conditions, mesh generation (blockMesh/snappyHexMesh), buoyant solvers, convergence, and post-processing. Trigger on: "CFD do datacenter", "simulação de resfriamento", "hot aisle/cold aisle simulation", "rack thermal simulation", "CRAC/CRAH airflow", "OpenFOAM datacenter", or requests to import a federated Revit/BIM model into a CFD workflow.
---

# CFD de Data Center com OpenFOAM (open-source, validado)

## Por que este approach

Ferramentas comerciais (6SigmaRoom/DCX, Future Facilities, Coolsim) fazem, por baixo dos panos,
exatamente o que está aqui: um solver de Navier-Stokes com flutuação térmica (buoyancy),
onde **racks e pisos perfurados são representados como meios porosos com queda de pressão
(Darcy-Forchheimer)**, não como geometria detalhada de servidor. Isso é o que torna a simulação
viável em minutos em vez de dias. Este skill documenta um pipeline 100% open-source (OpenFOAM)
que foi construído e **efetivamente executado e validado** neste ambiente — não é teoria.

## Arquitetura do pipeline (de trás pra frente, como o projeto foi construído)

```
[Modelo federado Revit] --IFC--> [IfcOpenShell: extrai só o que afeta ar] --STEP/STL-->
[Malha: blockMesh (sala simples) ou snappyHexMesh (geometria complexa)] -->
[fvOptions: rack = zona porosa + fonte de calor | CRAC = patch de velocidade] -->
[buoyantSimpleFoam (regime permanente, k-epsilon, ar como gás ideal)] -->
[foamToVTK + pyvista/ParaView: mapas de T, ar quente/frio, recirculação]
```

Comece sempre pela ponta final (um rack + um fan wall numa sala pequena, como no
`reference-case/`), valide fisicamente (o ar deve esquentar monotonicamente do corredor frio
para o corredor quente), e só depois generalize para múltiplos racks / geometria via IFC.

## ⚠️ Bug conhecido do pacote Ubuntu `openfoam` (apt) — resolva isso primeiro

O pacote `openfoam` do Ubuntu 24.04 (`apt install openfoam openfoam-examples`) instala os
solvers em `/usr/bin` mas **não inclui os scripts de shell** (`foamEtcFile`, `foamCleanPath`)
usados por `etc/bashrc`. Isso quebra a resolução do `controlDict` global e todo solver falha com:

```
FOAM FATAL ERROR: Could not find mandatory etc entry (mode=ugo) 'controlDict'
```

**Causa raiz real** (confirmada com `strace`): não é a ausência do script em si — os binários
não chamam `foamEtcFile` via `execve`. O problema é que `WM_PROJECT_USER_DIR` e outras variáveis
ficam poluídas/incorretas quando o `bashrc` é parcialmente executado com erros. A correção que
funciona é rodar os solvers com um **ambiente mínimo e limpo**, sem herdar o shell poluído:

```bash
cat > foam_env.sh << 'EOF'
export HOME=/root
export WM_PROJECT_DIR=/usr/share/openfoam
export WM_PROJECT=OpenFOAM
export WM_PROJECT_VERSION=v1912
export PATH=/usr/bin:/bin:/usr/local/bin
EOF

env -i $(cat foam_env.sh | sed 's/export //') bash -c 'cd /caminho/do/caso && blockMesh'
```

Use esse padrão (`env -i ...`) para **todo** comando OpenFOAM (`blockMesh`, `checkMesh`,
`topoSet`, `buoyantSimpleFoam`, `foamToVTK`, etc.) neste ambiente. Instalação:

```bash
apt-get update -qq && apt-get install -y openfoam openfoam-examples
```

## Passo a passo do caso de referência (1 rack + 1 fan wall)

Estrutura mínima de um caso OpenFOAM:
```
caso/
  system/{controlDict, fvSchemes, fvSolution, blockMeshDict, topoSetDict}
  constant/{g, turbulenceProperties, thermophysicalProperties, fvOptions}
  0/{U, p, p_rgh, T, k, epsilon, nut, alphat}
```

### 1. Geometria simplificada (blockMesh)
Uma sala é um único `hex` block. Defina patches nomeados para cada superfície funcional —
não use "wall" genérico para tudo:
- `fanwall`: patch de velocidade fixa (representa a parede de ventiladores/CRAC)
- `return`: patch de pressão fixa (retorno de ar, ralo/plenum)
- `floor`/`ceiling`/`sidewalls`: paredes sem escorregamento (`noSlip`), adiabáticas

### 2. Rack como zona porosa (topoSet + fvOptions)
```
// system/topoSetDict
actions
(
    { name rack; type cellSet;     action new; source boxToCell;
      box (3.0 1.5 0.0) (3.6 2.5 2.0); }
    { name rack; type cellZoneSet; action new; source setToCellZone; set rack; }
);
```
```
// constant/fvOptions
rackPorosity
{
    type explicitPorositySource;
    explicitPorositySourceCoeffs
    {
        selectionMode cellZone; cellZone rack;
        type DarcyForchheimer;
        DarcyForchheimerCoeffs
        {
            d d [0 -2 0 0 0 0 0] (20 100000 100000);   // baixa resistência no eixo do fluxo (x), altíssima nas laterais
            f f [0 -1 0 0 0 0 0] (2 500 500);
            coordinateSystem { type cartesian; origin (0 0 0);
              coordinateRotation { type axesRotation; e1 (1 0 0); e2 (0 1 0); } }
        }
    }
}
rackHeat
{
    type scalarSemiImplicitSource;
    scalarSemiImplicitSourceCoeffs
    {
        selectionMode cellZone; cellZone rack;
        volumeMode absolute;
        injectionRateSuSp { h (5000 0); }   // 5000 W de carga de TI no rack
    }
}
```
`d` alto nas direções y/z força o ar a atravessar o rack só no eixo x (frente-fundo), como um
rack real. `d` moderado em x dá a perda de carga do equipamento. O campo de energia é `h`
(entalpia sensível) porque `thermophysicalProperties` usa `energy sensibleEnthalpy`.

### 3. Solver: buoyantSimpleFoam
Regime permanente, ar como gás ideal (`perfectGas`), turbulência `kEpsilon`. Essencial ter
`constant/g` (gravidade) — é o que ativa o empuxo térmico. Rodar:
```bash
env -i $(cat foam_env.sh|sed 's/export //') bash -c 'cd caso && blockMesh && topoSet && checkMesh && buoyantSimpleFoam'
```
Case de referência convergiu em ~800 iterações / ~90s numa malha de 72k células.

### 4. Pós-processamento (sem GUI)
```bash
env -i ... foamToVTK -latestTime
python3 -c "
import pyvista as pv
m = pv.read('VTK/<case>_<time>/internal.vtu')
slice_ = m.cell_data_to_point_data().slice(normal='z', origin=(x,y,z))
p = pv.Plotter(off_screen=True)
p.add_mesh(slice_, scalars='T', cmap='coolwarm')
p.screenshot('T_slice.png')
"
```
Rode `xvfb-run -a python3 ...` se faltar display virtual.

## Checklist de validação física (sempre confira antes de confiar no resultado)
1. `checkMesh` sem "FAILED" (non-orthogonality, skewness, aspect ratio).
2. Temperatura sobe **monotonicamente** do corredor frio → dentro do rack → corredor quente.
3. `time step continuity errors` caindo e pequeno (<1e-6 típico).
4. Resíduos finais (`Ux, Uy, Uz, h, p_rgh, k, epsilon`) abaixo de 1e-4/1e-5.
5. Vazão mássica de entrada ≈ vazão de saída (conservação de massa em regime permanente).

## Como isso escala para um datacenter real (múltiplos racks + modelo federado)

1. **Extração de geometria do Revit federado**: exporte para IFC (nunca tente ler .rvt
   diretamente). Use `IfcOpenShell` (Python) para filtrar só as categorias relevantes ao
   fluxo de ar (paredes, piso elevado, forro, racks, CRAC) e descartar estrutura/elétrica/
   tubulação — isso é a "simplificação" que reduz o modelo BIM a um modelo CFD.
2. **Um cellZone porosa por rack** (ou por fileira de racks, se quiser simplificar mais),
   com a carga térmica (kW) vinda de um DCIM/planilha, não do Revit.
3. **Um patch de velocidade por CRAC/fan wall**, com vazão real (CFM/m³h) do equipamento,
   não um valor arbitrário — isso é o que mais muda o ΔT resultante.
4. **Piso elevado perfurado**: outro cellZone poroso (ou patch, se modelado como superfície),
   com coeficiente de perda de carga do tile (dado do fabricante ou ~40-60% de área livre).
5. Para geometria não regular, troque `blockMesh` por `snappyHexMesh` sobre um STL exportado
   do IFC simplificado.
6. Radiação geralmente é desprezível perto de racks (dominado por convecção forçada); ignore
   `radiationProperties` a menos que haja janelas/solar.

## Erros comuns e como corrigir
- **Solver diverge logo no início**: `d`/`f` da porosidade longe demais dos valores físicos, ou
  velocidade de entrada implicando Reynolds absurdo para a malha. Reduza `relaxationFactors`
  (U e h para 0.2-0.3) e comece com `simpleFoam` incompressível antes de ligar o `buoyant*`.
- **Campo `h` não existe / solver não reconhece a fonte de calor**: confira `energy` em
  `thermophysicalProperties` — só é `h` se for `sensibleEnthalpy`; se for `sensibleInternalEnergy`
  o campo é `e`.
- **Todo o domínio esquenta uniformemente (rack "não aparece")**: `cellZone` provavelmente vazia
  — rode `topoSet` antes do solver e confirme com `checkMesh` que a cellZone tem células.
- **`Could not find mandatory etc entry`**: ver seção do bug do apt acima.

## Arquivos de referência
Veja `reference-case/` neste skill: um caso OpenFOAM completo, testado e convergido
(1 rack + 1 fan wall, sala 6x4x3m) que pode ser copiado e adaptado como ponto de partida.
