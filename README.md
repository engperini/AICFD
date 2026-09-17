# AICFD

Pipeline open-source de CFD para data centers, empacotado como skill do Claude Code.

## O que tem aqui

- `.claude/skills/datacenter-cfd/` — skill `datacenter-cfd`: pipeline completo de simulação
  térmica/aerodinâmica de data center com OpenFOAM (racks como meio poroso Darcy-Forchheimer,
  CRAC/fan wall como patch de velocidade, `buoyantSimpleFoam`, pós-processamento sem GUI).
- `.claude/skills/datacenter-cfd/reference-case/` — caso OpenFOAM completo e convergido
  (1 rack + 1 fan wall, sala 6x4x3m) para copiar e adaptar.

## Usar a skill

Numa sessão do Claude Code neste repositório, a skill é carregada automaticamente quando o
assunto for CFD de data center (ex.: "simula o resfriamento desta sala", "hot aisle/cold aisle",
"importa o modelo federado Revit pro CFD").

## Rodar o caso de referência direto

```bash
apt-get install -y openfoam openfoam-examples
./.claude/skills/datacenter-cfd/reference-case/Allrun
```

Convergência esperada: ~800 iterações, ~90s numa malha de 72k células. Resultado validado:
ar entra a 17.85 °C, sai do rack a ~19.3 °C, com subida monotônica de temperatura
do corredor frio para o corredor quente.

Detalhes de parâmetros ajustáveis e checklist de validação física: veja
`.claude/skills/datacenter-cfd/SKILL.md` e o `README.md` do caso de referência.
